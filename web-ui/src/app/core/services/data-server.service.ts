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

export interface HealthResponse {
  status: string;
  loaded_projects: string[];
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
