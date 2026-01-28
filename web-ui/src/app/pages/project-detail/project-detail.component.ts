import { Component, OnInit, OnDestroy, computed, signal, effect } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { ProjectService } from '../../core/services/project.service';
import { FileService } from '../../core/services/file.service';
import { ToastService } from '../../core/services/toast.service';
import { DataServerService } from '../../core/services/data-server.service';
import {
  Project,
  ProjectStatus,
  CreateProjectDto,
  UpdateProjectDto,
  SerializedFile,
  FileType,
  getFileTypeFromName,
  isValidSerializedFileName,
} from '../../core/models/project.model';
import { CreateProjectModalComponent } from '../../shared/components/create-project-modal/create-project-modal.component';
import { ConfirmationModalComponent } from '../../shared/components/confirmation-modal/confirmation-modal.component';

type Section = 'description' | 'files' | 'chat';

interface StagedFile {
  file: File;
  fileType: FileType;
  status: 'pending' | 'uploading' | 'error';
  error?: string;
}

@Component({
  selector: 'app-project-detail',
  standalone: true,
  imports: [CreateProjectModalComponent, ConfirmationModalComponent],
  templateUrl: './project-detail.component.html',
  styleUrl: './project-detail.component.scss',
})
export class ProjectDetailComponent implements OnInit, OnDestroy {
  selectedProjectId = signal<string | null>(null);
  selectedSection = signal<Section>('description');
  showCreateModal = signal(false);
  modalLoading = signal(false);

  // Files state
  projectFiles = signal<SerializedFile[]>([]);
  filesLoading = signal(false);
  stagedFiles = signal<StagedFile[]>([]);
  uploadingFiles = signal(false);

  // Replace file confirmation modal
  showReplaceModal = signal(false);
  fileToReplace = signal<{ existing: SerializedFile; newFile: File } | null>(null);

  // Delete file confirmation modal
  showDeleteModal = signal(false);
  fileToDelete = signal<SerializedFile | null>(null);

  // Delete project confirmation modal
  showDeleteProjectModal = signal(false);
  deletingProject = signal(false);

  // Processing state
  processingData = signal(false);

  // Data server loading state
  loadingInDataServer = signal(false);

  // Track which project is currently loaded in data server
  loadedProjectId = signal<string | null>(null);

  // Polling interval reference
  private pollingInterval: any = null;

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

  // Derive data sources from files
  hasGit = computed(() => this.projectFiles().some(f => f.file_type === 'git'));
  hasGithub = computed(() => this.projectFiles().some(f => f.file_type === 'github'));
  hasJira = computed(() => this.projectFiles().some(f => f.file_type === 'jira'));
  hasDataSources = computed(() => this.projectFiles().length > 0);

