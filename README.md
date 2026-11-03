# pgweb2

A web PostgreSQL client for a team: browse and edit tables, run SQL with autocomplete, manual transactions, per-user accounts and an audit log.

- [`backend/`](backend/README.md): FastAPI API (accounts, encrypted connections, queries, transactions, audit)
- [`frontend/`](frontend/README.md): React UI

## Run with Docker

```bash
export PGWEB_BOOTSTRAP_ADMIN_EMAIL=you@example.com
export PGWEB_BOOTSTRAP_ADMIN_PASSWORD='a long passphrase'   # 10+ characters; only used while no users exist
export PGWEB_COOKIE_SECURE=false                            # plain-http trial run only, see below
docker compose up --build
```

Open <http://localhost:8080> and sign in. Then **remove the bootstrap password** from your environment and add teammates under Account menu → Team members.

| Service | What it does |
| --- | --- |
| `web` | nginx: serves the built UI and proxies `/api` to `api`. The only published port (`PGWEB_PORT`, default 8080). |
| `api` | FastAPI on the internal network only. SQLite data and the encryption key live in the `api_data` volume. |

For deployments behind a load balancer, set `PGWEB_TRUSTED_UPSTREAM_CIDR` to the actual source CIDR used by the proxy immediately in front of Nginx. Compose normalizes forwarded headers at Nginx and Uvicorn trusts only the fixed Nginx container address. The default trusts no external proxy chain. If `172.30.0.0/24` overlaps another Docker network, change `PGWEB_DOCKER_SUBNET`, `PGWEB_WEB_IP`, and `PGWEB_API_IP` together so both fixed addresses remain inside the subnet. Never use `*` for trusted forwarded IPs.

For anything beyond a trial:

- **Terminate HTTPS in front of `web`** (Caddy, a cloud load balancer, ...) and leave `PGWEB_COOKIE_SECURE` at its default `true`. Sessions are `Secure` cookies and the UI and API must share one origin. Add `Strict-Transport-Security` at that layer.
- **Set `PGWEB_SECRET_KEY`** from a secret store and back it up. It encrypts the stored database passwords; losing it makes every saved connection unreadable. (If unset, a key is generated into the volume, next to the data it protects.)
- **Set `PGWEB_ALLOWED_HOSTS`** to the database hosts people may register, e.g. `["*.rds.amazonaws.com"]`.
- **Back up the `api_data` volume.**
- Don't scale `api` beyond one replica: open transactions live in its memory.
