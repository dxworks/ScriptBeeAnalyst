# syntax=docker/dockerfile:1
# =============================================================================
# Single-image build for the whole ScriptBeeAssistant app.
#
#   Stage 1 (web):  Node — build the Angular SPA -> /web-ui/dist/web-ui/browser
#   Stage 2 (final): Python — the FastAPI data-server, with the built SPA copied
#                    in and served at "/" (StaticFiles + SPA fallback). The API
#                    lives under its existing paths on the same origin.
#
# One image runs both the UI and the API; Postgres is a separate compose
# service (`db`). See docker-compose.yml at the repo root.
# =============================================================================

# ---- Stage 1: build the Angular web-ui -------------------------------------
FROM node:22-slim AS web

WORKDIR /web-ui

# Install deps first for layer caching.
COPY web-ui/package.json web-ui/package-lock.json ./
RUN npm ci

# Build the production bundle (fileReplacements swaps in environment.prod.ts,
# which sets dataServerUrl='' so the UI calls the API on the same origin).
COPY web-ui/ ./
RUN npm run build -- --configuration production


# ---- Stage 2: Python runtime (data-server + static SPA) --------------------
FROM python:3.13-slim AS final

WORKDIR /app

# System deps for matplotlib/numpy wheels are already covered by slim + pip
# binary wheels; psycopg[binary] ships its own libpq.
COPY data-server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY data-server/src ./src

# Built SPA from stage 1 -> served by FastAPI at "/" via STATIC_DIR.
COPY --from=web /web-ui/dist/web-ui/browser /app/static

# Canonical schema migrations, applied on startup by src/bootstrap.py via
# yoyo-migrations (version-tracked in the _yoyo_migration table).
COPY data-server/migrations ./migrations

# Defaults; docker-compose overrides DATABASE_URL etc.
ENV STATIC_DIR=/app/static \
    SERIALIZED_FILES_DIR=/data/serialized-files \
    PYTHONUNBUFFERED=1

EXPOSE 8001

CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8001", "--lifespan", "on"]
