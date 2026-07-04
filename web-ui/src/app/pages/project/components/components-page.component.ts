import { Component, DestroyRef, OnInit, computed, inject, signal } from '@angular/core';
import { ActivatedRoute, Router } from '@angular/router';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  ComponentFileDto,
  ComponentSummaryDto,
  DataServerService,
  ProjectNotLoadedError,
} from '../../../core/services/data-server.service';
import { CurrentProjectService } from '../../../core/services/current-project.service';
import { ToastService } from '../../../core/services/toast.service';
import { ComponentColorService } from './component-color.service';
import {
  ComponentsTreemapComponent,
  TreemapContextMenuEvent,
  TreemapFileClickEvent,
} from './treemap/components-treemap.component';
import {
  CurationMenuAnchor,
  CurationMenuComponent,
  MoveToExistingEvent,
  MoveToNewEvent,
} from './curation-menu/curation-menu.component';

type PageStatus = 'loading' | 'ready' | 'not-loaded' | 'error';

/**
 * Per-component spec in the mapping draft. Matches the backend wire shape
 * accepted by `parse_component_mapping` at
 * `data-server/src/common/domains/components/resolver.py:52` — fields are
 * `path_prefix` (required) + `extra_paths` (optional list). Colours are
 * NOT part of the wire shape — they live in a sibling `draftColors` map.
 */
interface MappingDraftSpec {
  path_prefix: string;
  extra_paths: string[];
}

@Component({
  selector: 'app-components-page',
  standalone: true,
  imports: [ComponentsTreemapComponent, CurationMenuComponent],
  templateUrl: './components-page.component.html',
  styleUrl: './components-page.component.scss',
})
export class ComponentsPageComponent implements OnInit {
  // ── Data ──────────────────────────────────────────────────────────────────
  readonly components = signal<ComponentSummaryDto[]>([]);
  readonly files = signal<ComponentFileDto[]>([]);
  readonly status = signal<PageStatus>('loading');
  readonly errorMessage = signal<string | null>(null);

  // ── Curation draft state (F4) ─────────────────────────────────────────────
  // The draft is keyed by component name and mirrors the backend's accepted
  // wire shape (`path_prefix` + `extra_paths`). `extraPaths` takes precedence
  // over the natural `path_prefix` when resolving a file to its component —
  // this is how a right-click reassignment moves a file out from under one
  // component and into another without renaming any prefix.
  readonly mappingDraft = signal<Record<string, MappingDraftSpec>>({});

  /** New components created via "Move to new component…" need their colour
   *  to be visible in the treemap immediately, before the backend rebuild
   *  has a chance to send anything back. Colours are NOT part of the wire
   *  shape; they live in this sibling override map. */
  readonly draftColors = signal<Record<string, string>>({});

  /** Per-file overrides for the displayed assignment. Built from the draft
   *  on every change. Key is the file path; value is the target component
   *  name (or `null` to reassign back to (unassigned)). Computed signal so
   *  the displayedFiles derivation stays cheap. */
  readonly draftAssignments = computed<Record<string, string | null>>(() => {
    const draft = this.mappingDraft();
    const out: Record<string, string | null> = {};
    // For each component's extra_paths, claim those paths.
    for (const [name, spec] of Object.entries(draft)) {
      for (const p of spec.extra_paths) {
        out[p] = name;
      }
    }
    return out;
  });

  /** Files() but with draft reassignments folded in. Passed to the treemap
   *  so the user sees the pending edits live. */
  readonly displayedFiles = computed<ComponentFileDto[]>(() => {
    const overrides = this.draftAssignments();
    const baseline = this.files();
    if (Object.keys(overrides).length === 0) return baseline;
    return baseline.map((f) =>
      Object.prototype.hasOwnProperty.call(overrides, f.path)
        ? { ...f, component_name: overrides[f.path] }
        : f,
    );
  });

  /** True iff the draft reassigns at least one file. The header pill reads
   *  this; Save and Discard are visible together when it's true. */
  readonly hasPendingChanges = computed(() => {
    const overrides = this.draftAssignments();
    if (Object.keys(overrides).length === 0) return false;
    // Compare each override against the baseline component_name.
    const baseline = new Map(this.files().map((f) => [f.path, f.component_name]));
    for (const [path, target] of Object.entries(overrides)) {
      if (baseline.get(path) !== target) return true;
    }
    return false;
  });

  /** Distinct file count touched by the draft (badge in the header). */
  readonly pendingChangeCount = computed(() => {
    const overrides = this.draftAssignments();
    const baseline = new Map(this.files().map((f) => [f.path, f.component_name]));
    let n = 0;
    for (const [path, target] of Object.entries(overrides)) {
      if (baseline.get(path) !== target) n++;
    }
    return n;
  });

