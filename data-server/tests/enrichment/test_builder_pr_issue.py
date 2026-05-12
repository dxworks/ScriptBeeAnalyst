"""Substantive port test for :class:`PrIssueBuilder` (review round-2 blocking #3).

Synthetic mini-graph: 2 PRs, 2 issues. Verifies the two-pass linking:

* Pass 1 — PR title/body regex match against known issue keys.
* Pass 2 — JIRA-transition mention of ``"Pull Request #N"`` matches via
  the ``PullRequestRegistry.by_number`` index.

A pair seen by BOTH passes aggregates to ``strength == 2.0`` (a v2
enrichment over the legacy boolean "linked").
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from src.common.domains.github import (
    PullRequest,
    PullRequestRegistry,
)
from src.common.domains.jira import (
    Issue,
    IssueRegistry,
    IssueStatus,
    IssueStatusRegistry,
    IssueTransition,
    IssueType,
    IssueTypeRegistry,
    TransitionItem,
)
from src.common.kernel import EntityKind, EntityRef
from src.enrichment.relations import RelationRegistry, WindowKind
from src.enrichment.relations.implementations.pr_issue import PrIssueBuilder
from src.enrichment.tags import ClassifierRegistry, TraitRegistry


_PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id="proj")


@dataclass
class _Host:
    pull_requests: PullRequestRegistry
    issues: IssueRegistry
    issue_statuses: IssueStatusRegistry
    issue_types: IssueTypeRegistry
    relations: RelationRegistry
    traits: TraitRegistry
    classifiers: ClassifierRegistry
    recent_cutoff: Optional[datetime] = None


def _make_status() -> IssueStatus:
    return IssueStatus(
        id="open",
        project_ref=_PROJECT_REF,
        name="Open",
        category="indeterminate",
    )


def _make_type() -> IssueType:
    return IssueType(
        id="bug",
        project_ref=_PROJECT_REF,
        name="Bug",
    )


def _make_issue(
    key: str,
    *,
    summary: str = "",
    transitions: Optional[list[IssueTransition]] = None,
    status: Optional[IssueStatus] = None,
    type_: Optional[IssueType] = None,
) -> Issue:
    s = status or _make_status()
    t = type_ or _make_type()
    return Issue(
        id=key,
        project_ref=_PROJECT_REF,
        key=key,
        summary=summary,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        status_ref=s.ref(),
        type_ref=t.ref(),
        transitions=transitions or [],
    )


def _make_pr(number: int, *, title: str = "", body: str = "") -> PullRequest:
    return PullRequest(
        id=PullRequest.make_id(number),
        project_ref=_PROJECT_REF,
        number=number,
        title=title,
        body=body,
        state="open",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def host() -> _Host:
    return _Host(
        pull_requests=PullRequestRegistry(),
        issues=IssueRegistry(),
        issue_statuses=IssueStatusRegistry(),
        issue_types=IssueTypeRegistry(),
        relations=RelationRegistry(),
        traits=TraitRegistry(),
        classifiers=ClassifierRegistry(),
    )


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_pr_issue_pass1_text_regex_link(host: _Host) -> None:
    """PR body contains an issue key → one PR↔Issue edge with strength=1.0."""
    status = _make_status()
    type_ = _make_type()
    host.issue_statuses.add(status)
    host.issue_types.add(type_)
    issue = _make_issue("PROJ-123", status=status, type_=type_)
    host.issues.add(issue)

    pr = _make_pr(7, title="Fix bug", body="See PROJ-123 for context.")
    host.pull_requests.add(pr)

    relations = list(PrIssueBuilder().build(host))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.relation_kind == "pr_issue"
    assert rel.source == pr.ref()
    assert rel.target == issue.ref()
    assert rel.window == WindowKind.LIFETIME
    assert rel.strength == 1.0


def test_pr_issue_case_insensitive_and_word_boundary(host: _Host) -> None:
    """Regex is case-insensitive with word boundaries (matches legacy)."""
    status = _make_status()
    type_ = _make_type()
    host.issue_statuses.add(status)
    host.issue_types.add(type_)
    issue = _make_issue("PROJ-42", status=status, type_=type_)
    host.issues.add(issue)
    # Lowercase + at end-of-string position both pass the \b match.
    pr = _make_pr(1, title="", body="fix for proj-42")
    host.pull_requests.add(pr)

    relations = list(PrIssueBuilder().build(host))
    assert len(relations) == 1
    assert relations[0].strength == 1.0


def test_pr_issue_skips_unknown_keys(host: _Host) -> None:
    """A keylike string that doesn't match any registered issue → no edge."""
    status = _make_status()
    type_ = _make_type()
    host.issue_statuses.add(status)
    host.issue_types.add(type_)
    issue = _make_issue("PROJ-1", status=status, type_=type_)
    host.issues.add(issue)
    pr = _make_pr(1, body="See OTHER-99 — not in this project.")
    host.pull_requests.add(pr)

    relations = list(PrIssueBuilder().build(host))
    assert relations == []


