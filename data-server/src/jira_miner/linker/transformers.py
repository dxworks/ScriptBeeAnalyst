from src.common.models import IssueStatusCategory, IssueStatus, IssueType, Issue, JiraUser, JiraProject
from src.jira_miner.reader_dto.models import JsonFileFormatJira


class JiraProjectTransformer:
    def __init__(self, jira_data: JsonFileFormatJira, name : str = "Jira Project"):
        self.jira_data = jira_data
        self.name = name

    def transform(self) -> JiraProject:
        project = JiraProject(name=self.name)

        for status in self.jira_data.issueStatuses:
            category = IssueStatusCategory(
                key=status.statusCategory.key,
                name=status.statusCategory.name,
            )
            category = project.issue_status_category_registry.add(category)

            issue_status = IssueStatus(
                id=status.id,
                name=status.name,
                issue_status_categories=category,
            )
            issue_status = project.issue_status_registry.add(issue_status)

            # link status <-> category
            if issue_status not in category.issue_statuses:
                category.issue_statuses.append(issue_status)

        for issue_type in self.jira_data.issueTypes:
            it = IssueType(
                id=issue_type.id,
                name=issue_type.name,
                description=issue_type.description,
                isSubTask=issue_type.isSubTask,
            )
            project.issue_type_registry.add(it)

        for user in self.jira_data.users:
            jira_user = JiraUser(
                key=user.key,
                name=user.name,
                link=user.self_,
            )
            project.jira_user_registry.add(jira_user)

        for issue in self.jira_data.issues:
            i = Issue(
                id=issue.id,
                key=issue.key,
                summary=issue.summary,
                createdAt=issue.created,
                updatedAt=issue.updated,
            )
            i = project.issue_registry.add(i)

            issue_status = project.issue_status_registry.get_by_id(issue.status.id)
            if issue_status:
                if i not in issue_status.issues:
                    issue_status.issues.append(i)
                if issue_status not in i.issue_statuses:
                    i.issue_statuses.append(issue_status)

            issue_type = project.issue_type_registry.get_by_id(issue.issueType)
            if issue_type:
                if i not in issue_type.issues:
                    issue_type.issues.append(i)
                if issue_type not in i.issue_types:
                    i.issue_types.append(issue_type)

            reporter = project.jira_user_registry.get_by_id(issue.reporterId)
            if reporter:
                if i not in reporter.issues_as_reporter:
                    reporter.issues_as_reporter.append(i)
                i.reporter = reporter

            if issue.creatorId is not None:
                creator = project.jira_user_registry.get_by_id(issue.creatorId)
                if creator:
                    if i not in creator.issues_as_creator:
                        creator.issues_as_creator.append(i)
                    i.creator = creator

            if issue.assigneeId is not None:
                assignee = project.jira_user_registry.get_by_id(issue.assigneeId)
                if assignee:
                    if i not in assignee.issues_as_assignee:
                        assignee.issues_as_assignee.append(i)
                    if assignee not in i.jira_users_as_assignee:
                        i.jira_users_as_assignee.append(assignee)

        for jira_issue in self.jira_data.issues:
            current_issue = project.issue_registry.get_by_id(jira_issue.key)
            if not current_issue:
                print(f"Issue {jira_issue.key} not found in graph")
                continue

            if jira_issue.parent is not None:
                parent_issue = project.issue_registry.get_by_id(jira_issue.parent)
                if parent_issue:
                    current_issue.parent = parent_issue
                    if current_issue not in parent_issue.children:
                        parent_issue.children.append(current_issue)

        return project
