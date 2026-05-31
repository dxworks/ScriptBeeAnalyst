import asyncio
import io
import sys
import time
import traceback
import logging
from dataclasses import fields as dataclass_fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from fastapi.responses import JSONResponse, Response
from contextlib import asynccontextmanager
import matplotlib.pyplot as plt

import re
from datetime import datetime

from src.config import WORKSPACE_ROOT
from src.db import close_pool, connection, query_one
from src.bootstrap import apply_migrations
from src.logger import get_logger
# Chunk 19: smart-merge moved off the legacy ``graph_data: dict`` global.
# UnifiedUsers redesign §L (P4.C): :class:`UnifiedUser` is now the canonical
# graph entity in ``src.common.people.unified``; the former smart-merge DTO
# was collapsed into it. ``SourceIdentity`` remains an internal smart-merge
# DTO (D5) and identities are derived from the typed v2 :class:`Graph` via
# :mod:`src.smart_merge.identity_extractor`.
from src.smart_merge.engine import AuthorSmartMergeEngine
from src.smart_merge.identity import SourceIdentity
from src.smart_merge.identity_extractor import extract_all_identities
from src.smart_merge.state_store import smart_merge_state_store
from src.smart_merge.supabase_repository import SupabaseSmartMergeRepository
from src.smart_merge.types import (
    MAX_IDENTITIES_PER_SUGGESTION,
    RejectedPair,
    Suggestion,
    UserMapping,
)
# The v2 pipeline (``run_pipeline``) writes directly into the typed
# ``Graph`` (``graph.relations`` / ``graph.traits`` / ``graph.classifiers``).
# The legacy ``compute_enrichments`` / ``Enrichments`` /
# ``SupabaseEnrichmentRepository`` stack was deleted in Chunk 10 along
# with the rest of the pre-refactor enrichment layer.
from src.enrichment.pipeline import (
    PipelineResult,
    phase_b_relation_kinds,
    run_pipeline,
    run_pipeline_phase_b,
)
from src.enrichment.config import DEFAULT_CONFIG, EnrichmentConfig
from src.graph_store import graph_store
from src.common.kernel import EntityKind, Graph, MergeState
from src.common.people import UnifiedUser
from src.common.pickle_store import PickleStore
from src.smart_merge.rebind import rebind_account_refs_to_unified
from src.common.domains.components.resolver import parse_component_mapping
from src import processor as v2_processor
from src.sandbox import (
    QuerySandboxView,
    SetupSandboxView,
    commit_issues as _sandbox_commit_issues,
    issue_commits as _sandbox_issue_commits,
    pr_commits as _sandbox_pr_commits,
)
from src.config_overrides.router import config_overrides_router
from src.filter_rules.router import filter_rules_router
from src.filter_rules.store import filter_rule_store
from src.filter_rules.views import FilteredSandboxView
from src.projects.router import projects_router

logger = get_logger("data-server")


# Filter to suppress Uvicorn access logs for /projects/current endpoint
class SuppressCurrentProjectLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # Suppress logs that contain "GET /projects/current"
        return "GET /projects/current" not in record.getMessage()


# Apply filter to Uvicorn's access logger
logging.getLogger("uvicorn.access").addFilter(SuppressCurrentProjectLogFilter())


class CodeRequest(BaseModel):
    """Request body for code execution endpoints."""
    code: str


class BuildRequest(BaseModel):
    """Request body for ``POST /projects/{id}/build``.

    ``compute_annotated_lines`` is the per-build toggle for the (expensive)
    git annotated-lines reconstruction. When ``True`` the v2 processor flips
    the effective :class:`EnrichmentConfig` so
    :class:`GitLineAttributionMetric` emits ``git.loc`` / ``git.repo_size``
    classifiers + the per-file attribution trait (plan §§3-4).
    """
    compute_annotated_lines: bool = False


class ApplySuggestionRequest(BaseModel):
    """Request body for applying a merge suggestion."""
    suggestion_id: str
    selected_identity_keys: List[str]
    unselected_identity_keys: List[str] = []
    name: str
    email: str


class RejectSuggestionRequest(BaseModel):
    """Request body for rejecting a merge suggestion."""
    identity_keys: List[str]


# UU aftermath §Bug 3: account-side ``EntityKind`` values. Post-rebind
# no relation row may carry one of these on either endpoint — the
# rebind pass flips every role-typed account ref to a UNIFIED_USER ref,
# so any leftover Account-kinded relation row is a Phase A leak. The
# defensive cleanup pass at the top of /finalize drops them before
# Phase B runs against the rebound graph.
_ACCOUNT_KINDS: frozenset[EntityKind] = frozenset({
    EntityKind.GIT_ACCOUNT,
    EntityKind.GITHUB_USER,
    EntityKind.JIRA_USER,
})


def load_graph_v2_from_disk(project_id: str) -> Optional[Graph]:
    """Lazily load a typed v2 :class:`Graph` from the local pickle store.

    Chunk 8: every typed registry lives under
    ``/tmp/pickles/<project_id>/`` as a per-file pickle (see
    :class:`~src.common.pickle_store.PickleStore`). Returns ``None`` if
    no on-disk meta.json exists for this project — let the caller fall
    through to a build trigger or surface a "not built" message.
    """
    from src.processor import _project_pickle_dir

    base_dir = _project_pickle_dir(project_id)
    if not (base_dir / PickleStore.META_FILENAME).exists():
        return None
    try:
        return Graph.lazy(project_id, PickleStore(base_dir))
    except Exception as exc:  # noqa: BLE001
        # Per the greenfield rule: bumped schema_version / partial
        # layout differences surface here. Logging + None signals "user
        # needs to rebuild" without crashing the load endpoint.
        logger.warning(
            f"Graph.lazy failed for {project_id}: {type(exc).__name__}: {exc}"
        )
        return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager — server starts with no data loaded.

    Chunk 19: the legacy ``graph_data: dict`` global is gone. The v2
    typed :class:`Graph` lives in ``graph_store``; smart-merge state
    lives in ``smart_merge_state_store``. Both are wiped on shutdown.
    """
    global current_project_id
    logger.info("Data server started on port 8001")

    # Single-tenant local mode: the data-server owns its Postgres schema.
    # Apply the migrations (storage/publication statements stripped) if the
    # schema is absent — a no-op when public.projects already exists.
    #
    # The bootstrap is the single most likely first-run failure point (the DB
    # may not have accepted connections yet under a fresh `docker compose up`).
    # We retry the whole bootstrap with backoff to absorb a transient connection
    # blip, then RE-RAISE on the final failure so uvicorn exits non-zero and
    # Docker's `restart: unless-stopped` brings us back once the DB is ready.
    # Swallowing the error would leave a "healthy" server that 500s every
    # request because the schema is absent.
    _BOOTSTRAP_MAX_ATTEMPTS = 10
    _BOOTSTRAP_BACKOFF_SECONDS = 3.0
    for attempt in range(1, _BOOTSTRAP_MAX_ATTEMPTS + 1):
        try:
            apply_migrations()
            break
        except Exception as exc:  # noqa: BLE001
            if attempt >= _BOOTSTRAP_MAX_ATTEMPTS:
                logger.error(
                    "Schema bootstrap failed after %d attempts: %s — aborting "
                    "startup so the container restarts and retries",
                    attempt,
                    exc,
                )
                raise
            logger.warning(
                "Schema bootstrap attempt %d/%d failed: %s — retrying in %.1fs",
                attempt,
                _BOOTSTRAP_MAX_ATTEMPTS,
                exc,
                _BOOTSTRAP_BACKOFF_SECONDS,
            )
            time.sleep(_BOOTSTRAP_BACKOFF_SECONDS)

    try:
        yield
    finally:
        graph_store.clear()
        smart_merge_state_store.clear()
        current_project_id = None
        close_pool()
        logger.info("Shutdown complete - graph cleared from memory")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="ScriptBeeAssistant Data Server",
    description="""
## Overview

FastAPI backend for ScriptBeeAssistant. Loads typed v2 :class:`Graph`
instances per `project_id` via the ``/projects/{id}/build`` flow and
exposes query endpoints over them.

**No authentication required** — this is a standalone development server.

## Graph Data Structure

The `graph_data` variable available in /execute and /plot endpoints is
a sandbox view wrapping the loaded typed :class:`Graph`. The exact
class depends on the project's ``merge_state``:
:class:`SetupSandboxView` for PRE_MERGE projects (narrower surface;
diagnostics only), :class:`QuerySandboxView` for FINALIZED projects
(full query-stage surface). Read typed registries directly:

```python
print(len(graph_data.commits))
print(graph_data.git_accounts.all())
print(graph_data.issues.by_assignee[user_ref])
```

## Workflow

1. Upload serialized files via web UI
2. POST `/projects/{id}/build` — builds the typed Graph, runs the pipeline
3. POST `/execute` — run Python queries against the loaded Graph
4. POST `/plot` — same shape, returns matplotlib JPEG
5. DELETE `/projects/{id}/unload` — drop from memory

Smart-merge endpoints (`/projects/{id}/authors/*`) consume the loaded
typed Graph, derive `SourceIdentity` instances from
`graph.git_accounts` / `graph.jira_users` / `graph.github_users`, and
persist `UnifiedUser` records into Supabase.
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Add CORS middleware to allow requests from web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",  # Angular dev server
        "http://127.0.0.1:4200",  # Alternative localhost
    ],
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, DELETE, etc.)
    allow_headers=["*"],  # Allow all headers
)

app.include_router(projects_router)
app.include_router(filter_rules_router)
app.include_router(config_overrides_router)


# =============================================================================
# Endpoints
# =============================================================================

# Track currently loaded project (kept module-level for the /execute /
# /plot handlers' "which project's Graph do I expose?" lookup, and for
# /projects/current). Set on /load and /build; cleared on /unload and
# shutdown.
current_project_id = None
current_project_name = None


