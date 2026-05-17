"""Cross-cutting enrichment primitives.

Per Phase 2 plan §1 (decisions D1, D2, D3), the ``utils/`` package is the
single home for helpers that span the metric / relation / overview layers.
Members:

* :mod:`file_trait_utils` — per-file churn/bucket helpers shared between
  the ``anomaly_*`` metrics and the ``cochange_*`` relation builders.
* :mod:`temporal`         — :class:`TemporalIndex` for windowed lookups
  over commit timestamps (bisect-backed). Cached lazily on
  :class:`Graph` via :meth:`Graph.ensure_temporal_index`.
* :mod:`task_prefix`      — Jira-style task-prefix regex + parser feeding
  the ``CommitTaskPrefixClassifierMetric``.

All three are written against v2 typed registries (``graph.commits``,
``graph.changes``, ``graph.classifiers``) — no legacy ``Enrichments`` /
``tags_by_entity`` shape leaks through.
"""
from __future__ import annotations

from .file_trait_utils import (
    active_author_churn,
    author_churn,
    author_churn_within,
    bucket_start,
    change_churn,
    change_net_churn,
    commit_dates,
    files_touched_by_account,
    linear_slope,
    time_bucketed_churn,
    time_bucketed_commits,
)
from .task_prefix import (
    TASK_PREFIX_PATTERN,
    extract_task_prefixes,
    parse_task_prefix,
)
from .temporal import TemporalIndex

__all__ = [
    # file_trait_utils
    "active_author_churn",
    "author_churn",
    "author_churn_within",
    "bucket_start",
    "change_churn",
    "change_net_churn",
    "commit_dates",
    "files_touched_by_account",
    "linear_slope",
    "time_bucketed_churn",
    "time_bucketed_commits",
    # task_prefix
    "TASK_PREFIX_PATTERN",
    "extract_task_prefixes",
    "parse_task_prefix",
    # temporal
    "TemporalIndex",
]