  /** All components that appear in the treemap, including draft-only ones
   *  that don't have a backend summary yet. The treemap groups files by
   *  `component_name`; this list seeds the bucket-order. */
  readonly displayedComponents = computed<ComponentSummaryDto[]>(() => {
    const base = this.components();
    const knownNames = new Set(base.map((c) => c.name));
    const draftOnly = Object.keys(this.mappingDraft())
      .filter((n) => !knownNames.has(n))
      .map<ComponentSummaryDto>((name) => ({
        name,
        path_prefix: this.mappingDraft()[name]!.path_prefix,
        file_count: 0,
        total_loc: 0,
        color: this.draftColors()[name] ?? null,
      }));
    return [...base, ...draftOnly];
  });

  /** `{name → colour}` passed to the treemap and used for swatches in the
   *  components list. Draft colours win over the backend value and the
   *  hash-derived fallback (see `ComponentColorService.buildColorMap`). */
  readonly componentColors = computed<Record<string, string>>(() =>
    this.colorService.buildColorMap(this.displayedComponents(), this.draftColors()),
  );

  // ── Curation menu mount ───────────────────────────────────────────────────
  readonly openMenu = signal<CurationMenuAnchor | null>(null);

  // ── Save flow ─────────────────────────────────────────────────────────────
  readonly saveInProgress = signal(false);

  // ── Page state plumbing ───────────────────────────────────────────────────
  readonly status_ = this.status; // alias kept for template if needed
  readonly projectId = signal<string | null>(null);
  readonly loadedProjectName;

  private readonly destroyRef = inject(DestroyRef);

  constructor(
    private route: ActivatedRoute,
    private router: Router,
    private dataServer: DataServerService,
    private toast: ToastService,
    private colorService: ComponentColorService,
    public currentProject: CurrentProjectService,
  ) {
    this.loadedProjectName = this.currentProject.loadedProjectName;
  }

