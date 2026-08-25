# ashtonashton-backend

Two independent FastAPI services — `social/` and `drive/` — for
ashtonashton.net. Originally one combined app with its own local
username/password auth and a single SQLite file; both concerns were split
out and both apps now trust identity forwarded by Traefik instead of doing
their own auth. See [home-server-auth](../home-server-auth) for the
identity service both of these depend on.

## Architecture

- **Identity**: neither app has a login, register, or password anywhere in
  its own code. Both read `X-User-Id` / `X-Username` / `X-User-Role` off
  incoming requests via a shared `identity.py` dependency (duplicated in
  each app — small enough that a shared package would be overkill). These
  headers are only trustworthy because Traefik's ForwardAuth
  (`auth-free` Middleware, pointing at auth-service's `/verify`) gates every
  request first and its `authResponseHeaders` setting overwrites them from
  the verified response — a client cannot set these itself and have them
  survive the proxy hop. `identity.py` fails closed (401) if the headers are
  ever missing, with no fallback auth path.
- **Data**: each app has its own Postgres database (`socialdb`, `drivedb`)
  on the cluster's shared Postgres instance, with a dedicated least-privilege
  role per app — `social` can't touch `drive`'s tables or vice versa.
  SQLAlchemy async + Alembic migrations, same pattern as auth-service.
  Neither app has a local `users` table anymore; a lightweight `profiles`
  table (keyed by auth-service's UUID as text) holds app-specific per-user
  data instead — currently just storage quota tracking for `drive`, an
  otherwise-empty anchor row for `social`, created lazily on first request
  from a given user.
- **Routing**: every real API route lives under `/api/*` in both apps
  (`api = APIRouter(prefix="/api")`). This lets Traefik route by a single
  `PathPrefix(`/api`)` rule to the backend Service and everything else to
  the corresponding frontend Service, on the same domain, without needing a
  Traefik rule per endpoint. `/health` and `/` stay unprefixed for
  Kubernetes probes.
- **Admin**: user-level admin (list/ban/unban/set-role) lives entirely in
  auth-service now, not here — see that repo's README. `social` keeps only
  the admin actions that need its own tables: `GET /api/admin/feed`,
  `DELETE /api/admin/posts/{id}`, gated on `role=super` via the same
  identity headers.

## Route map

**drive** (`drive.ashtonashton.net`): `GET /api/files`, `POST /api/upload`,
`GET /api/download/{id}`, `DELETE /api/files/{id}`, `GET /api/usage`,
`GET /api/me`.

**social** (`ashtonashton.net`): `GET /api/feed`, `POST /api/posts/text`,
`POST /api/posts/file`, `DELETE /api/posts/{id}`,
`GET /api/posts/{id}/download`, `POST /api/posts/{id}/dislike`,
`POST /api/posts/{id}/reply/text`, `POST /api/posts/{id}/reply/file`,
`GET /api/posts/{id}/replies`, `GET /api/me`,
`GET /api/admin/feed` (super), `DELETE /api/admin/posts/{id}` (super).

Neither app has `/register`, `/login`, `/change-password`, or
`/admin/users*` — those either moved to auth-service or were deleted
outright (a couple of dead/duplicate routes from the old combined app
weren't carried forward at all).

## Local development

Each app is independent — pick one:

```bash
cd drive   # or social
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env   # edit DATABASE_URL to point at a local Postgres
alembic upgrade head
uvicorn main:app --reload --port 8001
```

Neither app does ForwardAuth locally — simulate it by setting the headers
by hand:

```bash
curl http://localhost:8001/api/files \
  -H 'X-User-Id: 11111111-1111-1111-1111-111111111111' \
  -H 'X-Username: testuser' \
  -H 'X-User-Role: free'
```

Docs at `http://localhost:8001/docs`.

## Deployment

Helm chart per app (`drive/helm/`, `social/helm/`) — Deployment + Service +
PVC (for uploaded file storage; the database itself is external Postgres,
not on the PVC). Images built and pushed manually:

```bash
cd drive   # or social
docker buildx build --platform linux/amd64 -t ashtonk216/ashtonashton-drive:X.Y.Z --push .
# bump helm/values.yaml image.tag, then:
helm upgrade ashtonahton-drive ./helm -n web
```

`--platform linux/amd64` matters — the k3s node is amd64; an image built on
Apple Silicon without this flag fails with `exec format error` at container
start.

Each app's Postgres credentials come from a pre-existing k8s Secret
(`drive-postgres-credentials`, `social-postgres-credentials`) in the `web`
namespace — the chart references it by name, doesn't create it. Traefik's
`auth-free` Middleware and both apps' IngressRoutes are plain `kubectl
apply`'d YAML, not part of either Helm chart (small, rarely-changed
resources; not worth templating).

## Migration scripts

`scripts/migrate_users.py` and `scripts/migrate_posts.py` — one-time,
already-run scripts that moved the real users and their text posts from the
old combined app's SQLite backup into the new system. Idempotent (safe to
re-run, checks for already-migrated rows before inserting) but there's
nothing left to migrate — kept for reference, not part of normal operation.
Notably: passwords were **not** carried over (a deliberate clean break, not
a limitation — see auth-service's README for the reset flow migrated users
need to go through), and file-post attachments were excluded (their bytes
live on the old droplet's filesystem, out of scope for that pass).

## Known limitations / possible follow-ups

- The old app's "ban a user" action cascade-deleted their posts; that
  behavior wasn't rebuilt when ban moved to auth-service (which knows
  nothing about posts). A banned user's existing posts stay up — a `super`
  admin can manually clean them up via `/api/admin/feed` /
  `/api/admin/posts/{id}` after banning, but there's no automatic cascade.
- `social`'s `/api/admin/feed` returns `user_id` (a UUID), not a resolved
  username, since there's no local `users` table to join against anymore.
