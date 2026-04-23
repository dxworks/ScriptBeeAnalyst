import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { AuthService } from './auth.service';
import { environment } from '../../../environments/environment';
import {
  SuggestionsResponse,
  SuggestionIdentitiesPage,
  ApplySuggestionRequest,
  RejectSuggestionRequest,
  UnifiedUserDto,
  UnifiedUsersResponse,
} from '../models/author-merge.model';

export interface BuildResult {
  success: boolean;
  message?: string;
  error?: string;
}

export interface LoadProjectResult {
  success: boolean;
  message?: string;
  project_id?: string;
  project_name?: string;
  stats?: {
    git_commits: number;
    jira_issues: number;
    github_prs: number;
  };
  error?: string;
}

export interface ScaffoldWorkspaceResult {
  success: boolean;
  path?: string;
  project_name?: string;
  folder_name?: string;
  error?: string;
}

export interface HealthResponse {
  status: string;
  loaded_projects: string[];
}

export interface CurrentProjectResponse {
  project_id: string;
  user_id: string;
  stats: {
    git_commits: number;
    jira_issues: number;
    github_prs: number;
  };
}

@Injectable({
  providedIn: 'root',
})
export class DataServerService {
  private readonly baseUrl = environment.dataServerUrl;

  constructor(
    private http: HttpClient,
    private authService: AuthService
  ) {}

  /**
   * Build project graph on data-server
   * @param projectId - UUID of the project
   * @returns BuildResult with success status and message
   */
  async buildProject(projectId: string): Promise<BuildResult> {
    const session = this.authService.session();
    if (!session?.access_token) {
      return {
        success: false,
        error: 'Not authenticated',
      };
    }

    const headers = new HttpHeaders({
      'Authorization': `Bearer ${session.access_token}`,
      'Content-Type': 'application/json',
    });

    try {
      const response = await firstValueFrom(
        this.http.post<{ message: string }>(
          `${this.baseUrl}/projects/${projectId}/build`,
          {},
          { headers }
        )
      );

      return {
        success: true,
        message: response.message,
      };
    } catch (err) {
      return this.handleError(err, 'build project');
    }
  }

  /**
   * Load project graph into data-server memory
   * @param projectId - UUID of the project
   * @returns LoadProjectResult with success status, stats, and message
   */
  async loadProject(projectId: string): Promise<LoadProjectResult> {
    try {
      const response = await firstValueFrom(
        this.http.post<{
          message: string;
          project_id: string;
          project_name: string;
          stats: {
            git_commits: number;
            jira_issues: number;
            github_prs: number;
          };
        }>(
          `${this.baseUrl}/projects/${projectId}/load`,
          {}
        )
      );

      return {
        success: true,
        message: response.message,
        project_id: response.project_id,
        project_name: response.project_name,
        stats: response.stats,
      };
    } catch (err) {
      return this.handleLoadError(err);
    }
  }

  /**
   * Unload project graph from data-server memory
   * @param projectId - UUID of the project
   * @returns true if successfully unloaded
   */
  async unloadProject(projectId: string): Promise<boolean> {
    const session = this.authService.session();
    if (!session?.access_token) {
      return false;
    }

    const headers = new HttpHeaders({
      'Authorization': `Bearer ${session.access_token}`,
    });

    try {
      await firstValueFrom(
        this.http.delete(
          `${this.baseUrl}/projects/${projectId}/unload`,
          { headers }
        )
      );
      return true;
    } catch (err) {
      console.error('Failed to unload project:', err);
      return false;
    }
  }

