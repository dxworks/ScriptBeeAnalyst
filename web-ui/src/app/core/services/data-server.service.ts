import { Injectable } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
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
  project_name?: string;
  stats: {
    git_commits: number;
    jira_issues: number;
    github_prs: number;
  };
}

// Raw response shape from data-server GET /projects/current.
// The server now always returns 200; the `loaded` flag distinguishes
// "a project is loaded" from "nothing is loaded right now".
interface CurrentProjectResponseRaw {
  loaded: boolean;
  project_id?: string;
  project_name?: string;
  stats?: {
    git_commits: number;
    jira_issues: number;
    github_prs: number;
  };
}

// ── Filter rules DTOs ───────────────────────────────────────────────────────
// Mirror the Pydantic shapes in data-server/src/filter_rules/models.py. The
// DSL is intentionally permissive on the wire: either a leaf {field, op,
// value} predicate or an { all_of: [...] } wrapper at depth 1.

export type FilterRuleOp =
  | 'lt'
  | 'le'
  | 'gt'
  | 'ge'
  | 'eq'
  | 'ne'
  | 'in'
  | 'not_in'
  | 'contains'
  | 'regex';

export interface FilterRulePredicate {
  field: string;
  op: FilterRuleOp;
  value: string | number | boolean | (string | number | boolean)[] | null;
}

export interface FilterRuleAllOf {
  all_of: FilterRulePredicate[];
}

export interface FilterRuleDSL {
  entity_kind: string;
  predicate: FilterRulePredicate | FilterRuleAllOf;
}

export interface FilterRuleDto {
  id: string;
  project_id: string;
  entity_kind: string;
  name: string;
  nl_description: string;
  dsl: FilterRuleDSL;
  created_at: string | null;
  /**
   * Number of entities this rule matches against the loaded graph.
   * `null` when the data-server has no graph loaded for this project
   * (the count cannot be computed). Populated by GET /projects/{id}/rules.
   */
  match_count?: number | null;
}

export interface FilterRulesListResponse {
  project_id: string;
  rules: FilterRuleDto[];
}

// ── Config overrides DTOs ───────────────────────────────────────────────────
// Mirror the Pydantic shapes in data-server/src/config_overrides/. The
// catalogue is read-only metadata; the overrides dict is the editable state.

/**
 * One editable knob the editor renders. `current` already reflects any
 * persisted override (the server overlays before returning), so the UI
 * shows `current` in the input and flags it modified when `current !=
 * default`. `metric_names` powers the "Used by" badge. `dx_baseline` is
 * `false` for ScriptBee-only traits (Cathedral, BusFactor1, etc.).
 */
export interface CatalogueFieldDto {
  name: string;
  type: string;
  default: unknown;
  current: unknown;
  metric_names: string[];
  dx_baseline: boolean;
}

export interface CatalogueFamilyDto {
  name: string;
  fields: CatalogueFieldDto[];
}

export interface CatalogueResponseDto {
  families: CatalogueFamilyDto[];
}

/**
 * GET /projects/{id}/config-overrides response shape. The server returns
 * the catalogue + the persisted overrides dict + the row's updated_at in
 * a single round-trip — the editor never needs a second call.
 */
export interface ConfigOverridesResponse {
  catalogue: CatalogueResponseDto;
  overrides: Record<string, unknown>;
  updated_at: string | null;
}

export interface ConfigOverridesWriteResponse {
  overrides: Record<string, unknown>;
  updated_at: string | null;
}

/**
 * Tagged error a caller can `instanceof`-check after a config-overrides
 * GET to render an "unknown project" empty state vs. a transport failure.
 */
export class ProjectNotFoundError extends Error {
  constructor(message = 'Project not found.') {
    super(message);
    this.name = 'ProjectNotFoundError';
  }
}

/**
 * Tagged 422 error from the config-overrides PUT endpoint. The server
 * envelope intentionally diverges from the codebase's plain ``{error}``
 * shape to give the UI both ``field`` (so the offending input row can be
 * flagged inline) AND ``error`` (the human-readable explanation).
 * Documented at the raise sites in
 * ``data-server/src/config_overrides/router.py``.
 */
export class ConfigOverridesValidationError extends Error {
  constructor(
    public readonly field: string,
    message: string,
  ) {
    super(message);
    this.name = 'ConfigOverridesValidationError';
  }
}

// ── Components page DTOs ────────────────────────────────────────────────────
// Mirror the contract locked by data-server B3 (src/server.py around line 1216).
// owner/status are intentionally absent — the data-server doesn't return them
// in v1 and the page tolerates their absence.

export interface ComponentSummaryDto {
  name: string;
  path_prefix: string;
  file_count: number;
  total_loc: number;
  color: string | null;
}

export interface ComponentFileDto {
  path: string;
  loc: number | null;
  component_name: string | null;
}

