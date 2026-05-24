import { Component, OnInit, computed, signal } from '@angular/core';
import { CurrentProjectService } from '../../../../core/services/current-project.service';
import {
  CatalogueFamilyDto,
  CatalogueFieldDto,
  ConfigOverridesResponse,
  DataServerService,
  ProjectNotFoundError,
} from '../../../../core/services/data-server.service';

type ViewState = 'loading' | 'error' | 'ready' | 'no-project';

/** Maximum "Used by" badges shown inline before collapsing into "+N more". */
const USED_BY_VISIBLE_LIMIT = 3;

/** Single source of truth for catalogue type strings → display pill labels.
 *
 * Covers every reachable catalogue field type. The hidden
 * ``components_mapping_data`` field (``Optional[dict[str, Any]]``) is filtered
 * out at the backend (``_HIDDEN_FIELDS`` in ``catalogue.py``) so it never
 * reaches this map — adding it here would be a lie about what's editable. */
const TYPE_PILL_LABELS: ReadonlyMap<string, string> = new Map<string, string>([
  ['int', 'int'],
  ['float', 'float'],
  ['bool', 'bool'],
  ['str', 'string'],
  ['Optional[str]', 'string?'],
  ['tuple[str, ...]', 'string list'],
  ['list[tuple[str, int]]', 'label/int pairs'],
  ['dict[str, tuple[int, int]]', 'label/range map'],
  ['list[Pattern[str]]', 'regex list'],
  ['list[tuple[str, Pattern[str]]]', 'label/regex pairs'],
]);

/** Tokens that should be upper-cased in human labels instead of title-cased.
 *
 * Hand-curated from the actual editable knob names — these are the only
 * acronyms that appear today (``pr_size_*``, ``polarised_*`` does NOT contain
 * one, ``dx_*`` doesn't surface in editable fields but is a domain term used
 * elsewhere in our copy, ``loc_*`` shows up in ``dynamicblob_loc_min``, ``ci_*``
 * is reserved for a future CI-noise classifier). Keep the set tight so it
 * doesn't silently capitalise unintended substrings (e.g. a knob named
 * ``citation_*`` should NOT get ``CItation``). */
const HUMAN_LABEL_ACRONYMS: ReadonlySet<string> = new Set(['pr', 'dx', 'ci', 'loc']);

/**
 * Stable, type-aware deep equality.
 *
 * Replaces the JSON.stringify byte-identity check used by the scaffold. The
 * catalogue ships defaults and current values through the same backend
 * serialiser, so primitives, arrays and dicts always match byte-for-byte on
 * read — but `JSON.stringify` is also order-sensitive on object keys, so as
 * soon as the editor introduces patch-style writes (commit 7) the simpler
 * helper would silently disagree with the server. The keys-as-set comparison
 * here removes that hidden coupling while keeping the read path correct.
 */
