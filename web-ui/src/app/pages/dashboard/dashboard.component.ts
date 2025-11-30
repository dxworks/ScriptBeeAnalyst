import { Component, OnInit, signal } from '@angular/core';
import { Router } from '@angular/router';
import { ProjectService } from '../../core/services/project.service';
import { Project, CreateProjectDto, UpdateProjectDto } from '../../core/models/project.model';
import { ProjectCardComponent } from '../../shared/components/project-card/project-card.component';
import { ConfirmationModalComponent } from '../../shared/components/confirmation-modal/confirmation-modal.component';
import { CreateProjectModalComponent } from '../../shared/components/create-project-modal/create-project-modal.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [
    ProjectCardComponent,
    ConfirmationModalComponent,
    CreateProjectModalComponent,
  ],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit {
  // Modal states
  showCreateModal = signal(false);
  showDeleteModal = signal(false);
  editingProject = signal<Project | null>(null);
  deletingProject = signal<Project | null>(null);
  modalLoading = signal(false);

  constructor(
    private router: Router,
    public projectService: ProjectService
  ) {}

  ngOnInit(): void {
    this.projectService.loadProjects();
  }

  // Create project
  openCreateModal(): void {
    this.editingProject.set(null);
    this.showCreateModal.set(true);
  }

  // Edit project
  onEditProject(project: Project): void {
    this.editingProject.set(project);
    this.showCreateModal.set(true);
  }

  async onSaveProject(dto: CreateProjectDto | UpdateProjectDto): Promise<void> {
    this.modalLoading.set(true);

    const editing = this.editingProject();
    let success: boolean;

    if (editing) {
      const result = await this.projectService.updateProject(editing.id, dto);
      success = result !== null;
    } else {
      const result = await this.projectService.createProject(dto as CreateProjectDto);
      success = result !== null;
    }

    this.modalLoading.set(false);

    if (success) {
      this.showCreateModal.set(false);
      this.editingProject.set(null);
    }
  }

  onCancelCreate(): void {
    this.showCreateModal.set(false);
    this.editingProject.set(null);
  }

  // Delete project
  onDeleteProject(project: Project): void {
    this.deletingProject.set(project);
    this.showDeleteModal.set(true);
  }

  async onConfirmDelete(): Promise<void> {
    const project = this.deletingProject();
    if (!project) return;

    this.modalLoading.set(true);
    const success = await this.projectService.deleteProject(project.id);
    this.modalLoading.set(false);

    if (success) {
      this.showDeleteModal.set(false);
      this.deletingProject.set(null);
    }
  }

  onCancelDelete(): void {
    this.showDeleteModal.set(false);
    this.deletingProject.set(null);
  }

  // View project detail
  onViewProject(project: Project): void {
    this.router.navigate(['/projects', project.id]);
  }

  // Upload (placeholder)
  onUploadFiles(project: Project): void {
    // Navigate to project detail with files section selected
    this.router.navigate(['/projects', project.id], { queryParams: { section: 'files' } });
  }

  // Open chat
  onOpenChat(project: Project): void {
    // Navigate to project detail with chat section selected
    this.router.navigate(['/projects', project.id], { queryParams: { section: 'chat' } });
  }
}
