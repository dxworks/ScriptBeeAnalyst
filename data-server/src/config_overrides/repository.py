"""Supabase-backed CRUD for the ``project_config_overrides`` table.

Single-tenant local mode: every call uses the service-role client. No JWT,
no user attribution, no RLS. Mirrors :class:`FilterRuleRepository` and the
``fetch_project_component_mapping`` helper — see the implementation plan
§3 for the rationale.

One row per project, keyed by ``project_id`` (also the FK to projects).
``upsert`` is the only write path because the editor sends the full dict
on every save.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from src.config_overrides.models import ConfigOverridesRow
from src.logger import get_logger
from src.supabase_client import get_service_client

LOG = get_logger(__name__)

_TABLE = "project_config_overrides"


def _parse_updated_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        LOG.warning("project_config_overrides.updated_at unparseable: %r", raw)
        return None


def _row_to_model(row: Dict[str, Any]) -> ConfigOverridesRow:
    overrides = row.get("overrides")
    if not isinstance(overrides, dict):
        overrides = {}
    return ConfigOverridesRow(
        project_id=row["project_id"],
        overrides=overrides,
        updated_at=_parse_updated_at(row.get("updated_at")),
    )


class ConfigOverridesRepository:
    """Thin wrapper over the Supabase ``project_config_overrides`` table."""

    def get(self, project_id: str) -> ConfigOverridesRow:
        """Return the row for ``project_id``, or an empty-dict placeholder.

        Callers that only need the overrides dict can read ``.overrides``
        directly. Network / auth errors degrade to the empty shape so the
        build path stays alive (mirrors ``fetch_project_component_mapping``).
        """
        try:
            response = (
                get_service_client()
                .table(_TABLE)
                .select("*")
                .eq("project_id", project_id)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 — keep build path robust
            LOG.warning(
                "project_config_overrides fetch failed for %s: %s", project_id, exc
            )
            return ConfigOverridesRow(project_id=project_id)

        rows = response.data or []
        if not rows:
            return ConfigOverridesRow(project_id=project_id)
        return _row_to_model(rows[0])

    def upsert(
        self, project_id: str, overrides: Dict[str, Any]
    ) -> ConfigOverridesRow:
        """Replace the whole overrides dict for ``project_id``.

        ``overrides`` is taken as-is; per-field validation belongs in the
        catalogue/router layer. An empty dict is persisted (it semantically
        equals "no overrides" but keeping the row simplifies updated_at
        tracking).
        """
        payload = {
            "project_id": project_id,
            "overrides": overrides,
        }
        response = (
            get_service_client()
            .table(_TABLE)
            .upsert(payload, on_conflict="project_id")
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError("upsert returned no row")
        return _row_to_model(rows[0])

    def delete(self, project_id: str) -> bool:
        """Drop the row (reset-to-defaults). Returns True if a row was removed."""
        response = (
            get_service_client()
            .table(_TABLE)
            .delete()
            .eq("project_id", project_id)
            .execute()
        )
        return bool(response.data)


__all__ = ["ConfigOverridesRepository"]