def _stats_for(graph: Optional[Graph]) -> dict:
    """Build the per-project stats block used by /health and /projects/current.

    Reads directly off the typed Graph registries; the per-source counts
    are the same shape the legacy ``graph_data``-backed endpoints
    surfaced. ``unified_users`` counts the typed :class:`UnifiedUser`
    registry on the Graph — pre-finalize this is the manual-merge
    population (mirrored from ``state.users`` by ``_apply_merge_to_graph``)
    and post-finalize it's the rebind output (manual merges + auto-created
    singletons). The smart-merge ``state.users`` list intentionally does
    NOT track the rebind's singletons, so reading it post-finalize gave
    the UI a stale count — see UU aftermath §Bug 2.
    """
    if graph is None:
        return {
            "git_commits": 0,
            "jira_issues": 0,
            "github_prs": 0,
            "unified_users": 0,
        }
    return {
        "git_commits": len(graph.commits),
        "jira_issues": len(graph.issues),
        "github_prs": len(graph.pull_requests),
        "unified_users": len(graph.unified_users),
    }


@app.get("/health")
async def health_check():
    """Health check endpoint.

    Chunk 19: ``data_loaded`` now reflects ``graph_store`` (the typed-v2
    home), and ``stats`` is computed off the loaded Graph rather than
    the deleted ``graph_data`` dict.
    """
    graph = (
        graph_store.get(current_project_id) if current_project_id else None
    )
    return {
        "status": "ok",
        "mode": "standalone",
        "data_loaded": graph is not None,
        "current_project_id": current_project_id,
        "loaded_projects": graph_store.get_all_project_ids(),
        "stats": _stats_for(graph),
    }


@app.get("/projects/current")
async def get_current_project():
    """Get information about the currently loaded project.

    Returns HTTP 200 in both states (loaded / not-loaded). The
    "no project loaded" case is communicated via the ``loaded`` flag in
    the JSON body rather than a 404 — the web-ui polls this endpoint on
    every navigation, and a 4xx response caused noisy red console errors
    even when handled correctly client-side.
    """
    graph = (
        graph_store.get(current_project_id) if current_project_id else None
    )
    if graph is None or not current_project_id:
        return {"loaded": False}
    # UnifiedUsers redesign §I (P5.B): surface the lifecycle stage so the
    # MCP layer can state-gate its tools without a second round-trip.
    return {
        "loaded": True,
        "project_id": current_project_id,
        "project_name": current_project_name,
        "merge_state": str(graph.merge_state),
        "stats": _stats_for(graph),
    }


@app.post("/projects/{project_id}/load")
async def load_project(project_id: str):
    """Load a previously-built v2 :class:`Graph` from the local pickle store.

    Chunk 19 rewrite: the v1 pickle-pull from Supabase Storage is gone —
    the v2 build path (``/projects/{id}/build`` → ``Graph.dump`` to
    ``/tmp/pickles/<project_id>/``) is the only source of truth for the
    typed graph.

    Reads:
    * Project metadata (name, status) from the ``projects`` table.
    * The typed Graph via :func:`load_graph_v2_from_disk` (or returns
      404 if no on-disk pickle exists).

    Side effects:
    * Sets ``graph_store[project_id]``.
    * Updates ``current_project_id`` / ``current_project_name``.
    * Replays persisted user mappings into
      ``smart_merge_state_store[project_id]``.
    """
    global current_project_id, current_project_name

    logger.info(f"Loading project: {project_id}")

    try:
        project = await asyncio.to_thread(
            query_one, "select * from projects where id = %s", (project_id,)
        )
        if not project:
            return JSONResponse(
                {"error": f"Project {project_id} not found"}, status_code=404
            )

        project_name = project["name"]
        project_status = project["status"]
        # UnifiedUsers redesign (§M): persist the lifecycle stage on
        # the Supabase row and mirror it onto the in-memory Graph at
        # load time. Default to PRE_MERGE when the column is absent
        # (e.g. row pre-dates the migration in a partially-migrated
        # dev DB) or carries an unknown value.
        project_merge_state_raw = project.get("merge_state") or MergeState.PRE_MERGE.value
        try:
            project_merge_state = MergeState(project_merge_state_raw)
        except ValueError:
            logger.warning(
                f"Project {project_id} has unknown merge_state="
                f"{project_merge_state_raw!r}; defaulting to PRE_MERGE"
            )
            project_merge_state = MergeState.PRE_MERGE

        if project_status != "ready":
            return JSONResponse(
                {"error": (
                    f"Project is not ready (status: {project_status}). "
                    "Please process the project first."
                )},
                status_code=400,
            )
    except Exception as e:
        logger.error(f"Failed to fetch project from database: {e}")
        return JSONResponse(
            {"error": f"Failed to fetch project: {str(e)}"}, status_code=500
        )

    # Drop any previously-loaded project's typed Graph + smart-merge cache.
    if current_project_id and current_project_id != project_id:
        graph_store.delete(current_project_id)
        smart_merge_state_store.delete(current_project_id)

    # Pull the typed Graph from the local pickle store.
    graph = await asyncio.to_thread(load_graph_v2_from_disk, project_id)
    if graph is None:
        return JSONResponse(
            {"error": (
                f"No built graph found for project {project_id}. "
                "Run `Build Graph` first."
            )},
            status_code=404,
        )

    # The Supabase ``projects`` row is the source of truth for the
    # lifecycle stage (§M). Mirror it onto the freshly loaded Graph so
    # state-gated endpoints / sandbox views can read it without an
    # extra DB round-trip.
    graph.merge_state = project_merge_state

    graph_store.set(project_id, graph)
    smart_merge_state_store.reset(project_id)
    try:
        filter_rule_store.refresh(project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"filter_rule_store.refresh failed on /load: {exc}")

    current_project_id = project_id
    current_project_name = project_name

    # Auto-replay persisted user mappings onto the loaded graph.
    replay_result = {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}
    try:
        replay_result = _replay_user_mappings(project_id)
    except Exception as e:
        logger.warning(f"Failed to replay user mappings (non-fatal): {e}")

    logger.info(f"Project {project_id} loaded into memory")

    return {
        "message": "Project loaded successfully",
        "project_id": project_id,
        "project_name": project_name,
        "stats": _stats_for(graph),
        "replay": replay_result,
    }


