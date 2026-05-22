import { Injectable, signal, computed } from '@angular/core';
import { DataServerService, CurrentProjectResponse } from './data-server.service';

const POLL_INTERVAL_MS = 5000;

@Injectable({
  providedIn: 'root',
})
export class CurrentProjectService {
  private readonly loadedProjectIdSignal = signal<string | null>(null);
  private readonly loadedProjectNameSignal = signal<string | null>(null);
  private readonly loadedProjectStatsSignal = signal<
    CurrentProjectResponse['stats'] | null
  >(null);
  private readonly connectedSignal = signal<boolean>(true);
  private readonly loadingSignal = signal<boolean>(false);

  readonly loadedProjectId = this.loadedProjectIdSignal.asReadonly();
  readonly loadedProjectName = this.loadedProjectNameSignal.asReadonly();
  readonly loadedProjectStats = this.loadedProjectStatsSignal.asReadonly();
  readonly connected = this.connectedSignal.asReadonly();
  readonly loading = this.loadingSignal.asReadonly();
  readonly hasLoadedProject = computed(() => this.loadedProjectIdSignal() !== null);

  private pollingInterval: ReturnType<typeof setInterval> | null = null;

  constructor(private dataServer: DataServerService) {}

  startPolling(): void {
    if (this.pollingInterval) return;
    this.refresh();
    this.pollingInterval = setInterval(() => this.refresh(), POLL_INTERVAL_MS);
  }

  stopPolling(): void {
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = null;
    }
  }

  // Refresh state from data-server. Optional `expectLoadedId` makes the poll
  // retry briefly when we just kicked off a load and the server hasn't yet
  // updated its global — without retries, an in-flight /current that started
  // before the load would resolve null and blank the just-set state.
  async refresh(expectLoadedId?: string): Promise<void> {
    const maxAttempts = expectLoadedId ? 4 : 1;
    for (let attempt = 1; attempt <= maxAttempts; attempt++) {
      try {
        const current = await this.dataServer.getCurrentProject();

        if (current) {
          this.loadedProjectIdSignal.set(current.project_id);
          this.loadedProjectNameSignal.set(current.project_name ?? null);
          this.loadedProjectStatsSignal.set(current.stats);
          this.connectedSignal.set(true);
          return;
        }

        if (expectLoadedId && attempt < maxAttempts) {
          await new Promise(r => setTimeout(r, 750));
          continue;
        }

        this.loadedProjectIdSignal.set(null);
        this.loadedProjectNameSignal.set(null);
        this.loadedProjectStatsSignal.set(null);
        this.connectedSignal.set(true);
        return;
      } catch (err) {
        this.connectedSignal.set(false);
        return;
      }
    }
  }

  async loadProject(projectId: string): Promise<{ success: boolean; error?: string }> {
    if (this.loadingSignal()) {
      return { success: false, error: 'Another load is already in progress' };
    }
    this.loadingSignal.set(true);

    const result = await this.dataServer.loadProject(projectId);

    if (result.success) {
      this.loadedProjectIdSignal.set(result.project_id ?? projectId);
      this.loadedProjectNameSignal.set(result.project_name ?? null);
      if (result.stats) {
        this.loadedProjectStatsSignal.set(result.stats);
      }
      this.dataServer.scaffoldWorkspace(projectId).catch(() => {});
      await this.refresh(result.project_id ?? projectId);
      this.loadingSignal.set(false);
      return { success: true };
    }

    this.loadingSignal.set(false);
    return { success: false, error: result.error };
  }

  async unloadProject(): Promise<boolean> {
    const id = this.loadedProjectIdSignal();
    if (!id) return false;

    this.loadingSignal.set(true);
    const ok = await this.dataServer.unloadProject(id);
    if (ok) {
      this.loadedProjectIdSignal.set(null);
      this.loadedProjectNameSignal.set(null);
      this.loadedProjectStatsSignal.set(null);
    }
    await this.refresh();
    this.loadingSignal.set(false);
    return ok;
  }

  isLoaded(projectId: string): boolean {
    return this.loadedProjectIdSignal() === projectId;
  }
}
