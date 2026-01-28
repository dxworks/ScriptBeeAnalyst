import { Injectable } from '@angular/core';
import { HttpClient, HttpHeaders, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { AuthService } from './auth.service';
import { environment } from '../../../environments/environment';

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
      console.error('Failed to get current project:', err);
      return null;
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
