import { Injectable, signal, computed } from '@angular/core';
import { SupabaseService } from './supabase.service';
import { AuthService } from './auth.service';
import { Project, CreateProjectDto, UpdateProjectDto } from '../models/project.model';

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
          has_git: false,
          has_github: false,
          has_jira: false,
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
}
