import { Component, computed, inject, signal } from '@angular/core';
import { CurrentProjectService } from '../../../../core/services/current-project.service';
import { DataServerService } from '../../../../core/services/data-server.service';
import { CodeEditorComponent } from '../../../../shared/components/code-editor/code-editor.component';

/** A single chunk of console output, tagged so stdout and errors render differently. */
interface ConsoleEntry {
  text: string;
  kind: 'out' | 'err';
}

const STARTER_CODE = `# Write Python and press Run (or Ctrl/Cmd+Enter).
# The loaded project graph is available as 'graph_data'.
print("hello from the data server")
`;

/**
 * Query-stage analysis surface. The setup flow navigates here on "Lock in &
 * Analyze", so this tab first hosts the **finalize loading bar** while Phase B
 * runs in the background (rebind → config snapshot → phase B → save). Once the
 * project is finalized it becomes a **Python workspace**: an editor whose code
 * is sent to the data-server's `/execute` endpoint (bound to the finalized
 * graph) and a console showing the captured stdout or traceback.
 */
@Component({
  selector: 'app-analysis',
  standalone: true,
  imports: [CodeEditorComponent],
  templateUrl: './analysis.component.html',
  styleUrl: './analysis.component.scss',
})
export class AnalysisComponent {
  private readonly currentProject = inject(CurrentProjectService);
  private readonly dataServer = inject(DataServerService);

  readonly finalizing = this.currentProject.finalizing;
  readonly isFinalizing = this.currentProject.isFinalizing;
  readonly isFinalized = this.currentProject.isFinalized;
  readonly progress = this.currentProject.progress;
  readonly progressStage = this.currentProject.progressStage;

  /** Current editor source. */
  readonly code = signal(STARTER_CODE);
  /** True while an `/execute` request is in flight. */
  readonly running = signal(false);
  /** Accumulated console output (stdout chunks and tracebacks). */
  readonly console = signal<ConsoleEntry[]>([]);

  /**
   * Show the finalize progress view while a finalize is running and the
   * project hasn't flipped to FINALIZED yet. "Running" is any of: the client
   * in-flight flag (`finalizing`, instant but lost on refresh), the persisted
   * FINALIZING stage (`isFinalizing`, survives a refresh mid-run), or a live
   * progress checkpoint on the row. When it completes, fall through to the
   * workspace.
   */
  readonly finalizeInProgress = computed(
    () =>
      !this.isFinalized() &&
      (this.finalizing() || this.isFinalizing() || this.progress() != null),
  );

  /** Determinate fill width; 0 until the first backend checkpoint arrives. */
  readonly percent = computed(() => this.progress() ?? 0);

  /**
   * Before the first checkpoint lands (or between the click and the first
   * /projects/current poll) we have no number yet — animate the bar instead
   * of parking it at 0.
   */
  readonly indeterminate = computed(() => this.progress() == null);

  /** Send the current source to the data-server and append the result. */
  async run(): Promise<void> {
    if (this.running()) return;
    const code = this.code();
    if (!code.trim()) return;

    this.running.set(true);
    try {
      const result = await this.dataServer.runCode(code);
      if (result.error != null) {
        this.append(result.error, 'err');
      } else {
        this.append(result.output ?? '', 'out');
      }
    } finally {
      this.running.set(false);
    }
  }

  clearConsole(): void {
    this.console.set([]);
  }

  private append(text: string, kind: 'out' | 'err'): void {
    // Normalise an empty stdout into a visible marker so a successful run that
    // printed nothing still gives feedback.
    const body = text.length > 0 ? text : kind === 'out' ? '(no output)' : text;
    this.console.update(entries => [...entries, { text: body, kind }]);
  }
}
