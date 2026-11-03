import pytest

from tests.conftest import ADMIN, make_client, login
from app.core.config import get_settings
from app.core import login_rate_limit
from app.core.login_rate_limit import LoginRateLimiter


async def test_requires_login(app):
    async with make_client(app) as c:
        assert (await c.get("/api/connections")).status_code == 401
        assert (await c.get("/api/auth/me")).status_code == 401
        assert (await c.get("/api/transactions")).status_code == 401


async def test_state_changing_requests_need_ajax_header(app):
    async with make_client(app) as c:
        c.headers.pop("X-Requested-With")
        r = await c.post("/api/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        assert r.status_code == 403


async def test_login_logout_and_cookie_flags(app):
    async with make_client(app) as c:
        r = await c.post("/api/auth/login", json={"email": "ADMIN@example.com ", "password": ADMIN[1]})
        assert r.status_code == 200 and r.json()["role"] == "admin"
        cookie = r.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie
        assert r.headers["cache-control"] == "private, no-store"
        assert "cookie" in r.headers.get("vary", "").lower()
        assert "password" not in r.text
        me = await c.get("/api/auth/me")
        assert me.status_code == 200
        assert me.headers["cache-control"] == "private, no-store"
        assert "cookie" in me.headers.get("vary", "").lower()
        raw = c.cookies.get("pgweb_session")
        assert (await c.post("/api/auth/logout")).status_code == 204
        c.cookies.set("pgweb_session", raw)  # replaying the old cookie must fail: the session is gone server-side
        assert (await c.get("/api/auth/me")).status_code == 401


async def test_sessions_are_independent_between_devices(app, new_user):
    first_device = await new_user()
    fresh_device = make_client(app)
    assert (await fresh_device.get("/api/auth/me")).status_code == 401

    await login(fresh_device, first_device.email, "a-long-password")
    first_cookie = first_device.cookies.get("pgweb_session")
    second_cookie = fresh_device.cookies.get("pgweb_session")
    assert first_cookie and second_cookie and first_cookie != second_cookie
    first_me = await first_device.get("/api/auth/me")
    second_me = await fresh_device.get("/api/auth/me")
    assert first_me.status_code == second_me.status_code == 200
    assert first_me.json()["id"] == second_me.json()["id"] == first_device.user_id

    await fresh_device.post("/api/auth/logout")
    assert (await fresh_device.get("/api/auth/me")).status_code == 401
    assert (await first_device.get("/api/auth/me")).status_code == 200
    await fresh_device.aclose()


async def test_each_session_resolves_its_own_account(new_user):
    user_a = await new_user()
    user_b = await new_user()
    response_a = await user_a.get("/api/auth/me")
    response_b = await user_b.get("/api/auth/me")
    assert response_a.status_code == response_b.status_code == 200
    assert response_a.json()["id"] == user_a.user_id
    assert response_b.json()["id"] == user_b.user_id
    assert response_a.json()["id"] != response_b.json()["id"]


async def test_all_api_responses_are_not_cacheable(app):
    async with make_client(app) as c:
        response = await c.get("/api/auth/me")
        assert response.status_code == 401
        assert response.headers["cache-control"] == "private, no-store"
        assert "cookie" in response.headers.get("vary", "").lower()


async def test_bad_credentials_are_uniform(app):
    async with make_client(app) as c:
        a = await c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "whatever-long"})
        b = await c.post("/api/auth/login", json={"email": ADMIN[0], "password": "wrong-password-x"})
        assert a.status_code == b.status_code == 401
        assert a.json() == b.json()  # can't tell which emails exist


async def test_login_failures_throttle_only_that_source_ip(app, new_user):
    victim = await new_user()
    async with make_client(app, ("192.0.2.10", 12001)) as c:
        for _ in range(3):
            assert (await c.post("/api/auth/login", json={"email": victim.email, "password": "nope-nope-nope"})).status_code == 401
        limited = await c.post("/api/auth/login", json={"email": victim.email, "password": "nope-nope-nope"})
        assert limited.status_code == 401 and limited.json() == {"detail": "Invalid email or password"}
    async with make_client(app, ("192.0.2.11", 12002)) as other_ip:
        r = await other_ip.post("/api/auth/login", json={"email": victim.email, "password": "a-long-password"})
        assert r.status_code == 200  # failures at one IP never lock the account for another IP