@app.post("/projects/{project_id}/build")
async def build_project(project_id: str, req: BuildRequest = BuildRequest()):
    """Build a typed v2 :class:`Graph` for ``project_id``.

    Chunk 8: drives the new processor end-to-end —
    download → per-source :class:`Transformer` → typed Graph →
    ``run_pipeline`` → persist. The freshly built Graph lands in
    :data:`graph_store` keyed by ``project_id``.

    The optional :class:`BuildRequest` body carries the per-build
    ``compute_annotated_lines`` toggle (plan §4), passed down to the v2
    processor. An empty POST (no body) keeps the flag off — existing
    callers need no change.
    """
    global current_project_id

    try:
        graph, pipeline_result = await asyncio.to_thread(
            v2_processor.build_graph,
            project_id,
            compute_annotated_lines=req.compute_annotated_lines,
        )
    except NotImplementedError as exc:
        logger.warning(f"build_graph deferred: {exc}")
        return JSONResponse(
            {"error": str(exc), "deferred": True}, status_code=501,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_graph failed")
        return JSONResponse(
            {"error": f"build_graph failed: {exc}"}, status_code=500,
        )

    graph_store.set(project_id, graph)
    # Fresh build invalidates any cached smart-merge state for this project.
    smart_merge_state_store.reset(project_id)
    try:
        filter_rule_store.refresh(project_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"filter_rule_store.refresh failed on /build: {exc}")

    # Update the "currently loaded" pointers so /execute against this id
    # works without an explicit /load round-trip.
    current_project_id = project_id

    return {
        "project_id": project_id,
        "schema_version": graph.schema_version,
        "built_at": graph.built_at.isoformat(),
        "pipeline": {
            "builders_run": pipeline_result.builders_run,
            "metrics_run": pipeline_result.metrics_run,
            "traits_emitted": pipeline_result.traits_emitted,
            "classifiers_emitted": pipeline_result.classifiers_emitted,
            "relations_emitted": pipeline_result.relations_emitted,
            "errors": [e.model_dump() for e in pipeline_result.errors],
        },
    }


# ---------------------------------------------------------------------------
# Finalize endpoint (UnifiedUsers redesign §G / §H / §M — task P4.A)
# ---------------------------------------------------------------------------
def _enrichment_config_to_jsonable(config: EnrichmentConfig) -> Dict[str, Any]:
    """Serialise an :class:`EnrichmentConfig` to a JSONB-safe dict.

    ``EnrichmentConfig`` is a ``@dataclass`` (not a Pydantic model) and
    carries a handful of fields that aren't natively JSON-serialisable —
    compiled ``re.Pattern`` objects on the ``*_patterns`` lists, tuple
    keys in ``daytime_buckets``, etc. We walk the declared fields and
    project each one into a JSON-friendly shape:

    * ``re.Pattern`` → its ``.pattern`` source string.
    * ``tuple``      → a list (Postgres JSONB has no tuple type).
    * ``list[tuple[str, Pattern]]`` (the regex catalogs) → list of
      ``[name, pattern_source]`` pairs.
    * primitives / dicts / lists / ``None`` pass through unchanged.

    Snapshotting at finalize time is "remember the exact thresholds the
    user signed off on" — re-running Phase B after a reload uses this
    blob (P6 follow-up) so the post-finalize metrics are reproducible
    regardless of global config drift.
    """
    import re

    def _coerce(value: Any) -> Any:
        if value is None or isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, re.Pattern):
            return value.pattern
        if isinstance(value, dict):
            return {str(k): _coerce(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_coerce(v) for v in value]
        # Fallback — stringify anything we don't recognise so the write
        # doesn't crash. The frozen-config column is forward-only (we
        # only ever read it back for reproducibility / audit).
        return str(value)

    out: Dict[str, Any] = {}
    for f in dataclass_fields(config):
        out[f.name] = _coerce(getattr(config, f.name))
    return out


@app.post("/projects/{project_id}/finalize")
async def finalize_project(project_id: str):
    """Transition a project from ``PRE_MERGE`` to ``FINALIZED``.

    UnifiedUsers redesign §G — single transactional unit:

    1. Assert the in-memory :class:`Graph` is ``PRE_MERGE``. Return 409
       if already ``FINALIZED``; 400 if missing / unexpected state.
    2. Run :func:`rebind_account_refs_to_unified` — auto-creates
       singleton :class:`UnifiedUser` entities for orphan accounts and
       rewrites every role-typed account ref to target a ``UNIFIED_USER``.
    3. Snapshot the current :class:`EnrichmentConfig` into Supabase
       (``projects.enrichment_config_frozen`` JSONB column) so any
       post-finalize re-run is reproducible (§M).
    4. Run :func:`run_pipeline_phase_b` against the rebound graph — the
       people-side relations / metrics / overviews are re-keyed on
       ``UNIFIED_USER`` refs (the kind-aware metric guards installed by
       P4.B do the right thing now that the rebind has flipped state).
    5. Re-dump the typed Graph via :meth:`Graph.dump` so the on-disk
       pickle reflects the new state.
    6. Persist ``merge_state = 'FINALIZED'`` to the Supabase ``projects``
       row.

    Half-finalized state contract: if any step AFTER rebind fails (the
    rebind itself is the only "destructive in-memory" operation), the
    graph is left FINALIZED in memory while Supabase still reads
    PRE_MERGE. We log loudly and re-raise so the operator sees it. On
    the next ``/load`` the Supabase row's ``merge_state`` wins (the row
    is the source of truth, see §M); since the on-disk pickle would
    have been re-dumped before the Supabase write in step 6, that load
    will read whatever was on disk and overwrite ``merge_state`` from
    Supabase — generally producing a consistent PRE_MERGE state again.
    The user can re-attempt the call. Re-import is the safest recovery
    path when half-finalize is suspected (per the plan §G).
    """
    graph = graph_store.get(project_id)
    if graph is None:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=404,
        )

    # --- state guard ------------------------------------------------------
    if graph.merge_state == MergeState.FINALIZED:
        return JSONResponse(
            {"error": "project_finalized"},
            status_code=409,
        )
    if graph.merge_state != MergeState.PRE_MERGE:
        return JSONResponse(
            {
                "error": (
                    f"Unexpected merge_state {graph.merge_state!r}; "
                    "finalize requires PRE_MERGE."
                )
            },
            status_code=400,
        )

    started = time.monotonic()

    # --- 0. defensive cleanup of leftover Account-keyed relations -------
    # UU aftermath §Bug 3: the v2 ``Relation.canonical_id`` includes the
    # source/target kind in the row id, so a per-source-keyed leak (e.g.
    # an ``ownership`` row authored as ``GIT_ACCOUNT → FILE`` pre-finalize)
    # would NOT dedup against the post-rebind ``UNIFIED_USER → FILE``
    # row Phase B emits. The two rows coexist and downstream consumers
    # double-count.
    #
    # Static audit of the seven Phase B builders (coauthor, ownership,
    # pr.reviewer, cochange.author_*, cochange.file_shared_devs,
    # cochange.component_shared_devs) shows every one already propagates
    # the entity's role-ref directly (``commit.author_ref`` etc.), so
    # the current code emits the right kinds. This cleanup pass is the
    # universal safety net: walk ``graph.relations`` once, drop every
    # row whose source/target carries an account-kind ref. Runs O(R)
    # at finalize — once per project lifecycle.
    #
    # Placed BEFORE the rebind so the audit operates on the catalog
    # exactly as Phase A left it; rebind itself does NOT touch
    # ``graph.relations`` (it walks ``AccountRoleRegistry`` over Entity
    # role-refs only), so the only risk it'd introduce by running
    # first is the (currently empty) class of relations that legitimately
    # carry account-kind endpoints — none exist, but the explicit
    # ordering keeps the contract auditable.
    people_kinds = phase_b_relation_kinds()
    cleanup_dropped = 0
    for rel_id in list(graph.relations.ids()):
        rel = graph.relations.get(rel_id)
        if rel is None:
            continue
        if (
            rel.source.kind in _ACCOUNT_KINDS
            or rel.target.kind in _ACCOUNT_KINDS
        ):
            graph.relations.remove(rel_id)
            cleanup_dropped += 1
    if cleanup_dropped:
        logger.warning(
            f"finalize: dropped {cleanup_dropped} Account-keyed relation row(s) "
            f"from project {project_id} before rebind "
            f"(people-side relation kinds: {sorted(people_kinds)})"
        )

    # --- 0b. repair the account -> UnifiedUser 1:1 invariant -------------
    # Re-merging an account into a freshly minted UU used to leave a stale
    # duplicate claim in the previous owner's ``account_refs``. The
    # write-time fix in ``_apply_merge_to_graph`` prevents new ones; this
    # heals any baked into the loaded pickle. Runs BEFORE rebind so Phase B
    # people-side counts are computed off de-duplicated ownership.
    refs_repaired = _repair_account_ref_ownership(graph)
    if refs_repaired:
        logger.warning(
            f"finalize: repaired {refs_repaired} duplicate account_ref "
            f"claim(s) in project {project_id} before rebind "
            "(account -> UnifiedUser 1:1 invariant)"
        )

    # --- 1. rebind --------------------------------------------------------
    # The rebind is the only step that mutates the in-memory graph
    # irreversibly. If it succeeds, the graph is in-memory FINALIZED
    # regardless of what happens after — see the docstring's
    # half-finalized contract.
    try:
        rebind_stats = await asyncio.to_thread(
            rebind_account_refs_to_unified, graph
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(f"rebind failed for project {project_id}")
        return JSONResponse(
            {"error": f"rebind failed: {exc}"},
            status_code=500,
        )

    # --- 2. snapshot the EnrichmentConfig --------------------------------
    # Source: the default config. The per-project config_overrides ARE
    # applied at build time (see ``processor._apply_project_overrides``)
    # but the resulting effective config is not retained on the Graph —
    # the snapshot here uses ``DEFAULT_CONFIG`` augmented with whatever
    # overrides the repository currently carries, mirroring the build
    # path. The repository is best-effort (Supabase failures degrade to
    # the bare default) so finalize never blocks on snapshotting.
    try:
        from src.config_overrides.repository import ConfigOverridesRepository
        from src.config_overrides.merge import apply_overrides

        overrides = ConfigOverridesRepository().get(project_id).overrides
        if overrides:
            effective_config = apply_overrides(DEFAULT_CONFIG, overrides)
        else:
            effective_config = DEFAULT_CONFIG
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"finalize: config_overrides lookup failed ({exc}); "
            "snapshotting bare DEFAULT_CONFIG"
        )
        effective_config = DEFAULT_CONFIG

    frozen_config = _enrichment_config_to_jsonable(effective_config)

    # --- 3. Run Phase B against the rebound graph ------------------------
    # The graph is now FINALIZED in memory; the kind-aware metric guards
    # installed by P4.B treat ``UNIFIED_USER`` refs as the canonical
    # author principal, so phase B emits author-side relations / metrics
    # / overviews keyed on the new UU refs.
    try:
        phase_b_result: PipelineResult = await asyncio.to_thread(
            run_pipeline_phase_b, graph, effective_config
        )
    except Exception as exc:  # noqa: BLE001
        # Half-finalized: graph is FINALIZED in memory but Supabase
        # still says PRE_MERGE. Log loudly + re-raise per the §G
        # contract.
        logger.exception(
            f"finalize: phase_b failed for project {project_id}; "
            "graph is FINALIZED in memory but merge_state has NOT been "
            "persisted to Supabase. Next /load will read PRE_MERGE from "
            "the projects row and overwrite the in-memory state."
        )
        return JSONResponse(
            {
                "error": (
                    f"phase_b failed (half-finalized state): {exc}. "
                    "Re-import the project to reset."
                )
            },
            status_code=500,
        )

    # --- 4. Re-dump the typed Graph --------------------------------------
    try:
        await asyncio.to_thread(v2_processor.save_graph_to_disk, graph)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            f"finalize: save_graph_to_disk failed for project {project_id}; "
            "in-memory graph is FINALIZED but on-disk pickle is stale."
        )
        return JSONResponse(
            {
                "error": (
                    f"Graph dump failed (half-finalized state): {exc}. "
                    "Re-import the project to reset."
                )
            },
            status_code=500,
        )

    # --- 5. Persist merge_state + frozen config to the database ----------
    import json as _json

    def _persist_finalize() -> None:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update projects set merge_state = %s, "
                    "enrichment_config_frozen = %s where id = %s",
                    (
                        MergeState.FINALIZED.value,
                        _json.dumps(frozen_config),
                        project_id,
                    ),
                )

    try:
        await asyncio.to_thread(_persist_finalize)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            f"finalize: DB persist failed for project {project_id}; "
            "graph is FINALIZED in memory + on disk but the projects row "
            "still reads PRE_MERGE. Next /load will overwrite the in-memory "
            "state from the DB, producing a PRE_MERGE graph again."
        )
        return JSONResponse(
            {
                "error": (
                    f"DB persist failed (half-finalized state): {exc}. "
                    "Re-import the project to reset."
                )
            },
            status_code=500,
        )

    duration_ms = int((time.monotonic() - started) * 1000)

    logger.info(
        f"Finalized project {project_id} in {duration_ms}ms — "
        f"unified_users_created={rebind_stats.unified_users_created}, "
        f"refs_rewritten={rebind_stats.refs_rewritten}, "
        f"account_refs_repaired={refs_repaired}, "
        f"phase_b_relations={phase_b_result.relations_emitted}, "
        f"phase_b_traits={phase_b_result.traits_emitted}, "
        f"phase_b_classifiers={phase_b_result.classifiers_emitted}"
    )

    return {
        "merge_state": MergeState.FINALIZED.value,
        "unified_users_created": rebind_stats.unified_users_created,
        "refs_rewritten": rebind_stats.refs_rewritten,
        "account_refs_repaired": refs_repaired,
        "phase_b_relations_built": phase_b_result.relations_emitted,
        "phase_b_traits_emitted": phase_b_result.traits_emitted,
        "phase_b_classifiers_emitted": phase_b_result.classifiers_emitted,
        "phase_b_errors": [e.model_dump() for e in phase_b_result.errors],
        "duration_ms": duration_ms,
    }


