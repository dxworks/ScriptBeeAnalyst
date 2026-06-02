import { Component, computed, inject } from '@angular/core';
import { CurrentProjectService } from '../../../../core/services/current-project.service';

/**
 * Query-stage analysis surface. The real analysis UI (agent queries against
 * the finalized UnifiedUser graph) is a separate piece of work; for now this
 * tab is the landing spot after the user clicks "Lock in & Analyze". It hosts
 * the **finalize loading bar**: the setup flow navigates here immediately on
 * confirm, the finalize (Phase B) runs in the background, and the bar tracks
 * the checkpoints the data-server writes onto the project row (rebind →
 * config snapshot → phase B → save). Once finalized it shows the placeholder.
 */
@Component({
  selector: 'app-analysis',
  standalone: true,
  imports: [],
  templateUrl: './analysis.component.html',
  styleUrl: './analysis.component.scss',
})
export class AnalysisComponent {
  private readonly currentProject = inject(CurrentProjectService);

  readonly finalizing = this.currentProject.finalizing;
  readonly isFinalized = this.currentProject.isFinalized;
  readonly progress = this.currentProject.progress;
  readonly progressStage = this.currentProject.progressStage;

  /**
   * Show the finalize progress view while a finalize is in flight (or the
   * backend still reports progress) and the project hasn't flipped to
   * FINALIZED yet. When it completes, fall through to the placeholder.
   */
  readonly finalizeInProgress = computed(
    () => !this.isFinalized() && (this.finalizing() || this.progress() != null),
  );

  /** Determinate fill width; 0 until the first backend checkpoint arrives. */
  readonly percent = computed(() => this.progress() ?? 0);

  /**
   * Before the first checkpoint lands (or between the click and the first
   * /projects/current poll) we have no number yet — animate the bar instead
   * of parking it at 0.
   */
  readonly indeterminate = computed(() => this.progress() == null);
}