  /**
   * Scaffold AI agent workspace folder for a project (no auth required)
   * Creates directory structure with README under analyzed_projects/projects/
   * @param projectId - UUID of the project
   * @returns ScaffoldWorkspaceResult with workspace path
   */
  async scaffoldWorkspace(projectId: string): Promise<ScaffoldWorkspaceResult> {
    try {
      const response = await firstValueFrom(
        this.http.post<{
          path: string;
          project_name: string;
          folder_name: string;
        }>(
          `${this.baseUrl}/projects/${projectId}/scaffold-workspace`,
          {}
        )
      );

      return {
        success: true,
        path: response.path,
        project_name: response.project_name,
        folder_name: response.folder_name,
      };
    } catch (err) {
      console.error('Failed to scaffold workspace:', err);
      return {
        success: false,
        error: err instanceof HttpErrorResponse
          ? (err.error?.error || 'Failed to create workspace')
          : 'Unexpected error',
      };
    }
  }

  /**
   * Get health status and loaded projects (no auth required)
   * @returns HealthResponse with status and loaded projects list
   */
  async getHealth(): Promise<HealthResponse | null> {
    try {
      const response = await firstValueFrom(
        this.http.get<HealthResponse>(`${this.baseUrl}/health`)
      );
      return response;
    } catch (err) {
      console.error('Failed to get health status:', err);
      return null;
    }
  }

  /**
   * Get currently loaded project from data server (no auth required)
   * @returns CurrentProjectResponse or null if no project is loaded
   * @throws Error if server is unreachable or returns an error
   */
  async getCurrentProject(): Promise<CurrentProjectResponse | null> {
    try {
      const response = await firstValueFrom(
        this.http.get<CurrentProjectResponse>(`${this.baseUrl}/projects/current`)
      );
      return response;
    } catch (err) {
      // 404 means no project is loaded, which is not an error
      if (err instanceof HttpErrorResponse && err.status === 404) {
        return null;
      }
      // For connection errors or other issues, throw to let caller handle
      if (err instanceof HttpErrorResponse && err.status === 0) {
        throw new Error('Data server unreachable');
      }
      console.error('Failed to get current project:', err);
      throw err;
    }
  }

  private handleLoadError(err: unknown): LoadProjectResult {
    if (err instanceof HttpErrorResponse) {
      // Server-side error
      if (err.status === 0) {
        return {
          success: false,
          error: `Unable to connect to data server. Please ensure it's running on ${this.baseUrl}`,
        };
      }

      // Extract error message from response body
      const errorMessage = err.error?.error || err.error?.detail || err.error?.message || err.message;

      switch (err.status) {
        case 404:
          return {
            success: false,
            error: errorMessage || 'Project not found or pickle file missing',
          };
        case 400:
          return {
            success: false,
            error: errorMessage || 'Project is not ready for loading',
          };
        case 500:
          return {
            success: false,
            error: errorMessage || 'Server error while loading project',
          };
        default:
          return {
            success: false,
            error: errorMessage || 'Failed to load project',
          };
      }
    }

    // Client-side or network error
    console.error('Error loading project:', err);
    return {
      success: false,
      error: `Unexpected error: ${err instanceof Error ? err.message : 'Unknown error'}`,
    };
  }

  // ── Author Smart Merge Methods ──────────────────────────────────────────────

  async getSuggestions(projectId: string): Promise<SuggestionsResponse | null> {
    try {
      return await firstValueFrom(
        this.http.get<SuggestionsResponse>(
          `${this.baseUrl}/projects/${projectId}/authors/suggestions`
        )
      );
    } catch (err) {
      console.error('Failed to get suggestions:', err);
      return null;
    }
  }

  async getSuggestionIdentitiesPage(
    projectId: string,
    suggestionId: string,
    offset: number,
    limit: number
  ): Promise<SuggestionIdentitiesPage | null> {
    try {
      const params = `offset=${offset}&limit=${limit}`;
      return await firstValueFrom(
        this.http.get<SuggestionIdentitiesPage>(
          `${this.baseUrl}/projects/${projectId}/authors/suggestions/${suggestionId}/identities?${params}`
        )
      );
    } catch (err) {
      console.error('Failed to get suggestion identities page:', err);
      return null;
    }
  }

