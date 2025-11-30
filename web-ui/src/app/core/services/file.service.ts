import { Injectable, signal } from '@angular/core';
import { SupabaseService } from './supabase.service';
import { AuthService } from './auth.service';
import {
  SerializedFile,
  FileType,
  getFileTypeFromName,
  isValidSerializedFileName,
} from '../models/project.model';

const BUCKET_NAME = 'serialized-files';
const MAX_FILE_SIZE_MB = 50; // Configurable - Supabase default is 50MB
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

@Injectable({
  providedIn: 'root',
})
export class FileService {
  private readonly loadingSignal = signal<boolean>(false);
  private readonly errorSignal = signal<string | null>(null);

  readonly loading = this.loadingSignal.asReadonly();
  readonly error = this.errorSignal.asReadonly();

  constructor(
    private supabase: SupabaseService,
    private authService: AuthService
  ) {}

  /**
   * Validate a file before upload
   */
  validateFile(file: File): FileValidationResult {
    // Check filename
    if (!isValidSerializedFileName(file.name)) {
      return {
        valid: false,
        error: `Invalid filename. Expected: git.iglog, github.json, or jira.json (case-insensitive)`,
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
   * Load all files for a project
   */
  async loadProjectFiles(projectId: string): Promise<SerializedFile[]> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    try {
      const { data, error } = await this.supabase.client
        .from('serialized_files')
        .select('*')
        .eq('project_id', projectId)
        .order('file_type', { ascending: true });

      if (error) {
        this.errorSignal.set(error.message);
        return [];
      }

      return data ?? [];
    } catch (err) {
      this.errorSignal.set('Failed to load files');
      return [];
    } finally {
      this.loadingSignal.set(false);
    }
  }

  /**
   * Check if a file of the given type already exists for the project
   */
  async checkFileExists(
    projectId: string,
    fileType: FileType
  ): Promise<SerializedFile | null> {
    const { data, error } = await this.supabase.client
      .from('serialized_files')
      .select('*')
      .eq('project_id', projectId)
      .eq('file_type', fileType)
      .single();

    if (error || !data) {
      return null;
    }

    return data;
  }

  /**
   * Upload a file to storage and save metadata to DB
   */
  async uploadFile(
    projectId: string,
    file: File
  ): Promise<SerializedFile | null> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    const user = this.authService.user();
    if (!user) {
      this.errorSignal.set('User not authenticated');
      this.loadingSignal.set(false);
      return null;
    }

    const validation = this.validateFile(file);
    if (!validation.valid) {
      this.errorSignal.set(validation.error!);
      this.loadingSignal.set(false);
      return null;
    }

    const fileType = validation.fileType!;

    try {
      // Generate unique storage path with hash before extension
      const timestamp = Date.now();
      const hash = this.generateHash(user.id, fileType, timestamp);
      const fileName = file.name.toLowerCase();
      const lastDotIndex = fileName.lastIndexOf('.');
      const baseName = lastDotIndex > 0 ? fileName.substring(0, lastDotIndex) : fileName;
      const extension = lastDotIndex > 0 ? fileName.substring(lastDotIndex) : '';
      const storagePath = `${user.id}/${projectId}/${baseName}_${hash}${extension}`;

      // Upload to storage
      const { error: uploadError } = await this.supabase.client.storage
        .from(BUCKET_NAME)
        .upload(storagePath, file, {
          cacheControl: '3600',
          upsert: false,
        });

      if (uploadError) {
        this.errorSignal.set(`Upload failed: ${uploadError.message}`);
        return null;
      }

      // Save metadata to DB
      const { data, error: dbError } = await this.supabase.client
        .from('serialized_files')
        .insert({
          name: file.name,
          file_type: fileType,
          storage_path: storagePath,
          size_bytes: file.size,
          project_id: projectId,
        })
        .select()
        .single();

      if (dbError) {
        // Rollback: delete the uploaded file
        await this.supabase.client.storage
          .from(BUCKET_NAME)
          .remove([storagePath]);
        this.errorSignal.set(`Failed to save file metadata: ${dbError.message}`);
        return null;
      }

      return data;
    } catch (err) {
      this.errorSignal.set('Failed to upload file');
      return null;
    } finally {
      this.loadingSignal.set(false);
    }
  }

  /**
   * Replace an existing file (delete old, upload new)
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
   * Delete a file from storage and DB
   */
  async deleteFile(file: SerializedFile): Promise<boolean> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    try {
      // Delete from storage
      const { error: storageError } = await this.supabase.client.storage
        .from(BUCKET_NAME)
        .remove([file.storage_path]);

      if (storageError) {
        this.errorSignal.set(`Failed to delete from storage: ${storageError.message}`);
        return false;
      }

      // Delete from DB
      const { error: dbError } = await this.supabase.client
        .from('serialized_files')
        .delete()
        .eq('id', file.id);

      if (dbError) {
        this.errorSignal.set(`Failed to delete file record: ${dbError.message}`);
        return false;
      }

      return true;
    } catch (err) {
      this.errorSignal.set('Failed to delete file');
      return false;
    } finally {
      this.loadingSignal.set(false);
    }
  }

  /**
   * Get a download URL for a file
   */
  async getDownloadUrl(file: SerializedFile): Promise<string | null> {
    const { data, error } = await this.supabase.client.storage
      .from(BUCKET_NAME)
      .createSignedUrl(file.storage_path, 3600); // 1 hour expiry

    if (error) {
      this.errorSignal.set(`Failed to generate download URL: ${error.message}`);
      return null;
    }

    return data.signedUrl;
  }

  /**
   * Download a file
   */
  async downloadFile(file: SerializedFile): Promise<void> {
    const url = await this.getDownloadUrl(file);
    if (!url) return;

    // Create a temporary link and click it to download
    const link = document.createElement('a');
    link.href = url;
    link.download = file.name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  /**
   * Generate a hash for unique storage path
   */
  private generateHash(userId: string, fileType: string, timestamp: number): string {
    const input = `${userId}-${fileType}-${timestamp}`;
    // Simple hash - in production you might want a proper hash function
    let hash = 0;
    for (let i = 0; i < input.length; i++) {
      const char = input.charCodeAt(i);
      hash = (hash << 5) - hash + char;
      hash = hash & hash; // Convert to 32-bit integer
    }
    return Math.abs(hash).toString(36);
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
}
