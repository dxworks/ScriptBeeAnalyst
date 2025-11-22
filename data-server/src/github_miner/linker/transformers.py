from src.github_miner import JsonFileFormatGithub
from src.common.models import GitHubUser, PullRequest, GitHubCommit, GitHubProject


class GitHubProjectTransformer:
    def __init__(self, git_hub_data: JsonFileFormatGithub, name: str = "GitHub Project"):
        self.git_hub_data = git_hub_data
        self.name = name

    def transform(self) -> GitHubProject:
        project = GitHubProject(name=self.name)

        for pr in self.git_hub_data.pullRequests:
            pull_request = PullRequest(
                number=pr.number,
                title=pr.title,
                state=pr.state,
                changedFiles=pr.changedFiles,
                createdAt=pr.createdAt,
                updatedAt=pr.updatedAt,
                body=pr.body,
                mergedAt=pr.mergedAt,
                closedAt=pr.closedAt,
            )
            pull_request = project.pull_request_registry.add(pull_request)

            for assignee in pr.assignees:
                assignee_user = GitHubUser(
                    url=assignee.url,
                    login=assignee.login,
                    name=assignee.name,
                )
                assignee_user = project.git_hub_user_registry.add(assignee_user)

                if assignee_user not in pull_request.assignees:
                    pull_request.assignees.append(assignee_user)
                if pull_request not in assignee_user.pull_requests_as_assignee:
                    assignee_user.pull_requests_as_assignee.append(pull_request)

            if pr.createdBy:
                creator_user = GitHubUser(
                    url=pr.createdBy.url,
                    login=pr.createdBy.login,
                    name=pr.createdBy.name,
                )
                creator_user = project.git_hub_user_registry.add(creator_user)

                pull_request.createdBy = creator_user
                if pull_request not in creator_user.pull_requests_as_creator:
                    creator_user.pull_requests_as_creator.append(pull_request)

            if pr.mergedBy:
                merger_user = GitHubUser(
                    url=pr.mergedBy.url,
                    login=pr.mergedBy.login,
                    name=pr.mergedBy.name,
                )
                merger_user = project.git_hub_user_registry.add(merger_user)

                pull_request.mergedBy = merger_user
                if pull_request not in merger_user.pull_requests_as_merged_by:
                    merger_user.pull_requests_as_merged_by.append(pull_request)

            for c in pr.commits:
                commit = GitHubCommit(
                    id=c.sha,
                    date=c.date,
                    message=c.message,
                    changedFiles=c.changedFiles,
                )
                commit = project.git_hub_commit_registry.add(commit)

                if commit not in pull_request.git_hub_commits:
                    pull_request.git_hub_commits.append(commit)
                if pull_request not in commit.pull_requests:
                    commit.pull_requests.append(pull_request)

        return project
