"""
Configuration module for data-server.
Loads environment variables from .env file (local dev) or environment (Docker).

Single-tenant local mode: no auth, no Supabase. The data-server owns a
PostgreSQL database (via ``DATABASE_URL``) and an on-disk file store (via
``SERIALIZED_FILES_DIR``) directly.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Try to load .env from project root (for local development)
# In Docker, environment variables are passed directly via docker-compose
project_root = Path(__file__).parent.parent.parent
dotenv_path = project_root / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path=dotenv_path)

# PostgreSQL connection string.
# - Local dev: postgresql://postgres:postgres@localhost:5432/postgres
# - Docker compose: postgresql://<user>:<pass>@db:5432/<db>
DATABASE_URL = os.getenv("DATABASE_URL", "")

# On-disk storage root that replaces the Supabase 'serialized-files' bucket.
# Each serialized_files.storage_path is a relative key under this directory:
# the file lives at {SERIALIZED_FILES_DIR}/{storage_path}.
SERIALIZED_FILES_DIR = os.getenv("SERIALIZED_FILES_DIR", "/data/serialized-files")

# Maximum upload size in megabytes (mirrors the old client MAX_FILE_SIZE_MB).
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))

# Python recursion limit for large graph pickling
# Default is 1000, increased to 50000 for deep object graphs
RECURSION_LIMIT = int(os.getenv("RECURSION_LIMIT", "50000"))

# Workspace configuration for AI agent project folders
WORKSPACE_ROOT = os.getenv("WORKSPACE_ROOT", str(project_root / "analyzed_projects" / "projects"))

# Directory of the built Angular SPA (dist/web-ui/browser). When present the
# data-server serves the static SPA at "/" with an index.html fallback so the
# whole system runs behind a single origin (one `docker compose up`). Empty /
# missing in pure dev where `ng serve` hosts the UI on :4200.
STATIC_DIR = os.getenv("STATIC_DIR", "")

# Validate critical settings — in single-tenant mode only the database URL is
# mandatory (no JWT / service key any more).
if not DATABASE_URL:
    raise ValueError("DATABASE_URL must be set (via .env file or environment variable)")
