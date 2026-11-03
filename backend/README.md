# pgweb2 backend

FastAPI + asyncpg. Saved connections and users live in SQLite (`PGWEB_DATA_DIR`); target databases are reached with encrypted stored credentials.

## Run

```bash
uv sync
alembic upgrade head                         # always, after pulling: schema changes ship as migrations
python -m app.cli create-user you@example.com   # first user becomes admin and adopts any pre-existing connections
PGWEB_COOKIE_SECURE=false uvicorn app.main:app --port 8000   # false only for plain-http local dev
```

Or set `PGWEB_BOOTSTRAP_ADMIN_EMAIL` / `PGWEB_BOOTSTRAP_ADMIN_PASSWORD` to create the first admin on startup (Docker), then remove the password. See `.env.example` for every setting.

**Run a single worker.** Manual transactions pin a database connection in this process's memory, so
`uvicorn --workers N` (or multiple replicas without sticky routing) would lose track of them.

## Security model

- **Accounts:** email + Argon2 password. No public signup; admins create users. Lockout after repeated failures; a deactivation or password reset ends that user's sessions immediately.
- **Sessions:** opaque random token in an HttpOnly, `SameSite=strict`, Secure cookie. Only its SHA-256 is stored. Absolute and idle expiry (`PGWEB_SESSION_TTL_DAYS`, `PGWEB_SESSION_IDLE_MINUTES`).
- **CSRF:** `SameSite=strict` plus a required `X-Requested-With: pgweb2` header on every state-changing request.
- **Connections:** private to their owner unless an admin marks them *shared* (members can use a shared connection but never see its password). Unauthorised ids return 404, not 403. Passwords are Fernet-encrypted at rest; set `PGWEB_SECRET_KEY` from a secret store so the key doesn't sit beside the database, and never lose it.
- **Read-only by default:** read-only connections run `default_transaction_read_only`, and table browsing is always read-only.
- **Staged table edits:** writable connections can apply updates, inserts, and deletes to ordinary or partitioned tables with primary keys. `POST /api/connections/{id}/tables/{schema}/{table}/changes` validates columns and applies a batch atomically; updates and deletes require the primary key and the row version returned by table browsing. Stale rows return HTTP 409. Views and tables without primary keys cannot be edited through this endpoint.
- **Audit log:** every query, table browse, transaction event, login and admin change, with the SQL text as typed (so it may contain sensitive values). Admins read it at `GET /api/audit`; entries are pruned after `PGWEB_AUDIT_RETENTION_DAYS`.
- **Network:** publish only the web/Nginx service. Compose pins Nginx to `172.30.0.20`, and Uvicorn trusts forwarded headers only from that address. Nginx replaces incoming forwarding headers with its normalized client address. If a trusted load balancer or reverse proxy sits immediately in front of Nginx, set `PGWEB_TRUSTED_UPSTREAM_CIDR` to that proxy's actual source CIDR; the default trusts loopback only. Choose a non-conflicting `PGWEB_DOCKER_SUBNET` if `172.30.0.0/24` overlaps another Docker network. Never set Uvicorn's forwarded allowlist to `*` or trust client-supplied forwarding headers.
- **Login throttling:** failed attempts are held in a bounded in-memory limiter, scoped to source IP/email and source IP, and expire after `PGWEB_LOGIN_ATTEMPT_WINDOW_SECONDS`. The limiter resets on process restart and relies on the single-worker API deployment. It does not lock accounts globally.

## Manual transactions

`POST /api/connections/{id}/transactions` pins a connection and opens a transaction; run statements with `POST /api/transactions/{tx}/query`, then `/commit` or `/rollback`.

- Each statement runs in a savepoint, so a failed statement is undone but the transaction stays usable.
- `BEGIN`/`COMMIT`/`ROLLBACK`/`SAVEPOINT` statements are rejected; use the endpoints.
- Idle transactions roll back after `PGWEB_TX_IDLE_TIMEOUT_SECONDS` (default 10 min), and none lives past `PGWEB_TX_MAX_AGE_SECONDS`. Postgres' own `idle_in_transaction_session_timeout` backs this up if the API process dies.
- At most `PGWEB_MAX_OPEN_TX_PER_USER` per user. Not available on read-only connections.
- Deleting a connection, deactivating a user, or shutting down rolls back open transactions first.

## Tests

```bash
uv run pytest
```

Integration tests run against a real throwaway Postgres (pip-packaged, no Docker needed) and a fresh SQLite database migrated with Alembic.
