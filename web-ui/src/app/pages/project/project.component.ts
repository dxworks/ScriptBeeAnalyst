import { Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink, RouterOutlet } from '@angular/router';
import { Subscription } from 'rxjs';
import { ProjectService } from '../../core/services/project.service';
import { CurrentProjectService } from '../../core/services/current-project.service';
import { ToastService } from '../../core/services/toast.service';

@Component({
  selector: 'app-project',
  standalone: true,
  imports: [RouterOutlet, RouterLink],
  templateUrl: './project.component.html',
  styleUrl: './project.component.scss',
})
export class ProjectComponent implements OnInit, OnDestroy {
  routeProjectId = signal<string | null>(null);

  readonly loadedProjectId;
  readonly loadingInProgress;

  private paramsSub?: Subscription;
  private loadAttempted = false;

  constructor(
    public currentProject: CurrentProjectService,
    private projectService: ProjectService,
    private route: ActivatedRoute,
    private router: Router,
    private toastService: ToastService,
  ) {
    this.loadedProjectId = this.currentProject.loadedProjectId;
    this.loadingInProgress = this.currentProject.loading;
  }

  ngOnInit(): void {
    if (this.projectService.projects().length === 0) {
      this.projectService.loadProjects();
    }
    this.projectService.subscribeToProjectChanges();

    this.paramsSub = this.route.paramMap.subscribe(params => {
      const id = params.get('id');
      this.routeProjectId.set(id);
      this.ensureProjectLoaded(id);
    });
  }

  ngOnDestroy(): void {
    this.paramsSub?.unsubscribe();
    this.projectService.unsubscribeFromProjectChanges();
  }

  private async ensureProjectLoaded(routeId: string | null): Promise<void> {
    if (!routeId) return;

    const loaded = this.currentProject.loadedProjectId();
    if (loaded === routeId) return;

    if (this.loadAttempted) return;
    this.loadAttempted = true;

    const result = await this.currentProject.loadProject(routeId);
    if (!result.success) {
      this.toastService.error(result.error ?? 'Failed to load project');
      this.router.navigate(['/dashboard']);
    }
  }
}