  ngOnInit(): void {
    // F1+F2 review carry-over: subscribe to paramMap so the page reloads when
    // the route id changes (e.g. user navigates between two projects without
    // unmounting). Snapshot-only would miss those transitions.
    const paramMap$ =
      this.route.parent?.paramMap ?? this.route.paramMap;
    paramMap$.pipe(takeUntilDestroyed(this.destroyRef)).subscribe((params) => {
      const id = params.get('id');
      const previous = this.projectId();
      if (id === previous) return;
      this.projectId.set(id);
      if (id) {
        this.loadData(id);
      } else {
        this.status.set('error');
        this.errorMessage.set('No project id in the route.');
      }
    });
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
      // Re-seed the draft from the baseline so any "Move whole folder" that
      // re-issues the original prefix still has somewhere to land.
      this.mappingDraft.set(this.draftFromBaseline(components));
      this.draftColors.set({});
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

  /** Build an initial mapping draft from the backend components — each
   *  bucket gets its `path_prefix` and an empty `extra_paths` so subsequent
   *  reassignments only need to append. */
  private draftFromBaseline(
    components: ReadonlyArray<ComponentSummaryDto>,
  ): Record<string, MappingDraftSpec> {
    const out: Record<string, MappingDraftSpec> = {};
    for (const c of components) {
      // Backend may not always send a non-empty `path_prefix` — defend with
      // a fallback to the name so the draft entry is wire-valid (the
      // resolver requires a non-empty `path_prefix`).
      out[c.name] = {
        path_prefix: c.path_prefix && c.path_prefix.length > 0 ? c.path_prefix : c.name,
        extra_paths: [],
      };
    }
    return out;
  }

  // ── Treemap event hooks ─────────────────────────────────────────────────

  onFileClick(_event: TreemapFileClickEvent): void {
    // Intentional stub — inspector panel arrives in a later iteration.
  }

  onContextMenu(event: TreemapContextMenuEvent): void {
    // The treemap's `path` is full for files and compressed for folders;
    // the menu just forwards what it gets to the page when an action fires.
    this.openMenu.set({
      x: event.x,
      y: event.y,
      kind: event.kind,
      path: event.path,
      componentName: event.componentName,
    });
  }

  onMenuClose(): void {
    this.openMenu.set(null);
  }

  // ── Draft mutations driven by the menu ─────────────────────────────────

  onMoveToExisting(event: MoveToExistingEvent): void {
    const a = this.openMenu();
    if (!a || a.kind !== 'file') return;
    this.assignPathsToComponent([a.path], event.targetName);
  }

  onMoveToNew(event: MoveToNewEvent): void {
    const a = this.openMenu();
    if (!a || a.kind !== 'file') return;
    this.ensureDraftBucket(event.name, a.path, event.color);
    this.assignPathsToComponent([a.path], event.name);
  }

  onMoveFolderToExisting(event: MoveToExistingEvent): void {
    const a = this.openMenu();
    if (!a || a.kind !== 'folder') return;
    const paths = this.filesUnderFolderAnchor(a);
    if (paths.length === 0) return;
    this.assignPathsToComponent(paths, event.targetName);
  }

  onMoveFolderToNew(event: MoveToNewEvent): void {
    const a = this.openMenu();
    if (!a || a.kind !== 'folder') return;
    const paths = this.filesUnderFolderAnchor(a);
    if (paths.length === 0) return;
    // Seed the new bucket's prefix from the on-disk prefix of the folder
    // anchor (parent component's prefix + the compressed label).
    const seedPath = paths[0]!;
    this.ensureDraftBucket(event.name, seedPath, event.color);
    this.assignPathsToComponent(paths, event.name);
  }

  /**
   * Resolve the file paths affected by a folder right-click.
   *
   * The treemap emits the **compressed** folder label in `event.path` (e.g.
   * `users/retrievers/dto`) rather than the on-disk prefix. We reconstruct
   * the on-disk prefix by combining it with the parent component's path
   * prefix and then scan the live `files()` snapshot for matches.
   *
   * The (unassigned) bucket is handled the same way — files there have a
   * `component_name === null` and no enclosing prefix, so we match against
   * the bare compressed label.
   */
  private filesUnderFolderAnchor(anchor: CurationMenuAnchor): string[] {
    const compressed = anchor.path;
    const parentName = anchor.componentName;
    let onDiskPrefix: string;

    if (parentName === null) {
      // Unassigned bucket: there's no enclosing prefix. Match the bare label.
      onDiskPrefix = compressed.endsWith('/') ? compressed : compressed + '/';
    } else {
      // The parent component's natural prefix lives in the draft entry (we
      // seeded it from the backend summary). The treemap strips the prefix
      // for compactness when it draws labels, so the on-disk prefix is
      // either: (a) the component's prefix exactly when compressed equals
      // the component name (depth-1 anchor), or (b) componentPrefix + '/' +
      // compressed otherwise.
      const draft = this.mappingDraft();
      const spec = draft[parentName];
      const componentPrefix = spec?.path_prefix ?? parentName;
      if (compressed === parentName) {
        // The user right-clicked the component-level rect itself. Treat
        // every file in the bucket as the candidate set.
        return this.files()
          .filter((f) => f.component_name === parentName)
          .map((f) => f.path);
      }
      const base = componentPrefix.endsWith('/')
        ? componentPrefix.slice(0, -1)
        : componentPrefix;
      onDiskPrefix = `${base}/${compressed}`;
      if (!onDiskPrefix.endsWith('/')) onDiskPrefix += '/';
    }

    // Use the live baseline + current draft assignments to find the set of
    // files that the user expects to move (the displayed view, not the raw
    // backend rows). This matters when the user has already reassigned some
    // files into the folder via earlier right-clicks.
    const displayed = this.displayedFiles();
    const out = displayed
      .filter((f) => {
        const inBucket =
          parentName === null ? f.component_name === null : f.component_name === parentName;
        if (!inBucket) return false;
        return f.path.startsWith(onDiskPrefix);
      })
      .map((f) => f.path);
    return out;
  }

  /**
   * Make sure the named bucket exists in the draft. Used by "Move to new
   * component…" — seeds a `path_prefix` from the first file being moved
   * (so the draft entry passes `parse_component_mapping`'s "non-empty
   * string" check) and stashes the chosen colour.
   */
  private ensureDraftBucket(name: string, seedPath: string, color: string): void {
    this.mappingDraft.update((draft) => {
      if (draft[name]) return draft;
      // Pick a path_prefix that's *plausible* — a leading directory of the
      // first moved file. The backend resolver uses longest-prefix-wins, so
      // a real prefix carrying the moved files would win for everything
      // under it. For new buckets created from a single file, fall back to
      // the file's basename so the entry is non-empty and unambiguous.
      const slashIdx = seedPath.lastIndexOf('/');
      const prefix =
        slashIdx > 0 ? seedPath.slice(0, slashIdx + 1) : seedPath || name;
      return {
        ...draft,
        [name]: { path_prefix: prefix, extra_paths: [] },
      };
    });
    this.draftColors.update((cur) => ({ ...cur, [name]: color }));
  }

  /**
   * Reassign every given path to `targetName`.
   *
   * Precedence note: every component's `extra_paths` is treated as the
   * source of truth for those paths in the draft. We strip the path from
   * every other bucket's `extra_paths` before adding it to the target, so
   * a file never appears in two buckets' `extra_paths`. Files that are in
   * a different bucket via `path_prefix` (not via `extra_paths`) are moved
   * by *adding* them to the target's `extra_paths` — the backend resolver
   * matches longest-prefix-wins, but `extra_paths` are also prefixes; for
   * a full file path, only an exact prefix-match wins, so an `extra_paths`
   * entry of the full file path uniquely names that file.
   */
  private assignPathsToComponent(paths: string[], targetName: string): void {
    if (paths.length === 0) return;
    const pathSet = new Set(paths);
    this.mappingDraft.update((draft) => {
      const next: Record<string, MappingDraftSpec> = {};
      // Strip the paths from any non-target bucket's extra_paths.
      for (const [name, spec] of Object.entries(draft)) {
        if (name === targetName) {
          next[name] = spec; // handled below
        } else {
          next[name] = {
            path_prefix: spec.path_prefix,
            extra_paths: spec.extra_paths.filter((p) => !pathSet.has(p)),
          };
        }
      }
      // Add them to the target. If the target doesn't exist yet (should
      // not happen — ensureDraftBucket is called first for the "new" case
      // and the "existing" case picks names already in the draft), seed it
      // from the first path so the wire shape stays valid.
      const target = next[targetName];
      if (!target) {
        const seed = paths[0]!;
        const slashIdx = seed.lastIndexOf('/');
        const prefix =
          slashIdx > 0 ? seed.slice(0, slashIdx + 1) : seed || targetName;
        next[targetName] = {
          path_prefix: prefix,
          extra_paths: [...new Set(paths)],
        };
      } else {
        const existing = new Set(target.extra_paths);
        for (const p of paths) existing.add(p);
        next[targetName] = {
          path_prefix: target.path_prefix,
          extra_paths: [...existing],
        };
      }
      return next;
    });
  }

  // ── Save / Discard ──────────────────────────────────────────────────────

  /**
   * Persist the curated mapping. Rewritten (not appended) per F4 — the old
   * stub just toggled `hasPendingChanges`.
   *
   * Branching on the service's `ComponentMappingUpdateResult`:
   *   - success: clear draft, reload, success toast.
   *   - 500 + `mappingPersisted: true`: warn the user that the rebuild
   *     failed but their edit was saved; KEEP the draft so they can see
   *     what they tried to commit and retry the rebuild manually.
   *   - 400 / other 5xx: error toast, do not clear the draft.
   *   - network: error toast, do not clear the draft.
   *
   * The rebuild blocks server-side, so the button shows a spinner while
   * the request is in flight (B3 review carry-over).
   */
  async onSaveChanges(): Promise<void> {
    const id = this.projectId();
    if (!id) return;
    if (!this.hasPendingChanges()) return;
    if (this.saveInProgress()) return;

    this.saveInProgress.set(true);
    const payload = this.buildWirePayload();
    const result = await this.dataServer.updateComponentMapping(id, payload);
    this.saveInProgress.set(false);

    if (result.success) {
      this.toast.success('Components updated.');
      this.draftColors.set({});
      await this.loadData(id);
      return;
    }

    if (result.mappingPersisted) {
      // The mapping is on Supabase but the rebuild failed. Keep the draft
      // visible (and the hasPendingChanges pill green) so the user knows
      // what they tried to commit — and surface the rebuild failure path
      // separately. They can retry by clicking Save again, which will
      // re-PUT the same payload and re-trigger the rebuild.
      this.toast.warning(
        `Mapping saved but rebuild failed: ${result.error ?? 'unknown error'}. Try Build Graph.`,
      );
      return;
    }

    if (result.error?.toLowerCase().includes('not loaded')) {
      this.toast.error('Project is not loaded — load it first.');
      this.status.set('not-loaded');
      return;
    }

    this.toast.error(result.error ?? 'Failed to save component mapping.');
  }

  /**
   * Drop the in-memory draft and revert the treemap to the baseline view.
   * Always available next to Save when there are pending changes.
   */
  onDiscardChanges(): void {
    this.mappingDraft.set(this.draftFromBaseline(this.components()));
    this.draftColors.set({});
  }

  /**
   * Build the JSON payload accepted by `parse_component_mapping`.
   * Empty `extra_paths` lists are kept — the resolver coerces a missing /
   * non-list value to `[]` anyway, but emitting them explicitly makes the
   * wire shape obvious in DevTools when debugging.
   */
  private buildWirePayload(): Record<string, MappingDraftSpec> {
    const out: Record<string, MappingDraftSpec> = {};
    for (const [name, spec] of Object.entries(this.mappingDraft())) {
      // Skip the synthetic "(unassigned)" bucket if it somehow leaked into
      // the draft — backend doesn't expect it as a real component.
      if (name === '(unassigned)') continue;
      out[name] = {
        path_prefix: spec.path_prefix,
        extra_paths: [...spec.extra_paths],
      };
    }
    return out;
  }

  // ── Misc ────────────────────────────────────────────────────────────────

  goToProject(): void {
    const id = this.projectId();
    if (id) {
      this.router.navigate(['/project', id]);
    } else {
      this.router.navigate(['/project']);
    }
  }
}
