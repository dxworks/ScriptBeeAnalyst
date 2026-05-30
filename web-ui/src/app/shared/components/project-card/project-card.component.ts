import { Component, input, output } from '@angular/core';
import { Project } from '../../../core/models/project.model';

@Component({
  selector: 'app-project-card',
  standalone: true,
  imports: [],
  templateUrl: './project-card.component.html',
  styleUrl: './project-card.component.scss',
})
export class ProjectCardComponent {
  project = input.required<Project>();
  isLoaded = input<boolean>(false);
  opening = input<boolean>(false);
  unloading = input<boolean>(false);

  edit = output<Project>();
  delete = output<Project>();
  upload = output<Project>();
  process = output<Project>();
  open = output<Project>();
  unload = output<Project>();

  onEdit(): void {
    this.edit.emit(this.project());
  }

  onDelete(): void {
    this.delete.emit(this.project());
  }

  onUpload(): void {
    this.upload.emit(this.project());
  }

  onProcess(): void {
    this.process.emit(this.project());
  }

  onOpen(): void {
    this.open.emit(this.project());
  }

  onUnload(): void {
    this.unload.emit(this.project());
  }

  getStatusClass(): string {
    const status = this.project().status;
    switch (status) {
      case 'ready':
        return 'badge-success';
      case 'processing':
      case 'resuming':
        return 'badge-warning';
      case 'idle':
        return 'badge-info';
      case 'error':
        return 'badge-error';
      default:
        return 'badge-neutral';
    }
  }

  /** True once the project has been finalized (query stage). */
  isFinalized(): boolean {
    return this.project().merge_state === 'FINALIZED';
  }

  /** Lifecycle badge text: query stage vs. setup stage. */
  getLifecycleLabel(): string {
    return this.isFinalized() ? 'Ready' : 'Setup';
  }

  getStatusLabel(): string {
    const status = this.project().status;
    switch (status) {
      case 'ready':
        return 'Ready';
      case 'processing':
        return 'Processing';
      case 'resuming':
        return 'Resuming';
      case 'idle':
        return 'Idle';
      case 'error':
        return 'Error';
      default:
        return 'Draft';
    }
  }

  formatDate(dateString: string): string {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }

  canOpen(): boolean {
    const status = this.project().status;
    return status === 'ready' || status === 'idle';
  }

  canProcess(): boolean {
    const status = this.project().status;
    return status === 'draft' || status === 'error';
  }
}
