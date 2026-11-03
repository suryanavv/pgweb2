# pgweb2 frontend

React 19 + TypeScript + Vite + Tailwind v4 + shadcn/ui (Base UI flavour).

## Run

```bash
# backend (from ../backend) — see its README; plain-http dev needs a non-Secure session cookie
alembic upgrade head
python -m app.cli create-user you@example.com      # first user is the admin
PGWEB_COOKIE_SECURE=false uvicorn app.main:app --port 8000

# frontend
npm install
npm run dev        # http://localhost:5173, /api is proxied to :8000 (override with API_TARGET=http://host:port)
```

Sign-in uses an HttpOnly `SameSite=strict` session cookie, so the browser must see the UI and API as
one origin. Dev uses the Vite proxy; in production serve the built `dist/` and `/api` from the same
host (reverse proxy).

Run the browser-independent auth-event checks with `npm test`. The account-switch acceptance check
requires two tabs in the same browser profile: populate data in one tab, change or end the session in
the other, and verify the first tab clears its workspace before rendering the refreshed identity.

## Layout

- `src/lib/api.ts` — fetch client for the cookie session; a 401 from a data endpoint signs the user out
- `src/stores/` — which connection is open and the open tabs, persisted with the verified owner identity
- `src/components/auth/` — login, account menu, change password, team members, audit log
- `src/components/grid/` — virtualized data grid (TanStack Virtual)
- `src/components/editor/` — CodeMirror 6 SQL editor + WHERE filter with table/column autocomplete
- `src/components/workspace/` — explorer, tabs, table view with staged row edits, structure view, query tab (with manual transactions), command palette
