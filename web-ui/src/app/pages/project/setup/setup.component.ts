import { Component, computed, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { CurrentProjectService } from '../../../core/services/current-project.service';
import { ToastService } from '../../../core/services/toast.service';
import { ConfirmationModalComponent } from '../../../shared/components/confirmation-modal/confirmation-modal.component';

interface SetupTab {
  id: string;
  label: string;
  path: string;
  /**
   * Lifecycle stage in which this tab is LOCKED (grayed, non-clickable):
   *  - 'pre-merge'  → locked during setup, unlocked once finalized (Analysis)
   *  - 'finalized'  → locked once finalized (Author Matching, Enrichment)
   *  - undefined    → never locked (Exclusion Rules survives finalize)
   */
  lockedIn?: 'pre-merge' | 'finalized';
}

@Component({
  selector: 'app-project-setup',
  standalone: true,
  imports: [RouterLink, RouterLinkActive, RouterOutlet, ConfirmationModalComponent],
  templateUrl: './setup.component.html',
  styleUrl: './setup.component.scss',
})
export class SetupComponent {
  // Tab list. Add new entries here as Setup grows; each one needs a matching
  // child route under `/project/:id/setup/` in app.routes.ts. The `lockedIn`
  // flag drives the per-stage lock (see SetupTab).
  readonly tabs: SetupTab[] = [
    { id: 'author-matching', label: 'Author Matching', path: 'author-matching', lockedIn: 'finalized' },
    { id: 'exclusion-rules', label: 'Exclusion Rules', path: 'exclusion-rules' },
    { id: 'enrichment-config', label: 'Enrichment Config', path: 'enrichment-config', lockedIn: 'finalized' },
    { id: 'analysis', label: 'Analysis', path: 'analysis', lockedIn: 'pre-merge' },
  ];

  // Lifecycle state, surfaced from the polled /projects/current. Getters (not
  // field initializers) so they don't touch the injected service before the
  // constructor has assigned it.
  get isFinalized() {
    return this.currentProject.isFinalized;
  }
  get finalizing() {
    return this.currentProject.finalizing;
  }
  get mergeState() {
    return this.currentProject.mergeState;
  }

  // The finalize CTA is actionable only once a project is loaded and still in
  // the PRE_MERGE stage. Computed is lazy, so referencing the injected service
  // inside the arrow is safe.
  readonly canFinalize = computed(
    () => this.currentProject.hasLoadedProject() && !this.currentProject.isFinalized(),
  );

  showConfirm = signal(false);

  constructor(
    private currentProject: CurrentProjectService,
    private toast: ToastService,
    private router: Router,
  ) {}

  /** A tab is locked when the current lifecycle stage matches its `lockedIn`. */
  isTabLocked(tab: SetupTab): boolean {
    if (tab.lockedIn === 'finalized') return this.isFinalized();
    if (tab.lockedIn === 'pre-merge') return !this.isFinalized();
    return false;
  }

  lockTooltip(tab: SetupTab): string {
    if (tab.lockedIn === 'pre-merge') {
      return 'Available once the project is finalized.';
    }
    return 'Locked — this project is finalized. Re-import to edit setup again.';
  }

  // ── Finalize flow ─────────────────────────────────────────────────────────

  openConfirm(): void {
    this.showConfirm.set(true);
  }

  cancelConfirm(): void {
    // Ignore cancel clicks while the call is in flight — the modal stays up
    // showing its spinner until the server responds.
    if (this.finalizing()) return;
    this.showConfirm.set(false);
  }

  async confirmFinalize(): Promise<void> {
    const projectId = this.currentProject.loadedProjectId();
    if (!projectId) {
      this.toast.error('No project is loaded.');
      this.showConfirm.set(false);
      return;
    }

    const result = await this.currentProject.finalize(projectId);
    this.showConfirm.set(false);

    if (result.success) {
      this.toast.success(
        `Project finalized — ${result.unified_users_created ?? 0} unified users created, ` +
          `${result.refs_rewritten ?? 0} refs rewritten.`,
      );
      // Setup is now locked; move the user on to the (newly unlocked) Analysis tab.
      this.router.navigate(['/project', projectId, 'setup', 'analysis']);
      return;
    }

    if (result.alreadyFinalized) {
      this.toast.info('Project is already finalized.');
      return;
    }

    this.toast.error(result.error ?? 'Failed to finalize project.');
  }
}
