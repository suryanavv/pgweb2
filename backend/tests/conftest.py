import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import httpx2
import pytest
import pytest_asyncio

# Settings and engines are cached at import time, so the environment must be set before `app` is imported.
_TMP = Path(tempfile.mkdtemp(prefix="pgweb2-test-"))
os.environ.update(
    PGWEB_DATA_DIR=str(_TMP / "api"),
    PGWEB_COOKIE_SECURE="false",
    PGWEB_MAX_LOGIN_FAILURES_PER_IP_EMAIL="3",
    PGWEB_MAX_LOGIN_FAILURES_PER_IP="100",
    PGWEB_BOOTSTRAP_ADMIN_EMAIL="admin@example.com",
    PGWEB_BOOTSTRAP_ADMIN_PASSWORD="correct horse battery",
    PGWEB_MAX_OPEN_TX_PER_USER="2",
)
ADMIN = ("admin@example.com", "correct horse battery")
HEADERS = {"X-Requested-With": "pgweb2"}


@pytest.fixture(scope="session")
def pg_socket() -> str:
    """A throwaway real Postgres (pip-packaged), reached over its unix socket."""
    import pixeltable_pgserver as pgserver

    srv = pgserver.get_server(_TMP / "pgdata", cleanup_mode="stop")
    srv.psql(
        "CREATE TABLE items (id serial PRIMARY KEY, name text NOT NULL);"
        "INSERT INTO items (name) SELECT 'seed' || g FROM generate_series(1, 5) g;"
    )
    yield re.search(r"host=(.+)$", srv.get_uri()).group(1)
    srv.cleanup()


@pytest.fixture(scope="session")
def migrated() -> None:
    backend = Path(__file__).resolve().parent.parent
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend, check=True, env=os.environ)


@pytest_asyncio.fixture(scope="session")
async def app(migrated):
    from app.main import app as fastapi_app

    async with fastapi_app.router.lifespan_context(fastapi_app):
        yield fastapi_app


def make_client(app, client=("127.0.0.1", 12345)) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app, client=client), base_url="http://test", headers=HEADERS)


async def login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest_asyncio.fixture(scope="session")
async def admin(app):
    async with make_client(app) as c:
        await login(c, *ADMIN)
        yield c


@pytest.fixture
def new_user(app, admin):
    """Factory: create a member and return a signed-in client for them."""
    clients: list[httpx2.AsyncClient] = []

    async def make(role="member"):
        email = f"{role}-{uuid.uuid4().hex[:8]}@example.com"
        r = await admin.post("/api/users", json={"email": email, "password": "a-long-password", "role": role})
        assert r.status_code == 201, r.text
        c = make_client(app)
        clients.append(c)
        await login(c, email, "a-long-password")
        c.email, c.user_id = email, r.json()["id"]
        return c

    yield make


@pytest.fixture
def conn_body(pg_socket):
    return dict(name="test db", host=pg_socket, port=5432, database="postgres", user="postgres",
                password="x", ssl_mode="disable", read_only=False)
