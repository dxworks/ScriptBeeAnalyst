"""Commit classifiers metric — six mandatory classifiers per commit.

Port of legacy ``src/enrichment/tagger/commit_classifiers.py``. Emits a
:class:`Classifier` per (commit, dimension) — six classifiers per commit.

Dimensions:

* ``"message.nature"``     — first regex match in ``config.nature_patterns``
                              (merge / revert / bugfix / docs / test /
                              refactor / chore / feature)
* ``"volume.churn"``       — ``focused`` / ``medium`` / ``bulk`` by
                              total added+deleted lines vs
                              ``churn_focused_max``/``churn_medium_max``
* ``"volume.spread"``      — ``narrow`` / ``wide`` by distinct files touched
                              vs ``spread_narrow_max``
* ``"daytime"``            — bucket from ``config.daytime_buckets``
* ``"weekday"``            — mon..sun
* ``"message.smartness"``  — ``smart`` if any ``issue_file`` /
                              ``issue_issue`` relation already references
                              this commit's author/files; ``dumb`` otherwise.
                              (Legacy used ``commit.issues`` — that field
                              is gone in v2; we use the v2 relation
                              registry instead.)

Reads from the host: ``commits``, ``changes`` (via ``by_commit`` index),
``hunks`` (via ``by_change`` index), ``relations`` (via ``of_kind``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Classifier

if TYPE_CHECKING:
    from src.common.kernel import Graph


_DEFAULT_CHURN_FOCUSED_MAX = 50
_DEFAULT_CHURN_MEDIUM_MAX = 500
_DEFAULT_SPREAD_NARROW_MAX = 3
_DEFAULT_DAYTIME_BUCKETS = {
    "night":     (0, 6),
    "morning":   (6, 12),
    "afternoon": (12, 18),
    "evening":   (18, 24),
}
_WEEKDAY_LABELS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@METRICS.register
class CommitClassifierMetric(Metric):
    """Emits six :class:`Classifier` rows per commit."""

    name: ClassVar[str] = "commit.classifiers"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.COMMIT)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=[
            "message.nature",
            "volume.churn",
            "volume.spread",
            "daytime",
            "weekday",
            "message.smartness",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "nature_patterns",
        "churn_focused_max",
        "churn_medium_max",
        "spread_narrow_max",
        "daytime_buckets",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Classifier]:
        commits = _safe_iter(getattr(graph, "commits", None))
        if not commits:
            return

        changes_by_commit = _changes_by_commit_index(graph)
        hunks_by_change = _hunks_by_change_index(graph)
        smart_commit_refs = _smart_commit_refs(graph)

        for commit in commits:
            commit_ref = commit.ref()
            commit_id = commit.id

            # ── message.nature ────────────────────────────────────────
            nature = _message_nature(commit, config)
            yield Classifier(
                id=f"message.nature:{commit_ref.kind.value}/{commit_id}",
                target=commit_ref,
                dimension="message.nature",
                value=nature,
            )

            # ── volume.churn ──────────────────────────────────────────
            changes = list(changes_by_commit(commit_ref))
            churn = 0
            for change in changes:
                for hunk in hunks_by_change(change.ref()):
                    churn += len(getattr(hunk, "added_lines", []) or [])
                    churn += len(getattr(hunk, "deleted_lines", []) or [])
            churn_focused_max = _config_field(
                config, "churn_focused_max", _DEFAULT_CHURN_FOCUSED_MAX
            )
            churn_medium_max = _config_field(
                config, "churn_medium_max", _DEFAULT_CHURN_MEDIUM_MAX
            )
            if churn <= churn_focused_max:
                churn_bucket = "focused"
            elif churn <= churn_medium_max:
                churn_bucket = "medium"
            else:
                churn_bucket = "bulk"
            yield Classifier(
                id=f"volume.churn:{commit_ref.kind.value}/{commit_id}",
                target=commit_ref,
                dimension="volume.churn",
                value=churn_bucket,
            )

            # ── volume.spread ─────────────────────────────────────────
            spread = len(changes)
            spread_narrow_max = _config_field(
                config, "spread_narrow_max", _DEFAULT_SPREAD_NARROW_MAX
            )
            spread_bucket = "narrow" if spread <= spread_narrow_max else "wide"
            yield Classifier(
                id=f"volume.spread:{commit_ref.kind.value}/{commit_id}",
                target=commit_ref,
                dimension="volume.spread",
                value=spread_bucket,
            )

            # ── daytime + weekday ─────────────────────────────────────
            dt = getattr(commit, "author_date", None)
            if dt is not None:
                buckets = _config_field(
                    config, "daytime_buckets", _DEFAULT_DAYTIME_BUCKETS
                )
                yield Classifier(
                    id=f"daytime:{commit_ref.kind.value}/{commit_id}",
                    target=commit_ref,
                    dimension="daytime",
                    value=_daytime_bucket(dt.hour, buckets),
                )
                yield Classifier(
                    id=f"weekday:{commit_ref.kind.value}/{commit_id}",
                    target=commit_ref,
                    dimension="weekday",
                    value=_WEEKDAY_LABELS[dt.weekday()],
                )

            # ── message.smartness ─────────────────────────────────────
            smart = commit_ref in smart_commit_refs
            yield Classifier(
                id=f"message.smartness:{commit_ref.kind.value}/{commit_id}",
                target=commit_ref,
                dimension="message.smartness",
                value="smart" if smart else "dumb",
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


def _smart_commit_refs(graph: Any) -> set[Any]:
    """Set of commit refs that have at least one ``issue_file`` /
    ``issue_issue`` relation pointing through them.

    Substitute for the legacy ``commit.issues`` list. We treat a commit
    as "smart" if it touches at least one file that an issue is linked
    to (via :class:`IssueFileBuilder`'s output). This is a conservative
    approximation; the original semantics ("linker attached any
    issues") will be re-fined in a follow-up chunk once we have a
    direct ``IssueCommitBuilder``.
    """
    relations = getattr(graph, "relations", None)
    if relations is None:
        return set()
    # Use the ``of_kind`` lookup when available.
    of_kind = getattr(relations, "of_kind", None)
    if of_kind is None:
        return set()
    smart: set[Any] = set()
    # Currently no direct issue↔commit relation kind in v2; we keep this
    # placeholder empty and rely on the conservative fallback above.
    # Any commit whose changes touch a file that's the target of an
    # ``issue_file`` relation is considered "smart".
    issue_file_rels = of_kind("issue_file")
    smart_files: set[Any] = {rel.target for rel in issue_file_rels}
    if not smart_files:
        return smart
    changes = getattr(graph, "changes", None)
    if changes is None:
        return smart
    try:
        change_iter = list(changes)
    except TypeError:
        return smart
    for ch in change_iter:
        fref = getattr(ch, "file_ref", None)
        if fref in smart_files:
            smart.add(ch.commit_ref)
    return smart


def _message_nature(commit: Any, config: Any) -> str:
    parents = getattr(commit, "parent_refs", None) or []
    if len(parents) > 1:
        return "merge"
    message = getattr(commit, "message", "") or ""
    patterns = _config_field(config, "nature_patterns", None)
    if patterns is None:
        return "chore"
    for name, pattern in patterns:
        if pattern.search(message):
            return name
    return "chore"


def _daytime_bucket(hour: int, buckets: dict[str, tuple[int, int]]) -> str:
    for label, (start, end) in buckets.items():
        if start <= hour < end:
            return label
    return "unknown"


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


__all__ = ["CommitClassifierMetric"]
