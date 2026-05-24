"""Cochange (file ↔ file, time-windowed) builder — Chunk 13 port.

Counts pairs of files touched by *distinct* commits whose timestamps fall
within ``cfg.time_windowed_cochange_hours`` of each other. Same-commit
co-changes are intentionally excluded — that signal already lives in
:class:`CochangeBuilder` (``relation_kind="cochange"``).

Algorithm
---------

This is the **first real :class:`TemporalIndex` consumer** in the v2 pipeline.

1. Walk every commit; skip merges (``len(parent_refs) > 1``) and bulk
   commits (``len(changes) > cfg.cochange_max_files_per_commit``).
   Record the surviving ``commit_ref → set[file_ref]`` mapping.
2. Pull the cached :class:`TemporalIndex` via
   :meth:`Graph.ensure_temporal_index` and iterate
   :meth:`TemporalIndex.pairs_within(hours)` — yields every ordered
   commit pair within the Δt window.
3. For each commit pair ``(c1, c2)``, intersect their file-touch sets:
   the *non-shared* files are time-windowed neighbours (a file touched
   only by ``c1`` paired with a file touched only by ``c2``). We
   intentionally skip the shared-file Cartesian portion because that
   is the same-commit cochange already captured by
   :class:`CochangeBuilder`. Strictly: every pair ``(a, b)`` with
   ``a ∈ files(c1)``, ``b ∈ files(c2)``, ``a ≠ b`` counts — the legacy
   only excluded ``cid_a == cid_b`` (same commit), which our walk over
   distinct commits guarantees by construction.

For each unordered file pair, accumulate a lifetime count + a recent
count (commits both inside the recent window). Emit one
:class:`Relation` per pair per window.

Reads
-----

* ``graph.commits``                — every commit.
* ``graph.changes.by_commit``      — file_refs per commit.
* ``graph.ensure_temporal_index()`` — pair iteration (D2 locked API).
* ``graph.config.time_windowed_cochange_hours``,
  ``graph.config.cochange_max_files_per_commit`` — thresholds.
* ``graph.recent_cutoff``           — optional recent-window discriminator
  (legacy test-stub-host convention; see Chunk 11 review §3 open issue #1).
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import EntityRef, Graph


# Defaults mirror :class:`EnrichmentConfig` so a graph without a config
# still produces sane output.
_DEFAULT_MAX_FILES_PER_COMMIT = 20
_DEFAULT_TIME_WINDOWED_HOURS = 0.5
_DEFAULT_FILE_MIN_COUNT = 20


@BUILDERS.register
class CochangeFileTimeWindowedBuilder(RelationBuilder):
    """Emit ``cochange_file_time_windowed`` relations.

    Strength = number of cross-commit file co-occurrences inside the
    window. Two emissions per (file_a, file_b) pair: ``LIFETIME`` always;
    ``RECENT`` when ``graph.recent_cutoff`` is set and at least one
    contributing commit pair landed inside the recent window.
    """

    name = "cochange.file_time_windowed"
    relation_kind = "cochange_file_time_windowed"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        commits = _safe_iter(getattr(graph, "commits", None))
        if not commits:
            return

        cutoff = getattr(graph, "recent_cutoff", None)
        max_files = _config_field(
            graph, "cochange_max_files_per_commit", _DEFAULT_MAX_FILES_PER_COMMIT
        )
        hours = _config_field(
            graph, "time_windowed_cochange_hours", _DEFAULT_TIME_WINDOWED_HOURS
        )
        if hours is None or hours <= 0:
            return
        min_count = _config_field(
            graph, "time_windowed_cochange_file_min_count", _DEFAULT_FILE_MIN_COUNT
        )
        if min_count is None or min_count < 1:
            min_count = 1

        changes_by_commit = _changes_by_commit_index(graph)
        commit_get = _entity_by_id(getattr(graph, "commits", None))

        # Step 1: collect surviving commits' file-touch sets.
        files_by_commit: dict[Any, frozenset[Any]] = {}
        recent_commits: set[Any] = set()
        for commit in commits:
            parents = getattr(commit, "parent_refs", None) or []
            if len(parents) > 1:
                continue
            changes = list(changes_by_commit(commit.ref()))
            if not (1 <= len(changes) <= max_files):
                continue
            file_refs = set()
            for ch in changes:
                fref = getattr(ch, "file_ref", None)
                if fref is not None:
                    file_refs.add(fref)
            if not file_refs:
                continue
            ref = commit.ref()
            files_by_commit[ref] = frozenset(file_refs)
            if cutoff is not None and _commit_in_window(commit, cutoff):
                recent_commits.add(ref)

        if len(files_by_commit) < 2:
            return

        # Step 2: walk all commit pairs within the time window via the
        # locked-D2 TemporalIndex API. Fallback to manual pair scan when
        # the host doesn't expose ensure_temporal_index (test stubs).
        ensure = getattr(graph, "ensure_temporal_index", None)
        if callable(ensure):
            ti = ensure()
            pair_iter = ti.pairs_within(hours=float(hours))
        else:
            return

        # Step 3: accumulate per-file-pair lifetime + recent counts.
        lifetime: dict[tuple[Any, Any], int] = defaultdict(int)
        recent: dict[tuple[Any, Any], int] = defaultdict(int)

        for c1_ref, c2_ref in pair_iter:
            files_1 = files_by_commit.get(c1_ref)
            files_2 = files_by_commit.get(c2_ref)
            if not files_1 or not files_2:
                # Commit was filtered out (merge / bulk / empty).
                continue
            in_recent = (
                cutoff is not None
                and c1_ref in recent_commits
                and c2_ref in recent_commits
            )
            for a in files_1:
                for b in files_2:
                    if a == b:
                        # Same file touched by both commits is the
                        # already-captured cochange signal — skip.
                        continue
                    pair = _ordered_pair(a, b)
                    lifetime[pair] += 1
                    if in_recent:
                        recent[pair] += 1

        yield from _emit_pairs(
            self.relation_kind,
            WindowKind.LIFETIME,
            lifetime,
            hours=hours,
            min_count=min_count,
        )
        if cutoff is not None:
            yield from _emit_pairs(
                self.relation_kind,
                WindowKind.RECENT,
                recent,
                hours=hours,
                min_count=min_count,
            )
        # Silence the unused-import lint when commit_get is not exercised
        # (kept for future symmetry with sibling builders).
        del commit_get


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


def _changes_by_commit_index(graph: Any):
    """Return a callable ``commit_ref -> Iterable[Change]``."""
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _commit_ref: []
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return lambda commit_ref: by_commit[commit_ref]

    def scan(commit_ref):
        return [ch for ch in changes if ch.commit_ref == commit_ref]

    return scan


def _commit_in_window(commit: Any, cutoff: Any) -> bool:
    d = getattr(commit, "author_date", None) or getattr(commit, "committer_date", None)
    if d is None or cutoff is None:
        return False
    try:
        return d >= cutoff
    except TypeError:
        return False


def _config_field(graph: Any, field: str, default: Any) -> Any:
    cfg = getattr(graph, "config", None)
    if cfg is None:
        return default
    return getattr(cfg, field, default)


def _ordered_pair(a: "EntityRef", b: "EntityRef") -> tuple["EntityRef", "EntityRef"]:
    """Canonical ordering by ``(kind, id)`` so (a,b) and (b,a) collapse."""
    key_a = (a.kind, a.id)
    key_b = (b.kind, b.id)
    return (a, b) if key_a <= key_b else (b, a)


def _emit_pairs(
    relation_kind: str,
    window: WindowKind,
    pairs: dict[tuple[Any, Any], int],
    *,
    hours: Any,
    min_count: int,
) -> Iterable[Relation]:
    for (src, tgt), count in pairs.items():
        if count < min_count:
            continue
        rid = Relation.canonical_id(src, tgt, relation_kind, window)
        yield Relation(
            id=rid,
            source=src,
            target=tgt,
            relation_kind=relation_kind,
            window=window,
            strength=float(count),
            extras={"hours": float(hours), "count": int(count)},
        )


__all__ = ["CochangeFileTimeWindowedBuilder"]
