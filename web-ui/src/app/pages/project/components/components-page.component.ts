import { Component, OnInit, computed, signal } from '@angular/core';
import { DecimalPipe } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import {
  ComponentFileDto,
  ComponentSummaryDto,
  DataServerService,
  ProjectNotLoadedError,
} from '../../../core/services/data-server.service';
import { CurrentProjectService } from '../../../core/services/current-project.service';

type PageStatus = 'loading' | 'ready' | 'not-loaded' | 'error';

@Component({
  selector: 'app-components-page',
  standalone: true,
  imports: [DecimalPipe],
  templateUrl: './components-page.component.html',
  styleUrl: './components-page.component.scss',
})
export class ComponentsPageComponent implements OnInit {
  // ── Data ──────────────────────────────────────────────────────────────────
  readonly components = signal<ComponentSummaryDto[]>([]);
  readonly files = signal<ComponentFileDto[]>([]);
  readonly status = signal<PageStatus>('loading');
  readonly errorMessage = signal<string | null>(null);

  // ── Curation draft state ──────────────────────────────────────────────────
  // F4 will mutate this when the right-click menu reassigns files. The pill
  // in the header reads `hasPendingChanges`; the Save handler is a stub that
  // will become a real call to `updateComponentMapping` later.
  readonly hasPendingChanges = signal(false);
  readonly selectedComponentName = signal<string | null>(null);

  // ── Route + project context ───────────────────────────────────────────────
  readonly projectId = signal<string | null>(null);
  readonly loadedProjectName;

  // Convenience for the template: components sorted by total LOC desc.
  readonly sortedComponents = computed(() =>
    [...this.components()].sort((a, b) => b.total_loc - a.total_loc),
  );

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private dataServer: DataServerService,
    public currentProject: CurrentProjectService,
  ) {
    this.loadedProjectName = this.currentProject.loadedProjectName;
  }

  ngOnInit(): void {
    const id = this.route.parent?.snapshot.paramMap.get('id') ?? null;
    this.projectId.set(id);
    if (id) {
      this.loadData(id);
    } else {
      this.status.set('error');
      this.errorMessage.set('No project id in the route.');
    }
  }

  async loadData(projectId: string): Promise<void> {
    this.status.set('loading');
    this.errorMessage.set(null);

    try {
      const [components, files] = await Promise.all([
        this.dataServer.getComponents(projectId),
        this.dataServer.getComponentFiles(projectId),
      ]);
      this.components.set(components);
      this.files.set(files);
      this.status.set('ready');
    } catch (err) {
      if (err instanceof ProjectNotLoadedError) {
        this.status.set('not-loaded');
        return;
      }
      this.status.set('error');
      this.errorMessage.set(err instanceof Error ? err.message : 'Failed to load components');
    }
  }

  onComponentClick(name: string): void {
    // F4: drives treemap focus + context-menu defaults. Keep stateful so the
    // treemap (F3) can read it once mounted.
    this.selectedComponentName.set(name);
  }

  // Placeholder for F4 — wired up here so the pill button has somewhere to go.
  async onSaveChanges(): Promise<void> {
    // Intentional stub. F4 will:
    //   1. assemble the draft mapping from selectedComponentName + reassignments
    //   2. call `dataServer.updateComponentMapping(projectId, draft)`
    //   3. on success: reload via `loadData(projectId)` and clear hasPendingChanges
    //   4. on `mappingPersisted` 500: toast "saved but rebuild failed"
    this.hasPendingChanges.set(false);
  }

  goToProject(): void {
    const id = this.projectId();
    if (id) {
      this.router.navigate(['/project', id]);
    } else {
      this.router.navigate(['/project']);
    }
  }
}
