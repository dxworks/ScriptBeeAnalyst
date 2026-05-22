import { Component, OnInit, computed, effect, input, output, signal } from '@angular/core';
import { FileService } from '../../../core/services/file.service';
import { ToastService } from '../../../core/services/toast.service';
import {
  FileType,
  Project,
  SerializedFile,
  getRepoNameFromFile,
  isValidSerializedFileName,
} from '../../../core/models/project.model';
import { ConfirmationModalComponent } from '../confirmation-modal/confirmation-modal.component';

interface StagedFile {
  file: File;
  fileType: FileType;
  repoName: string | null;
  status: 'pending' | 'uploading' | 'error';
  error?: string;
}

@Component({
  selector: 'app-upload-files-modal',
  standalone: true,
  imports: [ConfirmationModalComponent],
  templateUrl: './upload-files-modal.component.html',
  styleUrl: './upload-files-modal.component.scss',
})
export class UploadFilesModalComponent implements OnInit {
  project = input.required<Project>();
  closed = output<void>();

  projectFiles = signal<SerializedFile[]>([]);
  filesLoading = signal(false);
  stagedFiles = signal<StagedFile[]>([]);
  uploadingFiles = signal(false);

  // Replace file confirmation
  showReplaceModal = signal(false);
  fileToReplace = signal<{ existing: SerializedFile; newFile: File } | null>(null);

  // Delete file confirmation
  showDeleteModal = signal(false);
  fileToDelete = signal<SerializedFile | null>(null);

  hasAnyFiles = computed(
    () => this.projectFiles().length > 0 || this.stagedFiles().length > 0
  );

  constructor(
    public fileService: FileService,
    private toastService: ToastService,
  ) {
    effect(() => {
      const projectId = this.project()?.id;
      if (projectId) {
        this.loadFiles(projectId);
      }
    });
  }

  ngOnInit(): void {
    this.loadFiles(this.project().id);
  }

  private async loadFiles(projectId: string): Promise<void> {
    this.filesLoading.set(true);
    const files = await this.fileService.loadProjectFiles(projectId);
    this.projectFiles.set(files);
    this.filesLoading.set(false);
  }

  onClose(): void {
    this.closed.emit();
  }

  onBackdropClick(event: MouseEvent): void {
    if (event.target === event.currentTarget) {
      this.onClose();
    }
  }

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
      input.value = '';
    }
  }

  private async processSelectedFiles(fileList: FileList): Promise<void> {
    const projectId = this.project().id;
    // Snapshot before any await; the caller resets input.value synchronously
    // which clears the live FileList.
    const files = Array.from(fileList);

    const invalidFiles: string[] = [];
    const oversizedFiles: string[] = [];

    for (const file of files) {
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
      const repoName = getRepoNameFromFile(file.name);

      const existingFile = await this.fileService.checkFileExists(projectId, fileType, repoName);

      const alreadyStaged = this.stagedFiles().some(
        sf => sf.fileType === fileType && sf.repoName === repoName,
      );

      if (existingFile) {
        this.fileToReplace.set({ existing: existingFile, newFile: file });
        this.showReplaceModal.set(true);
      } else if (alreadyStaged) {
        this.stagedFiles.update(list =>
          list.map(sf =>
            sf.fileType === fileType && sf.repoName === repoName
              ? { file, fileType, repoName, status: 'pending' as const }
              : sf,
          ),
        );
      } else {
        this.stagedFiles.update(list => [
          ...list,
          { file, fileType, repoName, status: 'pending' as const },
        ]);
      }
    }

    if (invalidFiles.length > 0) {
      const fileNames =
        invalidFiles.length <= 3
          ? invalidFiles.join(', ')
          : `${invalidFiles.slice(0, 2).join(', ')} and ${invalidFiles.length - 2} more`;
      this.toastService.warning(
        `Invalid file${invalidFiles.length > 1 ? 's' : ''}: ${fileNames}. ` +
          `Expected: *.iglog, github.json, jira.json, *-lizard.csv, *-codeframe.jsonl, ` +
          `*-external_duplication.csv, *-internal_duplication.json, *-code_smells.json, ` +
          `or *-chronos-tags.json`,
      );
    }

    if (oversizedFiles.length > 0) {
      const maxSize = this.fileService.getMaxFileSizeMB();
      this.toastService.error(
        `File${oversizedFiles.length > 1 ? 's' : ''} too large (max ${maxSize}MB): ${oversizedFiles.join(', ')}`,
      );
    }
  }

  removeStagedFile(staged: StagedFile): void {
    this.stagedFiles.update(list =>
      list.filter(sf => !(sf.fileType === staged.fileType && sf.repoName === staged.repoName)),
    );
  }

  async confirmReplaceFile(): Promise<void> {
    const replaceData = this.fileToReplace();
    if (!replaceData) return;
    const projectId = this.project().id;

    this.showReplaceModal.set(false);
    this.uploadingFiles.set(true);
    const result = await this.fileService.replaceFile(projectId, replaceData.existing, replaceData.newFile);
    this.uploadingFiles.set(false);

    if (result) {
      await this.loadFiles(projectId);
    }
    this.fileToReplace.set(null);
  }

  cancelReplaceFile(): void {
    this.showReplaceModal.set(false);
    this.fileToReplace.set(null);
  }

  async uploadStagedFiles(): Promise<void> {
    const projectId = this.project().id;
    if (this.stagedFiles().length === 0) return;

    this.uploadingFiles.set(true);

    const match = (sf: StagedFile, t: StagedFile) =>
      sf.fileType === t.fileType && sf.repoName === t.repoName;

    for (const staged of this.stagedFiles()) {
      this.stagedFiles.update(list =>
        list.map(sf => (match(sf, staged) ? { ...sf, status: 'uploading' as const } : sf)),
      );

      const result = await this.fileService.uploadFile(projectId, staged.file);
      if (!result) {
        this.stagedFiles.update(list =>
          list.map(sf =>
            match(sf, staged)
              ? { ...sf, status: 'error' as const, error: this.fileService.error() ?? 'Upload failed' }
              : sf,
          ),
        );
      } else {
        this.stagedFiles.update(list => list.filter(sf => !match(sf, staged)));
      }
    }

    await this.loadFiles(projectId);
    this.uploadingFiles.set(false);
  }

  clearStagedFiles(): void {
    this.stagedFiles.set([]);
  }

  async downloadFile(file: SerializedFile): Promise<void> {
    await this.fileService.downloadFile(file);
  }

  confirmDeleteFile(file: SerializedFile): void {
    this.fileToDelete.set(file);
    this.showDeleteModal.set(true);
  }

  async deleteFile(): Promise<void> {
    const file = this.fileToDelete();
    if (!file) return;
    const projectId = this.project().id;

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

  getFileTypeLabel(fileType: FileType): string {
    switch (fileType) {
      case 'git':
        return 'Git';
      case 'github':
        return 'GitHub';
      case 'jira':
        return 'JIRA';
      case 'lizard':
        return 'Lizard (LOC + complexity)';
      case 'codeframe':
        return 'CodeFrame (code structure)';
      case 'dude_external':
        return 'DuDe external duplication';
      case 'dude_internal':
        return 'DuDe internal duplication';
      case 'quality_issues':
        return 'Insider code smells';
      case 'app_inspector':
        return 'AppInspector tags';
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
