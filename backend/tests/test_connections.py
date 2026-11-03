import uuid

import asyncpg


async def _create(client, body, **extra):
    r = await client.post("/api/connections", json={**body, **extra})
    assert r.status_code == 201, r.text
    return r.json()


async def test_connection_roundtrip_never_leaks_password(new_user, conn_body):
    u = await new_user()
    c = await _create(u, conn_body)
    assert "password" not in str(c) and c["owned"] and c["can_manage"] and not c["shared"]
    listed = (await u.get("/api/connections")).json()
    assert [x["id"] for x in listed] == [c["id"]]
    tables = await u.get(f"/api/connections/{c['id']}/tables")
    assert tables.status_code == 200 and any(t["name"] == "items" for t in tables.json())
    cols = await u.get(f"/api/connections/{c['id']}/tables/public/items/columns")
    assert [x["name"] for x in cols.json()] == ["id", "name"] and cols.json()[0]["is_primary_key"]


async def test_connections_are_private_to_their_owner(new_user, conn_body):
    alice, bob = await new_user(), await new_user()
    c = await _create(alice, conn_body)
    assert (await bob.get("/api/connections")).json() == []
    for method, path, kw in [
        ("get", f"/api/connections/{c['id']}/tables", {}),
        ("post", f"/api/connections/{c['id']}/query", {"json": {"sql": "select 1"}}),
        ("get", f"/api/connections/{c['id']}/tables/public/items/rows", {}),
        ("patch", f"/api/connections/{c['id']}", {"json": {"name": "mine now"}}),
        ("delete", f"/api/connections/{c['id']}", {}),
        ("post", f"/api/connections/{c['id']}/transactions", {}),
    ]:
        r = await getattr(bob, method)(path, **kw)
        assert r.status_code == 404, (method, path, r.status_code)  # 404, not 403: ids can't be probed


async def test_only_admins_share_and_members_cannot_manage_shared(admin, new_user, conn_body):
    member = await new_user()
    assert (await member.post("/api/connections", json={**conn_body, "shared": True})).status_code == 403

    shared = await _create(admin, conn_body, shared=True, name="team db")
    seen = (await member.get("/api/connections")).json()
    assert [(x["name"], x["can_manage"], x["owned"]) for x in seen] == [("team db", False, False)]
    assert (await member.post(f"/api/connections/{shared['id']}/query", json={"sql": "select count(*) from items"})).status_code == 200
    assert (await member.delete(f"/api/connections/{shared['id']}")).status_code == 403
    assert (await member.patch(f"/api/connections/{shared['id']}", json={"name": "hijack"})).status_code == 403
    # a member can't escalate by sharing their own connection either
    mine = await _create(member, conn_body)
    assert (await member.patch(f"/api/connections/{mine['id']}", json={"shared": True})).status_code == 403
    # but the owning admin can unshare
    assert (await admin.patch(f"/api/connections/{shared['id']}", json={"shared": False})).json()["shared"] is False
    assert [x["id"] for x in (await member.get("/api/connections")).json()] == [mine["id"]]  # unshared: gone for the member


async def test_bad_credentials_rejected_before_saving(new_user, conn_body):
    u = await new_user()
    r = await u.post("/api/connections", json={**conn_body, "port": 1})
    assert r.status_code == 400 and "Could not connect" in r.json()["detail"]
    assert (await u.get("/api/connections")).json() == []


async def test_read_only_blocks_writes_and_browse_is_always_read_only(new_user, conn_body):
    u = await new_user()
    ro = await _create(u, conn_body, read_only=True)
    r = await u.post(f"/api/connections/{ro['id']}/query", json={"sql": "insert into items (name) values ('x')"})
    assert r.status_code == 400 and "read-only" in r.json()["detail"]

    rw = await _create(u, conn_body, name="rw")
    r = await u.get(f"/api/connections/{rw['id']}/tables/public/items/rows?limit=2&order_by=id&desc=true")
    body = r.json()
    assert r.status_code == 200 and len(body["rows"]) == 2 and body["truncated"] is True
    assert body["rows"][0][0] > body["rows"][1][0]


