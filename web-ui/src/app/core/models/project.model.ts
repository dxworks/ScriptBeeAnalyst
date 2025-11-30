export interface Project {
  id: string;
  name: string;
  description: string | null;
  created_at: string;
  updated_at: string;
  user_id: string;
  status: ProjectStatus;
  has_git: boolean;
  has_github: boolean;
  has_jira: boolean;
}

export type ProjectStatus = 'draft' | 'processing' | 'ready' | 'idle' | 'resuming' | 'error';

export interface CreateProjectDto {
  name: string;
  description?: string;
}

export interface UpdateProjectDto {
  name?: string;
  description?: string;
}
