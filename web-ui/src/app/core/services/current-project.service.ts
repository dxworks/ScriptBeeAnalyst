import { Injectable, signal, computed } from '@angular/core';
import { DataServerService, CurrentProjectResponse, FinalizeResult } from './data-server.service';
import { MergeState } from '../models/project.model';

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
  // Lifecycle stage of the loaded graph, polled off /projects/current.
  // `null` when nothing is loaded.
  private readonly mergeStateSignal = signal<MergeState | null>(null);
  private readonly finalizingSignal = signal<boolean>(false);
  // Live pipeline progress for the loaded project, polled off
  // /projects/current. `null` when no build/finalize is running.
  private readonly progressSignal = signal<number | null>(null);
  private readonly progressStageSignal = signal<string | null>(null);

  readonly loadedProjectId = this.loadedProjectIdSignal.asReadonly();
  readonly loadedProjectName = this.loadedProjectNameSignal.asReadonly();
  readonly loadedProjectStats = this.loadedProjectStatsSignal.asReadonly();
  readonly connected = this.connectedSignal.asReadonly();
  readonly loading = this.loadingSignal.asReadonly();
  readonly hasLoadedProject = computed(() => this.loadedProjectIdSignal() !== null);
  /** Lifecycle stage of the loaded project (`null` when none loaded). */
  readonly mergeState = this.mergeStateSignal.asReadonly();
  /** True only when a project is loaded AND its graph is FINALIZED. */
  readonly isFinalized = computed(() => this.mergeStateSignal() === 'FINALIZED');
  /** True while a finalize call is in flight (drives the CTA spinner). */
  readonly finalizing = this.finalizingSignal.asReadonly();
  /** Live progress (0..100) of an in-flight build/finalize, or `null`. */
  readonly progress = this.progressSignal.asReadonly();
  /** Checkpoint label paired with {@link progress}. */
  readonly progressStage = this.progressStageSignal.asReadonly();

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
          this.mergeStateSignal.set(current.merge_state);
          this.progressSignal.set(current.progress ?? null);
          this.progressStageSignal.set(current.progressStage ?? null);
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
        this.mergeStateSignal.set(null);
        this.progressSignal.set(null);
        this.progressStageSignal.set(null);
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
      this.mergeStateSignal.set(null);
    }
    await this.refresh();
    this.loadingSignal.set(false);
    return ok;
  }

  /**
   * Finalize the given project (PRE_MERGE → FINALIZED). Blocking — Phase B
   * can take ~150s — so callers should drive UI off the `finalizing` signal.
   * On success (or a benign "already finalized" 409) the local state is
   * refreshed so `mergeState`/`isFinalized` flip without waiting for the
   * 5s poll. Returns the raw `FinalizeResult` for metric/error display.
   */
  async finalize(projectId: string): Promise<FinalizeResult> {
    if (this.finalizingSignal()) {
      return { success: false, error: 'A finalize is already in progress' };
    }
    this.finalizingSignal.set(true);
    try {
      const result = await this.dataServer.finalizeProject(projectId);
      if (result.success) {
        // Reflect the new stage immediately; the poll will reconcile too.
        this.mergeStateSignal.set('FINALIZED');
      } else if (result.alreadyFinalized) {
        this.mergeStateSignal.set('FINALIZED');
      }
      // Reconcile against the server regardless (cheap, and covers the
      // half-finalized contract where the server may still read PRE_MERGE).
      await this.refresh();
      return result;
    } finally {
      this.finalizingSignal.set(false);
    }
  }

  isLoaded(projectId: string): boolean {
    return this.loadedProjectIdSignal() === projectId;
  }
}