async def test_switch_database_reuses_sibling_and_keeps_owner(admin, new_user, conn_body):
    member = await new_user()
    shared = await _create(admin, conn_body, shared=True, name="team A")
    target_db = f"pgweb2_test_{uuid.uuid4().hex}"
    setup = await asyncpg.connect(
        host=conn_body["host"], port=conn_body["port"], database=conn_body["database"],
        user=conn_body["user"], password=conn_body["password"],
    )
    try:
        await setup.execute(f'CREATE DATABASE "{target_db}"')
    finally:
        await setup.close()

    denied = await member.post(f"/api/connections/{shared['id']}/database", json={"database": target_db})
    assert denied.status_code == 403
    assert {c["id"] for c in (await member.get("/api/connections")).json()} == {shared["id"]}

    target = await _create(admin, {**conn_body, "database": target_db}, shared=True, name="team B")
    assert target["shared"] and target["can_manage"] is True
    listed = (await member.get("/api/connections")).json()
    assert target["id"] in {c["id"] for c in listed}

    switched = await member.post(f"/api/connections/{shared['id']}/database", json={"database": target_db})
    assert switched.status_code == 200 and switched.json()["id"] == target["id"]
    assert switched.json()["can_manage"] is False  # the member cannot manage the shared credential

    log = (await admin.get("/api/audit", params={"user_email": member.email})).json()
    assert any(e["action"] == "connection_db_switch_denied" for e in log)
    assert any(e["action"] == "connection_db_switch" for e in log)


async def test_audit_trail_records_who_ran_what(admin, new_user, conn_body):
    u = await new_user()
    c = await _create(u, conn_body)
    await u.post(f"/api/connections/{c['id']}/query", json={"sql": "select 42 as answer"})
    await u.post(f"/api/connections/{c['id']}/query", json={"sql": "select * from nope"})
    log = (await admin.get("/api/audit", params={"user_email": u.email})).json()
    queries = [e for e in log if e["action"] == "query"]
    assert {e["detail"] for e in queries} == {"select 42 as answer", "select * from nope"}
    assert any(e["error"] for e in queries) and any(e["row_count"] == 1 for e in queries)
    assert all(e["user_email"] == u.email and e["connection_name"] == "test db" for e in queries)
    assert any(e["action"] == "connection_create" for e in log)


async def test_schema_metadata_endpoints_and_they_are_not_audited_as_user_queries(admin, new_user, conn_body):
    u = await new_user()
    c = await _create(u, conn_body)
    cols = (await u.get(f"/api/connections/{c['id']}/columns")).json()
    items = next(t for t in cols if t["name"] == "items")
    assert items["schema_name"] == "public" and items["columns"] == ["id", "name"]  # ordinal order

    r = await u.get(f"/api/connections/{c['id']}/tables/public/items/structure")
    assert r.status_code == 200, r.text
    s = r.json()
    assert [(x["type"], x["name"]) for x in s["constraints"]] == [("primary_key", "items_pkey")]  # no NOT NULL noise
    assert [i["name"] for i in s["indexes"]] == ["items_pkey"]
    missing = await u.get(f"/api/connections/{c['id']}/tables/public/nope/structure")
    assert missing.status_code == 200 and missing.json() == {"indexes": [], "constraints": []}

    log = (await admin.get("/api/audit", params={"user_email": u.email})).json()
    assert not [e for e in log if e["action"] in ("query", "browse")]  # only things the user actually ran are logged as queries


async def test_schema_metadata_requires_access(new_user, conn_body):
    alice, bob = await new_user(), await new_user()
    c = await _create(alice, conn_body)
    for path in ("columns", "tables/public/items/structure"):
        assert (await bob.get(f"/api/connections/{c['id']}/{path}")).status_code == 404
