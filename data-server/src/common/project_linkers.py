from __future__ import annotations

import re

from src.common.models import Project, GitProject, JiraProject, GitHubProject
from src.jira_miner.reader_dto.models import JsonFileFormatJira
from src.logger import get_logger

LOG = get_logger(__name__)


def _get_or_add(container: list, element):
    if element not in container:
        container.append(element)
    return element


def _build_issue_pattern(jira_project: JiraProject):
    """Build a compiled regex matching any issue key from the project."""
    issue_keys = [re.escape(issue.key) for issue in jira_project.issue_registry.all]
    if not issue_keys:
        return None
    return re.compile(r'\b(' + '|'.join(issue_keys) + r')\b', re.IGNORECASE)


class ProjectLinker:
    @classmethod
    def link_projects(cls, p1: Project, p2: Project, additional_data: JsonFileFormatJira = None) -> None:
        jira = next((p for p in (p1, p2) if isinstance(p, JiraProject)), None)
        git = next((p for p in (p1, p2) if isinstance(p, GitProject)), None)
        github = next((p for p in (p1, p2) if isinstance(p, GitHubProject)), None)

        if jira and git:
            cls.link_issues_with_git_commits(jira, git)
            jira.link(git)
        elif jira and github:
            cls.link_pull_requests_with_issues(jira, github, additional_data)
            jira.link(github)
        elif github and git:
            cls.link_pull_requests_with_git_commits(github, git)
            github.link(git)
        else:
            LOG.warning(f"Unhandled linking case: {type(p1).__name__} <-> {type(p2).__name__}")

    @classmethod
    def link_issues_with_git_commits(cls, jira_project: JiraProject, git_project: GitProject) -> None:
        """Link Jira issues to Git commits based on commit messages."""
        issue_pattern = _build_issue_pattern(jira_project)
        if issue_pattern is None:
            return

        links = 0
        commits_linked_with_issues = 0

        for commit in git_project.git_commit_registry.all:
            if not commit.message:
                continue

            matches = issue_pattern.findall(commit.message)

            if matches:
                commits_linked_with_issues += 1

            for match in set(matches):
                issue = jira_project.issue_registry.get_by_id(match.upper())
                if not issue:
                    continue
                _get_or_add(issue.git_commits, commit)
                _get_or_add(commit.issues, issue)

                links += 1

        LOG.debug(f"[Linker] Linked {links} Issue–Commit edges")
        LOG.debug(f"[Linker] {commits_linked_with_issues} commits associated with issues")

    @classmethod
    def link_pull_requests_with_issues(cls, jira_project: JiraProject, gh_project: GitHubProject, jira_data: JsonFileFormatJira) -> None:
        issue_pattern = _build_issue_pattern(jira_project)
        if issue_pattern is None:
            return

        prs_with_issues = 0

        # Pass 1: Link via PR title/body
        for pr in gh_project.pull_request_registry.all:
            text = (pr.title or "") + " " + (pr.body or "")
            matches = issue_pattern.findall(text)

            if matches:
                prs_with_issues += 1

            for match in set(matches):
                issue = jira_project.issue_registry.get_by_id(match.upper())
                if not issue:
                    continue
                _get_or_add(pr.issues, issue)
                _get_or_add(issue.pull_requests, pr)

        def extract_pr_number(text: str) -> int | None:
            match = re.search(r'#(\d+)', text)
            if match:
                return int(match.group(1))
            return None

        for issue in jira_data.issues:
            issues_pr_links = set()
            for change in issue.changes:
                for item in change.items:
                    if item.toString and "Pull Request #" in item.toString:
                        issues_pr_links.add(item.toString)
            if len(issues_pr_links) > 0:
                i = jira_project.issue_registry.get_by_id(issue.key)
                for link in issues_pr_links:
                    for pr in gh_project.pull_request_registry.all:
                        if extract_pr_number(link) == pr.number:
                            _get_or_add(i.pull_requests, pr)
                            _get_or_add(pr.issues, i)
                            break

        LOG.debug(f"[Linker] {prs_with_issues} PRs associated with issues")

    @classmethod
    def link_pull_requests_with_git_commits(cls, gh_project: GitHubProject, git_project: GitProject) -> None:
        direct_links = 0

        for pr in gh_project.pull_request_registry.all:
            for pr_commit in pr.git_hub_commits:
                git_commit = git_project.git_commit_registry.get_by_id(pr_commit.id)
                if not git_commit:
                    continue

                _get_or_add(git_commit.pull_requests, pr)
                _get_or_add(pr.git_commits, git_commit)

                direct_links += 1

        LOG.debug(f"[Linker] Direct PR–Commit links: {direct_links}")