export interface ComponentMappingUpdateResult {
  /** True when the call succeeded end-to-end (persist + rebuild). */
  success: boolean;
  /** Error message when success is false. */
  error?: string;
  /**
   * True when the mapping WAS written to Supabase but the rebuild failed.
   * Reported by the server as 500 + `mapping_persisted: true`. The caller
   * should surface a different message in that case ("saved but rebuild
   * failed; retry the build").
   */
  mappingPersisted?: boolean;
  /** True when the mapping was cleared (null/empty body). */
  cleared?: boolean;
  /** Number of components after rebuild. */
  componentCount?: number;
}

/**
 * Tagged error a caller can `instanceof`-check after a components GET to
 * distinguish "graph not loaded for this project" (a state the page should
 * recover from with a Load button) from a generic transport failure.
 */
export class ProjectNotLoadedError extends Error {
  constructor(message = 'Project is not loaded. Load the project first.') {
    super(message);
    this.name = 'ProjectNotLoadedError';
  }
}

@Injectable({
  providedIn: 'root',
})
export class DataServerService {
  private readonly baseUrl = environment.dataServerUrl;

  constructor(private http: HttpClient) {}

  /**
   * Build project graph on data-server
   */
  async buildProject(projectId: string): Promise<BuildResult> {
    try {
      const response = await firstValueFrom(
        this.http.post<{ message: string }>(
          `${this.baseUrl}/projects/${projectId}/build`,
          {},
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
   * Rerun the enrichment pipeline for a project. Delegates to ``buildProject``
   * because the existing ``/projects/{id}/build`` endpoint already re-runs the
   * full pipeline against the latest persisted overrides — no separate
   * "rerun" endpoint exists. The wrapper exists so editor callers spell out
   * their intent in service-call grammar instead of leaking the generic
   * "build" verb into UI code that means "rerun with my saved overrides".
   */
  async rerunEnrichments(projectId: string): Promise<BuildResult> {
    return this.buildProject(projectId);
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
    try {
      await firstValueFrom(
        this.http.delete(`${this.baseUrl}/projects/${projectId}/unload`)
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
        this.http.get<CurrentProjectResponseRaw>(`${this.baseUrl}/projects/current`)
      );
      // Server now returns 200 with `loaded: false` instead of a 404
      // when no project is in memory. Translate that to null so callers
      // see the same shape as before.
      if (!response || !response.loaded || !response.project_id) {
        return null;
      }
      return {
        project_id: response.project_id,
        project_name: response.project_name,
        stats: response.stats ?? { git_commits: 0, jira_issues: 0, github_prs: 0 },
      };
    } catch (err) {
      // Legacy 404 path (older data-server build still returning 404 for
      // "no project loaded") — treat as "no project".
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

  // ── Components page methods ─────────────────────────────────────────────

  /**
   * Fetch one row per Component in the loaded graph.
   * Throws `ProjectNotLoadedError` on the data-server's 400 "Project is not
   * loaded" response so the page can render a Load-prompt empty state.
   */
  async getComponents(projectId: string): Promise<ComponentSummaryDto[]> {
    try {
      const rows = await firstValueFrom(
        this.http.get<ComponentSummaryDto[]>(
          `${this.baseUrl}/projects/${projectId}/components`
        )
      );
      return rows ?? [];
    } catch (err) {
      throw this.toComponentsError(err);
    }
  }

  /**
   * Fetch a flat {path, loc, component_name} row per file in the project.
   * Throws `ProjectNotLoadedError` on the 400 "Project is not loaded" path.
   */
  async getComponentFiles(projectId: string): Promise<ComponentFileDto[]> {
    try {
      const rows = await firstValueFrom(
        this.http.get<ComponentFileDto[]>(
          `${this.baseUrl}/projects/${projectId}/components/files`
        )
      );
      return rows ?? [];
    } catch (err) {
      throw this.toComponentsError(err);
    }
  }

  /**
   * Persist a curated component mapping JSON, then trigger a rebuild.
   * Pass `null` (or `{}`) to clear the mapping.
   *
   * Distinguishes the two 500 paths via the server's `mapping_persisted` flag:
   *  - persist failed → `mappingPersisted: false`
   *  - rebuild failed after persist → `mappingPersisted: true`
   */
  async updateComponentMapping(
    projectId: string,
    mapping: Record<string, unknown> | null
  ): Promise<ComponentMappingUpdateResult> {
    try {
      const response = await firstValueFrom(
        this.http.put<{ ok: true; cleared: boolean; component_count: number }>(
          `${this.baseUrl}/projects/${projectId}/component-mapping`,
          mapping
        )
      );
      return {
        success: true,
        cleared: response.cleared,
        componentCount: response.component_count,
      };
    } catch (err) {
      if (err instanceof HttpErrorResponse) {
        const message = err.error?.error || err.error?.message || err.message;
        return {
          success: false,
          error: message || 'Failed to update component mapping',
          mappingPersisted: err.error?.mapping_persisted === true,
        };
      }
      return {
        success: false,
        error: `Unexpected error: ${err instanceof Error ? err.message : 'Unknown error'}`,
      };
    }
  }

  /**
   * Translate a GET-side HTTP error into either a `ProjectNotLoadedError` or
   * a generic `Error`. Centralised here so both components GETs behave the
   * same way and the page only has to `instanceof`-check once.
   */
  private toComponentsError(err: unknown): Error {
    if (err instanceof HttpErrorResponse) {
      if (err.status === 0) {
        return new Error(`Unable to connect to data server at ${this.baseUrl}`);
      }
      const message = err.error?.error || err.error?.message || err.message;
      if (err.status === 400 && typeof message === 'string' && message.includes('not loaded')) {
        return new ProjectNotLoadedError(message);
      }
      return new Error(message || `Components request failed (${err.status})`);
    }
    return err instanceof Error ? err : new Error('Unexpected error');
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

  // ── Filter Rules Methods ────────────────────────────────────────────────

  /**
   * List active filter rules for a project from the data-server's in-memory
   * cache. The data-server also persists to Supabase; the UI uses Supabase
   * realtime for live updates and this list call only as a fallback bootstrap.
   */
  async listFilterRules(projectId: string): Promise<FilterRuleDto[]> {
    try {
      const response = await firstValueFrom(
        this.http.get<FilterRulesListResponse>(
          `${this.baseUrl}/projects/${projectId}/rules`
        )
      );
      return response?.rules ?? [];
    } catch (err) {
      console.error('Failed to list filter rules:', err);
      return [];
    }
  }

  // ── Config Overrides Methods ────────────────────────────────────────────

  /**
   * Fetch the catalogue + persisted overrides + updated_at for the given
   * project in a single round-trip. Throws :class:`ProjectNotFoundError`
   * on 404 so the editor can render an "unknown project" empty state;
   * any other failure throws a generic Error.
   */
  async getConfigOverrides(projectId: string): Promise<ConfigOverridesResponse> {
    try {
      return await firstValueFrom(
        this.http.get<ConfigOverridesResponse>(
          `${this.baseUrl}/projects/${projectId}/config-overrides`,
        ),
      );
    } catch (err) {
      throw this.toConfigOverridesError(err);
    }
  }

  /**
   * Persist the full overrides dict for the given project. The server
   * replaces the whole dict on every save (no patch semantics) — the
   * caller is expected to send the merged ``{ ...current, ...pending }``
   * payload. Returns the persisted ``{ overrides, updated_at }`` row.
   *
   * Throws :class:`ConfigOverridesValidationError` on 422 so the editor
   * can highlight the offending field; :class:`ProjectNotFoundError` on
   * 404; a generic :class:`Error` on 500 / network failures.
   */
  async putConfigOverrides(
    projectId: string,
    overrides: Record<string, unknown>,
  ): Promise<ConfigOverridesWriteResponse> {
    try {
      return await firstValueFrom(
        this.http.put<ConfigOverridesWriteResponse>(
          `${this.baseUrl}/projects/${projectId}/config-overrides`,
          { overrides },
        ),
      );
    } catch (err) {
      throw this.toConfigOverridesError(err);
    }
  }

  private toConfigOverridesError(err: unknown): Error {
    if (err instanceof HttpErrorResponse) {
      if (err.status === 0) {
        return new Error(`Unable to connect to data server at ${this.baseUrl}`);
      }
      if (err.status === 404) {
        const message = err.error?.error || 'Project not found.';
        return new ProjectNotFoundError(message);
      }
      if (err.status === 422) {
        // The router's PUT validator surfaces `{field, error}` — preserve
        // both so the editor can light up the offending input row inline.
        const field = typeof err.error?.field === 'string' ? err.error.field : '';
        const message =
          typeof err.error?.error === 'string'
            ? err.error.error
            : 'Validation failed';
        return new ConfigOverridesValidationError(field, message);
      }
      const message = err.error?.error || err.error?.detail || err.message;
      return new Error(message || `Config overrides request failed (${err.status})`);
    }
    return err instanceof Error ? err : new Error('Unexpected error');
  }

  /**
   * Delete a filter rule via the data-server endpoint. The endpoint cascades
   * the delete to Supabase and evicts the in-memory cache in one shot — UI
   * must not delete directly through Supabase (see filter_files.md Flow D).
   */
  async deleteFilterRule(projectId: string, ruleId: string): Promise<boolean> {
    try {
      await firstValueFrom(
        this.http.delete(`${this.baseUrl}/projects/${projectId}/rules/${ruleId}`)
      );
      return true;
    } catch (err) {
      console.error('Failed to delete filter rule:', err);
      return false;
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
