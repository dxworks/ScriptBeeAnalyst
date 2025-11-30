import { Component, OnInit, signal } from '@angular/core';
import { Router } from '@angular/router';
import { ProjectService } from '../../core/services/project.service';
import { Project } from '../../core/models/project.model';
import { ProjectCardComponent } from '../../shared/components/project-card/project-card.component';
import { ConfirmationModalComponent } from '../../shared/components/confirmation-modal/confirmation-modal.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [
    ProjectCardComponent,
    ConfirmationModalComponent,
  ],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit {
  // Modal states
  showDeleteModal = signal(false);
  deletingProject = signal<Project | null>(null);
  modalLoading = signal(false);

  constructor(
    private router: Router,
    public projectService: ProjectService
  ) {}

  ngOnInit(): void {
    this.projectService.loadProjects();
  }

  // Edit project - navigate to project detail
  onEditProject(project: Project): void {
    this.router.navigate(['/projects', project.id]);
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

  // Upload - navigate to project detail files section
  onUploadFiles(project: Project): void {
    this.router.navigate(['/projects', project.id], { queryParams: { section: 'files' } });
  }

  // Open chat - navigate to project detail chat section
  onOpenChat(project: Project): void {
    this.router.navigate(['/projects', project.id], { queryParams: { section: 'chat' } });
  }
}
