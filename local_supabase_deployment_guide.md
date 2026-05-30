# Local Supabase Deployment Guide

The only supported Supabase target for ScriptBeeAssistant is the local
Docker stack under `local_supabase_deploy/`. No remote / self-hosted
deployment is in scope.

## What the local stack contains

`local_supabase_deploy/docker-compose.yml` is the standard Supabase
self-hosted compose file with the optional services switched off (per
`memory/scriptbee_supabase_trim.md`):

| Service | Container | Port (host) | Purpose |
|---|---|---|---|
| Kong API gateway | `supabase-kong` | 8000 | Single entry point for all Supabase HTTP traffic; the web-ui and the data-server both talk to this URL. |
| Postgres | `supabase-db` | (internal) | The database. Reached via `docker exec supabase-db psql -U postgres` for migrations. |
| GoTrue (auth) | `supabase-auth` | (internal) | Email + JWT auth; web-ui signs users in here. |
| PostgREST | `supabase-rest` | (internal) | REST surface over Postgres tables. |
| Realtime | `supabase-realtime` | (internal) | Postgres change-feed websocket. |
| Storage | `supabase-storage` | (internal) | Object storage (the `serialized-files` bucket where project uploads live). |
| Meta | `supabase-meta` | (internal) | Schema introspection used by the Studio UI. |
| Studio | `supabase-studio` | (optional profile) | Web admin UI; disabled by default in the trimmed compose. |

Trimmed off (intentionally): analytics, vector, pooler, imgproxy, edge
functions. Kong is capped to 2 nginx workers to keep RAM low on the
16 GB dev laptop.

The stack is gitignored (`local_supabase_deploy/` is in `.gitignore`)
because it's a vendored copy of the Supabase self-host bundle and not
part of the application source.

## Setup — first-time deploy

1. **Copy the env templates**
   ```bash
   cp .env.example .env
   cp .env.supabase.example .env.supabase
   ```

2. **Fill in `.env.supabase`** with the Supabase URL + keys you want
   the app to use:
   ```
   SUPABASE_URL=http://localhost:8000
   API_EXTERNAL_URL=http://localhost:8000
   SUPABASE_PUBLIC_URL=http://localhost:8000
   SUPABASE_ANON_KEY=<anon-jwt>
   SUPABASE_SERVICE_KEY=<service-role-jwt>
   JWT_SECRET=<the-secret-that-mints-those-jwts>
   ```
   The anon key and service-role key must be JWTs signed with
   `JWT_SECRET`. The Supabase self-host docs describe how to generate
   them; or reuse a fixed set across resets.

3. **Fill in `local_supabase_deploy/.env`** with the matching values:
   ```
   POSTGRES_PASSWORD=<postgres-password>
   JWT_SECRET=<same as .env.supabase>
   ANON_KEY=<same as SUPABASE_ANON_KEY>
   SERVICE_ROLE_KEY=<same as SUPABASE_SERVICE_KEY>
   DASHBOARD_USERNAME=supabase
   DASHBOARD_PASSWORD=<studio-login-password>
   SECRET_KEY_BASE=<openssl rand -base64 48>
   VAULT_ENC_KEY=<32-char string>
   PG_META_CRYPTO_KEY=<32-char string>
   ```
   plus the GoTrue / Kong / Studio settings the compose file references
   (see the full list in `.env.example`). The critical invariant: the
   JWT secret and the two minted JWTs in this file must match the ones
   in `.env.supabase` and root `.env`, otherwise web-ui / data-server
   requests will fail JWT validation at Kong.

4. **Propagate values into `.env` and the Angular environment**:
   ```bash
   ./env-switch.sh
   ```
   This reads `.env.supabase` and writes:
   - `SUPABASE_URL`, `SUPABASE_URL_DOCKER`, `API_EXTERNAL_URL`,
     `SUPABASE_PUBLIC_URL` into root `.env`
   - `web-ui/src/environments/environment.ts` (a fresh file with `url`
     and `anonKey`)

5. **Bring the stack up**:
   ```bash
   ./local_supabase_start.sh
   ```
   This `docker compose up -d`s under `local_supabase_deploy/` and waits
   for the database + Kong to answer. Tear down later with
   `./local_supabase_end.sh` (drops volumes).

