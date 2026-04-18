export interface SourceIdentityDto {
  source: 'git' | 'github' | 'jira';
  source_key: string;
  name: string | null;
  email: string | null;
  login: string | null;
}

export interface SuggestionDto {
  suggestion_id: string;
  default_name: string;
  default_email: string;
  confidence: number;
  total_identities: number;
  identities: SourceIdentityDto[];
}

export interface SuggestionIdentitiesPage {
  suggestion_id: string;
  total_identities: number;
  offset: number;
  limit: number;
  identities: SourceIdentityDto[];
}

export const OVERSIZE_CLUSTER_WARNING_THRESHOLD = 200;

export interface SuggestionsResponse {
  suggestions: SuggestionDto[];
  total_identities: number;
  existing_users: number;
}

export interface UnifiedUserDto {
  id: string;
  display_name: string;
  primary_email: string | null;
  identities: SourceIdentityDto[];
  stats: {
    commit_count: number;
    issue_count: number;
    pr_count: number;
  };
}

export interface UnifiedUsersResponse {
  users: UnifiedUserDto[];
  total: number;
}

export interface ApplySuggestionRequest {
  suggestion_id: string;
  selected_identity_keys: string[];
  unselected_identity_keys: string[];
  name: string;
  email: string;
}

export interface RejectSuggestionRequest {
  identity_keys: string[];
}

export function getIdentityKey(identity: SourceIdentityDto): string {
  return `${identity.source}:${identity.source_key}`;
}

export function getSourceLabel(source: string): string {
  switch (source) {
    case 'git': return 'Git';
    case 'github': return 'GitHub';
    case 'jira': return 'JIRA';
    default: return source;
  }
}

export function getConfidenceLevel(confidence: number): 'high' | 'medium' | 'low' {
  if (confidence >= 0.8) return 'high';
  if (confidence >= 0.5) return 'medium';
  return 'low';
}

export function getConfidenceLabel(confidence: number): string {
  const level = getConfidenceLevel(confidence);
  switch (level) {
    case 'high': return 'High';
    case 'medium': return 'Medium';
    case 'low': return 'Low';
  }
}
