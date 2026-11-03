import logging
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import audit
from app.core.bootstrap import ensure_bootstrap_admin
from app.core.config import get_settings
from app.db.connections import pools
from app.db.session import get_engine, sessionmaker
from app.db.transactions import txs
from app.routers import audit as audit_router
from app.routers import auth, connections, query, schema, transactions, users

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()  # fail fast on missing config
    async with sessionmaker()() as db:
        await ensure_bootstrap_admin(db, s.bootstrap_admin_email, s.bootstrap_admin_password)
        await audit.prune(db, s.audit_retention_days)
    txs.start_reaper()
    yield
    await pools.close_all()  # rolls back any open manual transactions first
    await get_engine().dispose()


app = FastAPI(title="pgweb2", lifespan=lifespan)

_SAFE = {"GET", "HEAD", "OPTIONS"}


@app.middleware("http")
async def require_ajax_header(request: Request, call_next):
    """CSRF defence in depth on top of SameSite=strict: browsers can't add this header cross-site without CORS approval."""
    if request.method not in _SAFE and request.url.path.startswith("/api/") and request.headers.get("x-requested-with") != "pgweb2":
        response = JSONResponse({"detail": "Missing X-Requested-With header"}, status_code=403)
    else:
        response = await call_next(request)

    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "private, no-store"
        vary = {value.strip() for value in response.headers.get("Vary", "").split(",") if value.strip()}
        vary.add("Cookie")
        response.headers["Vary"] = ", ".join(sorted(vary))
    return response


app.add_middleware(  # added last, so it is outermost and decorates every response
    CORSMiddleware,
    allow_origins=get_settings().cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
for r in (auth.router, users.router, audit_router.router, connections.router, schema.router, query.router, transactions.router):
    app.include_router(r)


@app.exception_handler(OSError)
@app.exception_handler(TimeoutError)
@app.exception_handler(asyncpg.PostgresError)
@app.exception_handler(asyncpg.InterfaceError)
async def upstream_db_error(_: Request, exc: Exception):
    """Pools connect lazily, so connect/auth/SQL errors surface here as 502 rather than 500."""
    return JSONResponse({"detail": f"Database error: {exc}"}, status_code=502)


@app.get("/healthz")
def healthz():
    return {"ok": True}