@app.delete("/projects/{project_id}/unload")
async def unload_project(project_id: str):
    """Unload a project's typed Graph + smart-merge state from memory.

    Chunk 19: drops both ``graph_store[project_id]`` AND
    ``smart_merge_state_store[project_id]``.
    """
    global current_project_id, current_project_name

    removed_graph = graph_store.delete(project_id)
    smart_merge_state_store.delete(project_id)

    if current_project_id == project_id:
        current_project_id = None
        current_project_name = None

    if not removed_graph:
        return JSONResponse(
            {"error": f"Project {project_id} is not currently loaded"},
            status_code=400,
        )

    logger.info(f"Project {project_id} unloaded successfully")
    return {"message": "Project unloaded successfully"}


@app.post("/projects/{project_id}/scaffold-workspace")
async def scaffold_workspace(project_id: str):
    """Create the per-project workspace folder for AI agent analysis.

    Creates a directory under WORKSPACE_ROOT with a README containing
    project info, stats, and UUID. Also creates outputs/ and scripts/
    subdirs.
    """
    try:
        project = query_one(
            "select name from projects where id = %s", (project_id,)
        )
        if not project:
            return JSONResponse({"error": f"Project {project_id} not found"}, status_code=404)
        project_name = project["name"]
    except Exception as e:
        logger.error(f"Failed to fetch project for scaffolding: {e}")
        return JSONResponse({"error": f"Failed to fetch project: {str(e)}"}, status_code=500)

    # Sanitize name for directory
    folder_name = re.sub(r'[^a-z0-9-]', '', project_name.lower().replace(' ', '-'))
    if not folder_name:
        folder_name = project_id[:8]

    workspace_path = Path(WORKSPACE_ROOT) / folder_name

    workspace_path.mkdir(parents=True, exist_ok=True)
    (workspace_path / "outputs").mkdir(exist_ok=True)
    (workspace_path / "scripts").mkdir(exist_ok=True)

    # Gather stats if project is loaded
    stats_text = ""
    graph = graph_store.get(project_id)
    if graph is not None:
        stats_text = (
            f"\n## Statistics\n\n"
            f"- Git commits: {len(graph.commits)}\n"
            f"- JIRA issues: {len(graph.issues)}\n"
            f"- GitHub PRs: {len(graph.pull_requests)}\n"
        )

    readme_content = (
        f"# {project_name}\n\n"
        f"- **Project UUID:** `{project_id}`\n"
        f"- **Workspace created:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"{stats_text}\n"
        f"## Usage\n\n"
        f"Open your AI agent in this directory to analyze this project:\n\n"
        f"```bash\n"
        f"cd analyzed_projects/projects/{folder_name}\n"
        f"opencode   # or: claude\n"
        f"```\n\n"
        f"The agent has MCP tools to query the data-server.\n"
        f"See `analyzed_projects/instructions/` for data model documentation.\n"
    )
    (workspace_path / "README.md").write_text(readme_content)

    relative_path = f"analyzed_projects/projects/{folder_name}"
    logger.info(f"Workspace scaffolded for project '{project_name}' at {relative_path}")

    return {
        "path": relative_path,
        "project_name": project_name,
        "folder_name": folder_name,
    }


# =============================================================================
# Author Smart Merge Endpoints
# =============================================================================
#
# Per Phase-2 decision D5 (architectural_change_followup.md §1) the
# smart-merge engine takes a typed v2 :class:`Graph` and derives
# :class:`SourceIdentity` instances from the typed registries
# (``git_accounts`` / ``jira_users`` / ``github_users``). UnifiedUser
# records are persisted into Supabase ``unified_users`` /
# ``user_identity_mappings`` / ``rejected_similarities`` (unchanged).
#
# The Phase-1 legacy ``graph_data`` global is GONE — per-project
# smart-merge state (unified-user list, base-graph cache, last-served
# suggestions) lives in :mod:`src.smart_merge.state_store` and is keyed
# by ``project_id``.

def _get_smart_merge_engine() -> AuthorSmartMergeEngine:
    """Create a smart merge engine instance with Supabase persistence."""
    return AuthorSmartMergeEngine(SupabaseSmartMergeRepository())


def _require_loaded_graph(project_id: str) -> Graph | JSONResponse:
    """Return the loaded :class:`Graph` for ``project_id`` or a 400 JSON.

    Smart-merge endpoints used to gate on
    ``not graph_data or current_project_id != project_id``; the v2
    equivalent is "is a Graph loaded for this project in
    ``graph_store``?". Returning a :class:`JSONResponse` lets callers
    short-circuit with a plain ``isinstance`` check.
    """
    graph = graph_store.get(project_id)
    if graph is None:
        return JSONResponse(
            {"error": "Project is not loaded. Load the project first."},
            status_code=400,
        )
    return graph


def _require_pre_merge(graph: Graph) -> JSONResponse | None:
    """Refuse setup-mutating calls once the project has been finalized.

    UU aftermath §Bug 1: every setup endpoint (apply / apply-batch /
    reject / delete-user / delete-all-users / replay) used to gate only
    on "graph loaded?", letting a direct HTTP caller mutate state after
    finalize. The MCP wrapper had the gate; the data-server underneath
    did not.

    Returns ``JSONResponse({"error": "project_finalized"}, status_code=409)``
    when ``graph.merge_state != MergeState.PRE_MERGE``; otherwise
    ``None``. The error key matches the existing ``/finalize`` 409 so the
    web UI / MCP layer's existing 409 → 'already finalized' mapping
    covers this without change. ``/finalize`` keeps its inline check —
    it must distinguish FINALIZED (409) from other non-PRE_MERGE states
    (400).
    """
    if graph.merge_state != MergeState.PRE_MERGE:
        return JSONResponse(
            {"error": "project_finalized"},
            status_code=409,
        )
    return None


def _get_activity_counts(graph: Graph) -> dict[str, int]:
    """Compute activity counts for every identity in the loaded graph.

    Replaces the Phase-1 helper that read off ``graph_data["git"].account_registry``
    and friends. v2 reads through the typed Graph's secondary indexes
    (``commits.by_author`` etc.) — those are O(1) bucket lookups so even
    a few-thousand-account graph is cheap.
    """
    counts: dict[str, int] = {}

    for account in graph.git_accounts.all():
        key = f"git:{account.id}"
        counts[key] = len(graph.commits.by_author[account.ref()])

    for user in graph.github_users.all():
        key = f"github:{user.id}"
        # PullRequestRegistry only declares ``by_author``; merged_by /
        # assignee aggregates would require a scan or new index. The
        # legacy code summed three lists kept on the GitHubUser entity
        # itself — those back-pointers are gone in v2, so we approximate
        # with the indexed ``by_author`` count, which is what the smart-
        # merge UI primarily sorts on (the most-active account wins the
        # name/email selection).
        counts[key] = len(graph.pull_requests.by_author[user.ref()])

    for user in graph.jira_users.all():
        key = f"jira:{user.id}"
        # IssueRegistry declares all three: by_reporter / by_creator /
        # by_assignee. Sum without dedup so we keep parity with the
        # legacy v1 helper (which also summed three flat lists).
        user_ref = user.ref()
        counts[key] = (
            len(graph.issues.by_reporter[user_ref])
            + len(graph.issues.by_creator[user_ref])
            + len(graph.issues.by_assignee[user_ref])
        )

    return counts


def _detach_account_ref(graph: Graph, ref, keep_uu_id: str) -> int:
    """Strip ``ref`` from every UnifiedUser except ``keep_uu_id``.

    Enforces the 1:1 account -> UnifiedUser invariant on the reverse side
    (``UnifiedUser.account_refs``). The forward mapping
    (``Account.unified_user_id``) is the source of truth; this removes any
    *other* UU's stale reverse-claim on the same account so a re-merge that
    moved the account to a new UU doesn't leave a duplicate behind.

    Maintains the ``by_account`` multi-index via remove-then-mutate-then-add:
    ``Registry.add`` only purges the old index buckets when the stored entity
    is a *different* object, so an in-place ``account_refs`` mutation would
    otherwise leave stale buckets. Returns the number of stale claims removed.
    """
    reg = graph.unified_users
    removed = 0
    # ``by_account[ref]`` is a tuple snapshot (see kernel ``Index.__getitem__``),
    # so mutating the registry while iterating it is safe.
    for owner in reg.by_account[ref]:  # type: ignore[attr-defined]
        if owner.id == keep_uu_id:
            continue
        if ref not in owner.account_refs:
            continue
        reg.remove(owner.id)
        owner.account_refs = [r for r in owner.account_refs if r != ref]
        reg.add(owner)
        removed += 1
    return removed


def _repair_account_ref_ownership(graph: Graph) -> int:
    """One-shot repair of the account -> UnifiedUser 1:1 invariant.

    Heals graphs (e.g. on-disk pickles) built before the write-time fix in
    :func:`_apply_merge_to_graph`, where re-merging an account into a freshly
    minted UU left a stale duplicate claim in the previous owner's
    ``account_refs``. The forward mapping ``Account.unified_user_id`` is the
    source of truth; for every account that carries one, strip its ref from
    every UU except the named owner. Returns the total number of stale
    reverse-claims removed (0 when the invariant already holds).
    """
    total = 0
    for reg_acc in (graph.git_accounts, graph.github_users, graph.jira_users):
        for account in reg_acc.all():
            owner_id = getattr(account, "unified_user_id", None)
            if owner_id is None:
                continue
            total += _detach_account_ref(graph, account.ref(), owner_id)
    return total


