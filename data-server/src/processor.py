#!/usr/bin/env python3
"""Graph Processor (v2) — thin dispatcher around per-domain transformers.

Chunk 8 of the architectural refactor. Per plan §9 + §12 steps 6–8:

* Each source ships a :class:`Transformer` (in ``src/common/domains/<x>/``)
  that turns a raw payload into a :class:`TransformResult` (project +
  entities bucketed by :class:`EntityKind`).
* This processor instantiates transformers, runs them, applies each
  result to a single typed :class:`Graph`, then drives the v2 enrichment
  pipeline (``run_pipeline``) over it.
* The result is persisted via :meth:`Graph.dump` into a per-project
  :class:`PickleStore`.

Long-running infrastructure (Supabase polling loop, downloads, status
updates, raw-payload smart-merge endpoints) is preserved verbatim from
the legacy processor — only the build pipeline middle (downloaded files
→ Graph) is rewritten. The legacy ``ProjectLinker`` step is gone; its
semantics now live in v2 :class:`RelationBuilder`\\s (see Chunk-7 handoff
"ProjectLinker → RelationBuilder mapping").

Greenfield contract: the new processor produces v2 graphs only. Old
``graph.pkl`` blobs are not readable by Chunk 8; the user must
re-trigger ``Build Graph`` from the web UI.

Bridge to legacy readers
------------------------

The v2 transformers (Chunks 4–6) accept ONLY pre-built entity bundles
(Mapping form), not raw bytes / DTOs. Building entity bundles from raw
inputs (.iglog, Jira JSON, GitHub JSON, CodeFrame, DuDe, Insider, Lizard
CSV) requires re-porting ~1300 LOC of legacy ``*_miner/linker``
machinery. That port is intentionally **deferred** — Chunk 8 ships:

* The full transformer-dispatch / pipeline-run / graph-dump end-to-end
  path, exercised by ``tests/chunk_08/`` against synthetic bundles
  (the form v2 transformers natively accept).
* A :func:`build_graph_from_bundles` callable that takes a
  ``Dict[SourceKind | str, Mapping]`` of pre-built bundles and returns
  a populated :class:`Graph`.
* :func:`build_graph` orchestrator that wires download → bundles →
  graph, with the legacy → bundles bridge stubbed (``NotImplementedError``
  with a "Chunk 10 cleanup will wire the readers" message). This keeps
  the supabase-polling loop alive — projects in ``status=processing``
  will surface a clear "deferred" error rather than silently fail.

When Chunk 10 deletes the legacy linker stack, the bridge becomes a
small wrapper around each ``*Transformer.transform(<raw>)`` extended
path; the dispatch layer above does not change.
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

# Add parent directory to path if running as script.
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from src.common.domains.app_inspector import AppInspectorTransformer, build_app_inspector_bundle
from src.common.domains.code_structure.bridge import build_code_structure_bundle
from src.common.domains.code_structure.transformer import CodeStructureTransformer
from src.common.domains.duplication.bridge import build_duplication_bundle
from src.common.domains.duplication.transformer import DuplicationTransformer
from src.common.domains.git.bridge import build_git_bundle
from src.common.domains.git.transformer import GitTransformer
from src.common.domains.github.bridge import build_github_bundle
from src.common.domains.github.transformer import GitHubTransformer
from src.common.domains.jira.bridge import build_jira_bundle
from src.common.domains.jira.transformer import JiraTransformer
from src.common.domains.metrics_lizard.bridge import build_lizard_bundle
from src.common.domains.metrics_lizard.transformer import LizardMetricsTransformer
from src.common.domains.quality.bridge import build_quality_bundle
from src.common.domains.quality.transformer import QualityTransformer
from src.common.domains.transformer import Transformer, TransformResult
from src.common.kernel import Graph
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore
from src import progress
from src import storage
from src.config import RECURSION_LIMIT
from src.config_overrides.merge import OverrideCoercionError, apply_overrides
from src.config_overrides.repository import ConfigOverridesRepository
from src.db import connection, query, query_one
from src.enrichment.config import DEFAULT_CONFIG, EnrichmentConfig
from src.enrichment.metrics.implementations.component_resolver import (
    build_components_from_relations,
)
from src.enrichment.pipeline import PipelineResult, run_pipeline_phase_a
# UnifiedUsers redesign §H (task P4.A): /build runs Phase A only —
# Phase B is the /finalize endpoint's job. Running Phase B at build
# would emit relations / classifiers / traits keyed on GIT_ACCOUNT refs;
# /finalize would then re-run Phase B against the rebound graph and emit
# the same items keyed on UNIFIED_USER refs. Because
# ``Relation.canonical_id`` (and the Classifier / Trait id mints)
# include the source / target ``kind`` in the hash, both copies would
# survive in the registries — agent queries post-finalize would see
# duplicate edges keyed on stale per-source accounts and stale
# UU-keyed ones. Skipping Phase B at build avoids the duplication and
# matches the strict §H end-state.
from src.logger import get_logger

logger = get_logger("processor")


# ---------------------------------------------------------------------------
# DownloadedFiles dataclass — preserved from the legacy processor.
# ---------------------------------------------------------------------------
@dataclass
class DownloadedFiles:
    """Per-source paths populated by ``download_serialized_files_from_supabase``.

    Multiple git files are allowed (one per repo); every other source is
    at most one. Field shape preserved verbatim from the legacy processor
    so the supabase-storage download layer didn't have to change.
    """

    git_files: List[Tuple[str, Path]] = field(default_factory=list)
    jira_file: Optional[Path] = None
    github_file: Optional[Path] = None
    lizard_file: Optional[Path] = None
    codeframe_file: Optional[Path] = None
    dude_external_file: Optional[Path] = None
    dude_internal_file: Optional[Path] = None
    quality_issues_file: Optional[Path] = None
    app_inspector_file: Optional[Path] = None


# ---------------------------------------------------------------------------
# Source -> transformer dispatch table.
# ---------------------------------------------------------------------------
#: Map every source kind to its concrete :class:`Transformer` subclass.
#: Code-structure / duplication / quality / lizard each have their own
#: :class:`SourceKind` enum value (added by Chunks 4–6) but are stored
#: alongside git/jira/github here so the dispatcher is one flat lookup.
_TRANSFORMERS: Dict[SourceKind, type[Transformer]] = {
    SourceKind.GIT: GitTransformer,
    SourceKind.JIRA: JiraTransformer,
    SourceKind.GITHUB: GitHubTransformer,
    SourceKind.CODE_STRUCTURE: CodeStructureTransformer,
    SourceKind.DUPLICATION: DuplicationTransformer,
    SourceKind.QUALITY: QualityTransformer,
    SourceKind.LIZARD: LizardMetricsTransformer,
    SourceKind.APP_INSPECTOR: AppInspectorTransformer,
}


def get_transformer(source: SourceKind) -> Transformer:
    """Return the singleton transformer for ``source``.

    Transformers are stateless (per their Chunk-4/5/6 design) so we
    instantiate one per call — cheap and matches the "one transformer
    per build" contract.
    """
    try:
        cls = _TRANSFORMERS[source]
    except KeyError as exc:
        raise ValueError(f"No transformer registered for source {source!r}") from exc
    return cls()


# ---------------------------------------------------------------------------
# Transform-result dispatcher.
# ---------------------------------------------------------------------------
def apply_transform_result(graph: Graph, result: TransformResult) -> None:
    """Route every entity in ``result`` into the matching typed registry.

    Routing rules:

    * ``result.project`` is added via :meth:`Graph.add_project` (dispatched
      by ``isinstance`` because multiple project registries share
      :attr:`EntityKind.PROJECT`).
    * Every other ``(kind, entities)`` bucket is fed to
      :meth:`Graph.registry_for(kind).add(entity)`. Each registry handles
      duplicate ids by ``replace`` per Chunk-1 contract.
    """
    # Add the project first — downstream entities reference it via
    # ``project_ref``, so its presence is a precondition for resolving
    # those refs later.
    graph.add_project(result.project)

    for kind, bucket in result.entities.items():
        registry = graph.registry_for(kind)
        if registry is None:
            # Defensive: TransformResult shouldn't ship buckets keyed on
            # EntityKind.PROJECT (the project goes via ``add_project``)
            # or on any kind that lacks a typed registry. If it does,
            # raise loudly — a silent skip would hide a real bug.
            raise ValueError(
                f"apply_transform_result: no registry for kind {kind!r} "
                f"(bucket size={len(bucket)}). Did a Transformer emit "
                f"an entity bucket keyed on EntityKind.PROJECT?"
            )
        for entity in bucket:
            registry.add(entity)


# ---------------------------------------------------------------------------
# Bundle-driven build (the v2-native entry point used by tests).
# ---------------------------------------------------------------------------
def build_graph_from_bundles(
    project_id: str,
    bundles: Mapping[SourceKind, List[Mapping]],
    *,
    config: Optional[EnrichmentConfig] = None,
    compute_annotated_lines: bool = False,
) -> Tuple[Graph, PipelineResult]:
    """Build a :class:`Graph` from pre-built per-source entity bundles.

    This is the canonical v2-native build path. Each ``bundles[source]``
    is a **list** of bundle Mappings (one per repo / per source file —
    typically a single-element list for non-git sources, but git can
    carry multiple bundles when a project has multiple repos uploaded).
    Each bundle is dispatched through its source's transformer and
    merged into the same typed :class:`Graph`. Per-domain registries
    handle multi-bundle merging naturally because entity ids are
    repo-scoped (see :meth:`git.Commit.make_id` / :meth:`git.File.make_id`).

    Pre-F1 the signature was ``Mapping[SourceKind, Mapping]`` and only
    one git bundle could survive; the dispatcher logged a warning for
    the dropped repos.

    Parameters
    ----------
    project_id
        UUID-ish identifier for the project. Stored on the Graph's
        ``project_id`` field and used as the storage key.
    bundles
        Mapping of :class:`SourceKind` → list of raw bundles. Only
        sources present in the dict have transformers invoked; missing
        sources are skipped (the resulting Graph just has empty
        registries for those domains).
    config
        Optional :class:`EnrichmentConfig` passed through to the
        pipeline. Defaults to :data:`DEFAULT_CONFIG`.
    compute_annotated_lines
        Build-time toggle for the (expensive) git annotated-lines
        reconstruction. When ``True`` it is set on the effective config so
        :class:`GitLineAttributionMetric` emits ``git.loc`` / ``git.repo_size``
        classifiers + the per-file attribution trait (plan §§3-4).
    """
    if config is None:
        config = DEFAULT_CONFIG

    # Flip the annotated-lines toggle on the effective config when requested.
    # ``replace`` keeps the caller's config intact (e.g. test fixtures).
    if compute_annotated_lines:
        config = replace(config, compute_annotated_lines=True)

    graph = Graph(project_id=project_id)

    # Source/bundle loop — the "before enrichments" span. Progress climbs
    # proportionally across the sources present, from 15% (files staged) to
    # 55% (all sources transformed), so the dashboard bar advances as each
    # source's transformer finishes. See ``src.progress``.
    _SRC_LO, _SRC_HI = 15, 55
    source_count = max(1, len(bundles))
    for idx, (source, source_bundles) in enumerate(bundles.items()):
        transformer = get_transformer(source)
        for bundle in source_bundles:
            result = transformer.transform(bundle)
            apply_transform_result(graph, result)
        pct = _SRC_LO + (_SRC_HI - _SRC_LO) * (idx + 1) // source_count
        progress.report(project_id, pct, "transforming")

    # Before-enrichments marker: every source is in the graph; enrichment
    # (Phase A) is the next, heavier span.
    progress.report(project_id, _SRC_HI, "transformed")

    # UnifiedUsers redesign §H — Phase A only at build. Phase B runs in
    # ``POST /projects/{id}/finalize`` after ``rebind_account_refs_to_unified``
    # has flipped role-typed refs to UNIFIED_USER kind. See the module-level
    # import comment for the duplicate-id rationale.
    pipeline_result = run_pipeline_phase_a(graph, config)
    progress.report(project_id, 85, "enriching")
    # Populate graph.components from the component_membership relations
    # emitted by ComponentResolverMetric. Must run AFTER run_pipeline:
    # the Metric ABC's purity contract forbids registry mutations from
    # inside compute(), so the post-pipeline helper owns the write into
    # graph.components.
    build_components_from_relations(graph)
    return graph, pipeline_result


# ---------------------------------------------------------------------------
# Database + on-disk storage infrastructure.
# ---------------------------------------------------------------------------
def download_serialized_files_from_supabase(project_id: str) -> DownloadedFiles:
    """Stage every serialized file for ``project_id`` into a temp dir.

    De-Supabased: the serialized_files rows come from the local Postgres
    database and the bytes are read directly from the on-disk store
    (:data:`src.config.SERIALIZED_FILES_DIR`) rather than a storage bucket.
    The name is kept for call-site compatibility.
    """
    logger.info("Staging serialized files from local on-disk store")

    if not project_id:
        raise ValueError("project_id must be provided")

    rows = query(
        "select * from serialized_files where project_id = %s",
        (project_id,),
    )

    if not rows:
        raise ValueError(f"No serialized files found for project_id: {project_id}")

    downloaded = DownloadedFiles()
    temp_dir = Path("/tmp/processor_downloads")
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_summaries = []
    for file_record in rows:
        file_type = file_record["file_type"]
        storage_path = file_record["storage_path"]
        file_name = file_record["name"]
        repo_name = file_record.get("repo_name")

        try:
            file_bytes = storage.read_bytes(storage_path)
        except Exception as exc:
            raise FileNotFoundError(f"Failed to read {storage_path}: {exc}") from exc

        if file_type == "git":
            safe_repo = repo_name or "git"
            temp_file_path = temp_dir / f"git_{safe_repo}_{project_id}.iglog"
            with open(temp_file_path, "wb") as f:
                f.write(file_bytes)
            downloaded.git_files.append((safe_repo, temp_file_path))
            file_summaries.append(f"git/{safe_repo} ({file_name})")
        else:
            temp_file_path = temp_dir / f"{file_type}_{project_id}{Path(file_name).suffix}"
            with open(temp_file_path, "wb") as f:
                f.write(file_bytes)
            if file_type == "jira":
                downloaded.jira_file = temp_file_path
            elif file_type == "github":
                downloaded.github_file = temp_file_path
            elif file_type == "lizard":
                downloaded.lizard_file = temp_file_path
            elif file_type == "codeframe":
                downloaded.codeframe_file = temp_file_path
            elif file_type == "dude_external":
                downloaded.dude_external_file = temp_file_path
            elif file_type == "dude_internal":
                downloaded.dude_internal_file = temp_file_path
            elif file_type == "quality_issues":
                downloaded.quality_issues_file = temp_file_path
            elif file_type == "app_inspector":
                downloaded.app_inspector_file = temp_file_path
            file_summaries.append(f"{file_type} ({file_name})")

    logger.info("Downloaded: %s", ", ".join(file_summaries))
    return downloaded


def update_project_status(project_id: str, status: str) -> None:
    """Update ``projects.status`` row for ``project_id``."""
    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update projects set status = %s where id = %s",
                    (status, project_id),
                )
    except Exception as exc:
        logger.warning(f"Failed to update project status: {exc}")


def fetch_project_component_mapping(project_id: str) -> Optional[Dict[str, Any]]:
    """Return the per-project ``component_mapping`` JSONB blob or ``None``.

    Reads ``projects.component_mapping`` (added by the B2 migration). Returns
    ``None`` when the row is absent, the column is null, or the database is
    unreachable — the caller falls back to the operator-level
    ``components_mapping_path`` knob (dev/test fallback).
    """
    try:
        row = query_one(
            "select component_mapping from projects where id = %s limit 1",
            (project_id,),
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, fall back on any error
        logger.warning(
            "Failed to fetch component_mapping for project %s: %s", project_id, exc
        )
        return None
    if not row:
        return None
    mapping = row.get("component_mapping")
    if isinstance(mapping, str):
        import json as _json
        try:
            mapping = _json.loads(mapping)
        except ValueError:
            mapping = None
    if isinstance(mapping, dict):
        return mapping
    if mapping is not None:
        # Defensive: the column is JSONB so the driver normally returns
        # dict / None / list. A non-dict, non-null value here means
        # something wrote a malformed payload (e.g. a top-level list or
        # a string). Silently returning None hides the bug from anyone
        # debugging "why did my mapping not stick?".
        logger.warning(
            "component_mapping for project %s is non-null but not a dict "
            "(type=%s) — falling back to heuristic",
            project_id, type(mapping).__name__,
        )
    return None


def get_next_project_to_process() -> Optional[Dict]:
    """Return the oldest ``status='processing'`` project, or None."""
    project = query_one(
        "select * from projects where status = 'processing' "
        "order by updated_at asc limit 1"
    )
    # psycopg returns Postgres ``uuid`` columns as ``uuid.UUID`` objects, but
    # the whole build path was written to the old Supabase/PostgREST contract
    # where ``id`` arrived as a JSON string (``Graph``/``ConfigOverridesRow``
    # declare ``project_id: str``). Coerce at this boundary so downstream code
    # keeps seeing strings.
    if project is not None and project.get("id") is not None:
        project["id"] = str(project["id"])
    return project


# ---------------------------------------------------------------------------
# Pickle storage — per-registry layout via PickleStore.
# ---------------------------------------------------------------------------
def _project_pickle_dir(project_id: str) -> Path:
    """Return the local directory where this project's registries land.

    The directory hosts the per-registry ``.pkl`` + ``meta.json`` shape
    that :class:`PickleStore` writes. Chunk-10 cleanup will revisit the
    Supabase-storage upload layer (legacy uploads a single ``graph.pkl``;
    v2 has many small files).
    """
    return Path(f"/tmp/pickles/{project_id}")


def save_graph_to_disk(graph: Graph) -> Path:
    """Dump ``graph`` into ``/tmp/pickles/<project_id>/`` and return the dir."""
    out_dir = _project_pickle_dir(graph.project_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    store = PickleStore(out_dir)
    graph.dump(store)
    return out_dir


# ---------------------------------------------------------------------------
# Build orchestration.
# ---------------------------------------------------------------------------
def _downloaded_files_to_bundles(
    downloaded: DownloadedFiles, project_name: str = "Project"
) -> Mapping[SourceKind, List[Mapping]]:
    """Translate :class:`DownloadedFiles` into per-source entity bundles.

    Each bridge module under ``src/common/domains/<x>/bridge.py`` exports
    a ``build_<x>_bundle(path, ...) -> Mapping`` callable that parses the
    raw payload and emits the entity-bundle shape its sibling Transformer
    consumes. We invoke them per-source and assemble the dispatch dict.

    Multi-repo (F1): every uploaded ``.iglog`` is processed; the git
    bundle list carries one entry per repo. Entity ids on the git side
    are repo-scoped (:meth:`git.Commit.make_id` / :meth:`git.File.make_id`)
    so multiple repos coexist without collisions in :class:`CommitRegistry`
    / :class:`FileRegistry`. Cross-source SHA joins (GitHub → git) go
    through :class:`CommitRegistry.by_sha` so they remain repo-agnostic.

    Dependent bridges (lizard, code-structure, duplication, quality) each
    take a single ``repo_name`` because their input artefacts (one CSV
    per project, one JSON per project) carry bare file paths with no
    repo discriminator. With multiple iglogs uploaded but only one
    dependent artefact, we anchor the dependent bridge to the first
    repo's name (the one the upstream tools most likely scanned) and
    emit a warning so the operator knows which repo the metrics will
    bind to.

    Each dependent bridge attaches a ``"_meta"`` entry to its bundle
    reporting ``all_rows_self_repo`` — when every parsed row carried
    its own ``repo_name`` no fallback was needed and the warning is
    suppressed for that bundle. The ``"_meta"`` key is popped before
    handing the bundle to its transformer so the strict bundle-key
    validation in :meth:`Transformer.collect_bundle` stays happy.
    """
    bundles: Dict[SourceKind, List[Mapping]] = {}

    # Pick the repo_name dependent bridges (lizard / code-structure /
    # duplication / quality) bind their refs against. Falls back to
    # project_name when no git repo was uploaded.
    if downloaded.git_files:
        repo_name = downloaded.git_files[0][0]
    else:
        repo_name = project_name

    if downloaded.git_files:
        git_bundles: List[Mapping] = []
        for repo, git_path in downloaded.git_files:
            git_bundles.append(build_git_bundle(git_path, repo, project_name))
        bundles[SourceKind.GIT] = git_bundles

    if downloaded.jira_file is not None:
        bundles[SourceKind.JIRA] = [
            build_jira_bundle(downloaded.jira_file, project_name)
        ]

    if downloaded.github_file is not None:
        bundles[SourceKind.GITHUB] = [
            build_github_bundle(downloaded.github_file, project_name)
        ]

    # Track per-source whether the bundle was fully self-described so
    # we can suppress the multi-repo warning when every row of every
    # dependent artefact carried its own ``repo_name``.
    self_repo_by_source: Dict[str, bool] = {}

    def _capture_meta(source_label: str, bundle: Dict[str, Any]) -> Mapping[str, Any]:
        meta = bundle.pop("_meta", None) or {}
        self_repo_by_source[source_label] = bool(meta.get("all_rows_self_repo"))
        return bundle

    if downloaded.lizard_file is not None:
        bundle = dict(
            build_lizard_bundle(downloaded.lizard_file, repo_name, project_name)
        )
        bundles[SourceKind.LIZARD] = [_capture_meta("lizard", bundle)]

    if downloaded.codeframe_file is not None:
        bundle = dict(
            build_code_structure_bundle(
                downloaded.codeframe_file, repo_name, project_name
            )
        )
        bundles[SourceKind.CODE_STRUCTURE] = [_capture_meta("code_structure", bundle)]

    if (
        downloaded.dude_external_file is not None
        or downloaded.dude_internal_file is not None
    ):
        bundle = dict(
            build_duplication_bundle(
                downloaded.dude_external_file,
                downloaded.dude_internal_file,
                repo_name,
                project_name,
            )
        )
        bundles[SourceKind.DUPLICATION] = [_capture_meta("duplication", bundle)]

    if downloaded.quality_issues_file is not None:
        bundle = dict(
            build_quality_bundle(
                downloaded.quality_issues_file, repo_name, project_name
            )
        )
        bundles[SourceKind.QUALITY] = [_capture_meta("quality", bundle)]

    if downloaded.app_inspector_file is not None:
        bundle = dict(
            build_app_inspector_bundle(
                downloaded.app_inspector_file, repo_name, project_name
            )
        )
        bundles[SourceKind.APP_INSPECTOR] = [_capture_meta("app_inspector", bundle)]

    # Multi-repo warning: only fire for bundles that had at least one
    # row fall back to the function-level anchor. Bundles where every
    # row carried its own repo_name resolve correctly without any
    # rebinding so the warning would be a false alarm.
    if len(downloaded.git_files) > 1 and self_repo_by_source:
        falling_back = [src for src, self_ok in self_repo_by_source.items() if not self_ok]
        if falling_back:
            other_repos = [r for r, _ in downloaded.git_files[1:]]
            logger.warning(
                "Multiple git repos uploaded (%s) but %s bridge(s) have "
                "rows without a self-describing repo_name — binding "
                "fallback refs to %r; data from %s will not resolve",
                [r for r, _ in downloaded.git_files],
                falling_back,
                repo_name,
                other_repos,
            )

    return bundles


def build_graph(
    project_id: str,
    project_name: str = "Project",
    *,
    config: Optional[EnrichmentConfig] = None,
    compute_annotated_lines: bool = False,
) -> Tuple[Graph, PipelineResult]:
    """End-to-end build for ``project_id``.

    Steps:

    1. Download serialized files from Supabase Storage.
    2. Translate them into per-source entity bundles (deferred to Chunk
       10 — see :func:`_downloaded_files_to_bundles`).
    3. Build a typed :class:`Graph` via :func:`build_graph_from_bundles`.
    4. Run the v2 enrichment pipeline.
    5. Dump the graph to local pickle storage.

    Returns the (graph, pipeline_result) tuple. The server's ``/build``
    endpoint hands the graph to :data:`graph_store` and surfaces the
    pipeline result to the UI.

    ``compute_annotated_lines`` is the per-build toggle threaded from the
    ``/build`` request body; when ``True`` it is forwarded to
    :func:`build_graph_from_bundles`, which flips the effective config so
    :class:`GitLineAttributionMetric` runs (plan §4).
    """
    progress.report(project_id, 5, "starting")
    downloaded = download_serialized_files_from_supabase(project_id)
    progress.report(project_id, 15, "staging files")
    bundles = _downloaded_files_to_bundles(downloaded, project_name=project_name)
    # Per-project mapping wins over EnrichmentConfig.components_mapping_path.
    # Cloning preserves the caller's config (e.g. test fixtures); we only
    # inject the data when Supabase has something to inject.
    mapping_data = fetch_project_component_mapping(project_id)
    if mapping_data is not None:
        # ``DEFAULT_CONFIG`` is referenced here AND inside
        # ``_apply_project_overrides`` — duplication is intentional. Each
        # branch defends its own concern (component mapping vs. config
        # overrides) so a failure in one does not cascade into the other.
        effective_config = replace(
            config if config is not None else DEFAULT_CONFIG,
            components_mapping_data=mapping_data,
        )
    else:
        effective_config = config
    effective_config = _apply_project_overrides(project_id, effective_config)
    graph, pipeline_result = build_graph_from_bundles(
        project_id,
        bundles,
        config=effective_config,
        compute_annotated_lines=compute_annotated_lines,
    )
    progress.report(project_id, 95, "saving")
    save_graph_to_disk(graph)
    return graph, pipeline_result


def _apply_project_overrides(
    project_id: str, base: Optional[EnrichmentConfig]
) -> Optional[EnrichmentConfig]:
    """Overlay the project's stored config overrides onto ``base``.

    Reads ``ConfigOverridesRepository`` once per ``build_graph`` call.
    On any failure (Supabase down, corrupt JSONB shape) the build path
    must stay alive — log the offending field/project at ERROR and
    return ``base`` unchanged so enrichment runs against defaults.

    The repository's ``get`` already degrades to an empty-overrides row
    when Supabase is unreachable, so the only raise path here is
    :class:`OverrideCoercionError` from :func:`apply_overrides` —
    triggered by hand-edited or schema-drifted JSONB.
    """
    try:
        overrides = ConfigOverridesRepository().get(project_id).overrides
    except Exception:  # noqa: BLE001 — never block the build on Supabase errors
        logger.exception(
            "config_overrides setup failed for project %s — using base config",
            project_id,
        )
        return base
    if not overrides:
        return base
    starting_point = base if base is not None else DEFAULT_CONFIG
    try:
        return apply_overrides(starting_point, overrides)
    except OverrideCoercionError as exc:
        logger.error(
            "config_overrides merge failed for project %s on field %s: %s — "
            "using base config",
            project_id,
            exc.field,
            exc,
        )
        return base


# ---------------------------------------------------------------------------
# Background loop entry point (unchanged shape).
# ---------------------------------------------------------------------------
def process_project(project_id: str, project_name: str = "Project") -> bool:
    """Run the full build for one project; update DB status on the way."""
    try:
        update_project_status(project_id, "processing")
        graph, result = build_graph(project_id, project_name=project_name)
        progress.report(project_id, 100, "ready")
        update_project_status(project_id, "ready")
        logger.info(
            "Built graph for %s — %d builders, %d metrics, %d errors",
            project_id,
            len(result.builders_run),
            len(result.metrics_run),
            len(result.errors),
        )
        return True
    except Exception as exc:
        logger.error("ERROR: Processing failed")
        logger.error(f"{type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        try:
            update_project_status(project_id, "error")
        except Exception:  # noqa: BLE001 — status update is best-effort
            pass
        return False
    finally:
        # Drop the progress entry so the dashboard bar disappears once the
        # build has finished (or failed) — no stuck bars.
        progress.clear(project_id)


def run_loop(poll_interval: int = 60) -> int:
    """Continuously poll Supabase and process projects."""
    logger.info(f"Processor started - Polling database every {poll_interval}s")
    try:
        while True:
            project = get_next_project_to_process()
            if project:
                logger.info(f"Processing: {project['name']} ({project['id']})")
                process_project(project["id"], project["name"])
            else:
                logger.info(f"No projects to process. Waiting {poll_interval}s")
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        logger.info("Stopped by user (Ctrl+C)")
        return 0


def main() -> int:
    sys.setrecursionlimit(RECURSION_LIMIT)
    parser = argparse.ArgumentParser(
        description=(
            "ScriptBeeAssistant Graph Processor (v2) — builds a typed Graph "
            "from per-source transformers + the v2 enrichment pipeline."
        ),
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Polling interval in seconds (default: 60).",
    )
    args = parser.parse_args()
    return run_loop(args.poll_interval)


__all__ = [
    "DownloadedFiles",
    "apply_transform_result",
    "build_graph",
    "build_graph_from_bundles",
    "download_serialized_files_from_supabase",
    "fetch_project_component_mapping",
    "get_transformer",
    "process_project",
    "run_loop",
    "save_graph_to_disk",
    "update_project_status",
]


if __name__ == "__main__":
    sys.exit(main())
