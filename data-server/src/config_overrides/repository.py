"""Postgres-backed CRUD for the ``project_config_overrides`` table.

Single-tenant local mode: no JWT, no user attribution, no RLS. Reads degrade
to an empty shape so the build path stays alive on transient DB errors; writes
let exceptions propagate so the router can map them to HTTP status codes.

One row per project, keyed by ``project_id`` (also the FK to projects).
``upsert`` is the only write path because the editor sends the full dict on
every save.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional

from src.config_overrides.models import ConfigOverridesRow
from src.db import execute, query
from src.logger import get_logger

LOG = get_logger(__name__)

_TABLE = "project_config_overrides"


def _coerce_updated_at(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            LOG.warning("project_config_overrides.updated_at unparseable: %r", raw)
            return None
    return None


def _row_to_model(row: Dict[str, Any]) -> ConfigOverridesRow:
    overrides = row.get("overrides")
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides)
        except ValueError:
            overrides = {}
    if not isinstance(overrides, dict):
        overrides = {}
    return ConfigOverridesRow(
        project_id=str(row["project_id"]),
        overrides=overrides,
        updated_at=_coerce_updated_at(row.get("updated_at")),
    )


class ConfigOverridesRepository:
    """Thin wrapper over the ``project_config_overrides`` table."""

    def get(self, project_id: str) -> ConfigOverridesRow:
        """Return the row for ``project_id``, or an empty-dict placeholder.

        Network errors degrade to the empty shape so the build path stays
        alive (mirrors ``fetch_project_component_mapping``).
        """
        try:
            rows = query(
                f"select * from {_TABLE} where project_id = %s limit 1",
                (project_id,),
            )
        except Exception as exc:  # noqa: BLE001 — keep build path robust
            LOG.warning(
                "project_config_overrides fetch failed for %s: %s", project_id, exc
            )
            return ConfigOverridesRow(project_id=project_id)

        if not rows:
            return ConfigOverridesRow(project_id=project_id)
        return _row_to_model(rows[0])

    def upsert(
        self, project_id: str, overrides: Dict[str, Any]
    ) -> ConfigOverridesRow:
        """Replace the whole overrides dict for ``project_id``."""
        rows = query(
            f"insert into {_TABLE} (project_id, overrides) values (%s, %s) "
            "on conflict (project_id) do update set "
            "overrides = excluded.overrides, updated_at = now() "
            "returning *",
            (project_id, json.dumps(overrides)),
        )
        if not rows:
            raise RuntimeError("upsert returned no row")
        return _row_to_model(rows[0])

    def delete(self, project_id: str) -> bool:
        """Drop the row (reset-to-defaults). Returns True if a row was removed."""
        affected = execute(
            f"delete from {_TABLE} where project_id = %s",
            (project_id,),
        )
        return affected > 0


__all__ = ["ConfigOverridesRepository"]
