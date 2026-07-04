"""Schema management — yoyo-migrations runner, applied on startup.

The canonical schema lives in ``data-server/migrations/*.sql`` — plain-Postgres
SQL files applied (and version-tracked in the ``_yoyo_migration`` table) by
yoyo-migrations. The first file, ``0001_database_initialization.sql``, is the
squash of the 14 historical ``supabase/migrations`` files with the
Supabase-platform-only statements removed; new migrations are added as
``0002_*.sql`` etc. and run exactly once each.

Pre-yoyo databases — schemas created by the old create-if-absent bootstrap on
the ``scriptbee-pgdata`` volume — are baselined: when ``public.projects``
already exists but yoyo has no record of the init migration, it is *marked*
applied instead of re-run (its CREATE TABLEs would otherwise collide). Any
later pending migrations then apply normally.
"""
from __future__ import annotations

from pathlib import Path

from yoyo import get_backend, read_migrations

from src.config import DATABASE_URL
from src.db import connection
from src.logger import get_logger

logger = get_logger("bootstrap")

# data-server/migrations in the repo == /app/migrations in the image (src/ sits
# next to migrations/ in both layouts).
_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"

# The squashed schema — the only migration eligible for baselining.
_INIT_MIGRATION_ID = "0001_database_initialization"


def _yoyo_url() -> str:
    """``DATABASE_URL`` with the scheme yoyo maps to the psycopg3 driver."""
    return DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


def _schema_present() -> bool:
    """Return True if ``public.projects`` already exists."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select to_regclass('public.projects') as reg")
            row = cur.fetchone()
            return bool(row and row.get("reg"))


def apply_migrations() -> None:
    """Apply pending migrations (baselining pre-yoyo schemas first).

    Logs loudly and re-raises on failure — a half-applied schema must surface,
    not silently degrade. (Each .sql file runs in its own transaction, so a
    failure rolls that file back; earlier files stay recorded as applied.)
    """
    if not _MIGRATIONS_DIR.is_dir():
        logger.warning(
            "Migrations dir %s not found — skipping bootstrap", _MIGRATIONS_DIR
        )
        return

    backend = get_backend(_yoyo_url())
    try:
        migrations = read_migrations(str(_MIGRATIONS_DIR))
        with backend.lock():
            to_apply = backend.to_apply(migrations)

            # Baseline: the schema exists but yoyo has never seen the init
            # migration -> record it as applied without running it.
            init_pending = to_apply.filter(lambda m: m.id == _INIT_MIGRATION_ID)
            if init_pending and _schema_present():
                logger.info(
                    "Pre-yoyo schema detected (public.projects exists) — "
                    "baselining %s without running it",
                    _INIT_MIGRATION_ID,
                )
                backend.mark_migrations(init_pending)
                to_apply = backend.to_apply(migrations)

            if not to_apply:
                logger.info("Schema up to date — no pending migrations")
                return

            logger.info(
                "Applying %d migration(s): %s",
                len(to_apply),
                ", ".join(m.id for m in to_apply),
            )
            backend.apply_migrations(to_apply)
    finally:
        backend.connection.close()

    logger.info("Schema bootstrap complete")


__all__ = ["apply_migrations"]