def test_login_limiter_enforces_pair_and_ip_limits_with_expiry_and_bounded_state(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "login_attempt_window_seconds", 5)
    monkeypatch.setattr(settings, "max_login_failures_per_ip_email", 2)
    monkeypatch.setattr(settings, "max_login_failures_per_ip", 3)
    monkeypatch.setattr(settings, "max_login_rate_entries", 4)
    clock = [100.0]
    monkeypatch.setattr(login_rate_limit, "monotonic", lambda: clock[0])
    limiter = LoginRateLimiter()

    assert limiter.reserve("192.0.2.20", "a@example.com")
    assert limiter.reserve("192.0.2.20", "a@example.com")
    assert not limiter.reserve("192.0.2.20", "a@example.com")  # in-flight attempts count toward the cap
    limiter.finish_failure("192.0.2.20", "a@example.com")
    limiter.finish_failure("192.0.2.20", "a@example.com")
    assert not limiter.reserve("192.0.2.20", "a@example.com")
    assert limiter.reserve("192.0.2.21", "a@example.com")
    limiter.release("192.0.2.21", "a@example.com")

    for email in ("b@example.com", "c@example.com", "d@example.com"):
        assert limiter.reserve("192.0.2.30", email)
        limiter.finish_failure("192.0.2.30", email)
    assert not limiter.reserve("192.0.2.30", "other@example.com")  # broader per-IP cap
    assert len(set(limiter._failures) | set(limiter._pending)) <= 4

    clock[0] += 6
    assert limiter.reserve("192.0.2.20", "a@example.com")
    limiter.release("192.0.2.20", "a@example.com")


async def test_user_admin_is_admin_only(app, admin, new_user):
    member = await new_user()
    assert (await member.get("/api/users")).status_code == 403
    assert (await member.post("/api/users", json={"email": "x@example.com", "password": "a-long-password"})).status_code == 403
    assert (await member.get("/api/audit")).status_code == 403
    assert (await admin.get("/api/users")).status_code == 200


async def test_password_policy_and_duplicates(admin):
    r = await admin.post("/api/users", json={"email": "weak@example.com", "password": "short"})
    assert r.status_code == 422
    r = await admin.post("/api/users", json={"email": "not-an-email", "password": "a-long-password"})
    assert r.status_code == 422
    r = await admin.post("/api/users", json={"email": ADMIN[0], "password": "a-long-password"})
    assert r.status_code == 409


async def test_deactivation_kills_sessions_immediately(admin, new_user):
    member = await new_user()
    assert (await member.get("/api/auth/me")).status_code == 200
    assert (await admin.patch(f"/api/users/{member.user_id}", json={"is_active": False})).status_code == 200
    assert (await member.get("/api/auth/me")).status_code == 401


async def test_cannot_lock_out_the_last_admin(admin):
    me = (await admin.get("/api/auth/me")).json()
    r = await admin.patch(f"/api/users/{me['id']}", json={"role": "member"})
    assert r.status_code == 400
    r = await admin.patch(f"/api/users/{me['id']}", json={"is_active": False})
    assert r.status_code == 400


async def test_change_password_signs_out_other_devices(app, new_user):
    a = await new_user()
    b = make_client(app)
    await login(b, a.email, "a-long-password")
    r = await a.post("/api/auth/password", json={"current_password": "a-long-password", "new_password": "another-long-password"})
    assert r.status_code == 204
    assert (await a.get("/api/auth/me")).status_code == 200  # this device stays signed in
    assert (await b.get("/api/auth/me")).status_code == 401  # others are signed out
    bad = await a.post("/api/auth/password", json={"current_password": "wrong-password-x", "new_password": "yet-another-long-one"})
    assert bad.status_code == 400
    await b.aclose()
