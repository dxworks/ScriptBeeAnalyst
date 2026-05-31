"""Schema bootstrap — apply the plain-Postgres migrations on startup.

The canonical schema lives in ``supabase/migrations/*.sql`` (repo root). In
the de-Supabased single-tenant world there is no Supabase CLI to run them, so
the data-server applies them itself on startup *if the schema is absent*
(probed by the existence of ``public.projects``).

Two adaptations vs. the raw files:

* ``storage.*`` statements (bucket inserts + storage.objects policies) are
  stripped — there is no ``storage`` schema in a plain Postgres instance. The
  on-disk file store (:data:`src.config.SERIALIZED_FILES_DIR`) replaces them.
* All table / index / trigger / function DDL is applied verbatim. The heavy
  JSONB columns are untouched.

This is intentionally minimal: it is a one-shot create-if-absent, NOT a
migration runner with version tracking. Re-running against a populated DB is a
no-op because the ``public.projects`` probe short-circuits.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import List

from src.db import connection
from src.logger import get_logger

logger = get_logger("bootstrap")

# Repo-root-relative location of the SQL migrations.
_MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "supabase" / "migrations"


def _strip_supabase_only_statements(sql: str) -> str:
    """Remove every Supabase-platform-only statement from a migration.

    Three classes of statement reference Supabase-managed schemas that do not
    exist in a plain Postgres instance and are dropped:

    * ``storage.buckets`` inserts + ``storage.objects`` RLS policies (the
      initial schema migration) — the on-disk file store replaces them.
    * ``alter publication supabase_realtime ...`` (the filter_rules migration)
      — realtime is replaced by HTTP polling; there is no such publication.

    All table / index / trigger / function DDL is kept verbatim. Statements are
    split on ``;`` at the top level, guarding ``$$``-quoted function bodies.
    """
    out: List[str] = []
    for stmt in _split_statements(sql):
        lowered = stmt.lower()
        if "storage.buckets" in lowered or "storage.objects" in lowered:
            continue
        if "alter publication" in lowered:
            continue
        out.append(stmt)
    return ";\n".join(out) + ";\n" if out else ""


def _split_statements(sql: str) -> List[str]:
    """Split SQL into top-level statements, respecting ``$$``-quoted bodies.

    ``handle_updated_at`` is defined with a ``$$ ... $$`` body that contains
    semicolons; a naive ``split(';')`` would shred it. We walk the text and
    only treat a ``;`` outside a ``$$`` block as a separator.

    SQL comments are skipped while scanning for the ``;`` separator so that a
    ``;`` *inside* a comment (e.g. ``-- Tables are open; the only writer ...``)
    is not mistaken for a statement terminator — which would otherwise emit the
    rest of the comment line as a bare, syntactically invalid statement. The
    comment text is preserved verbatim in the buffer (Postgres ignores it).
    """
    statements: List[str] = []
    buf: List[str] = []
    in_dollar = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        # ``$$``-quoted body toggles (never inside a comment — checked below).
        if sql.startswith("$$", i):
            in_dollar = not in_dollar
            buf.append("$$")
            i += 2
            continue
        if not in_dollar:
            # ``--`` line comment: copy through end-of-line verbatim.
            if sql.startswith("--", i):
                eol = sql.find("\n", i)
                if eol == -1:
                    eol = n
                buf.append(sql[i:eol])
                i = eol
                continue
            # ``/* ... */`` block comment: copy through the closing ``*/``.
            if sql.startswith("/*", i):
                end = sql.find("*/", i + 2)
                end = n if end == -1 else end + 2
                buf.append(sql[i:end])
                i = end
                continue
        if ch == ";" and not in_dollar:
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _schema_present() -> bool:
    """Return True if ``public.projects`` already exists."""
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute("select to_regclass('public.projects') as reg")
            row = cur.fetchone()
            return bool(row and row.get("reg"))


def apply_migrations() -> None:
    """Apply every migration .sql (storage statements stripped) if schema absent.

    No-op when ``public.projects`` already exists. Logs loudly and re-raises on
    failure — a half-applied schema must surface, not silently degrade.
    """
    if not _MIGRATIONS_DIR.is_dir():
        logger.warning(
            "Migrations dir %s not found — skipping bootstrap", _MIGRATIONS_DIR
        )
        return

    try:
        if _schema_present():
            logger.info("Schema already present (public.projects) — skipping bootstrap")
            return
    except Exception as exc:  # noqa: BLE001
        logger.error("Schema presence probe failed: %s", exc)
        raise

    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    logger.info("Bootstrapping schema from %d migration file(s)", len(files))

    for path in files:
        raw = path.read_text()
        cleaned = _strip_supabase_only_statements(raw)
        if not cleaned.strip():
            continue
        logger.info("Applying migration %s", path.name)
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(cleaned)

    logger.info("Schema bootstrap complete")


__all__ = ["apply_migrations"]