6. **Apply the application schema**:
   ```bash
   ./db-reset.sh    # drop + reapply everything
   # or
   ./db-push.sh     # apply migrations only
   ```
   Both run `docker exec -i supabase-db psql ...` directly — no SSH, no
   remote.

After step 6 the stack is ready. Start the data-server and web-ui as
documented in `CLAUDE.md`.

## Env files at a glance

Four files participate in Supabase communication. They are all
gitignored — only the `*.example` siblings are tracked.

| File | Who reads it | What's in it |
|---|---|---|
| `.env.supabase` | `env-switch.sh` | URL + the three JWT-related values (anon key, service-role key, JWT secret). Edited by hand; the single human-facing source of truth. |
| `.env` (root) | data-server (`docker compose --env-file ../.env`) and any script that sources it | Everything in `.env.example`: Postgres, GoTrue, Kong, Studio settings plus the `SUPABASE_URL` / `SUPABASE_URL_DOCKER` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_KEY` / `JWT_SECRET` the data-server uses. URL values are overwritten on every `env-switch.sh` run. |
| `local_supabase_deploy/.env` | the Supabase docker-compose stack | Standard Supabase self-host env (POSTGRES_PASSWORD, JWT_SECRET, ANON_KEY, SERVICE_ROLE_KEY, GoTrue/Kong/Studio settings). Must agree with the JWT secret + tokens in the other two files. |
| `web-ui/src/environments/environment.ts` | the Angular app at build time | `{ supabase: { url, anonKey }, dataServerUrl }`. **Generated** by `env-switch.sh` — do not edit by hand. |

## What each service needs to talk to Supabase

### web-ui (Angular)

Reads `environment.ts` and constructs the Supabase JS client with
`url` and `anonKey`. That's it. The data-server URL is also baked in
here (`dataServerUrl: 'http://localhost:8001'`).

To change the Supabase URL or the anon key, edit `.env.supabase` and
re-run `./env-switch.sh`, then restart `npm start`.

### data-server (FastAPI)

Reads env vars from process environment (loaded from root `.env` by
docker-compose `--env-file ../.env`, or by the shell when running
locally with `uvicorn`):

| Var | Required | Used for |
|---|---|---|
| `SUPABASE_URL` | yes | Base URL of the Supabase API. Code: `data-server/src/config.py:20`. |
| `SUPABASE_SERVICE_KEY` | yes | Service-role JWT used by `supabase_client.create_client(...)` — the data-server reads / writes user-scoped tables on behalf of all users. |
| `JWT_SECRET` | yes | Verifies the bearer token the web-ui forwards on every authenticated call. |
| `SUPABASE_ANON_KEY` | optional | Currently unused by code paths but kept in `.env` for symmetry. |
| `SUPABASE_URL_DOCKER` | optional | When the data-server runs inside a container, this is the address it uses to reach Supabase on the host (`http://host.docker.internal:8000`). |

`config.py` raises on startup if `JWT_SECRET` or
`SUPABASE_SERVICE_KEY` is missing — that's the first thing to check if
the data-server refuses to boot.

## Common troubleshooting

- **"supabase-db container is not running"** from `db-push.sh` /
  `db-reset.sh` — run `./local_supabase_start.sh` first.
- **JWT validation fails on web-ui auth** — the `JWT_SECRET` /
  `ANON_KEY` / `SERVICE_ROLE_KEY` triple in
  `local_supabase_deploy/.env` doesn't match the ones in
  `.env.supabase`. Re-align them and restart the stack (a restart is
  enough — keys are read fresh).
- **data-server boots but Supabase calls 401** — the `SUPABASE_URL` in
  root `.env` points somewhere different from where Kong is actually
  listening. Re-run `./env-switch.sh`.
- **Web-ui "url" / "anonKey" empty after `npm start`** — `env-switch.sh`
  hasn't run, so `web-ui/src/environments/environment.ts` is missing
  or stale. Run it and restart the dev server.
- **Wiping local data** — `./local_supabase_end.sh` (confirms then
  removes containers + volumes), then `./local_supabase_start.sh` and
  `./db-reset.sh`.
