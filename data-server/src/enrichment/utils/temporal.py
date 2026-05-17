"""Temporal index for windowed lookups over commit timestamps.

Per Phase 2 plan decision D2: every relation builder / metric that needs
"commits between t0 and t1" or "pairs of commits within Δt of each
other" gets that here, behind a sorted-list + bisect API. Built lazily by
:meth:`Graph.ensure_temporal_index` and cached on the graph instance.

Design
------
* **Internal storage:** ``dict[EntityKind, list[tuple[float, EntityRef]]]``
  — one sorted list per kind. Today only :attr:`EntityKind.COMMIT` is
  populated; adding ``PR``/``REVIEW`` later is purely an extension of the
  build step (the public API does not change).
* **Lookups:** :mod:`bisect` for range queries (``O(log N)``) and
  pair-finding (``O(N · K)`` where K is the average pair count per
  reference timestamp).
* **Timestamps:** stored as POSIX float seconds (``datetime.timestamp()``).
  Naive datetimes are coerced to UTC at build time so the index is
  monotone regardless of the upstream miner's tz hygiene.

The API surface intentionally stays minimal: per the chunk-11 plan, this
is the **locked contract** consumed by 5 later relation builders. Adding
a method here = updating the chunk-11 handoff.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timezone
from typing import Iterable, Iterator, Optional, Tuple

from src.common.kernel import EntityKind, EntityRef


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _to_unix(dt: datetime) -> float:
    """Convert a ``datetime`` to POSIX seconds, coercing naive→UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


# ----------------------------------------------------------------------
# TemporalIndex
# ----------------------------------------------------------------------
class TemporalIndex:
    """Sorted, per-kind timestamp index over graph entities.

    Constructed via :meth:`from_graph` (the supported path). Direct
    construction is reserved for tests — pass ``entries`` keyed by
    :class:`EntityKind`, each value an iterable of
    ``(timestamp_unix, EntityRef)`` tuples.

    Invariants
    ----------
    * Each ``_by_kind[k]`` list is sorted by the first tuple element.
    * Equal timestamps preserve insertion order (Python's sort is stable).
    * Empty kinds map to empty lists, NOT missing keys — callers can
      query any kind safely.
    """

    __slots__ = ("_by_kind",)

    def __init__(
        self,
        entries: Optional[dict[EntityKind, Iterable[Tuple[float, EntityRef]]]] = None,
    ) -> None:
        self._by_kind: dict[EntityKind, list[Tuple[float, EntityRef]]] = {}
        if entries:
            for kind, items in entries.items():
                materialised = list(items)
                materialised.sort(key=lambda pair: pair[0])
                self._by_kind[kind] = materialised

    # ------------------------------------------------------------------
    # Construction from a graph
    # ------------------------------------------------------------------
    @classmethod
    def from_graph(cls, graph: "object") -> "TemporalIndex":
        """Build a :class:`TemporalIndex` from a :class:`Graph` instance.

        Today indexes only ``graph.commits``; ``PR`` / ``REVIEW`` kinds
        can be added here as extra branches without changing the public
        accessor surface. We resolve attributes via ``getattr`` with a
        defensive ``None`` fallback so test stubs that omit a registry
        don't blow up.
        """
        commits_reg = getattr(graph, "commits", None)
        commit_entries: list[Tuple[float, EntityRef]] = []
        if commits_reg is not None:
            for commit in commits_reg:
                ts = _commit_timestamp(commit)
                if ts is None:
                    continue
                commit_entries.append((ts, commit.ref()))
        return cls(entries={EntityKind.COMMIT: commit_entries})

    # ------------------------------------------------------------------
    # Read-only accessors — LOCKED API surface (Chunk 11 handoff §D2)
    # ------------------------------------------------------------------
    def commits_in_window(
        self,
        start_ts_unix: float,
        end_ts_unix: float,
    ) -> list[EntityRef]:
        """Every commit ref with ``start_ts_unix <= ts < end_ts_unix``.

        Half-open interval — symmetric with :func:`bisect.bisect_right` /
        :func:`bisect.bisect_left`. Returns a fresh list (callers may
        mutate it without affecting the index).

        Empty list when no commits fall in the window or when the
        ``COMMIT`` kind was never populated.
        """
        if start_ts_unix > end_ts_unix:
            return []
        bucket = self._by_kind.get(EntityKind.COMMIT, [])
        if not bucket:
            return []
        keys = [pair[0] for pair in bucket]
        lo = bisect_left(keys, start_ts_unix)
        hi = bisect_left(keys, end_ts_unix)
        return [pair[1] for pair in bucket[lo:hi]]

    def pairs_within(
        self,
        hours: float,
    ) -> Iterator[Tuple[EntityRef, EntityRef]]:
        """Every distinct ordered commit pair whose timestamps are within
        ``hours`` of each other.

        Yields ``(earlier_ref, later_ref)`` tuples — left always ≤ right
        in timestamp. Self-pairs (same ref) are excluded.

        ``O(N · K)`` where K is the average pair count per anchor; well
        below the ``O(N²)`` naïve scan for typical commit histories
        because we exit each inner scan as soon as the timestamp delta
        exceeds the window.
        """
        if hours <= 0:
            return
        window_seconds = float(hours) * 3600.0
        bucket = self._by_kind.get(EntityKind.COMMIT, [])
        if len(bucket) < 2:
            return
        keys = [pair[0] for pair in bucket]
        for i, (ts_i, ref_i) in enumerate(bucket):
            # Find the first index j > i whose ts exceeds ts_i + window;
            # every (i, j-1) and earlier pair satisfies the window. We
            # start from i+1 with bisect_right against the upper bound.
            upper = ts_i + window_seconds
            j_stop = bisect_right(keys, upper, lo=i + 1)
            for j in range(i + 1, j_stop):
                ref_j = bucket[j][1]
                if ref_j == ref_i:
                    continue
                yield ref_i, ref_j

    # ------------------------------------------------------------------
    # Diagnostics — small surface for tests + debugging.
    # ------------------------------------------------------------------
    def kinds(self) -> tuple[EntityKind, ...]:
        """Every :class:`EntityKind` populated in this index."""
        return tuple(self._by_kind.keys())

    def __len__(self) -> int:
        """Total entries across all kinds."""
        return sum(len(v) for v in self._by_kind.values())

    def count(self, kind: EntityKind) -> int:
        """Entry count for ``kind`` (0 if not populated)."""
        return len(self._by_kind.get(kind, []))


def _commit_timestamp(commit: "object") -> Optional[float]:
    """Pick the best timestamp for a commit: ``author_date`` or
    ``committer_date`` fallback. Returns POSIX seconds or ``None``.
    """
    dt = getattr(commit, "author_date", None) or getattr(commit, "committer_date", None)
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return None
    return _to_unix(dt)


__all__ = ["TemporalIndex"]
