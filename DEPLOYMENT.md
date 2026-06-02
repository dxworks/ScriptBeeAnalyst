# Deployment

ScriptBeeAssistant runs as a **single `docker compose up`** — web UI, database, data
server, and file storage in one stack. It is **single-tenant, local-only**: no auth,
no RLS, and **no Supabase** (the project was migrated off Supabase to a plain
PostgreSQL + FastAPI design).

## Quick start

```bash
cp .env.example .env        # optional — every value has a sane default
docker compose up --build
# → open http://localhost:8001
```

`--build` is only needed the first time or after code changes. Stop with `Ctrl-C`;
bring it down (keeping your data) with `docker compose down`.

Requirements: Docker Engine + the `docker compose` plugin. On this Mac the engine is
provided by **Colima** — make sure it's running (`colima status`, or `colima start`).

## Architecture

```
            ┌──────────────── docker compose ────────────────┐
            │                                                 │
  browser ──┼─▶ app (:8001)  ── FastAPI serves:               │
            │     • Angular SPA at  /                         │
            │     • REST API under its existing paths         │
            │         │                                       │
            │         ├──▶ db (PostgreSQL 16)  ◀── processor   │
            │         │                                        │
            │         └──▶ serialized-files volume (uploads)   │
            └─────────────────────────────────────────────────┘
```

Three services, all from **one image** (`app` and `processor` share the root
`Dockerfile`):

| Service     | Role |
|-------------|------|
| `db`        | PostgreSQL 16 — the only datastore. Schema is applied on startup by the app (`data-server/src/bootstrap.py` runs `supabase/migrations/*.sql` with the `storage.*` / publication statements stripped). |
| `app`       | Single image: the built Angular SPA is served at `/` by the FastAPI data-server, the API under its existing paths, all on the **same origin** (port `8001`). |
| `processor` | The graph build/finalize worker (same image). Polls Postgres for work; shares the `serialized-files` + `pickles` volumes with `app`. |

There are **no** Supabase containers (no kong / gotrue / postgrest / realtime /
storage-api / studio / meta). What Supabase used to provide is now:

- **Database** → the `db` Postgres service (schema unchanged from the old migrations).
- **Auto REST API (PostgREST)** → explicit FastAPI endpoints on the data-server.
- **Storage bucket** → an on-disk directory (`SERIALIZED_FILES_DIR`) on a volume.
- **Realtime** → the web UI **polls** the data-server instead of subscribing.

## Persistence (named volumes)

Data survives `docker compose down` (it is removed only by `docker compose down -v`):

| Volume                       | Holds |
|------------------------------|-------|
| `scriptbee-pgdata`           | PostgreSQL data |
| `scriptbee-serialized-files` | Uploaded serialized files (replaces the Supabase bucket) |
| `scriptbee-pickles`          | Built graph pickles |
| `scriptbee-workspace`        | Per-project workspaces |

## Configuration (`.env`)

Every value has a default in `docker-compose.yml`, so an absent `.env` still boots.

| Variable               | Default                                         | Purpose |
|------------------------|-------------------------------------------------|---------|
| `POSTGRES_USER`        | `postgres`                                      | DB user |
| `POSTGRES_PASSWORD`    | `postgres`                                      | DB password |
| `POSTGRES_DB`          | `scriptbee`                                     | DB name |
| `DATABASE_URL`         | `postgresql://postgres:postgres@db:5432/scriptbee` | App/processor connection string — **keep in sync with the `POSTGRES_*` above** |
| `SERIALIZED_FILES_DIR` | `/data/serialized-files`                        | On-disk file store (mounted volume) |
| `MAX_UPLOAD_MB`        | `500`                                           | Max upload size |
| `RECURSION_LIMIT`      | `50000`                                         | Python recursion limit for deep-graph pickling |
| `LOG_LEVEL`            | `INFO`                                          | Log verbosity |

## Verified behaviour

A live deployment was smoke-tested end to end:

- `GET /health` → `200 {"status":"ok","mode":"standalone"}`
- `GET /` → `200`, serves the Angular SPA (`index.html`)
- Project CRUD: `POST /projects` → 201, `GET /projects` lists it,
  `GET /projects/{id}/files` → `[]`, `DELETE /projects/{id}` → 200
- DB persistence confirmed via `psql` (row present after create, gone after delete)
- `docker compose ps` shows only `scriptbee-app`, `scriptbee-db`,
  `scriptbee-processor` — no Supabase services

## Troubleshooting

- **`FATAL: password authentication failed for user "postgres"`** — your `.env`
  `DATABASE_URL` password/db name doesn't match the values the `scriptbee-pgdata`
  volume was first initialized with. Either make them consistent, or wipe the
  volume and start fresh: `docker compose down -v && docker compose up --build`.
- **Schema not applied / startup fails** — the app applies migrations on boot and
  retries with backoff; check `docker compose logs app`. A fresh DB volume always
  re-bootstraps the schema.
- **Port `8001` in use** — change the host side of the `app` port mapping in
  `docker-compose.yml` (`"8001:8001"` → e.g. `"9001:8001"`).

## Notes

- The old Supabase scaffolding (`local_supabase_deploy/`, `supabase/functions/`,
  `.env.supabase*`, `local_supabase_*.sh`, `env-switch.sh`, `db-push.sh`, `db-reset.sh`,
  `data-server/dev-{start,end}.sh`) has been **removed**. The only thing kept under
  `supabase/` is `migrations/` — the canonical schema the app applies on boot
  (`data-server/src/bootstrap.py`).
- A couple of modules still carry "supabase" in their names/docstrings
  (e.g. `data-server/src/smart_merge/supabase_repository.py`) but are fully
  Postgres-backed; the naming is cosmetic only.
