"""FastAPI routes for per-project enrichment-config overrides.

Three endpoints under ``/projects/{project_id}/config-overrides``:

* ``GET``    — bundled catalogue + stored overrides + ``updated_at`` (one
               round-trip; the editor never paginates).
* ``PUT``    — replace the stored overrides dict. Validates every key
               against the catalogue allow-set, normalises composite
               shapes, dry-runs the merge to catch shape errors before
               persisting, then upserts.
* ``DELETE`` — clear the row (idempotent; always returns 204).

Single-tenant local mode — no JWT (matches :mod:`src.filter_rules.router`).
Existence of ``project_id`` is verified against the ``projects`` table
via the service-role Supabase client; a missing project returns 404.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response  # Response: decorator-level response_class

from src.config_overrides.catalogue import (
    CatalogueResponse,
    _HIDDEN_FIELDS,
    build_catalogue,
    editable_field_names,
)
from src.config_overrides.merge import OverrideCoercionError, apply_overrides
from src.config_overrides.models import ConfigOverridesPayload, ConfigOverridesRow
from src.config_overrides.normalize import normalize_for_storage
from src.config_overrides.repository import ConfigOverridesRepository
from src.enrichment.config import DEFAULT_CONFIG
from src.logger import get_logger
from src.supabase_client import get_service_client

LOG = get_logger(__name__)

router = APIRouter(tags=["config-overrides"])

_repository = ConfigOverridesRepository()


def _project_exists(project_id: str) -> bool:
    """Quick existence probe against the projects table.

    Mirrors the pattern used in ``/projects/{id}/load`` / ``/projects/{id}/build``
    in :mod:`src.server`. A Supabase failure here is treated as
    "exists" — we don't want to gate the editor on a flaky probe; the
    GET/PUT layer fails loudly if the row truly doesn't exist.
    """
    try:
        response = (
            get_service_client()
            .table("projects")
            .select("id")
            .eq("id", project_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — defensive probe
        # TODO(multi-tenant): masking probe failures as "exists" is safe
        # only in the single-tenant local-mode deployment — under RLS the
        # caller may legitimately be denied and we must NOT degrade open.
        # Revisit when the project moves off service-role-only access.
        LOG.warning("project existence probe failed for %s: %s", project_id, exc)
        return True
    return bool(response.data)


def _row_envelope(row: ConfigOverridesRow) -> Dict[str, Any]:
    return {
        "overrides": row.overrides,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _validate_payload(overrides: Dict[str, Any]) -> JSONResponse | None:
    """Return a 422 JSONResponse on the first invalid key, else ``None``.

    Three rejection classes, distinguishable by ``error``:

    * ``"not editable"`` — the field exists on EnrichmentConfig but is
      hidden from the editor (e.g. ``components_mapping_data``). A UI
      sending this is a bug.
    * ``"unknown"`` — the field name doesn't exist on EnrichmentConfig
      at all.
    * shape mismatch (raised below by the dry-run merge).

    The first failing key short-circuits so the UI can highlight one
    input at a time.
    """
    # Envelope intentionally carries `field` so the UI can highlight the
    # offending input — diverges from the codebase's plain `{error}` shape.
    allowed = editable_field_names()
    for name in overrides.keys():
        if name in _HIDDEN_FIELDS:
            return JSONResponse(
                {"field": name, "error": "not editable"},
                status_code=422,
            )
        if name not in allowed:
            return JSONResponse(
                {"field": name, "error": "unknown"},
                status_code=422,
            )
    return None


@router.get("/projects/{project_id}/config-overrides")
async def get_config_overrides(project_id: str):
    """Return the catalogue + persisted overrides for ``project_id``.

    Catalogue ``current`` reflects the persisted overrides, so the UI
    can render the editor in one render without a second-pass overlay.
    """
    if not _project_exists(project_id):
        return JSONResponse(
            {"error": f"project {project_id} not found"}, status_code=404
        )

    row = _repository.get(project_id)
    catalogue: CatalogueResponse = build_catalogue(row.overrides)
    return {
        "catalogue": catalogue.model_dump(mode="json"),
        **_row_envelope(row),
    }


@router.put("/projects/{project_id}/config-overrides")
async def put_config_overrides(project_id: str, payload: ConfigOverridesPayload):
    """Replace the stored overrides for ``project_id``.

    Validation pipeline (every step short-circuits to 422 on failure):

    1. Reject hidden / unknown field names.
    2. Normalise composite shapes to the canonical compact form.
    3. Dry-run the merge against ``DEFAULT_CONFIG`` to catch shape
       errors (bad regex, wrong type, malformed bucket) before write.
    4. Upsert into Supabase.
    5. Return the persisted row.
    """
    if not _project_exists(project_id):
        return JSONResponse(
            {"error": f"project {project_id} not found"}, status_code=404
        )

    validation_error = _validate_payload(payload.overrides)
    if validation_error is not None:
        return validation_error

    normalised = normalize_for_storage(payload.overrides)

    try:
        apply_overrides(DEFAULT_CONFIG, normalised)
    except OverrideCoercionError as exc:
        # Envelope intentionally carries `field` so the UI can highlight
        # the offending input — diverges from the codebase's plain
        # `{error}` shape.
        return JSONResponse(
            {"field": exc.field, "error": str(exc)},
            status_code=422,
        )

    try:
        row = _repository.upsert(project_id, normalised)
    except Exception as exc:  # noqa: BLE001 — surface as 500 with context
        LOG.exception("config_overrides upsert failed for %s", project_id)
        raise HTTPException(
            status_code=500, detail=f"failed to persist overrides: {exc}"
        )

    return _row_envelope(row)


@router.delete(
    "/projects/{project_id}/config-overrides",
    status_code=204,
    response_class=Response,
)
async def delete_config_overrides(project_id: str):
    """Clear the overrides row. Idempotent — 204 whether or not a row existed.

    No project-existence check: deleting overrides for a project that
    never had any (or no longer exists) is harmless and the
    foreign-key cascade has already cleaned up if the project was
    removed.
    """
    try:
        _repository.delete(project_id)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("config_overrides delete failed for %s", project_id)
        raise HTTPException(
            status_code=500, detail=f"failed to delete overrides: {exc}"
        )


config_overrides_router = router


__all__ = ["config_overrides_router", "router"]