def _apply_merge_to_graph(graph: Graph, uu: UnifiedUser) -> None:
    """Mirror an accepted smart-merge ``UnifiedUser`` onto the typed Graph.

    UnifiedUsers redesign (``unified_users_change.md`` §F): smart-merge
    apply / apply-batch / replay endpoints used to write only to Supabase
    and to the in-memory ``state.users`` list. The typed graph side
    (per-source ``Account.unified_user_id`` back-pointers + the typed
    :class:`~src.common.people.UnifiedUser` entity in
    ``graph.unified_users``) was never populated, leaving the rebind pass
    (P3) with no data.

    This helper closes that gap. For every :class:`SourceIdentity` carried
    by ``uu``:

    1. Resolve the typed :class:`Account` in the matching domain registry
       (``git`` → ``graph.git_accounts``, ``github`` → ``graph.github_users``,
       ``jira`` → ``graph.jira_users``).
    2. Set ``account.unified_user_id = uu.id`` so the rebind pass finds
       a value to map.
    3. Build the typed ``UnifiedUser`` entity (create-or-update) and
       extend its ``account_refs`` with the freshly resolved accounts'
       refs, de-duplicated.

    The helper is idempotent: re-applying the same merge (same
    ``uu.id``, same identities) leaves the graph unchanged. Per the plan
    §F this runs during ``PRE_MERGE`` only — we do NOT touch
    ``graph.merge_state`` (that flips at finalize, P4) and we do NOT
    auto-create UUs for orphan accounts (that's the rebind pass, P3).
    Accounts that don't resolve are logged and skipped — never crash.
    """
    resolved_accounts: list = []
    for identity in uu.identities:
        source = identity.source
        source_key = identity.source_key
        if source == "git":
            account = graph.git_accounts.get(source_key)
        elif source == "github":
            account = graph.github_users.get(source_key)
        elif source == "jira":
            account = graph.jira_users.get(source_key)
        else:
            logger.warning(
                f"_apply_merge_to_graph: unknown identity source "
                f"{source!r} for unified_user {uu.id} (source_key={source_key!r})"
            )
            continue

        if account is None:
            logger.warning(
                f"_apply_merge_to_graph: typed account not found in graph "
                f"for {source}:{source_key} (unified_user {uu.id})"
            )
            continue

        account.unified_user_id = uu.id
        resolved_accounts.append(account)

    # Enforce the 1:1 account -> UnifiedUser invariant. Each resolved
    # account now belongs to ``uu`` (its ``unified_user_id`` was set above),
    # so strip its ref from any OTHER UnifiedUser that still claims it.
    # Without this, re-merging an account into a new UU leaves a stale
    # duplicate claim in the previous owner's ``account_refs`` — note that
    # ``apply`` / ``apply-batch`` mint a fresh ``uu.id`` per call, so a
    # re-merge routinely arrives under a different id than the prior owner.
    reg = graph.unified_users
    for account in resolved_accounts:
        _detach_account_ref(graph, account.ref(), uu.id)

    # Create-or-update the typed UnifiedUser entity. Subsequent merges
    # that fold more accounts into an existing UU extend ``account_refs``
    # rather than replace it. De-dup on extend keeps idempotency intact
    # when the same merge is replayed (e.g. on /load).
    existing = reg.get(uu.id)
    if existing is None:
        # P4.C: pass through ``uu.identities`` so the typed-graph
        # ``UnifiedUser`` entity carries the same smart-merge identities
        # the API-side object did (post-collapse there is one class —
        # the entity IS the DTO). The ``identities`` field is
        # ``exclude=True`` on the entity, so this does NOT affect pickle
        # layout; it just gives the bound instance the data its
        # ``commit_count`` / ``to_dict`` accessors expect.
        graph.unified_users.add(
            UnifiedUser(
                id=uu.id,
                display_name=uu.display_name,
                primary_email=uu.primary_email,
                account_refs=[a.ref() for a in resolved_accounts],
                identities=list(uu.identities),
            )
        )
        return

    # Update the existing entity in place. ``display_name`` /
    # ``primary_email`` adopt the latest values from the merge (a later
    # apply can rename the UU). ``account_refs`` extends with any refs
    # not already present so we never duplicate.
    existing.display_name = uu.display_name
    existing.primary_email = uu.primary_email
    existing_refs = set(existing.account_refs)
    new_refs = [a.ref() for a in resolved_accounts if a.ref() not in existing_refs]
    # P4.C: extend identities with any new entries (de-dup by
    # ``source`` + ``source_key``) so re-applying the same merge is
    # idempotent and folding a second merge into an existing UU widens
    # the identity set without dropping prior entries.
    seen_keys = {(i.source, i.source_key) for i in existing.identities}
    new_idents = [
        i for i in uu.identities if (i.source, i.source_key) not in seen_keys
    ]
    # Remove first so the ``by_account`` buckets keyed off the *current*
    # ``account_refs`` are purged, then mutate, then re-add to rebuild them.
    # ``Registry.add`` skips that purge for an in-place update (the stored
    # entity IS ``existing``), which would otherwise double-insert the
    # account keys into the multi-index on every replay.
    reg.remove(existing.id)
    if new_refs:
        existing.account_refs = list(existing.account_refs) + new_refs
    if new_idents:
        existing.identities = list(existing.identities) + new_idents
    reg.add(existing)


def _replay_user_mappings(project_id: str) -> dict:
    """Replay persisted Supabase user mappings onto the loaded Graph.

    Called by :func:`load_project` after the typed Graph slot is set.
    Re-creates the :class:`UnifiedUser` API objects from the persisted
    ``unified_users`` + ``user_identity_mappings`` rows, binds each to
    the typed Graph so the per-instance stats accessors resolve live,
    and stores them in :mod:`smart_merge_state_store`.
    """
    repo = SupabaseSmartMergeRepository()
    mappings = repo.get_user_mappings(project_id)

    state = smart_merge_state_store.reset(project_id)

    graph = graph_store.get(project_id)
    if graph is None:
        # Defensive — the only caller is load_project, which sets the
        # graph before calling here. If this fires, something's racing.
        return {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}

    if not mappings:
        return {"users_replayed": 0, "identities_matched": 0, "identities_missing": 0}

    users: list[UnifiedUser] = []
    total_matched = 0
    total_missing = 0

    available_keys = {i.key for i in extract_all_identities(graph)}

    for mapping in mappings:
        matched_identities = []
        for identity in mapping.identities:
            if identity.key in available_keys:
                matched_identities.append(identity)
                total_matched += 1
            else:
                total_missing += 1
                logger.warning(
                    f"Identity {identity.key} not found in current graph "
                    f"(unified user {mapping.unified_user_id})"
                )

        user = UnifiedUser(
            id=mapping.unified_user_id,
            display_name=mapping.display_name,
            primary_email=mapping.primary_email,
            identities=matched_identities,
        )
        user.bind_graph(graph)
        users.append(user)

        # UnifiedUsers redesign §F: rebuilding smart-merge state from
        # Supabase on /load must also rebuild the typed-graph side of
        # the merge (per-source ``Account.unified_user_id`` + a typed
        # ``UnifiedUser`` entity). Without this, a graph reload would
        # silently lose the typed-graph half — the rebind pass (P3)
        # would then see un-marked accounts and synthesise singleton UUs
        # for already-merged identities.
        try:
            _apply_merge_to_graph(graph, user)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"_apply_merge_to_graph failed during replay for "
                f"{user.id}: {exc}"
            )

    state.users = users

    # Heal any account -> UnifiedUser duplicate reverse-claims baked into
    # the loaded pickle (graphs built before the write-time fix in
    # ``_apply_merge_to_graph``). The per-mapping detach above already
    # prevents replay from introducing new ones; this final pass also
    # clears stale claims left by an owner whose account moved to a UU not
    # present in the current mappings.
    refs_repaired = _repair_account_ref_ownership(graph)

    logger.info(
        f"Replayed {len(users)} unified users "
        f"({total_matched} matched, {total_missing} missing, "
        f"{refs_repaired} duplicate account_ref claim(s) repaired)"
    )

    return {
        "users_replayed": len(users),
        "identities_matched": total_matched,
        "identities_missing": total_missing,
        "account_refs_repaired": refs_repaired,
    }


@app.get("/projects/{project_id}/authors/suggestions")
async def get_author_suggestions(project_id: str):
    """Compute and return author merge suggestions for the loaded project.

    Uses the smart merge engine to detect likely duplicate identities
    across Git, GitHub, and JIRA sources.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    try:
        state = smart_merge_state_store.get(project_id)
        all_identities = extract_all_identities(graph)
        existing_users: list[UnifiedUser] = state.users
        activity_counts = _get_activity_counts(graph)

        # Exclude identities already bound to a unified user. Re-scans should
        # only propose merges among the still-unmerged identities — otherwise
        # apply would violate uq_identity_mapping.
        mapped_keys = {
            identity.key
            for user in existing_users
            for identity in user.identities
        }
        unmapped_identities = [i for i in all_identities if i.key not in mapped_keys]

        # Invalidate the cached base graph if the unmapped set changed.
        cached_base = state.base_graph
        if cached_base is not None and set(cached_base.nodes.keys()) != {
            i.key for i in unmapped_identities
        }:
            cached_base = None

        engine = _get_smart_merge_engine()
        suggestions, base_graph = engine.compute_suggestions(
            identities=unmapped_identities,
            project_id=project_id,
            existing_users=[],
            activity_counts=activity_counts,
            base_graph=cached_base,
        )
        state.base_graph = base_graph
        state.last_suggestions = {s.suggestion_id: s for s in suggestions}

        return {
            "suggestions": [s.to_dict(activity_counts) for s in suggestions],
            "total_identities": len(all_identities),
            "existing_users": len(existing_users),
        }
    except Exception as e:
        logger.error(f"Failed to compute suggestions: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to compute suggestions: {str(e)}"},
            status_code=500,
        )


@app.get("/projects/{project_id}/authors/suggestions/{suggestion_id}/identities")
async def get_suggestion_identities(
    project_id: str,
    suggestion_id: str,
    offset: int = 0,
    limit: int = MAX_IDENTITIES_PER_SUGGESTION,
):
    """Return a page of identities for a previously computed suggestion.

    The suggestions endpoint caches the full list in memory keyed by
    suggestion_id. The UI calls this endpoint to page through clusters
    larger than MAX_IDENTITIES_PER_SUGGESTION.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    state = smart_merge_state_store.get(project_id)
    suggestion = state.last_suggestions.get(suggestion_id)
    if suggestion is None:
        return JSONResponse(
            {"error": "Suggestion not found. Recompute suggestions and try again."},
            status_code=404,
        )

    if offset < 0:
        offset = 0
    if limit <= 0:
        limit = MAX_IDENTITIES_PER_SUGGESTION

    activity_counts = _get_activity_counts(graph)
    ordered = sorted(
        suggestion.identities,
        key=lambda i: activity_counts.get(i.key, 0),
        reverse=True,
    )
    page = ordered[offset: offset + limit]

    return {
        "suggestion_id": suggestion_id,
        "total_identities": len(suggestion.identities),
        "offset": offset,
        "limit": limit,
        "identities": [
            {
                "source": i.source,
                "source_key": i.source_key,
                "name": i.name,
                "email": i.email,
                "login": i.login,
            }
            for i in page
        ],
    }


