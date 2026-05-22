import { Component, input, output, signal } from '@angular/core';
import { Project } from '../../../core/models/project.model';
import { ProjectService } from '../../../core/services/project.service';
import { ToastService } from '../../../core/services/toast.service';
import { ConfirmationModalComponent } from '../confirmation-modal/confirmation-modal.component';

@Component({
  selector: 'app-process-data-modal',
  standalone: true,
  imports: [ConfirmationModalComponent],
  template: `
    <app-confirmation-modal
      title="Process Data?"
      [message]="'Queue \\'' + project().name + '\\' for graph processing. The data server will build the project graph from the uploaded files. This may take a moment.'"
      confirmText="Start Processing"
      cancelText="Cancel"
      type="info"
      [loading]="loading()"
      (confirmed)="onConfirm()"
      (cancelled)="onCancel()"
    />
  `,
})
export class ProcessDataModalComponent {
  project = input.required<Project>();
  closed = output<void>();
  processed = output<void>();

  loading = signal(false);

  constructor(
    private projectService: ProjectService,
    private toastService: ToastService,
  ) {}

  async onConfirm(): Promise<void> {
    if (this.loading()) return;
    this.loading.set(true);
    const updated = await this.projectService.updateProjectStatus(this.project().id, 'processing');
    this.loading.set(false);

    if (!updated) {
      this.toastService.error('Failed to update project status');
      return;
    }

    this.toastService.info('Project queued for processing...');
    this.processed.emit();
    this.closed.emit();
  }

  onCancel(): void {
    this.closed.emit();
  }
}
