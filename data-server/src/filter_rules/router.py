"""FastAPI routes for the filter-rules feature.

Three endpoints under ``/projects/{project_id}/rules``:

* ``POST`` — create a rule (the OpenCode agent is the primary writer).
* ``GET``  — list active rules for a project.
* ``DELETE /{rule_id}`` — drop a rule (the web-ui's Exclusion Rules tab).

Single-tenant local mode: no JWT, no Authorization header.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from src.filter_rules.engine import (
    FilterRuleValidationError,
    supported_fields,
    validate_dsl,
)
from src.filter_rules.models import CreateFilterRuleRequest, FilterRule
from src.filter_rules.repository import FilterRuleRepository
from src.filter_rules.store import filter_rule_store
from src.logger import get_logger

LOG = get_logger(__name__)

router = APIRouter(tags=["filter-rules"])

_repository = FilterRuleRepository()


@router.post("/projects/{project_id}/rules")
async def create_filter_rule(project_id: str, body: CreateFilterRuleRequest):
    """Persist a new rule and refresh the in-memory cache."""
    try:
        validate_dsl(body.dsl)
    except FilterRuleValidationError as exc:
        return JSONResponse(
            {
                "error": str(exc),
                "supported_fields": supported_fields(),
            },
            status_code=400,
        )

    try:
        rule: FilterRule = _repository.create(
            project_id=project_id,
            dsl=body.dsl,
            name=body.name,
            nl_description=body.nl_description,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("create_filter_rule failed")
        return JSONResponse(
            {"error": f"failed to persist rule: {exc}"}, status_code=500
        )

    filter_rule_store.refresh(project_id)
    return rule.model_dump(mode="json")


@router.get("/projects/{project_id}/rules")
async def list_filter_rules(project_id: str):
    state = filter_rule_store.state(project_id)
    rules = []
    for r in state.rules:
        row = r.model_dump(mode="json")
        # match_count is None when no graph is loaded for the project —
        # the UI renders an em-dash in that case rather than a misleading 0.
        row["match_count"] = state.match_counts.get(r.id)
        rules.append(row)
    return {"project_id": project_id, "rules": rules}


@router.delete("/projects/{project_id}/rules/{rule_id}")
async def delete_filter_rule(project_id: str, rule_id: str):
    try:
        deleted = _repository.delete(project_id=project_id, rule_id=rule_id)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("delete_filter_rule failed")
        raise HTTPException(status_code=500, detail=f"failed to delete rule: {exc}")

    if not deleted:
        return JSONResponse(
            {"error": f"rule {rule_id} not found for project {project_id}"},
            status_code=404,
        )

    filter_rule_store.refresh(project_id)
    return {"deleted": True, "rule_id": rule_id, "project_id": project_id}


filter_rules_router = router


__all__ = ["filter_rules_router", "router"]
