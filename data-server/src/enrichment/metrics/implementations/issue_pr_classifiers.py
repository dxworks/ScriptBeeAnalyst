"""Issue/PR classifiers metric — v2 port (Chunk 16).

Port of legacy ``src/enrichment/tagger/issue_pr_classifiers.py``
(~209 LOC). Emits the mandatory classifiers for two cross-source entity
kinds:

Per-:class:`Issue` (``EntityKind.ISSUE``)::

    Classifier(dimension="issue.status",     value=<native Jira status>)
    Classifier(dimension="issue.type",       value=<native Jira type>)
    Classifier(dimension="issue.resolution", value="open"|"resolved")
    Classifier(dimension="issue.age",        value=<age-bucket label>)

Per-:class:`PullRequest` (``EntityKind.PULL_REQUEST``)::

    Classifier(dimension="pr.state",            value=<native GitHub state>)
    Classifier(dimension="pr.size",             value="XS"|"S"|"M"|"L"|"XL")
    Classifier(dimension="pr.review_intensity", value="none"|"light"|"moderate"|"heavy")

Reads:

* ``graph.issues``                    — issue population.
* ``graph.issue_statuses.get``        — name + category lookup for the
                                        current :class:`IssueStatus`.
* ``graph.issue_types.get``           — name lookup for current
                                        :class:`IssueType`.
* ``graph.pull_requests``             — PR population.
* ``graph.reviews.by_pull_request``   — review counts per PR.
* ``graph.relations.of_kind("pr_file")`` — linked-file fan-out for PR
                                           size (read once per pipeline
                                           call, indexed by source PR).
* ``graph.hunks.by_change``           — PR-size churn (via
                                        per-commit changes).
* ``graph.anchor_date`` (optional)    — age-bucket anchor; falls back
                                        to wall-clock now.

Naming change from legacy
-------------------------

Legacy classifier slots were unprefixed (``status``, ``type``,
``resolution``, ``age_bucket``, ``state``, ``size``,
``review_intensity``). v2 prefixes them (``issue.*`` / ``pr.*``) so the
single :class:`ClassifierRegistry` doesn't conflate per-source slots —
``issue.status`` and ``pr.state`` are clearly distinct. The
:attr:`MetricOutputs.emits_classifiers` list mirrors the new names.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional, Union

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Classifier, Trait

if TYPE_CHECKING:
    from src.common.kernel import Graph


# Defaults (mirror :class:`EnrichmentConfig`).
_DEFAULT_RESOLVED_CATEGORIES: tuple[str, ...] = (
    "done", "closed", "resolved", "complete", "completed",
)
_DEFAULT_ISSUE_AGE_BUCKETS: list[tuple[str, int]] = [
    ("<1w", 7),
    ("1-4w", 28),
    ("1-3m", 90),
    ("3-12m", 365),
    (">1y", 10**9),
]
_DEFAULT_PR_SIZE_XS_MAX = 50
_DEFAULT_PR_SIZE_S_MAX = 200
_DEFAULT_PR_SIZE_M_MAX = 600
_DEFAULT_PR_SIZE_L_MAX = 2000
_DEFAULT_REVIEW_INTENSITY_LIGHT_MAX = 2
_DEFAULT_REVIEW_INTENSITY_HEAVY_MIN = 5

# Review states that count toward review_intensity. DISMISSED is excluded
# because a dismissed review no longer represents reviewer engagement.
_REVIEW_INTENSITY_COUNTED_STATES = frozenset(
    {"APPROVED", "COMMENTED", "CHANGES_REQUESTED", "PENDING"}
)


@METRICS.register
class IssuePRClassifierMetric(Metric):
    name: ClassVar[str] = "issue_pr.classifiers"
    inputs: ClassVar[MetricInputs] = MetricInputs()  # walks two registries
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=[
            "issue.status",
            "issue.type",
            "issue.resolution",
            "issue.age",
            "pr.state",
            "pr.size",
            "pr.review_intensity",
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "issue_age_buckets",
        "resolved_status_categories",
        "pr_size_xs_max",
        "pr_size_s_max",
        "pr_size_m_max",
        "pr_size_l_max",
        "review_intensity_light_max",
        "review_intensity_heavy_min",
    ]

    def compute(
        self, graph: "Graph", config: Any
    ) -> Iterable[Union[Classifier, Trait]]:
        yield from self._issue_classifiers(graph, config)
        yield from self._pr_classifiers(graph, config)

    # ------------------------------------------------------------------
    # Issue side
    # ------------------------------------------------------------------
    def _issue_classifiers(
        self, graph: "Graph", config: Any
    ) -> Iterable[Classifier]:
        issues = _safe_iter(getattr(graph, "issues", None))
        if not issues:
            return

        statuses_reg = getattr(graph, "issue_statuses", None)
        types_reg = getattr(graph, "issue_types", None)

        resolved_raw = _config_field(
            config, "resolved_status_categories", _DEFAULT_RESOLVED_CATEGORIES
        )
        resolved = frozenset(str(s).strip().lower() for s in (resolved_raw or ()))
        buckets = list(_config_field(
            config, "issue_age_buckets", _DEFAULT_ISSUE_AGE_BUCKETS
        ))
        anchor = _anchor_now(graph)

        for issue in issues:
            issue_ref = issue.ref()

            status_name = _resolve_status_name(statuses_reg, issue.status_ref)
            if status_name:
                yield Classifier(
                    id=f"issue.status:{issue_ref.kind.value}/{issue_ref.id}",
                    target=issue_ref,
                    dimension="issue.status",
                    value=status_name,
                )

            type_name = _resolve_type_name(types_reg, issue.type_ref)
            if type_name:
                yield Classifier(
                    id=f"issue.type:{issue_ref.kind.value}/{issue_ref.id}",
                    target=issue_ref,
                    dimension="issue.type",
                    value=type_name,
                )

            resolution = _resolution(issue, statuses_reg, resolved)
            yield Classifier(
                id=f"issue.resolution:{issue_ref.kind.value}/{issue_ref.id}",
                target=issue_ref,
                dimension="issue.resolution",
                value=resolution,
            )

            age_days = _age_in_days(issue, statuses_reg, anchor, resolved)
            if age_days is not None:
                yield Classifier(
                    id=f"issue.age:{issue_ref.kind.value}/{issue_ref.id}",
                    target=issue_ref,
                    dimension="issue.age",
                    value=_age_bucket(age_days, buckets),
                )

    # ------------------------------------------------------------------
    # PR side
    # ------------------------------------------------------------------
    def _pr_classifiers(
        self, graph: "Graph", config: Any
    ) -> Iterable[Classifier]:
        prs = _safe_iter(getattr(graph, "pull_requests", None))
        if not prs:
            return

        xs_max = int(_config_field(config, "pr_size_xs_max", _DEFAULT_PR_SIZE_XS_MAX))
        s_max = int(_config_field(config, "pr_size_s_max", _DEFAULT_PR_SIZE_S_MAX))
        m_max = int(_config_field(config, "pr_size_m_max", _DEFAULT_PR_SIZE_M_MAX))
        l_max = int(_config_field(config, "pr_size_l_max", _DEFAULT_PR_SIZE_L_MAX))
        light_max = int(_config_field(
            config, "review_intensity_light_max",
            _DEFAULT_REVIEW_INTENSITY_LIGHT_MAX,
        ))
        heavy_min = int(_config_field(
            config, "review_intensity_heavy_min",
            _DEFAULT_REVIEW_INTENSITY_HEAVY_MIN,
        ))

        reviews_for_pr = _reviews_by_pr_lookup(graph)
        gh_commits_for_pr = _gh_commits_by_pr_lookup(graph)
        commits_reg = getattr(graph, "commits", None)
        changes_by_commit = _changes_by_commit_index(graph)
        hunks_by_change = _hunks_by_change_index(graph)
        pr_file_relations = _pr_file_lookup(graph)

        for pr in prs:
            pr_ref = pr.ref()

            state = getattr(pr, "state", None)
            if state:
                yield Classifier(
                    id=f"pr.state:{pr_ref.kind.value}/{pr_ref.id}",
                    target=pr_ref,
                    dimension="pr.state",
                    value=state,
                )

            size_bucket = _pr_size_bucket(
                pr, pr_ref,
                gh_commits_for_pr=gh_commits_for_pr,
                commits_reg=commits_reg,
                changes_by_commit=changes_by_commit,
                hunks_by_change=hunks_by_change,
                pr_file_relations=pr_file_relations,
                xs_max=xs_max, s_max=s_max, m_max=m_max, l_max=l_max,
            )
            yield Classifier(
                id=f"pr.size:{pr_ref.kind.value}/{pr_ref.id}",
                target=pr_ref,
                dimension="pr.size",
                value=size_bucket,
            )

            intensity = _review_intensity_bucket(
                reviews_for_pr(pr_ref), light_max, heavy_min
            )
            yield Classifier(
                id=f"pr.review_intensity:{pr_ref.kind.value}/{pr_ref.id}",
                target=pr_ref,
                dimension="pr.review_intensity",
                value=intensity,
            )


# ----------------------------------------------------------------------
# Issue helpers
# ----------------------------------------------------------------------
def _resolve_status_name(statuses_reg: Any, status_ref: Optional[EntityRef]) -> Optional[str]:
    if status_ref is None or statuses_reg is None:
        return None
    status = statuses_reg.get(status_ref.id) if hasattr(statuses_reg, "get") else None
    if status is None:
        return None
    return getattr(status, "name", None)


def _resolve_type_name(types_reg: Any, type_ref: Optional[EntityRef]) -> Optional[str]:
    if type_ref is None or types_reg is None:
        return None
    type_ = types_reg.get(type_ref.id) if hasattr(types_reg, "get") else None
    if type_ is None:
        return None
    return getattr(type_, "name", None)


def _resolution(issue: Any, statuses_reg: Any, resolved: frozenset[str]) -> str:
    """Collapse the issue's current status to ``"open"`` / ``"resolved"``.

    Uses the status entity's ``category`` (preferred — Jira's
    ``IssueStatusCategory`` key like ``"done"``) and falls back to the
    status ``name`` (lowercased) when the category isn't a member of the
    resolved set.
    """
    status_ref = getattr(issue, "status_ref", None)
    if status_ref is None or statuses_reg is None:
        return "open"
    status = statuses_reg.get(status_ref.id) if hasattr(statuses_reg, "get") else None
    if status is None:
        return "open"
    cat = (getattr(status, "category", None) or "").strip().lower()
    if cat and cat in resolved:
        return "resolved"
    name = (getattr(status, "name", None) or "").strip().lower()
    if name in resolved:
        return "resolved"
    return "open"


def _age_in_days(
    issue: Any,
    statuses_reg: Any,
    anchor: datetime,
    resolved: frozenset[str],
) -> Optional[int]:
    """``(anchor − created_at).days``; for resolved issues the upper
    bound is ``updated_at`` (or ``resolution_date`` when present)."""
    created = ensure_aware(getattr(issue, "created_at", None))
    if created is None:
        return None
    end = anchor
    status_ref = getattr(issue, "status_ref", None)
    if status_ref is not None and statuses_reg is not None:
        status = statuses_reg.get(status_ref.id) if hasattr(statuses_reg, "get") else None
        if status is not None:
            cat = (getattr(status, "category", None) or "").strip().lower()
            if cat and cat in resolved:
                resolved_dt = ensure_aware(getattr(issue, "resolution_date", None))
                if resolved_dt is not None:
                    end = resolved_dt
                else:
                    updated = ensure_aware(getattr(issue, "updated_at", None))
                    if updated is not None:
                        end = updated
    delta = end - created
    return max(0, delta.days)


def _age_bucket(age_days: int, buckets: list[tuple[str, int]]) -> str:
    for label, max_days in buckets:
        if age_days <= max_days:
            return label
    return buckets[-1][0] if buckets else "unknown"


# ----------------------------------------------------------------------
# PR helpers
# ----------------------------------------------------------------------
def _pr_size_bucket(
    pr: Any,
    pr_ref: EntityRef,
    *,
    gh_commits_for_pr,
    commits_reg: Any,
    changes_by_commit,
    hunks_by_change,
    pr_file_relations: dict[EntityRef, list[Any]],
    xs_max: int,
    s_max: int,
    m_max: int,
    l_max: int,
) -> str:
    """PR-size score = changed_files + GitHub-side commit churn + linked
    git-commit churn (from ``pr_file`` relations + git hunks).

    Mirrors the legacy ``_pr_size`` summation:

    * ``pr.changed_files`` — top-level GitHub count.
    * ``GitHubCommit.changed_files`` — per-commit GitHub-side count.
    * Linked git commit hunks (added + deleted lines) reached through the
      Chunk-7 ``pr_file`` relations, which already map PR → file. We
      re-resolve to the underlying changes via the per-relation
      ``commit_id`` extra when present, otherwise fall back to a per-PR
      commit-walk (matching the legacy ``pr.git_commits`` back-pointer).
    """
    score = int(getattr(pr, "changed_files", 0) or 0)

    for gh in gh_commits_for_pr(pr_ref):
        score += int(getattr(gh, "changed_files", 0) or 0)

    # Linked git-commit churn. Use pr_file relations (preferred — Chunk-7
    # builders already linked them) when each carries a `commit_id` extra;
    # otherwise sum across every linked commit referenced by the
    # pr_file relations' source commits.
    seen_change_ids: set[str] = set()
    for rel in pr_file_relations.get(pr_ref, []):
        extras = getattr(rel, "extras", None) or {}
        commit_id = extras.get("commit_id") if isinstance(extras, dict) else None
        if not commit_id:
            continue
        if commits_reg is None:
            continue
        commit = commits_reg.get(commit_id) if hasattr(commits_reg, "get") else None
        if commit is None:
            continue
        for change in changes_by_commit(commit.ref()):
            cid = getattr(change, "id", None)
            if cid is None or cid in seen_change_ids:
                continue
            seen_change_ids.add(cid)
            for hunk in hunks_by_change(change.ref()):
                score += len(getattr(hunk, "added_lines", []) or [])
                score += len(getattr(hunk, "deleted_lines", []) or [])

    if score <= xs_max:
        return "XS"
    if score <= s_max:
        return "S"
    if score <= m_max:
        return "M"
    if score <= l_max:
        return "L"
    return "XL"


def _review_intensity_bucket(
    reviews: Iterable[Any], light_max: int, heavy_min: int
) -> str:
    count = 0
    for r in reviews:
        if (getattr(r, "state", None) or "").upper() in _REVIEW_INTENSITY_COUNTED_STATES:
            count += 1
    if count == 0:
        return "none"
    if count <= light_max:
        return "light"
    if count < heavy_min:
        return "moderate"
    return "heavy"


# ----------------------------------------------------------------------
# Index accessors / safety wrappers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


def _anchor_now(graph: Any) -> datetime:
    explicit = getattr(graph, "anchor_date", None)
    if explicit is not None:
        d = ensure_aware(explicit)
        if d is not None:
            return d
    return datetime.now(timezone.utc)


def _reviews_by_pr_lookup(graph: Any):
    reviews = getattr(graph, "reviews", None)
    if reviews is None:
        return lambda _ref: ()
    by_pr = getattr(reviews, "by_pull_request", None)
    if by_pr is not None:
        return lambda pr_ref: by_pr[pr_ref]
    return lambda pr_ref: tuple(
        r for r in reviews if getattr(r, "pull_request_ref", None) == pr_ref
    )


def _gh_commits_by_pr_lookup(graph: Any):
    gh_commits = getattr(graph, "github_commits", None)
    if gh_commits is None:
        return lambda _ref: ()
    by_pr = getattr(gh_commits, "by_pull_request", None)
    if by_pr is not None:
        return lambda pr_ref: by_pr[pr_ref]
    return lambda pr_ref: tuple(
        c for c in gh_commits if getattr(c, "pull_request_ref", None) == pr_ref
    )


def _changes_by_commit_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _ref: ()
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return lambda commit_ref: by_commit[commit_ref]
    return lambda commit_ref: tuple(
        ch for ch in changes if getattr(ch, "commit_ref", None) == commit_ref
    )


def _hunks_by_change_index(graph: Any):
    hunks = getattr(graph, "hunks", None)
    if hunks is None:
        return lambda _ref: ()
    by_change = getattr(hunks, "by_change", None)
    if by_change is not None:
        return lambda change_ref: by_change[change_ref]
    return lambda change_ref: tuple(
        h for h in hunks if getattr(h, "change_ref", None) == change_ref
    )


def _pr_file_lookup(graph: Any) -> dict[EntityRef, list[Any]]:
    """Bucket ``pr_file`` relations by source PR ref for O(1) per-PR access.

    Built once per pipeline call. Empty when the relation registry is
    absent or no Chunk-7 ``pr_file`` builder has run.
    """
    relations = getattr(graph, "relations", None)
    if relations is None:
        return {}
    of_kind = getattr(relations, "of_kind", None)
    rel_iter: Iterable[Any]
    if of_kind is not None:
        rel_iter = of_kind("pr_file")
    else:
        rel_iter = (
            r for r in relations
            if getattr(r, "relation_kind", None) == "pr_file"
        )
    out: dict[EntityRef, list[Any]] = {}
    for rel in rel_iter:
        src = getattr(rel, "source", None)
        if src is None:
            continue
        out.setdefault(src, []).append(rel)
    return out


__all__ = ["IssuePRClassifierMetric"]