@app.post("/projects/{project_id}/authors/suggestions/apply")
async def apply_author_suggestion(project_id: str, request: ApplySuggestionRequest):
    """Apply a merge suggestion: create a UnifiedUser from selected identities.

    Optionally rejects unselected identities from the same suggestion.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph
    err = _require_pre_merge(graph)
    if err is not None:
        return err

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        all_identities = extract_all_identities(graph)
        identity_lookup = {i.key: i for i in all_identities}

        # Resolve selected identities
        selected: list[SourceIdentity] = []
        for key in request.selected_identity_keys:
            identity = identity_lookup.get(key)
            if identity:
                selected.append(identity)
            else:
                logger.warning(f"Selected identity key not found: {key}")

        if len(selected) < 2:
            return JSONResponse(
                {"error": "At least 2 valid identities are required to create a unified user."},
                status_code=400,
            )

        # Create the unified user
        user = UnifiedUser(
            display_name=request.name,
            primary_email=request.email if request.email != "unknown@unknown" else None,
            identities=selected,
        )
        user.bind_graph(graph)

        # Persist the mapping
        mapping = UserMapping(
            unified_user_id=user.id,
            display_name=user.display_name,
            primary_email=user.primary_email,
            identities=selected,
        )
        repo.upsert_user_mapping(mapping, project_id)

        # Update in-memory state
        state.users.append(user)
        state.invalidate_cache()

        # UnifiedUsers redesign §F: mirror the merge onto the typed graph
        # (per-source ``Account.unified_user_id`` back-pointers + a typed
        # ``UnifiedUser`` entity in ``graph.unified_users``). Wrapped in a
        # try/except so a graph-side hiccup never invalidates the
        # already-persisted Supabase mapping above.
        try:
            _apply_merge_to_graph(graph, user)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"_apply_merge_to_graph failed for {user.id} "
                f"(Supabase mapping was persisted): {exc}"
            )

        # Handle rejected (unselected) identities
        if request.unselected_identity_keys:
            unselected = [
                identity_lookup[k]
                for k in request.unselected_identity_keys
                if k in identity_lookup
            ]
            pairs = []
            for sel in selected:
                for unsel in unselected:
                    pairs.append(RejectedPair(
                        project_id=project_id,
                        first_source=sel.source,
                        first_source_key=sel.source_key,
                        second_source=unsel.source,
                        second_source_key=unsel.source_key,
                    ))
            if pairs:
                repo.add_rejected_similarities(project_id, pairs)

        return user.to_dict()

    except Exception as e:
        logger.error(f"Failed to apply suggestion: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to apply suggestion: {str(e)}"},
            status_code=500,
        )


@app.post("/projects/{project_id}/authors/suggestions/apply-batch")
async def apply_author_suggestions_batch(project_id: str):
    """Bulk-apply every cached suggestion using its default name/email and all identities.

    Expects the caller to have just fetched suggestions (so the cache is populated).
    Partial failures are reported but do not abort the loop.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph
    err = _require_pre_merge(graph)
    if err is not None:
        return err

    state = smart_merge_state_store.get(project_id)
    cache = state.last_suggestions
    if not cache:
        return JSONResponse(
            {"error": "No cached suggestions. Compute suggestions first."},
            status_code=400,
        )

    suggestions = list(cache.values())
    repo = SupabaseSmartMergeRepository()

    created_dtos: list[dict] = []
    failures: list[dict] = []

    for suggestion in suggestions:
        try:
            if len(suggestion.identities) < 2:
                failures.append({
                    "suggestion_id": suggestion.suggestion_id,
                    "error": "Suggestion has fewer than 2 identities.",
                })
                continue

            email = suggestion.default_email
            primary_email = email if email and email != "unknown@unknown" else None

            user = UnifiedUser(
                display_name=suggestion.default_name,
                primary_email=primary_email,
                identities=list(suggestion.identities),
            )
            user.bind_graph(graph)

            mapping = UserMapping(
                unified_user_id=user.id,
                display_name=user.display_name,
                primary_email=user.primary_email,
                identities=user.identities,
            )
            repo.upsert_user_mapping(mapping, project_id)

            # UnifiedUsers redesign §F: mirror the merge onto the typed
            # graph (per-source ``Account.unified_user_id`` + typed
            # ``UnifiedUser`` entity). Same try/except shape as the
            # single-apply path — a graph-side hiccup must not flip the
            # already-persisted mapping into the ``failures`` bucket.
            try:
                _apply_merge_to_graph(graph, user)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"_apply_merge_to_graph failed for {user.id} in batch "
                    f"(Supabase mapping was persisted): {exc}"
                )

            state.users.append(user)
            created_dtos.append(user.to_dict())

        except Exception as e:
            logger.error(
                f"Failed to apply suggestion {suggestion.suggestion_id} in batch: {e}",
                exc_info=True,
            )
            failures.append({
                "suggestion_id": suggestion.suggestion_id,
                "error": str(e),
            })

    state.invalidate_cache()

    return {
        "created": len(created_dtos),
        "failed": failures,
        "users": created_dtos,
    }


@app.post("/projects/{project_id}/authors/suggestions/reject")
async def reject_author_suggestion(project_id: str, request: RejectSuggestionRequest):
    """Reject a merge suggestion: persist rejected pairs so they don't reappear."""
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph
    err = _require_pre_merge(graph)
    if err is not None:
        return err

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        all_identities = extract_all_identities(graph)
        identity_lookup = {i.key: i for i in all_identities}

        identities = [
            identity_lookup[k]
            for k in request.identity_keys
            if k in identity_lookup
        ]

        if len(identities) < 2:
            return JSONResponse(
                {"error": "At least 2 valid identity keys required."},
                status_code=400,
            )

        # Create all pairwise rejected pairs
        pairs = []
        for i, a in enumerate(identities):
            for b in identities[i + 1:]:
                pairs.append(RejectedPair(
                    project_id=project_id,
                    first_source=a.source,
                    first_source_key=a.source_key,
                    second_source=b.source,
                    second_source_key=b.source_key,
                ))

        repo.add_rejected_similarities(project_id, pairs)
        state.invalidate_cache()

        return {"ok": True, "rejected_pairs": len(pairs)}

    except Exception as e:
        logger.error(f"Failed to reject suggestion: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to reject suggestion: {str(e)}"},
            status_code=500,
        )


@app.get("/projects/{project_id}/authors/users")
async def get_unified_users(project_id: str):
    """Get the current list of unified users for a project.

    Returns both persisted and in-memory state.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    state = smart_merge_state_store.get(project_id)
    return {
        "users": [u.to_dict() for u in state.users],
        "total": len(state.users),
    }


@app.delete("/projects/{project_id}/authors/users/{unified_user_id}")
async def delete_unified_user(project_id: str, unified_user_id: str):
    """Delete a unified user and its identity mappings.

    Removes the unified user from both database (cascade-deletes identity
    mappings) and in-memory graph state.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph
    err = _require_pre_merge(graph)
    if err is not None:
        return err

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        repo.delete_user_mapping(project_id, unified_user_id)

        state.users = [u for u in state.users if u.id != unified_user_id]
        state.invalidate_cache()

        return {"ok": True, "deleted_id": unified_user_id}

    except Exception as e:
        logger.error(f"Failed to delete unified user: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to delete unified user: {str(e)}"},
            status_code=500,
        )


@app.delete("/projects/{project_id}/authors/users")
async def delete_all_unified_users(project_id: str):
    """Reset author matching for a project as if it was freshly created.

    Wipes unified users + identity mappings + rejected-pair history in
    Supabase, clears the in-memory users list, and invalidates the
    smart-merge cache.

    Chunk 19: the Phase-1 ``_persist_project_pickle`` call (which would
    have raised ``ImportError: upload_pickle_to_supabase``) is gone —
    v2 persists each typed registry on /build via :meth:`Graph.dump`,
    and smart-merge state is the Supabase tables themselves. Deleting
    the rows IS the persistence operation.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph
    err = _require_pre_merge(graph)
    if err is not None:
        return err

    try:
        state = smart_merge_state_store.get(project_id)
        repo = SupabaseSmartMergeRepository()
        deleted_users = repo.delete_all_user_mappings(project_id)
        deleted_rejected = repo.delete_all_rejected_similarities(project_id)

        state.users = []
        state.invalidate_cache()

        return {
            "ok": True,
            "deleted_users": deleted_users,
            "deleted_rejected": deleted_rejected,
        }

    except Exception as e:
        logger.error(f"Failed to reset author matching: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to reset author matching: {str(e)}"},
            status_code=500,
        )


@app.post("/projects/{project_id}/save-graph-state")
async def save_graph_state(project_id: str):
    """Snapshot endpoint — preserved for the web-UI smart-merge button.

    Chunk 19: this used to call the dormant ``_persist_project_pickle``
    (which would have raised ``ImportError: upload_pickle_to_supabase``
    at runtime since the v2 processor stopped exporting that symbol —
    flagged in chunk_10_cleanup §H.2). In v2 there is no single
    ``graph.pkl`` blob to upload: the typed Graph is persisted per
    registry under ``/tmp/pickles/<project_id>/`` by :meth:`Graph.dump`
    at build time, and smart-merge state lives in Supabase
    (``unified_users`` / ``user_identity_mappings`` /
    ``rejected_similarities``) so each apply / reject / delete call IS
    the durable write.

    The endpoint stays mounted so the existing UI button doesn't 404; it
    now returns ``ok`` with the live user count for parity with the v1
    response shape. If a future need surfaces to dump the typed Graph
    on demand, call :meth:`Graph.dump` here against a fresh
    :class:`PickleStore`.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    state = smart_merge_state_store.get(project_id)
    return {
        "ok": True,
        "size_mb": 0.0,  # No upload happens; field preserved for v1 wire compat.
        "user_count": len(state.users),
    }


