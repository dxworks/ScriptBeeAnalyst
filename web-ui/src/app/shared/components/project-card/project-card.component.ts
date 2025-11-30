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

  edit = output<Project>();
  delete = output<Project>();
  upload = output<Project>();
  openChat = output<Project>();
  view = output<Project>();

  onEdit(): void {
    this.edit.emit(this.project());
  }

  onDelete(): void {
    this.delete.emit(this.project());
  }

  onUpload(): void {
    this.upload.emit(this.project());
  }

  onOpenChat(): void {
    this.openChat.emit(this.project());
  }

  onView(): void {
    this.view.emit(this.project());
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
}
