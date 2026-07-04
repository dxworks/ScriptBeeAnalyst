"""Cochange (author ↔ author, shared-task-prefixes) builder — Chunk 14 port.

Strength = number of distinct Jira-style task prefixes both authors have
touched through their commits. Mirrors the legacy
``cochange.author-author.shared-task-prefixes`` extractor.

Algorithm
---------

1. Walk every commit; extract ``extract_task_prefixes(commit.message)``
   inline.
2. Map ``commit.author_ref → set[prefix]``. The N1 trap from Chunk 13 —
   double-counting when commits overlap — manifests differently in the
   author domain: a single author may commit the same prefix from many
   commits. Using a *set* (not a counter) per author intrinsically dedups
   that case so a given prefix contributes at most ``1`` to the
   intersection strength regardless of how many commits the author made
   carrying it. This matches the legacy semantics.
3. For each unordered author pair, intersect prefix sets; emit
   ``strength = |shared prefixes|`` when non-empty.

Why we inline :func:`extract_task_prefixes` instead of reading the
``task_prefix`` classifier surface
-----------------------------------------------------------------

Same pipeline-ordering trap as the file-domain shared-task-prefixes
builder — see Chunk 13 handoff §"Task-prefix pipeline ordering" + the
docstring on
:class:`CochangeFileSharedTaskPrefixesBuilder`. Stage 1 builders fire
before stage 2 metrics, so ``graph.classifiers.with_value("task_prefix",
…)`` is empty within the same pipeline pass. Inline extraction is the
established precedent; both consumers share the same
:func:`extract_task_prefixes` regex contract.

Reads
-----

* ``graph.commits``       — every commit (for ``author_ref`` + ``message``).
* ``graph.recent_cutoff`` — optional recent-window discriminator.
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


@BUILDERS.register
class CochangeAuthorSharedTaskPrefixesBuilder(RelationBuilder):
    """Emit ``cochange_author_shared_task_prefixes`` relations.

    Two emissions per pair: ``LIFETIME`` (prefixes from any commit);
    ``RECENT`` (prefixes restricted to commits inside
    ``graph.recent_cutoff``) when a cutoff is present.
    """

    name = "cochange.author_shared_task_prefixes"
    relation_kind = "cochange_author_shared_task_prefixes"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        commits = _safe_iter(getattr(graph, "commits", None))
        if not commits:
            return

        cutoff = getattr(graph, "recent_cutoff", None)

        # Per-author prefix sets — set semantics naturally dedup repeat
        # prefixes across multiple commits by the same author.
        lifetime_prefixes: dict[Any, set[str]] = defaultdict(set)
        recent_prefixes: dict[Any, set[str]] = defaultdict(set)

        for commit in commits:
            author_ref = getattr(commit, "author_ref", None)
            if author_ref is None:
                continue
            message = getattr(commit, "message", "") or ""
            prefixes = set(extract_task_prefixes(message))
            if not prefixes:
                continue
            lifetime_prefixes[author_ref].update(prefixes)
            if cutoff is not None and _commit_in_window(commit, cutoff):
                recent_prefixes[author_ref].update(prefixes)

        yield from _emit_pairs(
            self.relation_kind, WindowKind.LIFETIME, lifetime_prefixes
        )
        if cutoff is not None:
            yield from _emit_pairs(
                self.relation_kind, WindowKind.RECENT, recent_prefixes
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


def _commit_in_window(commit: Any, cutoff: Any) -> bool:
    d = getattr(commit, "author_date", None) or getattr(commit, "committer_date", None)
    if d is None or cutoff is None:
        return False
    try:
        return d >= cutoff
    except TypeError:
        return False


def _ordered_pair(a: "EntityRef", b: "EntityRef") -> tuple["EntityRef", "EntityRef"]:
    """Canonical ordering by ``(kind, id)`` so (a,b) and (b,a) collapse."""
    key_a = (a.kind, a.id)
    key_b = (b.kind, b.id)
    return (a, b) if key_a <= key_b else (b, a)


def _emit_pairs(
    relation_kind: str,
    window: WindowKind,
    prefixes_per_author: dict[Any, set[str]],
) -> Iterable[Relation]:
    # Sort authors canonically so the pair iteration is deterministic.
    authors = sorted(prefixes_per_author.keys(), key=lambda r: (r.kind, r.id))
    for a, b in combinations(authors, 2):
        shared = prefixes_per_author[a] & prefixes_per_author[b]
        if not shared:
            continue
        # Authors already sorted, so the (a, b) order is already canonical.
        src, tgt = _ordered_pair(a, b)
        rid = Relation.canonical_id(src, tgt, relation_kind, window)
        yield Relation(
            id=rid,
            source=src,
            target=tgt,
            relation_kind=relation_kind,
            window=window,
            strength=float(len(shared)),
            extras={
                "shared_prefixes": sorted(shared),
                "count": int(len(shared)),
            },
        )


__all__ = ["CochangeAuthorSharedTaskPrefixesBuilder"]
