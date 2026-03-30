export interface EntityField {
  name: string;
  type: string;
  category: 'field' | 'relationship' | 'property';
  crossProject?: boolean;
}

export interface EntityDefinition {
  name: string;
  registry: string;
  project: 'git' | 'jira' | 'github';
  fields: EntityField[];
}

export const ENTITY_DEFINITIONS: Record<string, EntityDefinition> = {
  GitAccount: {
    name: 'GitAccount',
    registry: 'account_registry',
    project: 'git',
    fields: [
      { name: 'git_id', type: 'GitAccountId', category: 'field' },
      { name: 'name', type: 'str', category: 'field' },
      { name: 'commits', type: 'List[GitCommit]', category: 'relationship' },
      { name: 'project', type: 'Optional[Project]', category: 'relationship' },
      { name: 'developer', type: 'Optional[Developer]', category: 'relationship' },
      { name: 'id', type: 'str', category: 'property' },
      { name: 'changes', type: 'List[Change]', category: 'property' },
      { name: 'files', type: 'List[File]', category: 'property' },
    ],
  },
  GitCommit: {
    name: 'GitCommit',
    registry: 'git_commit_registry',
    project: 'git',
    fields: [
      { name: 'id', type: 'str', category: 'field' },
      { name: 'message', type: 'str', category: 'field' },
      { name: 'author_date', type: 'datetime', category: 'field' },
      { name: 'committer_date', type: 'datetime', category: 'field' },
      { name: 'branch_id', type: 'int', category: 'field' },
      { name: 'repo_size', type: 'int', category: 'field' },
      { name: 'author', type: 'Optional[GitAccount]', category: 'relationship' },
      { name: 'committer', type: 'Optional[GitAccount]', category: 'relationship' },
      { name: 'parents', type: 'List[GitCommit]', category: 'relationship' },
      { name: 'children', type: 'List[GitCommit]', category: 'relationship' },
      { name: 'changes', type: 'List[Change]', category: 'relationship' },
      { name: 'issues', type: 'List[Issue]', category: 'relationship', crossProject: true },
      { name: 'pull_requests', type: 'List[PullRequest]', category: 'relationship', crossProject: true },
      { name: 'is_merge_commit', type: 'bool', category: 'property' },
      { name: 'is_split_commit', type: 'bool', category: 'property' },
    ],
  },
  Change: {
    name: 'Change',
    registry: 'change_registry',
    project: 'git',
    fields: [
      { name: 'id', type: 'str', category: 'field' },
      { name: 'change_type', type: 'ChangeType', category: 'field' },
      { name: 'old_file_name', type: 'str', category: 'field' },
      { name: 'new_file_name', type: 'str', category: 'field' },
      { name: 'compute_annotated_lines', type: 'bool', category: 'field' },
      { name: 'commit', type: 'Optional[GitCommit]', category: 'relationship' },
      { name: 'file', type: 'Optional[File]', category: 'relationship' },
      { name: 'parent_commit', type: 'Optional[GitCommit]', category: 'relationship' },
      { name: 'hunks', type: 'List[Hunk]', category: 'relationship' },
      { name: 'annotated_lines', type: 'List[GitCommit]', category: 'relationship' },
      { name: 'parent_change', type: 'Optional[Change]', category: 'relationship' },
      { name: 'line_changes', type: 'List[LineChange]', category: 'property' },
      { name: 'deleted_lines', type: 'List[LineChange]', category: 'property' },
      { name: 'added_lines', type: 'List[LineChange]', category: 'property' },
    ],
  },
  File: {
    name: 'File',
    registry: 'file_registry',
    project: 'git',
    fields: [
      { name: 'id', type: 'uuid.UUID', category: 'field' },
      { name: 'is_binary', type: 'bool', category: 'field' },
      { name: 'project', type: 'Optional[GitProject]', category: 'relationship' },
      { name: 'changes', type: 'List[Change]', category: 'relationship' },
      { name: 'is_alive()', type: 'bool', category: 'property' },
      { name: 'file_name()', type: 'str', category: 'property' },
      { name: 'relative_path()', type: 'str', category: 'property' },
      { name: 'full_path()', type: 'str', category: 'property' },
      { name: 'last_existing_name()', type: 'str', category: 'property' },
    ],
  },
  IssueStatusCategory: {
    name: 'IssueStatusCategory',
    registry: 'issue_status_category_registry',
    project: 'jira',
    fields: [
      { name: 'key', type: 'str', category: 'field' },
      { name: 'name', type: 'str', category: 'field' },
      { name: 'issue_statuses', type: 'List[IssueStatus]', category: 'relationship' },
    ],
  },
  IssueStatus: {
    name: 'IssueStatus',
    registry: 'issue_status_registry',
    project: 'jira',
    fields: [
      { name: 'id', type: 'str', category: 'field' },
      { name: 'name', type: 'str', category: 'field' },
      { name: 'issue_status_categories', type: 'IssueStatusCategory', category: 'relationship' },
      { name: 'issues', type: 'List[Issue]', category: 'relationship' },
    ],
  },
  IssueType: {
    name: 'IssueType',
    registry: 'issue_type_registry',
    project: 'jira',
    fields: [
      { name: 'id', type: 'str', category: 'field' },
      { name: 'name', type: 'str', category: 'field' },
      { name: 'description', type: 'str', category: 'field' },
      { name: 'isSubTask', type: 'bool', category: 'field' },
      { name: 'issues', type: 'List[Issue]', category: 'relationship' },
    ],
  },
  Issue: {
    name: 'Issue',
    registry: 'issue_registry',
    project: 'jira',
    fields: [
      { name: 'id', type: 'int', category: 'field' },
      { name: 'key', type: 'str', category: 'field' },
      { name: 'summary', type: 'str', category: 'field' },
      { name: 'createdAt', type: 'datetime', category: 'field' },
      { name: 'updatedAt', type: 'datetime', category: 'field' },
      { name: 'issue_statuses', type: 'List[IssueStatus]', category: 'relationship' },
      { name: 'issue_types', type: 'List[IssueType]', category: 'relationship' },
      { name: 'creator', type: 'Optional[JiraUser]', category: 'relationship' },
      { name: 'jira_users_as_assignee', type: 'List[JiraUser]', category: 'relationship' },
      { name: 'reporter', type: 'Optional[JiraUser]', category: 'relationship' },
      { name: 'parent', type: 'Optional[Issue]', category: 'relationship' },
      { name: 'children', type: 'List[Issue]', category: 'relationship' },
      { name: 'git_commits', type: 'List[GitCommit]', category: 'relationship', crossProject: true },
      { name: 'pull_requests', type: 'List[PullRequest]', category: 'relationship', crossProject: true },
    ],
  },
  JiraUser: {
    name: 'JiraUser',
    registry: 'jira_user_registry',
    project: 'jira',
    fields: [
      { name: 'key', type: 'str', category: 'field' },
      { name: 'name', type: 'str', category: 'field' },
      { name: 'link', type: 'str', category: 'field' },
      { name: 'issues_as_reporter', type: 'List[Issue]', category: 'relationship' },
      { name: 'issues_as_creator', type: 'List[Issue]', category: 'relationship' },
      { name: 'issues_as_assignee', type: 'List[Issue]', category: 'relationship' },
    ],
  },
  GitHubUser: {
    name: 'GitHubUser',
    registry: 'git_hub_user_registry',
    project: 'github',
    fields: [
      { name: 'url', type: 'str', category: 'field' },
      { name: 'login', type: 'Optional[str]', category: 'field' },
      { name: 'name', type: 'Optional[str]', category: 'field' },
      { name: 'pull_requests_as_creator', type: 'List[PullRequest]', category: 'relationship' },
      { name: 'pull_requests_as_merged_by', type: 'List[PullRequest]', category: 'relationship' },
      { name: 'pull_requests_as_assignee', type: 'List[PullRequest]', category: 'relationship' },
    ],
  },
  PullRequest: {
    name: 'PullRequest',
    registry: 'pull_request_registry',
    project: 'github',
    fields: [
      { name: 'number', type: 'int', category: 'field' },
      { name: 'title', type: 'str', category: 'field' },
      { name: 'state', type: 'str', category: 'field' },
      { name: 'changedFiles', type: 'int', category: 'field' },
      { name: 'body', type: 'str', category: 'field' },
      { name: 'createdAt', type: 'datetime', category: 'field' },
      { name: 'mergedAt', type: 'Optional[datetime]', category: 'field' },
      { name: 'closedAt', type: 'Optional[datetime]', category: 'field' },
      { name: 'updatedAt', type: 'Optional[datetime]', category: 'field' },
      { name: 'createdBy', type: 'Optional[GitHubUser]', category: 'relationship' },
      { name: 'assignees', type: 'List[GitHubUser]', category: 'relationship' },
      { name: 'mergedBy', type: 'Optional[GitHubUser]', category: 'relationship' },
      { name: 'git_hub_commits', type: 'List[GitHubCommit]', category: 'relationship' },
      { name: 'issues', type: 'List[Issue]', category: 'relationship', crossProject: true },
      { name: 'git_commits', type: 'List[GitCommit]', category: 'relationship', crossProject: true },
    ],
  },
  GitHubCommit: {
    name: 'GitHubCommit',
    registry: 'git_hub_commit_registry',
    project: 'github',
    fields: [
      { name: 'id', type: 'str', category: 'field' },
      { name: 'date', type: 'datetime', category: 'field' },
      { name: 'message', type: 'str', category: 'field' },
      { name: 'changedFiles', type: 'int', category: 'field' },
      { name: 'pull_requests', type: 'List[PullRequest]', category: 'relationship' },
    ],
  },
};
