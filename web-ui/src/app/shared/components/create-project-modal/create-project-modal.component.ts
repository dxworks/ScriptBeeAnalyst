import { Component, input, output, signal, OnInit } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Project, CreateProjectDto, UpdateProjectDto } from '../../../core/models/project.model';

@Component({
  selector: 'app-create-project-modal',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './create-project-modal.component.html',
})
export class CreateProjectModalComponent implements OnInit {
  // If project is provided, we're in edit mode
  project = input<Project | null>(null);
  loading = input<boolean>(false);

  saved = output<CreateProjectDto | UpdateProjectDto>();
  cancelled = output<void>();

  name = signal('');
  description = signal('');
  error = signal<string | null>(null);

  get isEditMode(): boolean {
    return this.project() !== null;
  }

  get modalTitle(): string {
    return this.isEditMode ? 'Edit Project' : 'Create New Project';
  }

  get submitText(): string {
    return this.isEditMode ? 'Save Changes' : 'Create Project';
  }

  ngOnInit(): void {
    const proj = this.project();
    if (proj) {
      this.name.set(proj.name);
      this.description.set(proj.description ?? '');
    }
  }

  onSubmit(): void {
    const nameValue = this.name().trim();

    if (!nameValue) {
      this.error.set('Project name is required');
      return;
    }

    this.error.set(null);

    const dto: CreateProjectDto | UpdateProjectDto = {
      name: nameValue,
      description: this.description().trim() || undefined,
    };

    this.saved.emit(dto);
  }

  onCancel(): void {
    this.cancelled.emit();
  }

  onBackdropClick(event: MouseEvent): void {
    if (event.target === event.currentTarget) {
      this.onCancel();
    }
  }
}
