import { Component, OnInit, computed, signal } from '@angular/core';
import { CurrentProjectService } from '../../../../core/services/current-project.service';
import {
  CatalogueFamilyDto,
  ConfigOverridesResponse,
  DataServerService,
  ProjectNotFoundError,
} from '../../../../core/services/data-server.service';

type ViewState = 'loading' | 'error' | 'ready' | 'no-project';

@Component({
  selector: 'app-enrichment-config',
  standalone: true,
  imports: [],
  templateUrl: './enrichment-config.component.html',
  styleUrl: './enrichment-config.component.scss',
})
export class EnrichmentConfigComponent implements OnInit {
  // Page state.
  readonly state = signal<ViewState>('loading');
  readonly errorMessage = signal<string | null>(null);
  readonly response = signal<ConfigOverridesResponse | null>(null);

  // Convenience views derived from the response.
  readonly families = computed<CatalogueFamilyDto[]>(
    () => this.response()?.catalogue.families ?? [],
  );
  readonly overridesCount = computed(() => Object.keys(this.response()?.overrides ?? {}).length);
  readonly lastEdited = computed(() => this.response()?.updated_at ?? null);
  readonly hasOverrides = computed(() => this.overridesCount() > 0);

  // Both toolbar buttons stay structurally present but disabled until
  // their handlers land: Save in commit 7 (typed inputs + save flow),
  // Rerun in commit 8 (rerun trigger + progress UI). The flags below
  // are wired now so the next two commits only have to flip them on
  // rather than introducing new state.
  readonly dirty = signal(false);
  readonly rerunning = signal(false);
  readonly saveEnabled = signal(false);
  readonly rerunEnabled = signal(false);
  readonly canSave = computed(
    () => this.saveEnabled() && this.dirty() && this.state() === 'ready',
  );
  readonly canRerun = computed(
    () => this.rerunEnabled() && !this.rerunning() && this.state() === 'ready',
  );

  constructor(
    private dataServer: DataServerService,
    private currentProject: CurrentProjectService,
  ) {}

  ngOnInit(): void {
    void this.load();
  }

  async load(): Promise<void> {
    const projectId = this.currentProject.loadedProjectId();
    if (!projectId) {
      this.state.set('no-project');
      return;
    }
    this.state.set('loading');
    this.errorMessage.set(null);
    try {
      const response = await this.dataServer.getConfigOverrides(projectId);
      this.response.set(response);
      this.state.set('ready');
    } catch (err) {
      if (err instanceof ProjectNotFoundError) {
        this.state.set('no-project');
        return;
      }
      this.errorMessage.set(err instanceof Error ? err.message : 'Unexpected error');
      this.state.set('error');
    }
  }

  retry(): void {
    void this.load();
  }

  /**
   * Human-readable "X minutes ago" / ISO-date for the toolbar subhead.
   * Defaults to the ISO string when the row was just touched and `Date`
   * comparisons would round to 0.
   */
  formatLastEdited(iso: string | null): string {
    if (!iso) return '';
    const updated = new Date(iso);
    if (Number.isNaN(updated.getTime())) return iso;
    const diffMs = Date.now() - updated.getTime();
    const minute = 60_000;
    const hour = 60 * minute;
    const day = 24 * hour;
    if (diffMs < minute) return 'just now';
    if (diffMs < hour) {
      const mins = Math.floor(diffMs / minute);
      return `${mins} minute${mins === 1 ? '' : 's'} ago`;
    }
    if (diffMs < day) {
      const hours = Math.floor(diffMs / hour);
      return `${hours} hour${hours === 1 ? '' : 's'} ago`;
    }
    return updated.toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  }

  familyLabel(name: string): string {
    return name.charAt(0).toUpperCase() + name.slice(1);
  }

  familyMeta(family: CatalogueFamilyDto): string {
    const total = family.fields.length;
    const overridden = family.fields.filter(f => !this.isDefault(f.current, f.default)).length;
    if (overridden === 0) {
      return `${total} knob${total === 1 ? '' : 's'}`;
    }
    return `${total} knob${total === 1 ? '' : 's'} · ${overridden} overridden`;
  }

  // Defaults can be primitives, arrays, or dicts. JSON-stringify is good
  // enough for an equality check on the scaffold; commits 6+ replace this
  // with per-field type-aware comparisons.
  private isDefault(current: unknown, fallback: unknown): boolean {
    return JSON.stringify(current) === JSON.stringify(fallback);
  }
}
