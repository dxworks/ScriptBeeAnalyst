import { Injectable, signal, computed } from '@angular/core';
import { SupabaseService } from './supabase.service';
import { AuthService } from './auth.service';
import { Project, ProjectStatus, CreateProjectDto, UpdateProjectDto, SerializedFile } from '../models/project.model';

const BUCKET_NAME = 'serialized-files';

@Injectable({
  providedIn: 'root',
})
export class ProjectService {
  private readonly projectsSignal = signal<Project[]>([]);
  private readonly loadingSignal = signal<boolean>(false);
  private readonly errorSignal = signal<string | null>(null);

  readonly projects = this.projectsSignal.asReadonly();
  readonly loading = this.loadingSignal.asReadonly();
  readonly error = this.errorSignal.asReadonly();
  readonly projectCount = computed(() => this.projectsSignal().length);

  constructor(
    private supabase: SupabaseService,
    private authService: AuthService
  ) {}

  async loadProjects(): Promise<void> {
    this.loadingSignal.set(true);
    this.errorSignal.set(null);

    try {
      const { data, error } = await this.supabase.client
        .from('projects')
        .select('*')
        .order('updated_at', { ascending: false });

      if (error) {
        this.errorSignal.set(error.message);
        return;
      }

      this.projectsSignal.set(data ?? []);
    } catch (err) {
      this.errorSignal.set('Failed to load projects');
    } finally {
      this.loadingSignal.set(false);
    }
  }

  async createProject(dto: CreateProjectDto): Promise<Project | null> {
    this.errorSignal.set(null);

    const user = this.authService.user();
    if (!user) {
      this.errorSignal.set('User not authenticated');
      return null;
    }

    try {
      const { data, error } = await this.supabase.client
        .from('projects')
        .insert({
          name: dto.name,
          description: dto.description ?? null,
          user_id: user.id,
          status: 'draft',
        })
        .select()
        .single();

      if (error) {
        this.errorSignal.set(error.message);
        return null;
      }

      // Add to local state
      this.projectsSignal.update(projects => [data, ...projects]);
      return data;
    } catch (err) {
      this.errorSignal.set('Failed to create project');
      return null;
    }
  }

  async updateProject(id: string, dto: UpdateProjectDto): Promise<Project | null> {
    this.errorSignal.set(null);

    try {
      const { data, error } = await this.supabase.client
        .from('projects')
        .update({
          ...dto,
          updated_at: new Date().toISOString(),
        })
        .eq('id', id)
        .select()
        .single();

      if (error) {
        this.errorSignal.set(error.message);
        return null;
      }

      // Update local state
      this.projectsSignal.update(projects =>
        projects.map(p => (p.id === id ? data : p))
      );
      return data;
    } catch (err) {
      this.errorSignal.set('Failed to update project');
      return null;
    }
  }

  async deleteProject(id: string): Promise<boolean> {
    this.errorSignal.set(null);

    try {
      // First, get all files associated with this project to delete from storage
      const { data: files, error: filesError } = await this.supabase.client
        .from('serialized_files')
        .select('storage_path')
        .eq('project_id', id);

      if (filesError) {
        this.errorSignal.set(filesError.message);
        return false;
      }

      // Delete files from storage if any exist
      if (files && files.length > 0) {
        const storagePaths = files.map((f: { storage_path: string }) => f.storage_path);
        const { error: storageError } = await this.supabase.client.storage
          .from(BUCKET_NAME)
          .remove(storagePaths);

        if (storageError) {
          this.errorSignal.set(`Failed to delete storage files: ${storageError.message}`);
          return false;
        }
      }

      // Now delete the project (serialized_files entries cascade automatically)
      const { error } = await this.supabase.client
        .from('projects')
        .delete()
        .eq('id', id);

      if (error) {
        this.errorSignal.set(error.message);
        return false;
      }

      // Remove from local state
      this.projectsSignal.update(projects =>
        projects.filter(p => p.id !== id)
      );
      return true;
    } catch (err) {
      this.errorSignal.set('Failed to delete project');
      return false;
    }
  }

  getProjectById(id: string): Project | undefined {
    return this.projectsSignal().find(p => p.id === id);
  }

  async updateProjectStatus(id: string, status: ProjectStatus): Promise<Project | null> {
    this.errorSignal.set(null);

    try {
      const { data, error } = await this.supabase.client
        .from('projects')
        .update({
          status,
          updated_at: new Date().toISOString(),
        })
        .eq('id', id)
        .select()
        .single();

      if (error) {
        this.errorSignal.set(error.message);
        return null;
      }

      // Update local state
      this.projectsSignal.update(projects =>
        projects.map(p => (p.id === id ? data : p))
      );
      return data;
    } catch (err) {
      this.errorSignal.set('Failed to update project status');
      return null;
    }
  }
}
