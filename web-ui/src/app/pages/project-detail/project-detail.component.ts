import { Component, OnInit, computed, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { ProjectService } from '../../core/services/project.service';
import { Project, ProjectStatus, CreateProjectDto, UpdateProjectDto } from '../../core/models/project.model';
import { CreateProjectModalComponent } from '../../shared/components/create-project-modal/create-project-modal.component';

type Section = 'description' | 'files' | 'chat';

@Component({
  selector: 'app-project-detail',
  standalone: true,
  imports: [CreateProjectModalComponent],
  templateUrl: './project-detail.component.html',
  styleUrl: './project-detail.component.scss',
})
export class ProjectDetailComponent implements OnInit {
  selectedProjectId = signal<string | null>(null);
  selectedSection = signal<Section>('description');
  showCreateModal = signal(false);
  modalLoading = signal(false);

  // Sorted projects alphabetically
  sortedProjects = computed(() => {
    return [...this.projectService.projects()].sort((a, b) =>
      a.name.localeCompare(b.name)
    );
  });

  // Currently selected project
  selectedProject = computed(() => {
    const id = this.selectedProjectId();
    if (!id) return null;
    return this.projectService.projects().find(p => p.id === id) || null;
  });

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    public projectService: ProjectService
  ) {}

  ngOnInit(): void {
    // Load projects if not already loaded
    if (this.projectService.projects().length === 0) {
      this.projectService.loadProjects();
    }

    // Get project ID from route
    this.route.paramMap.subscribe(params => {
      const id = params.get('id');
      if (id) {
        this.selectedProjectId.set(id);
      }
    });

    // Check for section query param
    this.route.queryParamMap.subscribe(params => {
      const section = params.get('section') as Section;
      if (section && ['description', 'files', 'chat'].includes(section)) {
        this.selectedSection.set(section);
      }
    });
  }

  selectProject(project: Project): void {
    this.selectedProjectId.set(project.id);
    this.router.navigate(['/projects', project.id], { replaceUrl: true });
  }

  selectSection(section: Section): void {
    this.selectedSection.set(section);
  }

  isProjectSelected(project: Project): boolean {
    return this.selectedProjectId() === project.id;
  }

  isSectionSelected(section: Section): boolean {
    return this.selectedSection() === section;
  }

  formatDate(dateString: string): string {
    return new Date(dateString).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  }

  onFileDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    // TODO: Handle file drop
    console.log('Files dropped:', event.dataTransfer?.files);
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
  }

  onFileSelect(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      // TODO: Handle file selection
      console.log('Files selected:', input.files);
    }
  }

  // Create project modal
  openCreateModal(): void {
    this.showCreateModal.set(true);
  }

  async onSaveProject(dto: CreateProjectDto | UpdateProjectDto): Promise<void> {
    this.modalLoading.set(true);
    const result = await this.projectService.createProject(dto as CreateProjectDto);
    this.modalLoading.set(false);

    if (result) {
      this.showCreateModal.set(false);
      // Select the newly created project
      this.selectedProjectId.set(result.id);
      this.router.navigate(['/projects', result.id], { replaceUrl: true });
    }
  }

  onCancelCreate(): void {
    this.showCreateModal.set(false);
  }

  // Status helper methods
  getStatusClass(status: ProjectStatus): string {
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

  getStatusLabel(status: ProjectStatus): string {
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

  hasDataSources(): boolean {
    const p = this.selectedProject();
    if (!p) return false;
    return p.has_git || p.has_github || p.has_jira;
  }

  // Placeholder methods for backend integration
  onBuildGraph(): void {
    const project = this.selectedProject();
    if (!project) return;
    // TODO: Call backend to start graph building
    console.log('Build graph for project:', project.id);
  }

  onRetryProcessing(): void {
    const project = this.selectedProject();
    if (!project) return;
    // TODO: Call backend to retry processing
    console.log('Retry processing for project:', project.id);
  }
}