@app.post("/projects/{project_id}/authors/users/replay")
async def replay_unified_users(project_id: str):
    """Replay persisted user mappings onto the loaded graph.

    Called automatically on project load, but can be triggered manually.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph
    err = _require_pre_merge(graph)
    if err is not None:
        return err

    try:
        result = _replay_user_mappings(project_id)
        return result
    except Exception as e:
        logger.error(f"Failed to replay user mappings: {e}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to replay user mappings: {str(e)}"},
            status_code=500,
        )


# =============================================================================
# Components endpoints (B3 — REST surface for the components page)
# =============================================================================
#
# Three endpoints power the post-setup Components page:
#
# * ``GET  /projects/{id}/components``         — one row per component with
#                                                aggregate file_count + total_loc
#                                                (Lizard sum_nloc summed across
#                                                the component's files).
# * ``GET  /projects/{id}/components/files``   — flat list of every file in the
#                                                project with its component name
#                                                and LOC, sized for a single
#                                                LOC-proportional treemap.
# * ``PUT  /projects/{id}/component-mapping``  — persist a curated mapping JSON
#                                                to ``projects.component_mapping``
#                                                and trigger a full build so
#                                                ``graph.components`` reflects
#                                                the new shape.
#
# The two GETs gate on "is a typed Graph loaded for this project" via
# :func:`_require_loaded_graph` (same 400 contract as the smart-merge endpoints).
# The PUT validates the payload through :func:`parse_component_mapping` BEFORE
# touching Supabase so a malformed body never persists.
#
# Ownership: no existing endpoint in this server enforces project ownership
# today (single-tenant dev mode; ``get_service_client`` bypasses RLS for every
# Supabase touchpoint). These endpoints stay consistent with that contract; if
# the codebase later adds an auth gate the same dependency should be applied
# here.


def _component_total_loc(
    file_refs: List[Any], sum_nloc_by_file: Dict[Any, float]
) -> float:
    """Sum the ``sum_nloc`` Lizard metric across a component's file refs.

    Missing entries (files without a Lizard row) contribute 0. Matches the
    convention used by :func:`anomaly_complexity._sum_nloc_index`.
    """
    total = 0.0
    for ref in file_refs:
        v = sum_nloc_by_file.get(ref)
        if v is not None:
            total += v
    return total


def _sum_nloc_index(graph: Graph) -> Dict[Any, float]:
    """Single-pass ``{file_ref: sum_nloc}`` map. Mirrors the helper at
    :func:`src.enrichment.metrics.implementations.anomaly_complexity._file_metric_index`
    but inlined here so the server doesn't reach into a metric implementation.
    """
    file_metrics = getattr(graph, "file_metrics", None)
    if file_metrics is None:
        return {}
    by_name = getattr(file_metrics, "by_name", None)
    if by_name is not None:
        rows = by_name["sum_nloc"]
    else:
        try:
            rows = [m for m in file_metrics if getattr(m, "metric_name", None) == "sum_nloc"]
        except TypeError:
            return {}
    out: Dict[Any, float] = {}
    for row in rows:
        fref = getattr(row, "file_ref", None)
        val = getattr(row, "value", None)
        if fref is None or val is None:
            continue
        out[fref] = float(val)
    return out


def _file_component_name_index(graph: Graph) -> Dict[Any, str]:
    """Return ``{file_ref: component_name}`` for every component in the graph.

    Walks ``graph.components.all()`` once and fans each ``file_refs`` entry
    into the output map. When a file resolves to multiple components (rare;
    the resolver is single-assignment by design) the last component wins —
    consistent with :class:`ComponentRegistry`'s ``by_file`` multi-index.
    """
    out: Dict[Any, str] = {}
    for component in graph.components.all():
        for ref in component.file_refs:
            out[ref] = component.name
    return out


@app.get("/projects/{project_id}/components")
async def get_project_components(project_id: str):
    """Return one row per :class:`Component` in the loaded graph.

    Response shape::

        [
          {
            "name": "<component_name>",
            "path_prefix": "<resolver prefix>",
            "file_count": <int>,
            "total_loc": <float>,
            "color": null,
          },
          ...
        ]

    ``total_loc`` is the sum of Lizard ``sum_nloc`` across the component's
    files; files without a Lizard row contribute 0. ``color`` is reserved for
    a future server-side palette; v1 leaves it null and the web-UI computes
    a stable palette client-side from the component name.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    excluded_files = filter_rule_store.excluded_ids_for(project_id).get(
        EntityKind.FILE, set()
    )
    sum_nloc_by_file = _sum_nloc_index(graph)
    rows = []
    for component in graph.components.all():
        kept_refs = [
            r for r in component.file_refs
            if not (r.kind is EntityKind.FILE and r.id in excluded_files)
        ]
        if not kept_refs:
            continue
        rows.append({
            "name": component.name,
            "path_prefix": component.path_prefix,
            "file_count": len(kept_refs),
            "total_loc": _component_total_loc(kept_refs, sum_nloc_by_file),
            "color": None,
        })
    return rows


@app.get("/projects/{project_id}/components/files")
async def get_project_component_files(project_id: str):
    """Return a flat ``{path, loc, component_name, owner?, status?}`` per file.

    The web-UI renders a single LOC-proportional treemap over every file in
    the project at once, so we walk ``graph.files`` (not just the files
    referenced by a component) and tag each entry with its resolved component
    name when one exists.

    ``loc`` reads ``graph.file_metrics`` where ``metric_name == "sum_nloc"``
    (declared at ``src/common/domains/metrics_lizard/models.py:164``). Files
    without a Lizard row report ``loc=null`` — the treemap layer treats
    ``null`` and ``0`` identically (drops them below the min-size threshold).

    ``owner`` and ``status`` are v1-optional and currently omitted; the
    underlying ownership / file-status data isn't trivially derivable in a
    single pass and the web-UI tolerates absent fields. They'll be wired in
    a follow-up once an authoritative source is picked.
    """
    graph = _require_loaded_graph(project_id)
    if isinstance(graph, JSONResponse):
        return graph

    excluded_files = filter_rule_store.excluded_ids_for(project_id).get(
        EntityKind.FILE, set()
    )
    sum_nloc_by_file = _sum_nloc_index(graph)
    component_by_file = _file_component_name_index(graph)

    rows = []
    for file_ in graph.files.all():
        if file_.id in excluded_files:
            continue
        ref = file_.ref()
        rows.append({
            "path": file_.path,
            "loc": sum_nloc_by_file.get(ref),
            "component_name": component_by_file.get(ref),
        })
    return rows