function deepEqual(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (a === null || b === null) return false;
  if (typeof a !== typeof b) return false;
  if (Array.isArray(a)) {
    if (!Array.isArray(b) || a.length !== b.length) return false;
    return a.every((item, i) => deepEqual(item, b[i]));
  }
  if (Array.isArray(b)) return false;
  if (typeof a === 'object' && typeof b === 'object') {
    const aKeys = Object.keys(a as Record<string, unknown>);
    const bKeys = Object.keys(b as Record<string, unknown>);
    if (aKeys.length !== bKeys.length) return false;
    return aKeys.every(
      k =>
        Object.prototype.hasOwnProperty.call(b, k) &&
        deepEqual(
          (a as Record<string, unknown>)[k],
          (b as Record<string, unknown>)[k],
        ),
    );
  }
  return false;
}

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

  // Collapsed-state tracker for the per-family `<details>` panels. Default
  // is "expanded" everywhere; the map only records the explicit overrides
  // the user makes during the session. No localStorage yet — commit 11 may
  // promote this to persistent state if it sticks in user testing.
  private readonly collapsedFamiliesSignal = signal<Map<string, boolean>>(new Map());

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
    const overridden = family.fields.filter(f => !deepEqual(f.current, f.default)).length;
    if (overridden === 0) {
      return `${total} knob${total === 1 ? '' : 's'}`;
    }
    return `${total} knob${total === 1 ? '' : 's'} · ${overridden} overridden`;
  }

  // ── Collapsible families ────────────────────────────────────────────────
  // The Map only carries explicit user toggles; "open by default" is encoded
  // by the absence of an entry. This keeps the signal small (mostly empty)
  // and the template predicate trivial.
  isFamilyOpen(family: CatalogueFamilyDto): boolean {
    return this.collapsedFamiliesSignal().get(family.name) !== true;
  }

  /**
   * Mirror the native `<details>` toggle into our signal so a future
   * "Collapse all" button (commit 11 polish) has a single map to mutate.
   * We can't use `[open]` two-way because Angular doesn't expose a
   * `(openChange)` for the details element; reading from the DOM via
   * `(toggle)` is the idiomatic shape.
   */
  onFamilyToggle(family: CatalogueFamilyDto, event: Event): void {
    const target = event.target as HTMLDetailsElement;
    const collapsed = !target.open;
    this.collapsedFamiliesSignal.update(prev => {
      const next = new Map(prev);
      if (collapsed) {
        next.set(family.name, true);
      } else {
        next.delete(family.name);
      }
      return next;
    });
  }

  // ── Per-knob row helpers ────────────────────────────────────────────────

  isFieldDefault(field: CatalogueFieldDto): boolean {
    return deepEqual(field.current, field.default);
  }

  /** snake_case → Title Case Words, with acronyms upper-cased.
   *
   * Tokens in :data:`HUMAN_LABEL_ACRONYMS` (e.g. ``pr``, ``loc``) become
   * ``PR``/``LOC`` instead of ``Pr``/``Loc`` so domain shorthand reads
   * naturally. Everything else gets a plain Title Case pass. */
  humanLabel(fieldName: string): string {
    return fieldName
      .split('_')
      .filter(Boolean)
      .map(part => {
        if (HUMAN_LABEL_ACRONYMS.has(part.toLowerCase())) {
          return part.toUpperCase();
        }
        return part.charAt(0).toUpperCase() + part.slice(1);
      })
      .join(' ');
  }

  /**
   * Friendly label for the type pill. Falls back to the raw type string
   * when the catalogue surfaces a shape we haven't enumerated — better to
   * leak the dataclass type than to drop information.
   */
  typeLabel(typeString: string): string {
    return TYPE_PILL_LABELS.get(typeString) ?? typeString;
  }

  /** First-N metric names with the "+M more" tail captured separately. */
  visibleMetricNames(field: CatalogueFieldDto): string[] {
    return field.metric_names.slice(0, USED_BY_VISIBLE_LIMIT);
  }

  overflowMetricCount(field: CatalogueFieldDto): number {
    return Math.max(0, field.metric_names.length - USED_BY_VISIBLE_LIMIT);
  }

  overflowMetricTitle(field: CatalogueFieldDto): string {
    return field.metric_names.slice(USED_BY_VISIBLE_LIMIT).join(', ');
  }

  /**
   * Render a value into a single human-readable line.
   *
   * The catalogue surfaces six concrete shapes (int / float / bool / string
   * / list / dict). Lists are truncated with an ellipsis past 4 entries so
   * a 17-pattern `nature_patterns` default doesn't blow the row layout;
   * the full value remains accessible via the row's `title` tooltip.
   */
  formatValue(value: unknown): string {
    if (value === null || value === undefined) return '—';
    if (typeof value === 'boolean') return value ? 'true' : 'false';
    if (typeof value === 'number') {
      if (!Number.isFinite(value)) return String(value);
      if (Number.isInteger(value)) return value.toString();
      // Clamp float precision so an override saved as 0.7000000000000001 (a
      // common JS float artefact) doesn't visually scream "modified" by
      // rendering 17 trailing digits next to a clean 0.7 default.
      return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
    }
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      if (value.length === 0) return '[]';
      const head = value.slice(0, 4).map(v => this.formatValue(v));
      const more = value.length - head.length;
      return more > 0 ? `${head.join(', ')}, … (+${more})` : head.join(', ');
    }
    if (typeof value === 'object') {
      const entries = Object.entries(value as Record<string, unknown>);
      if (entries.length === 0) return '{}';
      const head = entries
        .slice(0, 3)
        .map(([k, v]) => `${k}: ${this.formatValue(v)}`);
      const more = entries.length - head.length;
      return more > 0 ? `${head.join(', ')}, … (+${more})` : head.join(', ');
    }
    return String(value);
  }

  /** Full JSON serialisation used as a row tooltip so the truncated
   * collection values are inspectable without leaving the page. HTML
   * ``title=""`` collapses whitespace and many browsers truncate long
   * tooltips, so the compact (no-indent) form maximises useful content. */
  fullValueTitle(value: unknown): string {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
}
