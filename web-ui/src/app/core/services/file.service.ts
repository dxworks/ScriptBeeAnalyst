import { Injectable, signal } from '@angular/core';
import { HttpClient, HttpErrorResponse, HttpParams } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import {
  SerializedFile,
  FileType,
  getFileTypeFromName,
  getRepoNameFromFile,
  isValidSerializedFileName,
} from '../models/project.model';

const MAX_FILE_SIZE_MB = 500; // Matches the data-server MAX_UPLOAD_MB default.
const MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024;

export interface FileValidationResult {
  valid: boolean;
  error?: string;
  fileType?: FileType;
}

export interface UploadProgress {
  filename: string;
  progress: number; // 0-100
  status: 'pending' | 'uploading' | 'complete' | 'error';
  error?: string;
}

/** Wire shape of GET /projects/{id}/files/exists. */
interface FileExistsResponse {
  exists: boolean;
  file: SerializedFile | null;
}

@Injectable({
  providedIn: 'root',
})
export class FileService {
  private readonly baseUrl = environment.dataServerUrl;

  private readonly loadingSignal = signal<boolean>(false);
  private readonly errorSignal = signal<string | null>(null);

  readonly loading = this.loadingSignal.asReadonly();
  readonly error = this.errorSignal.asReadonly();

  constructor(private http: HttpClient) {}

  /**
   * Validate a file before upload. The data-server re-validates server-side;
   * this keeps the fast client-side feedback the staging loop relies on.
   */
  validateFile(file: File): FileValidationResult {
    // Check filename
    if (!isValidSerializedFileName(file.name)) {
      return {
        valid: false,
        error:
          `Invalid filename. Expected: *.iglog, github.json, jira.json, ` +
          `*-codeframe.jsonl, *-code_smells.json, *-chronos-tags.json, ` +
          `*-external_duplication.csv, *-internal_duplication.json, or *-lizard.csv`,
      };
    }

    // Check file size
    if (file.size > MAX_FILE_SIZE_BYTES) {
      return {
        valid: false,
        error: `File too large. Maximum size is ${MAX_FILE_SIZE_MB}MB`,
      };
    }

    const fileType = getFileTypeFromName(file.name);
    return { valid: true, fileType: fileType! };
  }

  /**
   * Load all files for a project (GET /projects/{id}/files, ordered by
   * file_type ASC server-side).
   */
  async loadProjectFiles(projectId: string): Promise<SerializedFile[]> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    try {
      const data = await firstValueFrom(
        this.http.get<SerializedFile[]>(`${this.baseUrl}/projects/${projectId}/files`)
      );
      return data ?? [];
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to load files'));
      return [];
    } finally {
      this.loadingSignal.set(false);
    }
  }

  /**
   * Check if a file of the given type (and repo name for git) already exists
   * for the project. Mirrors the old Supabase maybeSingle: returns null on no
   * match (the endpoint never 404s on "0 rows"). Omitting repoName matches
   * rows where repo_name IS NULL.
   */
  async checkFileExists(
    projectId: string,
    fileType: FileType,
    repoName: string | null = null
  ): Promise<SerializedFile | null> {
    let params = new HttpParams().set('file_type', fileType);
    if (repoName !== null) {
      params = params.set('repo_name', repoName);
    }

    try {
      const res = await firstValueFrom(
        this.http.get<FileExistsResponse>(
          `${this.baseUrl}/projects/${projectId}/files/exists`,
          { params }
        )
      );
      return res?.exists ? res.file : null;
    } catch {
      // Match the old behaviour: any failure is treated as "no existing file"
      // so the surrounding multi-file staging loop keeps going.
      return null;
    }
  }

  /**
   * Upload a file via multipart to POST /projects/{id}/files. The data-server
   * validates, writes the bytes to disk, derives the storage_path (server now
   * owns the hash), and inserts the serialized_files row.
   */
  async uploadFile(
    projectId: string,
    file: File
  ): Promise<SerializedFile | null> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    const validation = this.validateFile(file);
    if (!validation.valid) {
      this.errorSignal.set(validation.error!);
      this.loadingSignal.set(false);
      return null;
    }

    const fileType = validation.fileType!;
    const repoName = getRepoNameFromFile(file.name);

    try {
      const form = new FormData();
      form.append('file', file, file.name);
      form.append('file_type', fileType);
      if (repoName !== null) {
        form.append('repo_name', repoName);
      }

      const data = await firstValueFrom(
        this.http.post<SerializedFile>(
          `${this.baseUrl}/projects/${projectId}/files`,
          form
        )
      );
      return data;
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to upload file'));
      return null;
    } finally {
      this.loadingSignal.set(false);
    }
  }

  /**
   * Replace an existing file (delete old, upload new). Two server calls; no
   * dedicated endpoint needed.
   */
  async replaceFile(
    projectId: string,
    existingFile: SerializedFile,
    newFile: File
  ): Promise<SerializedFile | null> {
    // Delete the old file first
    const deleted = await this.deleteFile(existingFile);
    if (!deleted) {
      return null;
    }

    // Upload the new file
    return this.uploadFile(projectId, newFile);
  }

  /**
   * Delete a file via DELETE /projects/{id}/files/{file_id}. The data-server
   * unlinks the bytes from disk then deletes the row in one call.
   */
  async deleteFile(file: SerializedFile): Promise<boolean> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    try {
      await firstValueFrom(
        this.http.delete(
          `${this.baseUrl}/projects/${file.project_id}/files/${file.id}`
        )
      );
      return true;
    } catch (err) {
      this.errorSignal.set(this.errorMessage(err, 'Failed to delete file'));
      return false;
    } finally {
      this.loadingSignal.set(false);
    }
  }

  /**
   * Stable download URL for a file. The data-server streams the bytes with an
   * attachment disposition, so unlike the old Supabase signed URL this needs
   * no async pre-fetch — callers can point a link straight at it.
   */
  getDownloadUrl(file: SerializedFile): string {
    return `${this.baseUrl}/projects/${file.project_id}/files/${file.id}/download`;
  }

  /**
   * Download a file by clicking a temporary link pointed at the stable
   * download endpoint.
   */
  async downloadFile(file: SerializedFile): Promise<void> {
    const url = this.getDownloadUrl(file);

    const link = document.createElement('a');
    link.href = url;
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  /**
   * Format file size for display
   */
  formatFileSize(bytes: number): string {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  }

  /**
   * Get max file size for display
   */
  getMaxFileSizeMB(): number {
    return MAX_FILE_SIZE_MB;
  }

  private errorMessage(err: unknown, fallback: string): string {
    if (err instanceof HttpErrorResponse) {
      return err.error?.error || err.error?.message || err.message || fallback;
    }
    return fallback;
  }
}
