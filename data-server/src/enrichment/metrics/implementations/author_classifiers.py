"""Author classifiers metric — v2 port.

Port of legacy ``src/enrichment/tagger/author_classifiers.py`` (~76 LOC).
Emits two classifiers per :class:`GitAccount`:

* ``dimension="activity"``  — ``"active"`` (last commit within the
  recent window) or ``"idle"``.
* ``dimension="seniority"`` — ``"newcomer"`` / ``"established"`` /
  ``"senior"`` / ``"veteran"`` bucketed by the span (in days) between
  the author's first and last commits.

Reads from the host: ``git_accounts`` (whole registry) + ``commits``
(``by_author`` index). The recent cutoff comes from
``graph.recent_cutoff`` when the host carries one, else falls back to
the latest commit date minus ``cfg.recent_window_days``.

The ``"activity"`` dimension is load-bearing: ``file_trait_utils.active_author_churn``
filters by ``classifier(dimension="activity", value="active")`` (locked
in the Chunk-11 handoff §"Decisions" point 6).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Classifier

if TYPE_CHECKING:
    from src.common.kernel import Graph


# Legacy defaults (from ``EnrichmentConfig``).
_DEFAULT_RECENT_WINDOW_DAYS = 336
_DEFAULT_NEWCOMER_MAX_DAYS = 30
_DEFAULT_ESTABLISHED_MAX_DAYS = 180
_DEFAULT_SENIOR_MAX_DAYS = 730


@METRICS.register
class AuthorClassifierMetric(Metric):
    """Per-author mandatory classifiers: ``activity``, ``seniority``."""

    name: ClassVar[str] = "author.classifiers"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.GIT_ACCOUNT
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=["activity", "seniority"]
    )
    config_fields: ClassVar[list[str]] = [
        "recent_window_days",
        "newcomer_max_days",
        "established_max_days",
        "senior_max_days",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Classifier]:
        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return

        by_author = getattr(commits_reg, "by_author", None)

        # Resolve recent cutoff: honour an externally-attached
        # ``graph.recent_cutoff`` (test stub convention used by the
        # legacy taggers) when present; otherwise compute from the
        # latest observed commit anchor minus ``recent_window_days``.
        cutoff = _resolve_recent_cutoff(graph, commits_reg, config)

        newcomer_max = _config_field(
            config, "newcomer_max_days", _DEFAULT_NEWCOMER_MAX_DAYS
        )
        established_max = _config_field(
            config, "established_max_days", _DEFAULT_ESTABLISHED_MAX_DAYS
        )
        senior_max = _config_field(
            config, "senior_max_days", _DEFAULT_SENIOR_MAX_DAYS
        )

        # Post-finalize (UnifiedUsers redesign §H) the author target IS
        # the UnifiedUser. Phase B runs this metric *after*
        # ``rebind_account_refs_to_unified``, so ``commits.by_author`` is
        # re-keyed on UU refs. Iterate ``graph.unified_users`` so every
        # emitted classifier target is ``EntityKind.UNIFIED_USER`` —
        # matching the kind-check expectations in ``anomaly_knowledge``,
        # ``authorship_table``, ``knowledge_table``, and
        # ``file_trait_utils.active_author_churn``.
        #
        # Pre-finalize behaviour is preserved: iterate per-source git
        # accounts and emit GIT_ACCOUNT-keyed classifiers (today's
        # behaviour, exercised by ``tests/enrichment/test_author_classifiers.py``).
        principals = _resolve_author_principals(graph)
        if not principals:
            return

        for principal in principals:
            principal_ref = principal.ref()
            commits = _commits_for_author(commits_reg, by_author, principal_ref)
            dates: list[datetime] = []
            for c in commits:
                d = ensure_aware(getattr(c, "author_date", None))
                if d is not None:
                    dates.append(d)
            if not dates:
                continue
            first = min(dates)
            last = max(dates)

            # activity — active if last commit falls inside recent window
            if cutoff is None or last >= cutoff:
                activity_value = "active"
            else:
                activity_value = "idle"
            yield Classifier(
                id=f"activity:{principal_ref.kind.value}/{principal.id}",
                target=principal_ref,
                dimension="activity",
                value=activity_value,
            )

            # seniority — span between first and last commit
            span_days = (last - first).days
            seniority_value = _seniority_bucket(
                span_days, newcomer_max, established_max, senior_max
            )
            yield Classifier(
                id=f"seniority:{principal_ref.kind.value}/{principal.id}",
                target=principal_ref,
                dimension="seniority",
                value=seniority_value,
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


def _resolve_author_principals(graph: Any) -> list[Any]:
    """Return the entities to classify, switching on ``graph.merge_state``.

    * Pre-finalize (PRE_MERGE, the historical contract) — per-source
      ``GitAccount`` entities. Targets carry ``EntityKind.GIT_ACCOUNT``.
    * Post-finalize (FINALIZED) — :class:`UnifiedUser` entities, after
      :func:`src.smart_merge.rebind.rebind_account_refs_to_unified` has
      re-keyed ``commits.by_author`` on UU refs. Targets carry
      ``EntityKind.UNIFIED_USER`` so the downstream people-side
      consumers (`anomaly_knowledge`, `authorship_table`,
      `knowledge_table`, `file_trait_utils.active_author_churn`) match.

    When the graph carries no ``merge_state`` attribute (e.g. ad-hoc
    test stubs), fall back to today's git-account behaviour.
    """
    try:
        from src.common.kernel.merge_state import MergeState
    except Exception:  # pragma: no cover — defensive: keep tests importable
        MergeState = None  # type: ignore[assignment]

    state = getattr(graph, "merge_state", None)
    if MergeState is not None and state == MergeState.FINALIZED:
        return _safe_iter(getattr(graph, "unified_users", None))
    return _safe_iter(getattr(graph, "git_accounts", None))


def _commits_for_author(commits_reg: Any, by_author: Any, account_ref: Any) -> list[Any]:
    if by_author is not None:
        return list(by_author[account_ref])
    return [
        c for c in commits_reg if getattr(c, "author_ref", None) == account_ref
    ]


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


def _resolve_recent_cutoff(graph: Any, commits_reg: Any, config: Any) -> Optional[datetime]:
    """Reproduce the legacy ``recent_cutoff`` semantics.

    Priority order (matching the established v2 builder pattern):

    1. An explicit ``graph.recent_cutoff`` attribute attached by the
       caller (used by the legacy test stubs and the production
       processor when it carries a snapshot anchor).
    2. ``latest_commit_date(commits) - recent_window_days``.
    3. ``None`` (no commits — caller treats authors as active).
    """
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    window_days = _config_field(
        config, "recent_window_days", _DEFAULT_RECENT_WINDOW_DAYS
    )
    latest: Optional[datetime] = None
    try:
        for c in commits_reg:
            d = ensure_aware(
                getattr(c, "author_date", None)
                or getattr(c, "committer_date", None)
            )
            if d is None:
                continue
            if latest is None or d > latest:
                latest = d
    except TypeError:
        return None
    if latest is None:
        return None
    return latest - timedelta(days=window_days)


def _seniority_bucket(
    span_days: int,
    newcomer_max: int,
    established_max: int,
    senior_max: int,
) -> str:
    if span_days <= newcomer_max:
        return "newcomer"
    if span_days <= established_max:
        return "established"
    if span_days <= senior_max:
        return "senior"
    return "veteran"


__all__ = ["AuthorClassifierMetric"]
