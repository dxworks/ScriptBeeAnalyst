import { Injectable, signal, computed } from '@angular/core';
import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Project, ProjectStatus, CreateProjectDto, UpdateProjectDto } from '../models/project.model';

/**
 * Interval (ms) at which {@link ProjectService.subscribeToProjectChanges}
 * re-fetches the project list. Replaces the old Supabase realtime channel
 * `projects-changes`; project mutations are user-initiated and low-frequency
 * so a coarse poll is fine.
 */
const POLL_INTERVAL_MS = 4000;

@Injectable({
  providedIn: 'root',
})
export class ProjectService {
  private readonly baseUrl = environment.dataServerUrl;

  private readonly projectsSignal = signal<Project[]>([]);
  private readonly loadingSignal = signal<boolean>(false);
  private readonly errorSignal = signal<string | null>(null);
  private pollTimer: ReturnType<typeof setInterval> | null = null;

  readonly projects = this.projectsSignal.asReadonly();
  readonly loading = this.loadingSignal.asReadonly();
  readonly error = this.errorSignal.asReadonly();
  readonly projectCount = computed(() => this.projectsSignal().length);

  constructor(private http: HttpClient) {}

  async loadProjects(): Promise<void> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    try {
      const data = await firstValueFrom(
        this.http.get<Project[]>(`${this.baseUrl}/projects`)
      );
      this.projectsSignal.set(data ?? []);
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to load projects'));
    } finally {
      this.loadingSignal.set(false);
    }
  }

  async createProject(dto: CreateProjectDto): Promise<Project | null> {
    this.errorSignal.set(null);

    try {
      const data = await firstValueFrom(
        this.http.post<Project>(`${this.baseUrl}/projects`, {
          name: dto.name,
          description: dto.description ?? null,
        })
      );

      this.projectsSignal.update(projects => [data, ...projects]);
      return data;
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to create project'));
      return null;
    }
  }

  async updateProject(id: string, dto: UpdateProjectDto): Promise<Project | null> {
    this.errorSignal.set(null);

    try {
      const data = await firstValueFrom(
        this.http.patch<Project>(`${this.baseUrl}/projects/${id}`, { ...dto })
      );

      this.projectsSignal.update(projects =>
        projects.map(p => (p.id === id ? data : p))
      );
      return data;
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to update project'));
      return null;
    }
  }

  async deleteProject(id: string): Promise<boolean> {
    this.errorSignal.set(null);

    try {
      // The data-server handles the cascade: it unlinks all of the project's
      // serialized file bytes on disk then deletes the row (FK ON DELETE
      // CASCADE clears serialized_files + dependent tables).
      await firstValueFrom(
        this.http.delete(`${this.baseUrl}/projects/${id}`)
      );

      this.projectsSignal.update(projects =>
        projects.filter(p => p.id !== id)
      );
      return true;
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to delete project'));
      return false;
    }
  }

  getProjectById(id: string): Project | undefined {
    return this.projectsSignal().find(p => p.id === id);
  }

  async updateProjectStatus(id: string, status: ProjectStatus): Promise<Project | null> {
    this.errorSignal.set(null);

    try {
      const data = await firstValueFrom(
        this.http.patch<Project>(`${this.baseUrl}/projects/${id}/status`, { status })
      );

      this.projectsSignal.update(projects =>
        projects.map(p => (p.id === id ? data : p))
      );
      return data;
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to update project status'));
      return null;
    }
  }

  /**
   * Start polling the data-server for project changes. Replaces the old
   * Supabase realtime channel `projects-changes`: every {@link
   * POLL_INTERVAL_MS} it re-fetches GET /projects and reconciles the result
   * into {@link projectsSignal} by id (add new ids, replace rows whose
   * updated_at changed, drop ids no longer present). Public name kept so
   * existing callers (dashboard, project page) don't break.
   */
  subscribeToProjectChanges(): void {
    if (this.pollTimer) {
      console.warn('Project polling already active');
      return;
    }

    this.pollTimer = setInterval(() => {
      void this.pollProjects();
    }, POLL_INTERVAL_MS);
  }

  unsubscribeFromProjectChanges(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  /**
   * Fetch /projects and reconcile into the signal without flipping the
   * loading flag (so the poll doesn't flash spinners). Reconciles by id and
   * updated_at to avoid replacing the array reference when nothing changed.
   */
  private async pollProjects(): Promise<void> {
    let fetched: Project[];
    try {
      fetched = await firstValueFrom(
        this.http.get<Project[]>(`${this.baseUrl}/projects`)
      );
    } catch {
      // Transient poll failure — keep the last known list, try again next tick.
      return;
    }

    const incoming = fetched ?? [];
    this.projectsSignal.update(current => {
      const currentById = new Map(current.map(p => [p.id, p]));

      let changed = incoming.length !== current.length;
      // A row is "unchanged" only when both its updated_at AND its live
      // pipeline progress match. updated_at doesn't move during a build, so
      // without the progress check the card's loading bar would never advance.
      const sameRow = (a: Project, b: Project): boolean =>
        a.updated_at === b.updated_at && a.progress === b.progress;

      if (!changed) {
        for (const p of incoming) {
          const existing = currentById.get(p.id);
          if (!existing || !sameRow(existing, p)) {
            changed = true;
            break;
          }
        }
      }

      if (!changed) {
        return current;
      }

      // Preserve the server's ordering (updated_at DESC), substituting the
      // existing object reference when the row is unchanged.
      return incoming.map(p => {
        const existing = currentById.get(p.id);
        return existing && sameRow(existing, p) ? existing : p;
      });
    });
  }

  private errorMessage(err: unknown, fallback: string): string {
    if (err instanceof HttpErrorResponse) {
      return err.error?.error || err.error?.message || err.message || fallback;
    }
    return fallback;
  }
}
