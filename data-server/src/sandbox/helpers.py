"""Sandbox-side helper functions for cross-entity navigation.

Plan §11 rows 4–6 spell out three legacy navigation patterns that v2
no longer exposes as entity-side methods (the v2 domain models are
sealed — no instance methods reach back into the Graph):

* ``commit.issues``           → ``commit_issues(commit, graph_data)``
* ``pr.git_commits``          → ``pr_commits(pr, graph_data)``
* ``issue.git_commits``       → ``issue_commits(issue, graph_data)``

These three free helpers wrap the v2 read shapes. They take the entity
plus the :class:`MCPSandboxView` (or the bare :class:`Graph` — both
work because the view's read-through ``__getattr__`` proxies every
typed registry attribute the helpers touch) and return concrete entity
lists.

Why free functions, not methods on the entities?
------------------------------------------------

v2 domain models are pure Pydantic data — they carry their own fields
plus :class:`EntityRef` cross-refs, but nothing that reaches back into
the :class:`Graph`. Adding "give me my related issues" as a method on
:class:`~src.common.domains.git.models.Commit` would couple the
domain layer to the enrichment / relations layer, which the kernel
architecture explicitly forbids. So these helpers live in the sandbox
package instead, alongside :class:`MCPSandboxView`.

Derivation strategy
-------------------

* ``commit_issues``: re-runs the same issue-key regex that
  :class:`~src.enrichment.relations.implementations.issue_file.IssueFileBuilder`
  uses (commit-message regex against every known issue key). This is
  the v2 substitute for the legacy ``ProjectLinker``-populated
  ``commit.issues`` list — bare ``issue ↔ commit`` is intentionally
  not a separate first-class relation in v2 (Chunk-7 handoff Design
  Choice §3). Same semantic, no per-call extra graph state.
* ``pr_commits``: walks ``pr.commit_refs`` → :class:`GitHubCommit`
  (via ``graph.github_commits.get(...)``) → ``.sha`` → git
  :class:`Commit` (via :class:`CommitRegistry.by_sha`). The bare-SHA
  index keeps this join repo-agnostic now that git Commit ids are
  repo-scoped (``{repo}:{sha}``). Same join the
  :class:`~src.enrichment.relations.implementations.pr_file.PrFileBuilder`
  performs internally.
* ``issue_commits``: re-runs the commit-message regex against this
  one issue's ``key`` (inverse of ``commit_issues``).

All three return concrete entity lists — empty when the join produces
nothing OR when the required side is missing (e.g. no jira project
loaded → ``commit_issues`` returns ``[]``).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Iterable, Union

if TYPE_CHECKING:
    from src.common.domains.git.models import Commit
    from src.common.domains.github.models import PullRequest
    from src.common.domains.jira.models import Issue
    from src.common.kernel import Graph
    from src.sandbox.inject import MCPSandboxView


# Either the typed Graph itself or the sandbox view (which proxies
# every needed attribute via __getattr__ — see inject.py).
GraphLike = Union["Graph", "MCPSandboxView"]


def commit_issues(commit: "Commit", graph_data: GraphLike) -> list["Issue"]:
    """Resolve the JIRA issues mentioned in ``commit.message``.

    Replaces the legacy ``commit.issues`` property (which the legacy
    ``ProjectLinker`` populated by scanning commit messages for issue
    keys). The v2 derivation matches the
    :class:`~src.enrichment.relations.implementations.issue_file.IssueFileBuilder`
    semantics — case-insensitive ``\\b<key>\\b`` match against every
    known issue key in the loaded graph.

    Returns an empty list when no jira side is loaded or no key
    matches. Order is the iteration order over the issue registry
    (insertion order); duplicates are collapsed.
    """
    issue_registry = getattr(graph_data, "issues", None)
    if issue_registry is None:
        return []
    issues_iter: Iterable["Issue"] = issue_registry.all()  # type: ignore[union-attr]
    issue_by_key: dict[str, "Issue"] = {}
    for issue in issues_iter:
        key = getattr(issue, "key", None)
        if key:
            issue_by_key[key.upper()] = issue
    if not issue_by_key:
        return []

    message = getattr(commit, "message", "") or ""
    if not message:
        return []

    pattern = _issue_key_pattern(issue_by_key.keys())
    if pattern is None:
        return []

    matches = pattern.findall(message)
    if not matches:
        return []

    out: list["Issue"] = []
    seen: set[str] = set()
    for match in matches:
        key_upper = match.upper()
        if key_upper in seen:
            continue
        seen.add(key_upper)
        issue = issue_by_key.get(key_upper)
        if issue is not None:
            out.append(issue)
    return out


def pr_commits(pr: "PullRequest", graph_data: GraphLike) -> list["Commit"]:
    """Resolve the git :class:`Commit` instances belonging to ``pr``.

    Walks ``pr.commit_refs`` (the typed list of
    :class:`~src.common.kernel.EntityRef` to
    :class:`~src.common.domains.github.models.GitHubCommit` records),
    pulls the GitHubCommit via ``graph.github_commits.get(ref.id)``,
    extracts ``gh_commit.sha``, and resolves the matching git
    :class:`Commit` via ``graph.commits.get(sha)`` (since git commit
    ids in v2 ARE the sha — see Chunk-4 design docs).

    Returns an empty list when no commits resolve (e.g. PR not
    associated with any commits yet, or the git side is not loaded).
    Order is ``commit_refs`` order; duplicates skipped.
    """
    github_commits = getattr(graph_data, "github_commits", None)
    commits = getattr(graph_data, "commits", None)
    if github_commits is None or commits is None:
        return []

    # Post-F1: git Commit.id is repo-scoped (``{repo}:{sha}``) so a
    # bare-SHA lookup goes through CommitRegistry.by_sha (multi-valued
    # because two repos can carry the same subtree SHA). For a typical
    # one-repo project the inner list has one element; multi-repo
    # projects emit one git Commit per repo for the same SHA.
    sha_index = getattr(commits, "by_sha", None)

    refs = getattr(pr, "commit_refs", None) or []
    out: list["Commit"] = []
    seen_ids: set[str] = set()
    for ref in refs:
        gh_commit = github_commits.get(ref.id)
        if gh_commit is None:
            continue
        sha = getattr(gh_commit, "sha", None)
        if not sha:
            continue
        if sha_index is not None:
            matches = sha_index.get(sha, ())
        else:
            # Defensive fallback: tolerate registries that haven't been
            # rebuilt with the by_sha index yet (e.g. legacy pickles).
            matches = ()
        for git_commit in matches:
            if git_commit.id in seen_ids:
                continue
            seen_ids.add(git_commit.id)
            out.append(git_commit)
    return out


def issue_commits(issue: "Issue", graph_data: GraphLike) -> list["Commit"]:
    """Resolve the git :class:`Commit` instances mentioning ``issue``.

    Re-derivation of the legacy ``issue.git_commits`` list: scans
    every commit message in the loaded graph for ``\\b<issue.key>\\b``
    (case-insensitive). Mirrors :func:`commit_issues` from the other
    side and matches the
    :class:`~src.enrichment.relations.implementations.issue_file.IssueFileBuilder`
    semantics.

    Returns an empty list when ``issue.key`` is missing or no commits
    are loaded. Order is the iteration order over the commit registry.
    """
    commits_registry = getattr(graph_data, "commits", None)
    if commits_registry is None:
        return []

    key = getattr(issue, "key", None)
    if not key:
        return []
    escaped = re.escape(key)
    pattern = re.compile(rf"\b{escaped}\b", re.IGNORECASE)

    out: list["Commit"] = []
    for commit in commits_registry.all():
        message = getattr(commit, "message", "") or ""
        if pattern.search(message):
            out.append(commit)
    return out


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------
def _issue_key_pattern(keys: Iterable[str]) -> "re.Pattern[str] | None":
    """Compile ``\\b(KEY1|KEY2|...)\\b`` case-insensitive, or ``None``
    if no non-empty keys remain.

    Mirrors
    :func:`src.enrichment.relations.implementations.issue_file._build_issue_pattern`
    — kept duplicated rather than imported so the sandbox package
    doesn't reach into builder internals (the helper is a private
    function on the builder module).
    """
    escaped = [re.escape(k) for k in keys if k]
    if not escaped:
        return None
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


__all__ = ["commit_issues", "issue_commits", "pr_commits"]