@app.put("/projects/{project_id}/component-mapping")
async def update_component_mapping(project_id: str, request: Request):
    """Persist a curated component mapping JSON, then trigger a rebuild.

    Body shape mirrors the resolver's :class:`ComponentMapping`::

        {
          "<component_name>": {
            "path_prefix": "src/foo/",
            "extra_paths": ["lib/foo-helpers/"]
          },
          ...
        }

    A ``null`` body (or an empty object ``{}``) clears the curated mapping:
    we write SQL ``NULL`` to ``projects.component_mapping`` so the resolver
    falls back to the top-folder heuristic on the next build. This matches the
    "empty = no curated mapping" comment on
    :attr:`EnrichmentConfig.components_mapping_data`.

    Validation runs through :func:`parse_component_mapping` BEFORE the write.
    A 400 is returned when the payload is structurally malformed
    (non-object root, no valid component entries from a non-empty input).
    The parser is intentionally lenient about per-entry shape mismatches
    (drops bad entries) but a payload that boils down to zero valid entries
    after parsing is rejected so the operator notices.

    Flow:

    1. Parse the body → reject 400 on bad shape.
    2. Write to Supabase. On failure, return 500 with no rebuild attempt
       (the column stays at whatever it was — the user can retry).
    3. Trigger a rebuild via :func:`v2_processor.build_graph`. The B2 fetch
       picks the new mapping up automatically (Supabase is the source of
       truth). Rebuild failure returns 500 but the mapping is already
       persisted — the user can retry the build manually.
    """
    # ----- 1. parse + validate the body -----
    try:
        raw_body = await request.body()
        payload = await request.json() if raw_body else None
    except Exception as exc:  # noqa: BLE001 — bad JSON → 400, not 500
        return JSONResponse(
            {"error": f"Invalid JSON body: {exc}"},
            status_code=400,
        )

    # null / {} both mean "clear the mapping": persist SQL NULL so the
    # resolver re-engages the heuristic on next build.
    clearing = payload is None or (isinstance(payload, dict) and not payload)
    if not clearing:
        if not isinstance(payload, dict):
            return JSONResponse(
                {"error": (
                    "component-mapping body must be a JSON object or null; "
                    f"got {type(payload).__name__}"
                )},
                status_code=400,
            )
        parsed = parse_component_mapping(payload)
        if parsed.is_empty():
            # A non-empty input that produces zero valid components means every
            # entry was malformed (missing path_prefix, wrong types, etc).
            # The lenient parser drops them silently; we surface the error here.
            return JSONResponse(
                {"error": (
                    "component-mapping body contained no valid component entries. "
                    "Each entry must be an object with a string 'path_prefix'."
                )},
                status_code=400,
            )
        mapping_to_persist: Optional[Dict[str, Any]] = payload
    else:
        mapping_to_persist = None  # SQL NULL

    # ----- 2. persist to the database -----
    import json as _json

    mapping_json = (
        _json.dumps(mapping_to_persist) if mapping_to_persist is not None else None
    )

    def _persist_mapping() -> None:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update projects set component_mapping = %s where id = %s",
                    (mapping_json, project_id),
                )

    try:
        await asyncio.to_thread(_persist_mapping)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"Failed to persist component_mapping: {exc}", exc_info=True)
        return JSONResponse(
            {"error": f"Failed to persist component mapping: {exc}"},
            status_code=500,
        )

    # ----- 3. trigger rebuild -----
    # Match /build's choice: synchronous, off the request thread via
    # asyncio.to_thread (the build path is CPU-bound and reads disk).
    try:
        graph, _pipeline_result = await asyncio.to_thread(
            v2_processor.build_graph, project_id
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("build_graph failed after component_mapping update")
        return JSONResponse(
            {
                "error": (
                    f"Component mapping was saved but rebuild failed: {exc}. "
                    "Re-trigger Build Graph from the UI to apply the new mapping."
                ),
                "mapping_persisted": True,
            },
            status_code=500,
        )

    graph_store.set(project_id, graph)
    smart_merge_state_store.reset(project_id)

    return {
        "ok": True,
        "cleared": clearing,
        "component_count": len(graph.components),
    }


# =============================================================================
# Enrichment endpoints (legacy Enrichments blob removed in Chunk 10).
# The v2 typed Graph exposes graph.traits / graph.classifiers /
# graph.relations directly; agents should query them via the MCP sandbox
# (see src/sandbox/inject.py). Per-source summary endpoints (lizard,
# code-structure, duplication, quality-issues) were also dropped — the same
# data is available on the typed registries on graph_store.get(project_id).
# =============================================================================


@app.post("/execute")
async def execute_code(request: CodeRequest):
    """Execute arbitrary Python code against the loaded project graph.

    **Available variables:**
    - `graph_data` — sandbox view wrapping the loaded typed Graph.
      :class:`SetupSandboxView` when ``graph.merge_state`` is
      ``PRE_MERGE`` (narrow setup-stage surface — per-source registries,
      primary entity surfaces, per-domain summaries; no unified_users
      aggregates, no enrichment helpers); :class:`QuerySandboxView`
      when ``FINALIZED`` (full query-stage surface).

    **Example code:**
    ```python
    commits = graph_data.commits.all()
    print(f'Total commits: {len(commits)}')

    for commit in commits[:5]:
        print(f'{commit.id[:8]} - {commit.message[:50]}')
    ```
    """
    code = request.code
    stdout = io.StringIO()

    try:
        sys_stdout = sys.stdout
        sys.stdout = stdout

        # Pick the sandbox-view class from the project's lifecycle
        # state. P5.A: SetupSandboxView (PRE_MERGE) hides post-finalize
        # attrs (unified_users, traits, classifiers, relations,
        # components, file_metrics, overviews, people-aware helpers);
        # QuerySandboxView (FINALIZED) exposes the full v2 surface.
        # Filter rules apply at the query stage only (see
        # ``src/filter_rules/views.py``), so the filtered wrapper is
        # layered on top of the Query view only.
        v2_graph: Optional[Graph] = (
            graph_store.get(current_project_id) if current_project_id else None
        )
        if v2_graph is None:
            raw_view: Any = None
            filtered_view: Any = None
        else:
            view_cls = (
                SetupSandboxView
                if v2_graph.merge_state == MergeState.PRE_MERGE
                else QuerySandboxView
            )
            raw_view = view_cls(v2_graph)
            if view_cls is QuerySandboxView and current_project_id:
                excluded = filter_rule_store.excluded_ids_for(current_project_id)
                filtered_view = (
                    FilteredSandboxView(v2_graph, excluded) if excluded else raw_view
                )
            else:
                filtered_view = raw_view
        # ``graph_data_full`` is the documented unfiltered escape hatch for
        # "across the entire history" prompts. The bare ``graph`` global is
        # the lower-level raw Graph — a deliberate third hatch for power
        # users who want to bypass the sandbox-view surface entirely.
        # The enrichment-aware helpers (``find_files_with_trait`` etc.)
        # live on QuerySandboxView only — on a SetupSandboxView the
        # ``getattr(..., None)`` falls through to a no-op binding.
        exec_globals = {
            "graph_data": filtered_view,
            "graph_data_full": raw_view,
            "graph": v2_graph,
            "commit_issues": _sandbox_commit_issues,
            "issue_commits": _sandbox_issue_commits,
            "pr_commits": _sandbox_pr_commits,
            # Bind the filtered view's per-method helpers as top-level
            # callables so legacy snippets calling ``find_files_with_trait("x")``
            # without going through ``graph_data.`` still inherit filtering.
            "find_files_with_trait": (
                getattr(filtered_view, "find_files_with_trait", None)
                or (lambda *_a, **_k: [])
            ),
            "cochange_neighbors": (
                getattr(filtered_view, "cochange_neighbors", None)
                or (lambda *_a, **_k: [])
            ),
            "overview_as_dict": (
                getattr(filtered_view, "overview_as_dict", None)
                or (lambda *_a, **_k: None)
            ),
        }
        exec(code, exec_globals)

        output = stdout.getvalue()
        return JSONResponse({"output": output})
    except Exception:
        tb = traceback.format_exc()
        return JSONResponse({"error": tb}, status_code=400)
    finally:
        sys.stdout = sys_stdout


@app.post("/plot")
async def generate_plot(request: CodeRequest):
    """Execute Python code that generates a matplotlib plot and return
    it as a JPEG image.

    **Available variables:**
    - `graph_data` — sandbox view wrapping the loaded typed Graph.
      :class:`SetupSandboxView` for PRE_MERGE projects (narrow
      setup-stage surface), :class:`QuerySandboxView` for FINALIZED
      projects (full query-stage surface). Same selection rule as
      ``/execute``.
    - `plt` — matplotlib.pyplot module
    """
    code = request.code
    stdout = io.StringIO()

    try:
        sys_stdout = sys.stdout
        sys.stdout = stdout

        # State-driven view selection — mirrors /execute. See that
        # endpoint for the rationale.
        v2_graph: Optional[Graph] = (
            graph_store.get(current_project_id) if current_project_id else None
        )
        if v2_graph is None:
            raw_view: Any = None
            filtered_view: Any = None
        else:
            view_cls = (
                SetupSandboxView
                if v2_graph.merge_state == MergeState.PRE_MERGE
                else QuerySandboxView
            )
            raw_view = view_cls(v2_graph)
            if view_cls is QuerySandboxView and current_project_id:
                excluded = filter_rule_store.excluded_ids_for(current_project_id)
                filtered_view = (
                    FilteredSandboxView(v2_graph, excluded) if excluded else raw_view
                )
            else:
                filtered_view = raw_view
        # See /execute for the three-hatch rationale (graph_data / graph_data_full / graph).
        exec_globals = {
            "graph_data": filtered_view,
            "graph_data_full": raw_view,
            "graph": v2_graph,
            "plt": plt,
            "commit_issues": _sandbox_commit_issues,
            "issue_commits": _sandbox_issue_commits,
            "pr_commits": _sandbox_pr_commits,
            "find_files_with_trait": (
                filtered_view.find_files_with_trait if filtered_view else lambda *_a, **_k: []
            ),
            "cochange_neighbors": (
                filtered_view.cochange_neighbors if filtered_view else lambda *_a, **_k: []
            ),
            "overview_as_dict": (
                filtered_view.overview_as_dict if filtered_view else lambda *_a, **_k: None
            ),
        }

        exec(code, exec_globals)

        fig = plt.gcf()
        img_bytes = io.BytesIO()
        fig.savefig(img_bytes, format="jpg", bbox_inches="tight")
        img_bytes.seek(0)
        plt.close(fig)

        return Response(content=img_bytes.getvalue(), media_type="image/jpeg")

    except Exception:
        tb = traceback.format_exc()
        return JSONResponse({"error": tb}, status_code=400)

    finally:
        sys.stdout = sys_stdout


# =============================================================================
# Static SPA (single-origin packaging)
# =============================================================================
#
# When STATIC_DIR points at the built Angular bundle (dist/web-ui/browser), the
# data-server serves the SPA at "/" so the whole system lives behind ONE origin
# (one `docker compose up`). The web-ui calls the API with relative URLs
# (environment.dataServerUrl === ''), so there is no CORS/origin split.
#
# This is registered LAST, after every API route above, because the catch-all
# SPA fallback would otherwise shadow them. Real API routes are declared above
# and match first; anything else either maps to a concrete file in the bundle
# (hashed JS chunks, favicon, etc.) or falls back to index.html so Angular's
# client-side router owns deep links.
from src.config import STATIC_DIR as _STATIC_DIR  # noqa: E402

if _STATIC_DIR:
    from fastapi.responses import FileResponse  # noqa: E402

    _static_root = Path(_STATIC_DIR)
    _index_file = _static_root / "index.html"

    if _index_file.is_file():

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            """Serve a built static file if it exists, else index.html."""
            candidate = (_static_root / full_path).resolve()
            try:
                candidate.relative_to(_static_root.resolve())
            except ValueError:
                # Path-traversal attempt — serve the shell instead.
                return FileResponse(_index_file)
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(_index_file)

        logger.info("Serving static SPA from %s", _static_root)
    else:
        logger.warning(
            "STATIC_DIR=%s set but %s missing — SPA not served",
            _STATIC_DIR,
            _index_file,
        )
