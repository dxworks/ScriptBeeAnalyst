"""Cochange (author ↔ author, time-windowed) builder — Chunk 14 port.

Counts pairs of **distinct** authors whose commits fall within
``cfg.time_windowed_cochange_hours`` of each other. The signal proxies
"people working in the same time-frame" — pair-programming, on-call
buddies, spike coordination.

Algorithm
---------

Generalises the Chunk-13 file-cochange pattern to the author domain:

1. Pull the cached :class:`TemporalIndex` via
   :meth:`Graph.ensure_temporal_index` (kind-agnostic D2 contract).
2. Iterate ``ti.pairs_within(hours=N)`` — every ordered commit pair in
   the window.
3. For each commit pair ``(c1, c2)``, resolve ``commit.author_ref`` for
   each side. If both refs exist and differ, increment the author-pair
   counter.
4. Emit one :class:`Relation` per (author_a, author_b) pair per window
   with ``strength = count``.

The N1 trap (Chunk-13 review) — "double-count when commit file-sets
overlap" — does **not** apply here because each commit pair contributes
**exactly one** increment per author pair (no Cartesian inner loop).
However, an author can commit several times inside a single window, so a
single pair ``(Alice, Bob)`` can accumulate many counts across distinct
``(c1, c2)`` pairs; that is the intended semantics (legacy behavior).

TemporalIndex contract
----------------------

This builder **does NOT extend** the TemporalIndex API. The index
remains commit-only (per D2); author resolution happens caller-side via
``commit.author_ref``. The kind-agnostic property of D2 is preserved —
no new ``EntityKind`` is added to the index.

Reads
-----

* ``graph.commits``                — for ``author_ref`` lookup per ref.
* ``graph.ensure_temporal_index()`` — pair iteration (D2 locked API).
* ``graph.config.time_windowed_cochange_hours`` — Δt threshold.
* ``graph.recent_cutoff``           — optional recent-window discriminator.
"""
from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any, Iterable

from src.enrichment.relations import Relation, RelationBuilder, WindowKind
from src.enrichment.relations.builders import BUILDERS

if TYPE_CHECKING:
    from src.common.kernel import EntityRef, Graph


_DEFAULT_TIME_WINDOWED_HOURS = 0.5


@BUILDERS.register
class CochangeAuthorTimeWindowedBuilder(RelationBuilder):
    """Emit ``cochange_author_time_windowed`` relations.

    Strength = number of cross-author commit pairs inside the window.
    Two emissions per pair: ``LIFETIME`` always; ``RECENT`` only when
    ``graph.recent_cutoff`` is set and both contributing commits land
    inside the recent window.
    """

    name = "cochange.author_time_windowed"
    relation_kind = "cochange_author_time_windowed"
    window = WindowKind.LIFETIME

    def build(self, graph: "Graph") -> Iterable[Relation]:
        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return

        hours = _config_field(
            graph, "time_windowed_cochange_hours", _DEFAULT_TIME_WINDOWED_HOURS
        )
        if hours is None or hours <= 0:
            return

        cutoff = getattr(graph, "recent_cutoff", None)

        # Resolve commit refs → author refs once; also remember which
        # commits fall inside the recent window. We deliberately keep the
        # full author_by_commit map (merges + bulk commits included) so
        # the time-windowed pair walk matches the legacy semantics
        # (legacy walked ``account.commits`` which included merges).
        author_by_commit: dict[Any, Any] = {}
        recent_commits: set[Any] = set()
        commit_get = getattr(commits_reg, "get", None)
        for commit in _safe_iter(commits_reg):
            author_ref = getattr(commit, "author_ref", None)
            if author_ref is None:
                continue
            ref = commit.ref()
            author_by_commit[ref] = author_ref
            if cutoff is not None and _commit_in_window(commit, cutoff):
                recent_commits.add(ref)

        if len(author_by_commit) < 2:
            return

        ensure = getattr(graph, "ensure_temporal_index", None)
        if not callable(ensure):
            return
        ti = ensure()
        pair_iter = ti.pairs_within(hours=float(hours))

        lifetime: dict[tuple[Any, Any], int] = defaultdict(int)
        recent: dict[tuple[Any, Any], int] = defaultdict(int)

        for c1_ref, c2_ref in pair_iter:
            a1 = author_by_commit.get(c1_ref)
            a2 = author_by_commit.get(c2_ref)
            if a1 is None or a2 is None or a1 == a2:
                # Same-author pairs are not a cross-developer signal; skip.
                continue
            pair = _ordered_pair(a1, a2)
            lifetime[pair] += 1
            if (
                cutoff is not None
                and c1_ref in recent_commits
                and c2_ref in recent_commits
            ):
                recent[pair] += 1

        yield from _emit_pairs(
            self.relation_kind, WindowKind.LIFETIME, lifetime, hours=hours
        )
        if cutoff is not None:
            yield from _emit_pairs(
                self.relation_kind, WindowKind.RECENT, recent, hours=hours
            )
        # Quiet the lint for the unused symmetric helper kept for parity
        # with sibling builders.
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
) -> Iterable[Relation]:
    for (src, tgt), count in pairs.items():
        if count <= 0:
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


__all__ = ["CochangeAuthorTimeWindowedBuilder"]
