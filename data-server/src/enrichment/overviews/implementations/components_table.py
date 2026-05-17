"""Components overview — v2 port (Chunk 18).

Port of legacy ``src/enrichment/overview/components_table.py``. Per
"component" rollup of activity + ownership + quality signals.

The v2 :class:`ComponentResolverMetric` (Chunk 7) emits
``component_membership`` :class:`Relation` rows rather than
:class:`Component` entities directly; this overview reconstructs the
"which files belong to which component?" view from the relation set.
When the :class:`ComponentRegistry` is also populated (future hook),
its names take precedence — the relation-derived view is the
fallback.

Reads:

* ``graph.files`` + ``graph.changes`` + ``graph.hunks`` — file
  population + per-author churn (mirrors :class:`OwnershipBuilder`).
* ``graph.relations.of_kind("component_membership")`` — file ↔
  component membership (Chunk-14 ``ComponentResolverMetric`` output).
* ``graph.components`` (when non-empty) — explicit
  :class:`Component` entities, used to pin row ordering.
* ``graph.classifiers`` (dimension ``message.nature``) — for the
  bugfix-ratio column.
* ``graph.traits``  (names ``anomaly.knowledge.BusFactor1`` /
  ``anomaly.testing.BugMagnet``) — per-file count columns.
* ``graph.file_metrics`` (Lizard) — per-file ``sum_nloc`` / ``max_ccn``
  for the LOC + complexity columns.

Columns:

  file_count, total_churn, commit_count, distinct_authors,
  bugfix_ratio, bus_factor_1_files, bugmagnet_files, total_loc,
  avg_loc_per_file, max_ccn.

Rows: synthetic ``(project)`` aggregate + one per resolved component.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, ClassVar, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.overviews.models import (
    OverviewCell,
    OverviewRow,
    OverviewTable,
    OverviewTableBuilder,
)
from src.enrichment.overviews.registries import OVERVIEWS
from src.enrichment.recent_window import ensure_aware, trend_percent

if TYPE_CHECKING:
    from src.common.kernel import Graph


COLUMNS: list[str] = [
    "file_count",
    "total_churn",
    "commit_count",
    "distinct_authors",
    "bugfix_ratio",
    "bus_factor_1_files",
    "bugmagnet_files",
    "total_loc",
    "avg_loc_per_file",
    "max_ccn",
]

_BUSFACTOR_TRAIT = "anomaly.knowledge.BusFactor1"
_BUGMAGNET_TRAIT = "anomaly.testing.BugMagnet"
_NATURE_DIM = "message.nature"


@OVERVIEWS.register
class ComponentsTableBuilder(OverviewTableBuilder):
    """One row per component + a synthetic ``(project)`` aggregate."""

    name: ClassVar[str] = "components"

    def build(self, graph: "Graph", config: Any) -> OverviewTable:
        files_reg = getattr(graph, "files", None)
        if files_reg is None:
            return OverviewTable(
                name=self.name, entity_kind="component",
                columns=COLUMNS, rows=[],
            )
        try:
            files = list(files_reg)
        except TypeError:
            files = []

        cutoff = _resolve_recent_cutoff(graph)

        # Membership: file_id -> component_name (single membership per
        # file, mirroring the legacy ``resolver.resolve`` contract).
        component_of_file = _component_of_file(graph)

        # Order components: explicit :class:`Component` entities first,
        # then any membership-only names alphabetical.
        ordered_components = _ordered_component_names(graph, component_of_file)

        files_by_component: dict[str, list[Any]] = defaultdict(list)
        for f in files:
            comp = component_of_file.get(f.id)
            if comp is None:
                continue
            files_by_component[comp].append(f)

        nature_by_commit_id = _classifier_by_target_id(
            graph, _NATURE_DIM, EntityKind.COMMIT,
        )
        busfactor_file_ids = _file_ids_with_trait(graph, _BUSFACTOR_TRAIT)
        bugmagnet_file_ids = _file_ids_with_trait(graph, _BUGMAGNET_TRAIT)
        nloc_by_file_id, max_ccn_by_file_id = _lizard_indexes(graph)

        changes_by_file = _changes_by_file_index(graph)
        commits_get = _entity_by_id(getattr(graph, "commits", None))
        hunks_by_change = _hunks_by_change_index(graph)

        rows: list[OverviewRow] = [
            _row_for(
                "(project)", files, cutoff,
                nature_by_commit_id, busfactor_file_ids, bugmagnet_file_ids,
                nloc_by_file_id, max_ccn_by_file_id,
                changes_by_file, commits_get, hunks_by_change,
            )
        ]
        for name in ordered_components:
            rows.append(
                _row_for(
                    name,
                    files_by_component.get(name, []),
                    cutoff,
                    nature_by_commit_id, busfactor_file_ids, bugmagnet_file_ids,
                    nloc_by_file_id, max_ccn_by_file_id,
                    changes_by_file, commits_get, hunks_by_change,
                )
            )

        return OverviewTable(
            name=self.name,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )


# ----------------------------------------------------------------------
# Per-row aggregation
# ----------------------------------------------------------------------
def _row_for(
    entity_id: str,
    files: list[Any],
    cutoff: Optional[Any],
    nature_by_commit_id: dict[str, str],
    busfactor_file_ids: set[str],
    bugmagnet_file_ids: set[str],
    nloc_by_file_id: dict[str, float],
    max_ccn_by_file_id: dict[str, float],
    changes_by_file,
    commits_get,
    hunks_by_change,
) -> OverviewRow:
    lifetime_churn = 0
    recent_churn = 0
    lifetime_commits: set[str] = set()
    recent_commits: set[str] = set()
    lifetime_authors: set[str] = set()
    recent_authors: set[str] = set()
    lifetime_bugfix: set[str] = set()
    recent_bugfix: set[str] = set()
    bf1 = 0
    bm = 0
    loc_total = 0.0
    files_with_loc = 0
    max_ccn_seen = 0.0

    for f in files:
        if f.id in busfactor_file_ids:
            bf1 += 1
        if f.id in bugmagnet_file_ids:
            bm += 1
        nloc = nloc_by_file_id.get(f.id)
        if nloc is not None:
            loc_total += nloc
            files_with_loc += 1
        ccn = max_ccn_by_file_id.get(f.id)
        if ccn is not None and ccn > max_ccn_seen:
            max_ccn_seen = ccn

        for change in changes_by_file(f.ref()):
            commit_ref = getattr(change, "commit_ref", None)
            if commit_ref is None:
                continue
            commit = commits_get(commit_ref.id)
            if commit is None:
                continue
            cid = commit.id
            churn = _change_churn(change, hunks_by_change)
            lifetime_churn += churn
            lifetime_commits.add(cid)
            author_ref = getattr(commit, "author_ref", None)
            if author_ref is not None:
                lifetime_authors.add(author_ref.id)
            in_recent = cutoff is not None and _commit_in_window(commit, cutoff)
            if in_recent:
                recent_churn += churn
                recent_commits.add(cid)
                if author_ref is not None:
                    recent_authors.add(author_ref.id)
            if nature_by_commit_id.get(cid) == "bugfix":
                lifetime_bugfix.add(cid)
                if in_recent:
                    recent_bugfix.add(cid)

    cells: dict[str, OverviewCell] = {}
    cells["file_count"] = OverviewCell(
        lifetime_value=len(files),
        recent_value=len(files),
        trend_percent=None,
    )
    cells["total_churn"] = _rate_cell(lifetime_churn, recent_churn)
    cells["commit_count"] = _rate_cell(
        len(lifetime_commits), len(recent_commits),
    )
    cells["distinct_authors"] = OverviewCell(
        lifetime_value=len(lifetime_authors),
        recent_value=len(recent_authors),
        trend_percent=None,
    )
    lt_ratio = _safe_ratio(len(lifetime_bugfix), len(lifetime_commits))
    rc_ratio = _safe_ratio(len(recent_bugfix), len(recent_commits))
    cells["bugfix_ratio"] = OverviewCell(
        lifetime_value=lt_ratio,
        recent_value=rc_ratio,
        trend_percent=trend_percent(lt_ratio, rc_ratio),
    )
    cells["bus_factor_1_files"] = OverviewCell(
        lifetime_value=bf1, recent_value=bf1, trend_percent=None,
    )
    cells["bugmagnet_files"] = OverviewCell(
        lifetime_value=bm, recent_value=bm, trend_percent=None,
    )
    loc_value = round(loc_total, 2) if files_with_loc > 0 else None
    avg_loc = round(loc_total / files_with_loc, 2) if files_with_loc > 0 else None
    max_ccn_value = round(max_ccn_seen, 2) if files_with_loc > 0 else None
    cells["total_loc"] = OverviewCell(
        lifetime_value=loc_value, recent_value=loc_value, trend_percent=None,
    )
    cells["avg_loc_per_file"] = OverviewCell(
        lifetime_value=avg_loc, recent_value=avg_loc, trend_percent=None,
    )
    cells["max_ccn"] = OverviewCell(
        lifetime_value=max_ccn_value, recent_value=max_ccn_value, trend_percent=None,
    )
    return OverviewRow(entity_id=entity_id, cells=cells)


# ----------------------------------------------------------------------
# Membership + component-ordering helpers
# ----------------------------------------------------------------------
def _component_of_file(graph: Any) -> dict[str, str]:
    """``file_id -> component_name`` from ``component_membership`` relations."""
    relations = getattr(graph, "relations", None)
    if relations is None:
        return {}
    of_kind = getattr(relations, "of_kind", None)
    if of_kind is None:
        return {}
    out: dict[str, str] = {}
    for rel in of_kind("component_membership"):
        src = getattr(rel, "source", None)
        tgt = getattr(rel, "target", None)
        if (
            src is None or tgt is None
            or src.kind != EntityKind.FILE
            or tgt.kind != EntityKind.COMPONENT
        ):
            continue
        # First wins — resolver emits one membership per file.
        out.setdefault(src.id, tgt.id)
    return out


def _ordered_component_names(
    graph: Any, component_of_file: dict[str, str],
) -> list[str]:
    """Component names in row order: explicit registry entries first."""
    out: list[str] = []
    seen: set[str] = set()
    components = getattr(graph, "components", None)
    if components is not None:
        try:
            for comp in components:
                name = getattr(comp, "name", None) or getattr(comp, "id", None)
                if not name or name in seen:
                    continue
                seen.add(name)
                out.append(name)
        except TypeError:
            pass
    # Membership-derived names: alphabetical, excluding any already-listed.
    extras = sorted(set(component_of_file.values()) - seen)
    out.extend(extras)
    return out


# ----------------------------------------------------------------------
# Lookup helpers
# ----------------------------------------------------------------------
def _classifier_by_target_id(
    graph: Any, dimension: str, kind: EntityKind,
) -> dict[str, str]:
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None:
        return {}
    of_dimension = getattr(classifiers, "of_dimension", None)
    if of_dimension is None:
        return {}
    out: dict[str, str] = {}
    for cls_obj in of_dimension(dimension):
        target: EntityRef = cls_obj.target
        if target.kind != kind:
            continue
        out[target.id] = cls_obj.value
    return out


def _file_ids_with_trait(graph: Any, trait_name: str) -> set[str]:
    traits = getattr(graph, "traits", None)
    if traits is None:
        return set()
    of_name = getattr(traits, "of_name", None)
    if of_name is None:
        return set()
    out: set[str] = set()
    for t in of_name(trait_name):
        target: EntityRef = t.target
        if target.kind != EntityKind.FILE:
            continue
        out.add(target.id)
    return out


def _lizard_indexes(
    graph: Any,
) -> tuple[dict[str, float], dict[str, float]]:
    """Return ``(sum_nloc_by_file_id, max_ccn_by_file_id)`` from
    :class:`FileMetricRegistry`. Each metric row carries one
    ``(file_ref, metric_name, value)`` triple.
    """
    file_metrics = getattr(graph, "file_metrics", None)
    if file_metrics is None:
        return {}, {}
    try:
        rows = list(file_metrics)
    except TypeError:
        return {}, {}
    nloc: dict[str, float] = {}
    ccn: dict[str, float] = {}
    for row in rows:
        fref = getattr(row, "file_ref", None)
        name = getattr(row, "metric_name", None)
        value = getattr(row, "value", None)
        if fref is None or name is None or value is None:
            continue
        if name == "sum_nloc":
            nloc[fref.id] = float(value)
        elif name == "max_ccn":
            ccn[fref.id] = float(value)
    return nloc, ccn


def _changes_by_file_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _file_ref: []
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]

    def scan(file_ref):
        return [ch for ch in changes if ch.file_ref == file_ref]

    return scan


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


def _hunks_by_change_index(graph: Any):
    hunks = getattr(graph, "hunks", None)
    if hunks is None:
        return lambda _change_ref: []
    by_change = getattr(hunks, "by_change", None)
    if by_change is not None:
        return lambda change_ref: by_change[change_ref]

    def scan(change_ref):
        return [h for h in hunks if h.change_ref == change_ref]

    return scan


def _change_churn(change: Any, hunks_by_change) -> int:
    total = 0
    for hunk in hunks_by_change(change.ref()):
        total += len(getattr(hunk, "added_lines", []) or [])
        total += len(getattr(hunk, "deleted_lines", []) or [])
    return total if total > 0 else 1


def _commit_in_window(commit: Any, cutoff: Any) -> bool:
    d = ensure_aware(
        getattr(commit, "author_date", None)
        or getattr(commit, "committer_date", None)
    )
    if d is None or cutoff is None:
        return False
    try:
        return d >= cutoff
    except TypeError:
        return False


def _resolve_recent_cutoff(graph: Any) -> Optional[Any]:
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    return None


def _safe_ratio(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return round(n / d, 4)


def _rate_cell(lt: int, rc: int) -> OverviewCell:
    return OverviewCell(
        lifetime_value=lt,
        recent_value=rc,
        trend_percent=trend_percent(lt or None, rc or None),
    )


__all__ = ["ComponentsTableBuilder", "COLUMNS"]
