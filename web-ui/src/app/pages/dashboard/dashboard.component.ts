import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { Router } from '@angular/router';
import { ProjectService } from '../../core/services/project.service';
import { CurrentProjectService } from '../../core/services/current-project.service';
import { ToastService } from '../../core/services/toast.service';
import { Project, CreateProjectDto, UpdateProjectDto } from '../../core/models/project.model';
import { ProjectCardComponent } from '../../shared/components/project-card/project-card.component';
import { ConfirmationModalComponent } from '../../shared/components/confirmation-modal/confirmation-modal.component';
import { CreateProjectModalComponent } from '../../shared/components/create-project-modal/create-project-modal.component';
import { UploadFilesModalComponent } from '../../shared/components/upload-files-modal/upload-files-modal.component';
import { ProcessDataModalComponent } from '../../shared/components/process-data-modal/process-data-modal.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [
    ProjectCardComponent,
    ConfirmationModalComponent,
    CreateProjectModalComponent,
    UploadFilesModalComponent,
    ProcessDataModalComponent,
  ],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss',
})
export class DashboardComponent implements OnInit, OnDestroy {
  // Create / edit project modal
  showCreateModal = signal(false);
  editingProject = signal<Project | null>(null);
  createModalLoading = signal(false);

  // Delete project modal
  showDeleteModal = signal(false);
  deletingProject = signal<Project | null>(null);
  deleteModalLoading = signal(false);

  // Upload files modal
  showUploadModal = signal(false);
  uploadingForProject = signal<Project | null>(null);

  // Process data modal
  showProcessModal = signal(false);
  processingForProject = signal<Project | null>(null);

  // Open / load in data server
  openingProjectId = signal<string | null>(null);

  // Unload from data server
  unloadingProjectId = signal<string | null>(null);

  constructor(
    private router: Router,
    public projectService: ProjectService,
    private currentProject: CurrentProjectService,
    private toastService: ToastService,
  ) {}

  ngOnInit(): void {
    this.projectService.loadProjects();
    this.projectService.subscribeToProjectChanges();
  }

  ngOnDestroy(): void {
    this.projectService.unsubscribeFromProjectChanges();
  }

  isProjectLoaded(project: Project): boolean {
    return this.currentProject.loadedProjectId() === project.id;
  }

  // ── Create / edit ──────────────────────────────────────────────────────────

  openCreateModal(): void {
    this.editingProject.set(null);
    this.showCreateModal.set(true);
  }

  onEditProject(project: Project): void {
    this.editingProject.set(project);
    this.showCreateModal.set(true);
  }

  async onSaveProject(dto: CreateProjectDto | UpdateProjectDto): Promise<void> {
    this.createModalLoading.set(true);
    const editing = this.editingProject();

    const result = editing
      ? await this.projectService.updateProject(editing.id, dto as UpdateProjectDto)
      : await this.projectService.createProject(dto as CreateProjectDto);

    this.createModalLoading.set(false);

    if (result) {
      this.toastService.success(editing ? 'Project updated' : `Project "${result.name}" created`);
      this.showCreateModal.set(false);
      this.editingProject.set(null);
    } else {
      this.toastService.error(this.projectService.error() ?? 'Failed to save project');
    }
  }

  onCancelCreate(): void {
    this.showCreateModal.set(false);
    this.editingProject.set(null);
  }

  // ── Delete ─────────────────────────────────────────────────────────────────

  onDeleteProject(project: Project): void {
    this.deletingProject.set(project);
    this.showDeleteModal.set(true);
  }

  async onConfirmDelete(): Promise<void> {
    const project = this.deletingProject();
    if (!project) return;

    this.deleteModalLoading.set(true);
    const success = await this.projectService.deleteProject(project.id);
    this.deleteModalLoading.set(false);

    if (success) {
      this.toastService.success(`Project "${project.name}" deleted`);
      this.showDeleteModal.set(false);
      this.deletingProject.set(null);
    } else {
      this.toastService.error(this.projectService.error() ?? 'Failed to delete project');
    }
  }

  onCancelDelete(): void {
    this.showDeleteModal.set(false);
    this.deletingProject.set(null);
  }

  // ── Upload files ──────────────────────────────────────────────────────────

  onUploadFiles(project: Project): void {
    this.uploadingForProject.set(project);
    this.showUploadModal.set(true);
  }

  onCloseUpload(): void {
    this.showUploadModal.set(false);
    this.uploadingForProject.set(null);
  }

  // ── Process data ──────────────────────────────────────────────────────────

  onProcessData(project: Project): void {
    this.processingForProject.set(project);
    this.showProcessModal.set(true);
  }

  onCloseProcess(): void {
    this.showProcessModal.set(false);
    this.processingForProject.set(null);
  }

  // ── Open / load ───────────────────────────────────────────────────────────

  async onOpenProject(project: Project): Promise<void> {
    if (this.openingProjectId()) return;

    // Already loaded → just navigate.
    if (this.currentProject.loadedProjectId() === project.id) {
      this.router.navigate(['/project', project.id]);
      return;
    }

    this.openingProjectId.set(project.id);
    const result = await this.currentProject.loadProject(project.id);
    this.openingProjectId.set(null);

    if (result.success) {
      this.router.navigate(['/project', project.id]);
    } else {
      this.toastService.error(result.error ?? 'Failed to open project');
    }
  }

  // ── Unload ────────────────────────────────────────────────────────────────

  async onUnloadProject(project: Project): Promise<void> {
    if (this.unloadingProjectId()) return;

    this.unloadingProjectId.set(project.id);
    const ok = await this.currentProject.unloadProject();
    this.unloadingProjectId.set(null);

    if (ok) {
      this.toastService.success(`Project "${project.name}" unloaded`);
    } else {
      this.toastService.error('Failed to unload project');
    }
  }
}
