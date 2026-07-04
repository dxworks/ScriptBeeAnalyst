"""Cochange (file ↔ file, shared-task-prefixes) builder — Chunk 13 port.

For each pair of files that co-changed in a single commit, the strength
is ``|prefixes(a) ∩ prefixes(b)|`` where ``prefixes(f)`` is the set of
Jira-style task prefixes (``PROJ`` from ``PROJ-123``) drawn from the
commit messages that touched ``f``.

Why we inline :func:`extract_task_prefixes` instead of reading the
``task_prefix`` classifier surface
-----------------------------------------------------------------

The :class:`CommitTaskPrefixClassifierMetric` (Chunk 11) emits one
:class:`Classifier(dimension="task_prefix")` per distinct prefix per
commit. Relation builders run in **stage 1** of
:func:`run_pipeline`; metrics run in **stage 2**. So when this builder
runs, ``graph.classifiers.with_value("task_prefix", …)`` is empty
within the same pipeline pass — see the Chunk 11 review §3 open issue
#1 and the Chunk 12 handoff §"Open issues" #2.

Per the plan §1 D3 spirit, **inline extraction is the right choice for
stage-1 builders**: the classifier surface (the D3 win) remains the
agent-facing query path for downstream consumers and external MCP
calls. Both share the same :func:`extract_task_prefixes` regex contract
so the prefix set is identical regardless of consumer.

The alternative — promoting the classifier to a stage-0 phase or
running it twice — would add ~30+ LOC of pipeline plumbing for a
one-builder concern. Rejected as out of scope for Chunk 13.

Reads
-----

* ``graph.commits``                — commit messages.
* ``graph.changes.by_commit``      — file refs per commit.
* ``graph.config.cochange_max_files_per_commit`` — bulk-commit filter.
* ``graph.recent_cutoff``          — optional recent-window discriminator.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS
from src.enrichment.utils.task_prefix import extract_task_prefixes

if TYPE_CHECKING:
    from src.common.kernel import EntityRef, Graph


_DEFAULT_MAX_FILES_PER_COMMIT = 20


@BUILDERS.register
class CochangeFileSharedTaskPrefixesBuilder(RelationBuilder):
    """Emit ``cochange_file_shared_task_prefixes`` relations.

    Two emissions per pair: ``LIFETIME`` (prefixes from any contributing
    commit); ``RECENT`` (prefixes restricted to commits inside
    ``graph.recent_cutoff``) when a cutoff is present.
    """

    name = "cochange.file_shared_task_prefixes"
    relation_kind = "cochange_file_shared_task_prefixes"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        commits = _safe_iter(getattr(graph, "commits", None))
        if not commits:
            return

        cutoff = getattr(graph, "recent_cutoff", None)
        max_files = _config_field(
            graph, "cochange_max_files_per_commit", _DEFAULT_MAX_FILES_PER_COMMIT
        )

        changes_by_commit = _changes_by_commit_index(graph)

        # Per-file prefix sets + the set of file pairs that co-changed in
        # a single commit (the legacy emission gate).
        lifetime_prefixes: dict[Any, set[str]] = defaultdict(set)
        recent_prefixes: dict[Any, set[str]] = defaultdict(set)
        lifetime_pairs: set[tuple[Any, Any]] = set()
        recent_pairs: set[tuple[Any, Any]] = set()

        for commit in commits:
            parents = getattr(commit, "parent_refs", None) or []
            if len(parents) > 1:
                continue
            changes = list(changes_by_commit(commit.ref()))
            if not (2 <= len(changes) <= max_files):
                continue

            file_refs: set[Any] = set()
            for ch in changes:
                fref = getattr(ch, "file_ref", None)
                if fref is not None:
                    file_refs.add(fref)
            if len(file_refs) < 2:
                continue

            message = getattr(commit, "message", "") or ""
            prefixes = set(extract_task_prefixes(message))

            in_recent = cutoff is not None and _commit_in_window(commit, cutoff)

            for fref in file_refs:
                if prefixes:
                    lifetime_prefixes[fref].update(prefixes)
                    if in_recent:
                        recent_prefixes[fref].update(prefixes)

            unique = sorted(file_refs, key=lambda r: (r.kind, r.id))
            for a, b in combinations(unique, 2):
                pair = (a, b)
                lifetime_pairs.add(pair)
                if in_recent:
                    recent_pairs.add(pair)

        yield from _emit_pairs(
            self.relation_kind,
            WindowKind.LIFETIME,
            lifetime_pairs,
            lifetime_prefixes,
        )
        if cutoff is not None:
            yield from _emit_pairs(
                self.relation_kind,
                WindowKind.RECENT,
                recent_pairs,
                recent_prefixes,
            )


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


def _changes_by_commit_index(graph: Any):
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


def _emit_pairs(
    relation_kind: str,
    window: WindowKind,
    pairs: set[tuple[Any, Any]],
    prefixes_per_file: dict[Any, set[str]],
) -> Iterable[Relation]:
    for a, b in pairs:
        shared = prefixes_per_file.get(a, set()) & prefixes_per_file.get(b, set())
        if not shared:
            continue
        rid = Relation.canonical_id(a, b, relation_kind, window)
        yield Relation(
            id=rid,
            source=a,
            target=b,
            relation_kind=relation_kind,
            window=window,
            strength=float(len(shared)),
            extras={
                "shared_prefixes": sorted(shared),
                "count": int(len(shared)),
            },
        )


__all__ = ["CochangeFileSharedTaskPrefixesBuilder"]