  // Delete project confirmation message
  deleteProjectMessage = computed(() => {
    const project = this.selectedProject();
    const name = project?.name ?? 'this project';
    return `Are you sure you want to delete "${name}"? This will permanently delete the project and all its uploaded files. This action cannot be undone.`;
  });

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    public projectService: ProjectService,
    public fileService: FileService,
    private toastService: ToastService,
    private dataServerService: DataServerService
  ) {
    // Load files when project changes
    effect(() => {
      const projectId = this.selectedProjectId();
      if (projectId) {
        this.loadFiles(projectId);
      } else {
        this.projectFiles.set([]);
      }
    });

    // Show toast notifications when selected project status changes
    effect(() => {
      const project = this.selectedProject();
      if (!project) return;

      // Only show toasts for certain status changes (avoid on initial load)
      const status = project.status;

      // Track previous status to detect changes
      const previousStatus = this.previousStatus();
      if (previousStatus === status) return; // No change

      this.previousStatus.set(status);

      // Show toast only if we're not currently processing (to avoid duplicate toasts)
      if (this.processingData()) return;

      // Show toasts for status changes
      if (status === 'ready' && previousStatus === 'processing') {
        this.toastService.success('Project graph built successfully!');
      } else if (status === 'error' && previousStatus === 'processing') {
        this.toastService.error('Failed to build project graph');
      } else if (status === 'idle' && previousStatus === 'ready') {
        this.toastService.info('Project suspended to save resources');
      } else if (status === 'ready' && previousStatus === 'resuming') {
        this.toastService.success('Project resumed successfully');
      }
    });
  }

  // Track previous status for toast notifications
  private previousStatus = signal<ProjectStatus | null>(null);

  ngOnInit(): void {
    // Load projects if not already loaded
    if (this.projectService.projects().length === 0) {
      this.projectService.loadProjects();
    }

    // Subscribe to realtime project changes
    this.projectService.subscribeToProjectChanges();

    // Poll data server state to track loaded project
    this.pollServerState(); // Initial poll
    this.pollingInterval = setInterval(() => {
      this.pollServerState();
    }, 5000); // Poll every 5 seconds

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

  ngOnDestroy(): void {
    // Unsubscribe from realtime changes
    this.projectService.unsubscribeFromProjectChanges();

    // Clear polling interval
    if (this.pollingInterval) {
      clearInterval(this.pollingInterval);
      this.pollingInterval = null;
    }
  }

  /**
   * Poll data server to check which project is currently loaded in memory
   */
  private async pollServerState(): Promise<void> {
    const currentProject = await this.dataServerService.getCurrentProject();

    if (currentProject) {
      this.loadedProjectId.set(currentProject.project_id);
    } else {
      this.loadedProjectId.set(null);
    }
  }

  private async loadFiles(projectId: string): Promise<void> {
    this.filesLoading.set(true);
    const files = await this.fileService.loadProjectFiles(projectId);
    this.projectFiles.set(files);
    this.filesLoading.set(false);
  }

  selectProject(project: Project): void {
    this.selectedProjectId.set(project.id);
    this.stagedFiles.set([]); // Clear staged files when switching projects
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

  // File handling
  onFileDrop(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
    const files = event.dataTransfer?.files;
    if (files) {
      this.processSelectedFiles(files);
    }
  }

  onDragOver(event: DragEvent): void {
    event.preventDefault();
    event.stopPropagation();
  }

  onFileSelect(event: Event): void {
    const input = event.target as HTMLInputElement;
    if (input.files) {
      this.processSelectedFiles(input.files);
      // Reset input so the same file can be selected again
      input.value = '';
    }
  }

  private async processSelectedFiles(fileList: FileList): Promise<void> {
    const projectId = this.selectedProjectId();
    if (!projectId) return;

    const invalidFiles: string[] = [];
    const oversizedFiles: string[] = [];

    for (let i = 0; i < fileList.length; i++) {
      const file = fileList[i];

      // Validate filename
      if (!isValidSerializedFileName(file.name)) {
        invalidFiles.push(file.name);
        continue;
      }

      const validation = this.fileService.validateFile(file);
      if (!validation.valid) {
        if (validation.error?.includes('too large')) {
          oversizedFiles.push(file.name);
        } else {
          invalidFiles.push(file.name);
        }
        continue;
      }

      const fileType = validation.fileType!;

      // Check if file of this type already exists (in DB)
      const existingFile = await this.fileService.checkFileExists(projectId, fileType);

      // Also check if already staged
      const alreadyStaged = this.stagedFiles().some(sf => sf.fileType === fileType);

      if (existingFile) {
        // Show confirmation modal to replace
        this.fileToReplace.set({ existing: existingFile, newFile: file });
        this.showReplaceModal.set(true);
      } else if (alreadyStaged) {
        // Replace in staged files
        this.stagedFiles.update(files =>
          files.map(sf => sf.fileType === fileType ? { file, fileType, status: 'pending' as const } : sf)
        );
      } else {
        // Add to staged files
        this.stagedFiles.update(files => [...files, { file, fileType, status: 'pending' as const }]);
      }
    }

    // Show toast notifications for invalid files
    if (invalidFiles.length > 0) {
      const fileNames = invalidFiles.length <= 3
        ? invalidFiles.join(', ')
        : `${invalidFiles.slice(0, 2).join(', ')} and ${invalidFiles.length - 2} more`;
      this.toastService.warning(
        `Invalid file${invalidFiles.length > 1 ? 's' : ''}: ${fileNames}. Expected: git.iglog, github.json, or jira.json`
      );
    }

    if (oversizedFiles.length > 0) {
      const maxSize = this.fileService.getMaxFileSizeMB();
      this.toastService.error(
        `File${oversizedFiles.length > 1 ? 's' : ''} too large (max ${maxSize}MB): ${oversizedFiles.join(', ')}`
      );
    }
  }

  removeStagedFile(fileType: FileType): void {
    this.stagedFiles.update(files => files.filter(sf => sf.fileType !== fileType));
  }

  async confirmReplaceFile(): Promise<void> {
    const replaceData = this.fileToReplace();
    if (!replaceData) return;

    const projectId = this.selectedProjectId();
    if (!projectId) return;

    this.showReplaceModal.set(false);

    // Upload replacing the existing file
    this.uploadingFiles.set(true);
    const result = await this.fileService.replaceFile(
      projectId,
      replaceData.existing,
      replaceData.newFile
    );
    this.uploadingFiles.set(false);

    if (result) {
      // Reload files
      await this.loadFiles(projectId);
    }

    this.fileToReplace.set(null);
  }

  cancelReplaceFile(): void {
    this.showReplaceModal.set(false);
    this.fileToReplace.set(null);
  }

  async uploadStagedFiles(): Promise<void> {
    const projectId = this.selectedProjectId();
    if (!projectId || this.stagedFiles().length === 0) return;

    this.uploadingFiles.set(true);

    for (const staged of this.stagedFiles()) {
      // Update status to uploading
      this.stagedFiles.update(files =>
        files.map(sf => sf.fileType === staged.fileType ? { ...sf, status: 'uploading' as const } : sf)
      );

      const result = await this.fileService.uploadFile(projectId, staged.file);

      if (!result) {
        // Mark as error
        this.stagedFiles.update(files =>
          files.map(sf => sf.fileType === staged.fileType
            ? { ...sf, status: 'error' as const, error: this.fileService.error() ?? 'Upload failed' }
            : sf
          )
        );
      } else {
        // Remove from staged
        this.stagedFiles.update(files => files.filter(sf => sf.fileType !== staged.fileType));
      }
    }

    // Reload files
    await this.loadFiles(projectId);
    this.uploadingFiles.set(false);
  }

  clearStagedFiles(): void {
    this.stagedFiles.set([]);
  }

  async downloadFile(file: SerializedFile): Promise<void> {
    await this.fileService.downloadFile(file);
  }

  // Delete file
  confirmDeleteFile(file: SerializedFile): void {
    this.fileToDelete.set(file);
    this.showDeleteModal.set(true);
  }

  async deleteFile(): Promise<void> {
    const file = this.fileToDelete();
    if (!file) return;

    const projectId = this.selectedProjectId();
    if (!projectId) return;

    this.showDeleteModal.set(false);

    const success = await this.fileService.deleteFile(file);
    if (success) {
      this.toastService.success(`${file.name} deleted successfully`);
      await this.loadFiles(projectId);
    } else {
      this.toastService.error(`Failed to delete ${file.name}`);
    }

    this.fileToDelete.set(null);
  }

  cancelDeleteFile(): void {
    this.showDeleteModal.set(false);
    this.fileToDelete.set(null);
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

  // Process data - update status to 'processing', queue processor will handle build
  async onProcessData(): Promise<void> {
    const project = this.selectedProject();
    if (!project || this.processingData()) return;

    this.processingData.set(true);

    // Update status to 'processing' in the database
    // The data-server queue processor will detect this and build the graph
    const statusUpdate = await this.projectService.updateProjectStatus(project.id, 'processing');

    this.processingData.set(false);

    if (!statusUpdate) {
      this.toastService.error('Failed to update project status');
      return;
    }

    // Show info toast to let user know processing started
    this.toastService.info('Project queued for processing...');

    // Status will be updated to 'ready' or 'error' by the data-server queue processor via realtime
  }

  async onRetryProcessing(): Promise<void> {
    // Retry is the same as processing - call the build endpoint
    await this.onProcessData();
  }

  // Load project in data server
  async onLoadInDataServer(): Promise<void> {
    const project = this.selectedProject();
    if (!project || this.loadingInDataServer()) return;

    this.loadingInDataServer.set(true);

    const result = await this.dataServerService.loadProject(project.id);

    this.loadingInDataServer.set(false);

    if (result.success) {
      this.toastService.success(
        `Project loaded successfully! ${result.stats?.git_commits || 0} commits, ${result.stats?.jira_issues || 0} issues, ${result.stats?.github_prs || 0} PRs`
      );
      // Poll immediately to update loaded state
      await this.pollServerState();
    } else {
      this.toastService.error(result.error || 'Failed to load project in data server');
    }
  }

  // Unload project from data server (unloads whatever is currently loaded)
  async onUnloadFromDataServer(): Promise<void> {
    const loadedId = this.loadedProjectId();
    if (!loadedId || this.loadingInDataServer()) return;

    this.loadingInDataServer.set(true);

    const success = await this.dataServerService.unloadProject(loadedId);

    this.loadingInDataServer.set(false);

    if (success) {
      this.toastService.success('Project unloaded from server memory');
      // Poll immediately to update loaded state
      await this.pollServerState();
    } else {
      this.toastService.error('Failed to unload project from server');
    }
  }

  // Check if current project is loaded in data server
  isCurrentProjectLoaded(): boolean {
    const project = this.selectedProject();
    if (!project) return false;
    return this.loadedProjectId() === project.id;
  }

  // Check if a different project is loaded in data server
  isDifferentProjectLoaded(): boolean {
    const project = this.selectedProject();
    if (!project) return false;
    const loadedId = this.loadedProjectId();
    return !!loadedId && loadedId !== project.id;
  }

  // File type label helper
  getFileTypeLabel(fileType: FileType): string {
    switch (fileType) {
      case 'git':
        return 'Git';
      case 'github':
        return 'GitHub';
      case 'jira':
        return 'JIRA';
    }
  }

  // Delete project
  confirmDeleteProject(): void {
    this.showDeleteProjectModal.set(true);
  }

  async deleteProject(): Promise<void> {
    const project = this.selectedProject();
    if (!project) return;

    this.deletingProject.set(true);
    const success = await this.projectService.deleteProject(project.id);
    this.deletingProject.set(false);
    this.showDeleteProjectModal.set(false);

    if (success) {
      this.toastService.success(`Project "${project.name}" deleted successfully`);
      // Navigate to projects list without selection
      this.selectedProjectId.set(null);
      this.router.navigate(['/projects'], { replaceUrl: true });
    } else {
      this.toastService.error(`Failed to delete project: ${this.projectService.error()}`);
    }
  }

  cancelDeleteProject(): void {
    this.showDeleteProjectModal.set(false);
  }
}
