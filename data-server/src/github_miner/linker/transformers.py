from src.github_miner import JsonFileFormatGithub
from src.common.models import GitHubUser, PullRequest, GitHubCommit, GitHubProject, Review, ReviewComment


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

            for review_dto in pr.reviews:
                review_user = None
                if review_dto.user:
                    review_user = GitHubUser(
                        url=review_dto.user.url,
                        login=review_dto.user.login,
                        name=review_dto.user.name,
                    )
                    review_user = project.git_hub_user_registry.add(review_user)

                review = Review(
                    state=review_dto.state,
                    submittedAt=review_dto.submittedAt,
                    body=review_dto.body or "",
                    user=review_user,
                )
                pull_request.reviews.append(review)

                for rc_dto in review_dto.comments:
                    rc_author = None
                    if rc_dto.author:
                        rc_author = GitHubUser(
                            url=rc_dto.author.url,
                            login=rc_dto.author.login,
                            name=rc_dto.author.name,
                        )
                        rc_author = project.git_hub_user_registry.add(rc_author)

                    review_comment = ReviewComment(
                        url=rc_dto.url,
                        body=rc_dto.body,
                        createdAt=rc_dto.createdAt,
                        updatedAt=rc_dto.updatedAt,
                        author=rc_author,
                    )
                    pull_request.reviewComments.append(review_comment)

            for rr_dto in pr.reviewRequests:
                requested = GitHubUser(
                    url=rr_dto.requestedReviewer.url,
                    login=rr_dto.requestedReviewer.login,
                    name=rr_dto.requestedReviewer.name,
                )
                requested = project.git_hub_user_registry.add(requested)
                if requested not in pull_request.requestedReviewers:
                    pull_request.requestedReviewers.append(requested)

        return project