  async applySuggestion(projectId: string, request: ApplySuggestionRequest): Promise<UnifiedUserDto | null> {
    try {
      return await firstValueFrom(
        this.http.post<UnifiedUserDto>(
          `${this.baseUrl}/projects/${projectId}/authors/suggestions/apply`,
          request
        )
      );
    } catch (err) {
      console.error('Failed to apply suggestion:', err);
      return null;
    }
  }

  async rejectSuggestion(projectId: string, identityKeys: string[]): Promise<boolean> {
    try {
      await firstValueFrom(
        this.http.post(
          `${this.baseUrl}/projects/${projectId}/authors/suggestions/reject`,
          { identity_keys: identityKeys } as RejectSuggestionRequest
        )
      );
      return true;
    } catch (err) {
      console.error('Failed to reject suggestion:', err);
      return false;
    }
  }

  async deleteUnifiedUser(projectId: string, unifiedUserId: string): Promise<boolean> {
    try {
      await firstValueFrom(
        this.http.delete(
          `${this.baseUrl}/projects/${projectId}/authors/users/${unifiedUserId}`
        )
      );
      return true;
    } catch (err) {
      console.error('Failed to delete unified user:', err);
      return false;
    }
  }

  async deleteAllUnifiedUsers(
    projectId: string
  ): Promise<{ deleted_users: number; deleted_rejected: number } | null> {
    try {
      return await firstValueFrom(
        this.http.delete<{ ok: true; deleted_users: number; deleted_rejected: number }>(
          `${this.baseUrl}/projects/${projectId}/authors/users`
        )
      );
    } catch (err) {
      console.error('Failed to reset author matching:', err);
      return null;
    }
  }

  async getUnifiedUsers(projectId: string): Promise<UnifiedUsersResponse | null> {
    try {
      return await firstValueFrom(
        this.http.get<UnifiedUsersResponse>(
          `${this.baseUrl}/projects/${projectId}/authors/users`
        )
      );
    } catch (err) {
      console.error('Failed to get unified users:', err);
      return null;
    }
  }

  async applyAllSuggestions(
    projectId: string
  ): Promise<{ created: number; failed: { suggestion_id: string; error: string }[]; users: UnifiedUserDto[] } | null> {
    try {
      return await firstValueFrom(
        this.http.post<{ created: number; failed: { suggestion_id: string; error: string }[]; users: UnifiedUserDto[] }>(
          `${this.baseUrl}/projects/${projectId}/authors/suggestions/apply-batch`,
          {}
        )
      );
    } catch (err) {
      console.error('Failed to apply all suggestions:', err);
      return null;
    }
  }

  async saveGraphState(
    projectId: string
  ): Promise<{ ok: true; size_mb: number; user_count: number } | null> {
    try {
      return await firstValueFrom(
        this.http.post<{ ok: true; size_mb: number; user_count: number }>(
          `${this.baseUrl}/projects/${projectId}/save-graph-state`,
          {}
        )
      );
    } catch (err) {
      console.error('Failed to save graph state:', err);
      return null;
    }
  }

  private handleError(err: unknown, operation: string): BuildResult {
    if (err instanceof HttpErrorResponse) {
      // Server-side error
      if (err.status === 0) {
        return {
          success: false,
          error: `Unable to connect to data server. Please ensure it's running on ${this.baseUrl}`,
        };
      }

      // Extract error message from response body
      const errorMessage = err.error?.detail || err.error?.message || err.message;

      switch (err.status) {
        case 401:
        case 403:
          return {
            success: false,
            error: 'Authentication failed',
          };
        case 404:
          return {
            success: false,
            error: 'Project not found on data server',
          };
        case 400:
          return {
            success: false,
            error: errorMessage || 'Invalid request',
          };
        case 500:
          return {
            success: false,
            error: errorMessage || 'Server error while processing',
          };
        default:
          return {
            success: false,
            error: errorMessage || `Failed to ${operation}`,
          };
      }
    }

    // Client-side or network error
    console.error(`Error during ${operation}:`, err);
    return {
      success: false,
      error: `Unexpected error: ${err instanceof Error ? err.message : 'Unknown error'}`,
    };
  }
}
