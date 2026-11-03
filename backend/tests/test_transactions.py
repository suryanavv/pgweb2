import asyncio

import pytest

from app.core.config import get_settings
from app.db.transactions import TransactionManager, TxError, txs


async def _conn(client, body, **extra):
    r = await client.post("/api/connections", json={**body, **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _begin(client, cid):
    r = await client.post(f"/api/connections/{cid}/transactions")
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _count(client, cid, where="true"):
    r = await client.post(f"/api/connections/{cid}/query", json={"sql": f"select count(*) from items where {where}"})
    return int(r.json()["rows"][0][0])


async def test_rollback_discards_and_isolation_holds(new_user, conn_body):
    u = await new_user()
    cid = await _conn(u, conn_body)
    tx = await _begin(u, cid)
    r = await u.post(f"/api/transactions/{tx}/query", json={"sql": "insert into items (name) values ('rolled-back')"})
    assert r.status_code == 200 and r.json()["status"] == "INSERT 0 1" and r.json()["tx"]["statements"] == 1

    inside = await u.post(f"/api/transactions/{tx}/query", json={"sql": "select count(*) from items where name = 'rolled-back'"})
    assert inside.json()["rows"] == [[1]]  # visible in the transaction...
    assert await _count(u, cid, "name = 'rolled-back'") == 0  # ...but not to anyone else yet

    assert (await u.post(f"/api/transactions/{tx}/rollback")).status_code == 204
    assert await _count(u, cid, "name = 'rolled-back'") == 0
    assert (await u.post(f"/api/transactions/{tx}/query", json={"sql": "select 1"})).status_code == 404


async def test_commit_persists(new_user, conn_body):
    u = await new_user()
    cid = await _conn(u, conn_body)
    tx = await _begin(u, cid)
    await u.post(f"/api/transactions/{tx}/query", json={"sql": "insert into items (name) values ('committed')"})
    assert await _count(u, cid, "name = 'committed'") == 0
    assert (await u.post(f"/api/transactions/{tx}/commit")).status_code == 204
    assert await _count(u, cid, "name = 'committed'") == 1
    assert (await u.get("/api/transactions")).json() == []


async def test_a_failed_statement_does_not_poison_the_transaction(new_user, conn_body):
    u = await new_user()
    cid = await _conn(u, conn_body)
    tx = await _begin(u, cid)
    q = lambda sql: u.post(f"/api/transactions/{tx}/query", json={"sql": sql})
    assert (await q("insert into items (name) values ('kept')")).status_code == 200
    bad = await q("insert into items (name) values (null)")  # NOT NULL violation
    assert bad.status_code == 400 and "null value" in bad.json()["detail"]
    assert (await q("selec typo")).status_code == 400
    ok = await q("select count(*) from items where name = 'kept'")
    assert ok.status_code == 200 and ok.json()["rows"] == [[1]]  # earlier work survived both errors
    assert ok.json()["tx"]["statements"] == 2  # only successful statements count
    assert (await u.post(f"/api/transactions/{tx}/commit")).status_code == 204
    assert await _count(u, cid, "name = 'kept'") == 1


@pytest.mark.parametrize("sql", ["COMMIT", "rollback", "  begin", "START TRANSACTION", "savepoint x", "release x", "END"])
async def test_transaction_control_statements_are_rejected(new_user, conn_body, sql):
    u = await new_user()
    cid = await _conn(u, conn_body)
    tx = await _begin(u, cid)
    r = await u.post(f"/api/transactions/{tx}/query", json={"sql": sql})
    assert r.status_code == 400 and "Commit and Rollback" in r.json()["detail"]
    assert (await u.post(f"/api/transactions/{tx}/rollback")).status_code == 204


async def test_read_only_connections_cannot_begin(new_user, conn_body):
    u = await new_user()
    cid = await _conn(u, conn_body, read_only=True)
    r = await u.post(f"/api/connections/{cid}/transactions")
    assert r.status_code == 400 and "read-only" in r.json()["detail"]


async def test_transactions_belong_to_their_user(new_user, conn_body):
    alice, bob = await new_user(), await new_user()
    cid = await _conn(alice, conn_body)
    tx = await _begin(alice, cid)
    for path in (f"/api/transactions/{tx}/query", f"/api/transactions/{tx}/commit", f"/api/transactions/{tx}/rollback"):
        r = await bob.post(path, json={"sql": "select 1"})
        assert r.status_code == 404
    assert (await bob.get("/api/transactions")).json() == []
    listed = (await alice.get("/api/transactions")).json()
    assert [t["id"] for t in listed] == [tx]  # the UI can re-attach after a reload
    await alice.post(f"/api/transactions/{tx}/rollback")


async def test_open_transaction_cap(new_user, conn_body):
    u = await new_user()
    cid = await _conn(u, conn_body)
    a, b = await _begin(u, cid), await _begin(u, cid)
    r = await u.post(f"/api/connections/{cid}/transactions")
    assert r.status_code == 409 and "open transactions" in r.json()["detail"]
    for t in (a, b):
        await u.post(f"/api/transactions/{t}/rollback")
    assert (await u.post(f"/api/connections/{cid}/transactions")).status_code == 201


async def test_concurrent_begin_requests_reserve_slots_before_waiting(monkeypatch):
    class FakeTransaction:
        async def start(self):
            await asyncio.sleep(0.02)

        async def rollback(self):
            return None

        async def commit(self):
            return None

    class FakeConnection:
        def transaction(self):
            return FakeTransaction()

    class FakePool:
        def __init__(self):
            self.released = 0
            self.in_use = 0

        async def acquire(self, timeout):
            if self.in_use >= 3:
                raise TimeoutError
            await asyncio.sleep(0.02)
            self.in_use += 1
            return FakeConnection()

        async def release(self, _conn):
            self.released += 1
            self.in_use -= 1

    monkeypatch.setattr(get_settings(), "max_open_tx_per_user", 2)
    manager = TransactionManager()
    pool = FakePool()
    results = await asyncio.gather(
        *(manager.begin("dummy-user", "dummy-connection", pool) for _ in range(8)),
        return_exceptions=True,
    )
    opened = [value for value in results if not isinstance(value, BaseException)]
    rejected = [value for value in results if isinstance(value, TxError)]
    assert len(opened) == 2
    assert len(rejected) == 6 and all(error.status == 409 for error in rejected)
    assert len(manager.list_for("dummy-user")) == 2
    assert manager._starting_by_user == {}
    other_user_session = await manager.begin("another-dummy-user", "dummy-connection", pool)
    assert pool.in_use == 3

    for session in opened:
        await manager.finish(session, commit=False)
    await manager.finish(other_user_session, commit=False)
    assert pool.released == 3 and pool.in_use == 0


async def test_failed_transaction_start_releases_reservation(monkeypatch):
    class FailedTransaction:
        async def start(self):
            raise RuntimeError("dummy start failure")

    class FakeConnection:
        def transaction(self):
            return FailedTransaction()

    class FakePool:
        released = 0

        async def acquire(self, timeout):
            return FakeConnection()

        async def release(self, _conn):
            self.released += 1

    monkeypatch.setattr(get_settings(), "max_open_tx_per_user", 1)
    manager = TransactionManager()
    pool = FakePool()
    with pytest.raises(RuntimeError, match="dummy start failure"):
        await manager.begin("dummy-user", "dummy-connection", pool)
    assert manager._starting_by_user == {}
    assert not manager.list_for("dummy-user")
    assert pool.released == 1


async def test_pool_acquire_timeout_releases_transaction_reservation(monkeypatch):
    class FakePool:
        async def acquire(self, timeout):
            raise TimeoutError

    monkeypatch.setattr(get_settings(), "max_open_tx_per_user", 1)
    manager = TransactionManager()
    with pytest.raises(TxError) as error:
        await manager.begin("dummy-user", "dummy-connection", FakePool())
    assert error.value.status == 503
    assert manager._starting_by_user == {}


async def test_idle_transactions_are_reaped_and_release_their_connection(new_user, conn_body, monkeypatch):
    u = await new_user()
    cid = await _conn(u, conn_body)
    tx = await _begin(u, cid)
    await u.post(f"/api/transactions/{tx}/query", json={"sql": "insert into items (name) values ('abandoned')"})
    monkeypatch.setattr(get_settings(), "tx_idle_timeout_seconds", 0)
    await asyncio.sleep(0.05)
    await txs.reap_once()
    monkeypatch.undo()
    assert (await u.get("/api/transactions")).json() == []
    assert (await u.post(f"/api/transactions/{tx}/query", json={"sql": "select 1"})).status_code == 404
    assert await _count(u, cid, "name = 'abandoned'") == 0  # rolled back, not committed
    # the pinned connection went back to the pool: new work still runs
    assert await _count(u, cid) >= 5


async def test_deleting_a_connection_rolls_back_its_open_transactions(new_user, conn_body):
    u = await new_user()
    cid = await _conn(u, conn_body)
    tx = await _begin(u, cid)
    await u.post(f"/api/transactions/{tx}/query", json={"sql": "insert into items (name) values ('gone')"})
    r = await asyncio.wait_for(u.delete(f"/api/connections/{cid}"), timeout=10)  # would hang if the pool waited on the pinned conn
    assert r.status_code == 204
    assert (await u.get("/api/transactions")).json() == []
    cid2 = await _conn(u, conn_body)
    assert await _count(u, cid2, "name = 'gone'") == 0


async def test_transaction_events_are_audited(admin, new_user, conn_body):
    u = await new_user()
    cid = await _conn(u, conn_body)
    tx = await _begin(u, cid)
    await u.post(f"/api/transactions/{tx}/query", json={"sql": "update items set name = name where id = 1"})
    await u.post(f"/api/transactions/{tx}/commit")
    actions = [e["action"] for e in (await admin.get("/api/audit", params={"user_email": u.email})).json()]
    for expected in ("tx_begin", "tx_query", "tx_commit"):
        assert expected in actions