def test_pr_issue_pass2_jira_transition_mention(host: _Host) -> None:
    """JIRA transition with ``Pull Request #N`` matches via by_number index."""
    status = _make_status()
    type_ = _make_type()
    host.issue_statuses.add(status)
    host.issue_types.add(type_)

    transition = IssueTransition(
        id=1,
        created=datetime(2024, 6, 1, tzinfo=timezone.utc),
        items=[
            TransitionItem(
                field="status",
                to_string="Pull Request #42 created",
            )
        ],
    )
    issue = _make_issue(
        "PROJ-1",
        transitions=[transition],
        status=status,
        type_=type_,
    )
    host.issues.add(issue)

    # The matching PR — title/body intentionally contain NO mention of
    # PROJ-1 so we know the edge came from the transition pass alone.
    pr = _make_pr(42, title="Unrelated title", body="No keys here.")
    host.pull_requests.add(pr)

    relations = list(PrIssueBuilder().build(host))
    assert len(relations) == 1
    rel = relations[0]
    assert rel.source == pr.ref()
    assert rel.target == issue.ref()
    assert rel.strength == 1.0


def test_pr_issue_both_passes_aggregate_to_strength_2(host: _Host) -> None:
    """A pair found by BOTH passes gets ``strength=2.0``."""
    status = _make_status()
    type_ = _make_type()
    host.issue_statuses.add(status)
    host.issue_types.add(type_)

    transition = IssueTransition(
        id=1,
        created=datetime(2024, 6, 1, tzinfo=timezone.utc),
        items=[
            TransitionItem(
                field="status",
                to_string="Pull Request #7 opened",
            )
        ],
    )
    issue = _make_issue(
        "PROJ-1",
        transitions=[transition],
        status=status,
        type_=type_,
    )
    host.issues.add(issue)

    # Pass 1 will find PROJ-1 in the body; Pass 2 will find PR #7 in
    # the transition. Both contribute weight 1.0.
    pr = _make_pr(7, title="Fix", body="See PROJ-1.")
    host.pull_requests.add(pr)

    relations = list(PrIssueBuilder().build(host))
    assert len(relations) == 1
    assert relations[0].strength == 2.0


def test_pr_issue_canonical_id_is_stable(host: _Host) -> None:
    """Re-running the builder twice yields the same canonical id."""
    status = _make_status()
    type_ = _make_type()
    host.issue_statuses.add(status)
    host.issue_types.add(type_)
    issue = _make_issue("PROJ-1", status=status, type_=type_)
    host.issues.add(issue)
    pr = _make_pr(1, body="Fix PROJ-1.")
    host.pull_requests.add(pr)

    builder = PrIssueBuilder()
    ids_first = sorted(r.id for r in builder.build(host))
    ids_second = sorted(r.id for r in builder.build(host))
    assert ids_first == ids_second


def test_pr_issue_handles_empty_host_gracefully(host: _Host) -> None:
    """No PRs or no issues → empty output, no exception."""
    relations = list(PrIssueBuilder().build(host))
    assert relations == []


def test_pr_issue_intra_pr_dedup(host: _Host) -> None:
    """A PR mentioning the same issue twice counts once (set-dedup)."""
    status = _make_status()
    type_ = _make_type()
    host.issue_statuses.add(status)
    host.issue_types.add(type_)
    issue = _make_issue("PROJ-1", status=status, type_=type_)
    host.issues.add(issue)
    pr = _make_pr(1, title="PROJ-1", body="PROJ-1 again. PROJ-1 once more.")
    host.pull_requests.add(pr)

    relations = list(PrIssueBuilder().build(host))
    assert len(relations) == 1
    assert relations[0].strength == 1.0  # one PR, one issue, one weight
