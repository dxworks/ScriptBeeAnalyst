import re
from src.common.models import Project, GitProject, JiraProject, GitHubProject
from src.jira_miner.reader_dto.models import JsonFileFormatJira
from src.logger import get_logger

LOG = get_logger(__name__)

def get_or_add(container: list, element):
    if element not in container:
        container.append(element)
    return element

class ProjectLinker:
    @classmethod
    def link_projects(cls, p1: Project, p2: Project, additional_data:JsonFileFormatJira = None) -> None:
        if isinstance(p1, JiraProject) and isinstance(p2, GitProject):
            cls.link_issues_with_git_commits(p1, p2)
            p1.link(p2)
        elif isinstance(p2, JiraProject) and isinstance(p1, GitProject):
            cls.link_issues_with_git_commits(p2, p1)
            p2.link(p1)
        elif isinstance(p1, JiraProject) and isinstance(p2, GitHubProject):
            cls.link_pull_requests_with_issues(p1, p2, additional_data)
            p1.link(p2)
        elif isinstance(p2, JiraProject) and isinstance(p1, GitHubProject):
            cls.link_pull_requests_with_issues(p2, p1, additional_data)
            p2.link(p1)
        elif isinstance(p1, GitHubProject) and isinstance(p2, GitProject):
            cls.link_pull_requests_with_git_commits(p1, p2)
            p1.link(p2)
        elif isinstance(p2, GitHubProject) and isinstance(p1, GitProject):
            cls.link_pull_requests_with_git_commits(p2, p1)
            p2.link(p1)
        else:
            print(f"[Linker] Unhandled linking case: {type(p1)} ↔ {type(p2)}")

    @classmethod
    def link_issues_with_git_commits(cls, jira_project: JiraProject, git_project: GitProject) -> None:
        """Link Jira issues to Git commits based on commit messages."""
        issue_keys = [re.escape(issue.key) for issue in jira_project.issue_registry.all]
        if not issue_keys:
            return

        issue_pattern = re.compile(r'\b(' + '|'.join(issue_keys) + r')\b', re.IGNORECASE)

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
                get_or_add(issue.git_commits, commit)
                get_or_add(commit.issues, issue)

                links += 1

        LOG.debug(f"[Linker] Linked {links} Issue–Commit edges")
        LOG.debug(f"[Linker] {commits_linked_with_issues} commits associated with issues")

    @classmethod
    def link_pull_requests_with_issues(cls, jira_project: JiraProject, gh_project: GitHubProject, jira_data: JsonFileFormatJira) -> None:
        issue_keys = [re.escape(issue.key) for issue in jira_project.issue_registry.all]
        if not issue_keys:
            return

        issue_pattern = re.compile(r'\b(' + '|'.join(issue_keys) + r')\b', re.IGNORECASE)

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
                get_or_add(pr.issues, issue)
                get_or_add(issue.pull_requests, pr)

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
                            get_or_add(i.pull_requests, pr)
                            get_or_add(pr.issues, i)
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

                get_or_add(git_commit.pull_requests, pr)
                get_or_add(pr.git_commits, git_commit)

                direct_links += 1

        LOG.debug(f"[Linker] Direct PR–Commit links: {direct_links}")
