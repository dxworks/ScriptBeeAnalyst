import { Component, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { CurrentProjectService } from '../../../../core/services/current-project.service';
import {
  CatalogueFamilyDto,
  CatalogueFieldDto,
  ConfigOverridesResponse,
  ConfigOverridesValidationError,
  DataServerService,
  ProjectNotFoundError,
} from '../../../../core/services/data-server.service';
import { ToastService } from '../../../../core/services/toast.service';

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

/** Tokens that should be upper-cased in human labels instead of title-cased. */
const HUMAN_LABEL_ACRONYMS: ReadonlySet<string> = new Set(['pr', 'dx', 'ci', 'loc']);

/** Discriminator used by the template to dispatch to the correct input. */
export type KnobInputKind =
  | 'int'
  | 'float'
  | 'bool'
  | 'string'
  | 'string-optional'
  | 'string-list'
  | 'label-int-pairs'
  | 'label-range-map'
  | 'regex-list'
  | 'label-regex-pairs'
  | 'unsupported';

/** Single editable row inside a label/int-pairs composite editor. */
export interface LabelIntPair {
  label: string;
  value: number | null;
}

/** Single editable row inside a label/range-map composite editor. */
export interface LabelRangePair {
  label: string;
  start: number | null;
  end: number | null;
}

/** Single editable row inside a label/regex-pairs composite editor. */
export interface LabelRegexPair {
  label: string;
  regex: string;
}

/**
 * Stable, type-aware deep equality.
 *
 * Replaces the JSON.stringify byte-identity check used by the scaffold. The
 * catalogue ships defaults and current values through the same backend
 * serialiser, so primitives, arrays and dicts always match byte-for-byte on
 * read — but `JSON.stringify` is also order-sensitive on object keys, so as
 * soon as the editor introduces patch-style writes the simpler helper would
 * silently disagree with the server. The keys-as-set comparison here removes
 * that hidden coupling while keeping the read path correct.
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
  imports: [FormsModule],
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
  readonly persistedOverridesCount = computed(
    () => Object.keys(this.response()?.overrides ?? {}).length,
  );
  readonly lastEdited = computed(() => this.response()?.updated_at ?? null);
  readonly hasOverrides = computed(() => this.persistedOverridesCount() > 0);

  // Collapsed-state tracker for the per-family `<details>` panels.
  private readonly collapsedFamiliesSignal = signal<Map<string, boolean>>(new Map());

  // ── Editing state ──────────────────────────────────────────────────────
  // ``pendingOverridesSignal`` holds the user's unsaved edits, keyed by
  // field name. Map mutations always go through a fresh ``new Map(prev)``
  // so signal change detection fires. A key is REMOVED (not set to the
  // current value) when the user reverts an edit back to ``field.current`` —
  // that way ``size === 0`` is the single source of "clean".
  private readonly pendingOverridesSignal = signal<Map<string, unknown>>(new Map());

  /** Server-reported 422 — points the UI at the offending row. */
  readonly fieldError = signal<{ field: string; error: string } | null>(null);

  /**
   * Set of ``"<fieldName>#<rowIndex>"`` keys for composite-editor sub-rows
   * the user has touched. Used to delay "(label required)" / duplicate-label
   * hints until the user actually edits a freshly-added row — newly added
   * empty rows shouldn't render with an error-shaped annotation. Reset
   * alongside ``pendingOverridesSignal`` on save success / discard.
   */
  private readonly compositeDirtyRows = signal<Set<string>>(new Set());

  readonly saving = signal(false);
  readonly rerunning = signal(false);

  /** True iff the user has at least one unsaved edit. */
  readonly dirty = computed(() => this.pendingOverridesSignal().size > 0);

  readonly canSave = computed(
    () => this.dirty() && !this.saving() && this.state() === 'ready',
  );

  /** Rerun stays disabled in commit 7; commit 8 turns it on. */
  readonly canRerun = signal(false);

  /** Pending count shown next to the Save / Discard buttons. */
  readonly pendingCount = computed(() => this.pendingOverridesSignal().size);

  constructor(
    private dataServer: DataServerService,
    private currentProject: CurrentProjectService,
    private toast: ToastService,
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
    const overridden = family.fields.filter(f => this.isFieldOverridden(f)).length;
    if (overridden === 0) {
      return `${total} knob${total === 1 ? '' : 's'}`;
    }
    return `${total} knob${total === 1 ? '' : 's'} · ${overridden} modified`;
  }

  // ── Collapsible families ────────────────────────────────────────────────
  isFamilyOpen(family: CatalogueFamilyDto): boolean {
    return this.collapsedFamiliesSignal().get(family.name) !== true;
  }

  onFamilyToggle(family: CatalogueFamilyDto, event: Event): void {
    const target = event.target as HTMLDetailsElement;
    const collapsed = !target.open;
    this.collapsedFamiliesSignal.update(prev => {
      const next = new Map(prev);
      if (collapsed) next.set(family.name, true);
      else next.delete(family.name);
      return next;
    });
  }

  // ── Per-knob row helpers ────────────────────────────────────────────────

  /** snake_case → Title Case Words, with acronyms upper-cased. */
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

  /** Stable DOM id for ARIA wiring (``aria-labelledby``). */
  knobLabelId(field: CatalogueFieldDto): string {
    return `knob-label-${field.name}`;
  }

  /** Stable DOM id for the inline error region under a row. */
  knobErrorId(field: CatalogueFieldDto): string {
    return `knob-error-${field.name}`;
  }

  typeLabel(typeString: string): string {
    return TYPE_PILL_LABELS.get(typeString) ?? typeString;
  }

  visibleMetricNames(field: CatalogueFieldDto): string[] {
    return field.metric_names.slice(0, USED_BY_VISIBLE_LIMIT);
  }

  overflowMetricCount(field: CatalogueFieldDto): number {
    return Math.max(0, field.metric_names.length - USED_BY_VISIBLE_LIMIT);
  }

  overflowMetricTitle(field: CatalogueFieldDto): string {
    return field.metric_names.slice(USED_BY_VISIBLE_LIMIT).join(', ');
  }

  /** True iff the (pending OR persisted) value differs from the dataclass default. */
  isFieldOverridden(field: CatalogueFieldDto): boolean {
    return !deepEqual(this.effectiveValue(field), field.default);
  }

  /** True iff there is an unsaved edit on this field. */
  isFieldPending(field: CatalogueFieldDto): boolean {
    return this.pendingOverridesSignal().has(field.name);
  }

  /** True iff the server returned a 422 pointing at this field. */
  hasFieldError(field: CatalogueFieldDto): boolean {
    return this.fieldError()?.field === field.name;
  }

  /** Effective value the input should reflect — pending first, else current. */
  effectiveValue(field: CatalogueFieldDto): unknown {
    const pending = this.pendingOverridesSignal();
    return pending.has(field.name) ? pending.get(field.name) : field.current;
  }

  /** Per-type narrowed accessors so the template can stay strictly typed. */
  effectiveString(field: CatalogueFieldDto): string {
    const value = this.effectiveValue(field);
    return value == null ? '' : String(value);
  }

  effectiveNumber(field: CatalogueFieldDto): number {
    const value = this.effectiveValue(field);
    return typeof value === 'number' && Number.isFinite(value) ? value : 0;
  }

  effectiveBool(field: CatalogueFieldDto): boolean {
    return this.effectiveValue(field) === true;
  }

  // ── Input kind dispatcher ───────────────────────────────────────────────
  /**
   * Map the catalogue's raw dataclass type string onto the discriminator
   * the template uses to render the right input. Unrecognised types fall
   * back to ``"unsupported"`` which renders read-only — better than
   * silently dropping a future field type.
   */
  inputKind(field: CatalogueFieldDto): KnobInputKind {
    switch (field.type) {
      case 'int':
        return 'int';
      case 'float':
        return 'float';
      case 'bool':
        return 'bool';
      case 'str':
        return 'string';
      case 'Optional[str]':
        return 'string-optional';
      case 'tuple[str, ...]':
        return 'string-list';
      case 'list[tuple[str, int]]':
        return 'label-int-pairs';
      case 'dict[str, tuple[int, int]]':
        return 'label-range-map';
      case 'list[Pattern[str]]':
        return 'regex-list';
      case 'list[tuple[str, Pattern[str]]]':
        return 'label-regex-pairs';
      default:
        return 'unsupported';
    }
  }

  // ── Pending-state mutation API ──────────────────────────────────────────

  /**
   * Set ``value`` as the pending edit for ``field``. When ``value`` deep-
   * equals ``field.current`` the key is REMOVED instead — that way going
   * "5 → 7 → 5" with a manual revert clears the pending state without a
   * separate "discard this field" gesture.
   */
  setPending(field: CatalogueFieldDto, value: unknown): void {
    this.pendingOverridesSignal.update(prev => {
      const next = new Map(prev);
      if (deepEqual(value, field.current)) {
        next.delete(field.name);
      } else {
        next.set(field.name, value);
      }
      return next;
    });
    // Any further edit dismisses a previous inline error so the user sees
    // the new value isn't pre-flagged. The server is the source of truth
    // for "is this value valid" — we don't try to mirror the validator
    // client-side past obvious shape checks (composite editors).
    if (this.fieldError()?.field === field.name) {
      this.fieldError.set(null);
    }
  }

  /** Reset a single field's pending edit — used by the per-row "Reset" link. */
  resetField(field: CatalogueFieldDto): void {
    this.pendingOverridesSignal.update(prev => {
      if (!prev.has(field.name)) return prev;
      const next = new Map(prev);
      next.delete(field.name);
      return next;
    });
    if (this.fieldError()?.field === field.name) {
      this.fieldError.set(null);
    }
  }

  /** Drop every pending edit and dismiss any inline error. */
  discardAll(): void {
    this.pendingOverridesSignal.set(new Map());
    this.compositeDirtyRows.set(new Set());
    this.fieldError.set(null);
  }

  // ── int / float input adapters ──────────────────────────────────────────
  // Template-driven inputs deliver strings; we coerce to number once at the
  // boundary so the pending map stores the right type. Empty / lone-minus
  // inputs write ``null`` (NOT ``0``) so clearing the field doesn't pretend
  // the user typed a valid 0. The pending-map equality check in
  // ``setPending`` then auto-clears the entry when the current value is
  // already ``null``-equivalent. Saving with a ``null`` numeric is the
  // backstop — the server's coercer 422s on it. Mirrors the composite-
  // editor semantics (``onLabelIntPairValue`` / ``onLabelRangePairStart``).
  onIntInput(field: CatalogueFieldDto, raw: string): void {
    if (raw === '' || raw === '-') {
      this.setPending(field, null);
      return;
    }
    const value = Number.parseInt(raw, 10);
    if (Number.isFinite(value)) this.setPending(field, value);
  }

  onFloatInput(field: CatalogueFieldDto, raw: string): void {
    if (raw === '' || raw === '-') {
      this.setPending(field, null);
      return;
    }
    const value = Number.parseFloat(raw);
    if (Number.isFinite(value)) this.setPending(field, value);
  }

  onBoolChange(field: CatalogueFieldDto, value: boolean): void {
    this.setPending(field, value);
  }

  onStringInput(field: CatalogueFieldDto, value: string): void {
    this.setPending(field, value);
  }

  /** Clear an Optional[str] back to ``null`` via the row's "×" button. */
  clearOptionalString(field: CatalogueFieldDto): void {
    this.setPending(field, null);
  }

  // ── string-list editor (``tuple[str, ...]``) ────────────────────────────
  /**
   * Read the current effective value as a string list. The wire encoding
   * for ``tuple[str, ...]`` is JSON ``string[]`` — guarded fallback to
   * ``[]`` for the rare unsupported shape so the editor never crashes.
   */
  asStringList(field: CatalogueFieldDto): string[] {
    const value = this.effectiveValue(field);
    return Array.isArray(value) ? (value as unknown[]).map(v => String(v)) : [];
  }

  onStringListAdd(field: CatalogueFieldDto, input: HTMLInputElement): void {
    const trimmed = input.value.trim();
    if (!trimmed) return;
    const current = this.asStringList(field);
    const existingIndex = current.indexOf(trimmed);
    if (existingIndex !== -1) {
      // Dedupe: flash the existing chip instead of pushing a duplicate.
      this.flashRejectedChip(field, existingIndex);
      input.value = '';
      return;
    }
    const next = [...current, trimmed];
    this.setPending(field, next);
    input.value = '';
  }

  /**
   * Pulse a chip to acknowledge a duplicate "Add" attempt. The signal
   * carries the ``"<fieldName>#<index>"`` key for ~1s; the template binds
   * a CSS class via ``isChipJustRejected`` so the chip plays a one-shot
   * animation. Cleared via ``setTimeout`` rather than a separate cleanup
   * effect — the cost is one orphan timer per duplicate attempt, which is
   * cheap and self-resolving.
   */
  private readonly chipJustRejected = signal<string | null>(null);

  isChipJustRejected(field: CatalogueFieldDto, index: number): boolean {
    return this.chipJustRejected() === this.rowKey(field, index);
  }

  private flashRejectedChip(field: CatalogueFieldDto, index: number): void {
    const key = this.rowKey(field, index);
    this.chipJustRejected.set(key);
    setTimeout(() => {
      // Only clear if our key is still the active one; a subsequent flash
      // on a different chip must not be wiped by an earlier timer.
      if (this.chipJustRejected() === key) this.chipJustRejected.set(null);
    }, 1000);
  }

  onStringListRemove(field: CatalogueFieldDto, index: number): void {
    const next = this.asStringList(field).filter((_, i) => i !== index);
    this.setPending(field, next);
  }

  // ── regex-list editor (``list[Pattern[str]]``) ──────────────────────────
  // Edited as a multiline textarea, one regex per line. Empty lines drop
  // (a regex that matches nothing is useless and would only confuse the
  // backend coercer). The server compiles each pattern on PUT — we don't
  // try to mirror Python regex flavour client-side.
  asRegexLines(field: CatalogueFieldDto): string {
    const value = this.effectiveValue(field);
    if (!Array.isArray(value)) return '';
    return (value as unknown[]).map(v => String(v)).join('\n');
  }

  onRegexLinesInput(field: CatalogueFieldDto, raw: string): void {
    const lines = raw
      .split('\n')
      .map(line => line.trim())
      .filter(Boolean);
    this.setPending(field, lines);
  }

  // ── label/int-pairs editor (``list[tuple[str, int]]``) ──────────────────
  // The catalogue serialises ``list[tuple[str, int]]`` as ``[[label, value],
  // ...]`` (canonical). We read both that shape AND the descriptive
  // ``[{label, max_days}, ...]`` form (the merge layer tolerates both as
  // defense-in-depth — hand-edited JSONB rows may carry either). We always
  // WRITE the canonical list-of-list form; the router's
  // ``normalize_for_storage`` keeps it canonical end-to-end.
  asLabelIntPairs(field: CatalogueFieldDto): LabelIntPair[] {
    const value = this.effectiveValue(field);
    if (!Array.isArray(value)) return [];
    return (value as unknown[]).map(item => {
      if (Array.isArray(item) && item.length >= 2) {
        const v = item[1];
        return {
          label: String(item[0] ?? ''),
          value: typeof v === 'number' && Number.isFinite(v) ? v : null,
        };
      }
      if (item && typeof item === 'object') {
        const rec = item as Record<string, unknown>;
        const v = rec['max_days'] ?? rec['value'];
        return {
          label: String(rec['label'] ?? ''),
          value: typeof v === 'number' && Number.isFinite(v) ? v : null,
        };
      }
      return { label: '', value: null };
    });
  }

  private writeLabelIntPairs(field: CatalogueFieldDto, rows: LabelIntPair[]): void {
    // Canonical wire form: ``[[label, value], ...]``. We coerce nulls to 0
    // to avoid sending nonsensical payloads — the server's coercer would
    // reject ``null`` anyway, but failing inline is cleaner.
    const next = rows.map(r => [r.label, r.value ?? 0]);
    this.setPending(field, next);
  }

  onLabelIntPairLabel(field: CatalogueFieldDto, index: number, label: string): void {
    const rows = this.asLabelIntPairs(field);
    rows[index] = { ...rows[index], label };
    this.writeLabelIntPairs(field, rows);
  }

  onLabelIntPairValue(field: CatalogueFieldDto, index: number, raw: string): void {
    const rows = this.asLabelIntPairs(field);
    const parsed = raw === '' ? null : Number.parseInt(raw, 10);
    rows[index] = {
      ...rows[index],
      value: parsed !== null && Number.isFinite(parsed) ? parsed : null,
    };
    this.writeLabelIntPairs(field, rows);
  }

  onLabelIntPairAdd(field: CatalogueFieldDto): void {
    const rows = [...this.asLabelIntPairs(field), { label: '', value: 0 }];
    this.writeLabelIntPairs(field, rows);
  }

  onLabelIntPairRemove(field: CatalogueFieldDto, index: number): void {
    const rows = this.asLabelIntPairs(field).filter((_, i) => i !== index);
    this.writeLabelIntPairs(field, rows);
  }

  // ── label/range-map editor (``dict[str, tuple[int, int]]``) ─────────────
  asLabelRangePairs(field: CatalogueFieldDto): LabelRangePair[] {
    const value = this.effectiveValue(field);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
    return Object.entries(value as Record<string, unknown>).map(([label, range]) => {
      if (Array.isArray(range) && range.length >= 2) {
        const a = range[0];
        const b = range[1];
        return {
          label,
          start: typeof a === 'number' && Number.isFinite(a) ? a : null,
          end: typeof b === 'number' && Number.isFinite(b) ? b : null,
        };
      }
      return { label, start: null, end: null };
    });
  }

  private writeLabelRangePairs(
    field: CatalogueFieldDto,
    rows: LabelRangePair[],
  ): void {
    // Canonical dict form: ``{label: [start, end]}``. Empty labels are
    // dropped silently — a row with no label is mid-edit, not a payload.
    const next: Record<string, [number, number]> = {};
    for (const r of rows) {
      if (!r.label) continue;
      next[r.label] = [r.start ?? 0, r.end ?? 0];
    }
    this.setPending(field, next);
  }

  onLabelRangePairLabel(field: CatalogueFieldDto, index: number, label: string): void {
    this.markRowDirty(field, index);
    const rows = this.asLabelRangePairs(field);
    rows[index] = { ...rows[index], label };
    this.writeLabelRangePairs(field, rows);
  }

  onLabelRangePairStart(field: CatalogueFieldDto, index: number, raw: string): void {
    this.markRowDirty(field, index);
    const rows = this.asLabelRangePairs(field);
    const parsed = raw === '' ? null : Number.parseInt(raw, 10);
    rows[index] = {
      ...rows[index],
      start: parsed !== null && Number.isFinite(parsed) ? parsed : null,
    };
    this.writeLabelRangePairs(field, rows);
  }

  onLabelRangePairEnd(field: CatalogueFieldDto, index: number, raw: string): void {
    this.markRowDirty(field, index);
    const rows = this.asLabelRangePairs(field);
    const parsed = raw === '' ? null : Number.parseInt(raw, 10);
    rows[index] = {
      ...rows[index],
      end: parsed !== null && Number.isFinite(parsed) ? parsed : null,
    };
    this.writeLabelRangePairs(field, rows);
  }

  onLabelRangePairAdd(field: CatalogueFieldDto): void {
    // New row deliberately NOT marked dirty — it stays hint-free until the
    // user touches one of its inputs.
    const rows = [...this.asLabelRangePairs(field), { label: '', start: 0, end: 0 }];
    this.writeLabelRangePairs(field, rows);
  }

  onLabelRangePairRemove(field: CatalogueFieldDto, index: number): void {
    const rows = this.asLabelRangePairs(field).filter((_, i) => i !== index);
    this.writeLabelRangePairs(field, rows);
    this.shiftDirtyRowsAfterRemove(field, index);
  }

  // ── label/regex-pairs editor (``list[tuple[str, Pattern[str]]]``) ───────
  asLabelRegexPairs(field: CatalogueFieldDto): LabelRegexPair[] {
    const value = this.effectiveValue(field);
    if (!Array.isArray(value)) return [];
    return (value as unknown[]).map(item => {
      if (Array.isArray(item) && item.length >= 2) {
        return { label: String(item[0] ?? ''), regex: String(item[1] ?? '') };
      }
      if (item && typeof item === 'object') {
        const rec = item as Record<string, unknown>;
        return {
          label: String(rec['label'] ?? ''),
          regex: String(rec['regex'] ?? ''),
        };
      }
      return { label: '', regex: '' };
    });
  }

  private writeLabelRegexPairs(
    field: CatalogueFieldDto,
    rows: LabelRegexPair[],
  ): void {
    // Canonical wire form: ``[[label, regex_source], ...]``. The server's
    // merge layer compiles the regex; client-side we only trim leading and
    // trailing whitespace.
    const next = rows.map(r => [r.label, r.regex.trim()]);
    this.setPending(field, next);
  }

  onLabelRegexPairLabel(field: CatalogueFieldDto, index: number, label: string): void {
    this.markRowDirty(field, index);
    const rows = this.asLabelRegexPairs(field);
    rows[index] = { ...rows[index], label };
    this.writeLabelRegexPairs(field, rows);
  }

  onLabelRegexPairPattern(field: CatalogueFieldDto, index: number, regex: string): void {
    this.markRowDirty(field, index);
    const rows = this.asLabelRegexPairs(field);
    rows[index] = { ...rows[index], regex };
    this.writeLabelRegexPairs(field, rows);
  }

  onLabelRegexPairAdd(field: CatalogueFieldDto): void {
    // New row deliberately NOT marked dirty — see ``onLabelRangePairAdd``.
    const rows = [...this.asLabelRegexPairs(field), { label: '', regex: '' }];
    this.writeLabelRegexPairs(field, rows);
  }

  onLabelRegexPairRemove(field: CatalogueFieldDto, index: number): void {
    const rows = this.asLabelRegexPairs(field).filter((_, i) => i !== index);
    this.writeLabelRegexPairs(field, rows);
    this.shiftDirtyRowsAfterRemove(field, index);
  }

  // ── Composite-editor row hints ──────────────────────────────────────────
  // Two hints fire here, both soft (don't block save — the server is the
  // source of truth for "is this payload valid"):
  //
  //  * "(label required)" — the row has been touched but its label is
  //    blank. A freshly-added row stays hint-free until the user actually
  //    edits one of its inputs, so the affordance reads as feedback,
  //    NOT pre-emptive scolding.
  //  * "(duplicate label)" — two or more rows in the SAME composite editor
  //    share the same non-empty label. Renders on EVERY duplicate row so
  //    the user can pick which one to rename without a "first wins"
  //    surprise. Only used by the dict-shaped editor (``label/range-map``)
  //    where duplicate labels silently collapse on PUT; list-of-pair
  //    editors preserve duplicates so they don't get this hint.

  private rowKey(field: CatalogueFieldDto, index: number): string {
    return `${field.name}#${index}`;
  }

  /** Flag the given row as user-edited so its hints can render. */
  private markRowDirty(field: CatalogueFieldDto, index: number): void {
    const key = this.rowKey(field, index);
    this.compositeDirtyRows.update(prev => {
      if (prev.has(key)) return prev;
      const next = new Set(prev);
      next.add(key);
      return next;
    });
  }

  /**
   * Re-key the dirty-row set after a row was removed at ``removedIndex``.
   * Every higher-indexed dirty row shifts down by one; the removed row's
   * key drops. Without this the hints would land on the wrong rows after
   * a delete-from-middle.
   */
  private shiftDirtyRowsAfterRemove(
    field: CatalogueFieldDto,
    removedIndex: number,
  ): void {
    this.compositeDirtyRows.update(prev => {
      const next = new Set<string>();
      const prefix = `${field.name}#`;
      for (const key of prev) {
        if (!key.startsWith(prefix)) {
          next.add(key);
          continue;
        }
        const idx = Number.parseInt(key.slice(prefix.length), 10);
        if (idx === removedIndex) continue;
        if (idx > removedIndex) next.add(`${prefix}${idx - 1}`);
        else next.add(key);
      }
      return next;
    });
  }

  /** True iff the row has been touched by the user this session. */
  isCompositeRowDirty(field: CatalogueFieldDto, index: number): boolean {
    return this.compositeDirtyRows().has(this.rowKey(field, index));
  }

  /** Show "(label required)" iff the row has been touched AND label is blank. */
  needsLabel(field: CatalogueFieldDto, index: number, label: string): boolean {
    return this.isCompositeRowDirty(field, index) && label.trim() === '';
  }

  /** Pre-compute the set of duplicate labels for a label/range-map field. */
  duplicateLabels(field: CatalogueFieldDto): Set<string> {
    const seen = new Map<string, number>();
    const dupes = new Set<string>();
    for (const row of this.asLabelRangePairs(field)) {
      const label = row.label.trim();
      if (!label) continue;
      const prior = seen.get(label) ?? 0;
      if (prior >= 1) dupes.add(label);
      seen.set(label, prior + 1);
    }
    return dupes;
  }

  isDuplicateLabel(field: CatalogueFieldDto, label: string): boolean {
    if (!label.trim()) return false;
    return this.duplicateLabels(field).has(label.trim());
  }

  // ── Save flow ───────────────────────────────────────────────────────────
  async onSave(): Promise<void> {
    const projectId = this.currentProject.loadedProjectId();
    if (!projectId || !this.canSave()) return;

    const persisted = this.response()?.overrides ?? {};
    const pending = this.pendingOverridesSignal();

    // Merge persisted dict with the pending edits — the PUT endpoint
    // replaces the whole dict, so we must send the full effective state.
    const merged: Record<string, unknown> = { ...persisted };
    for (const [name, value] of pending.entries()) {
      merged[name] = value;
    }

    this.saving.set(true);
    this.fieldError.set(null);
    try {
      await this.dataServer.putConfigOverrides(projectId, merged);
      // Refetch the catalogue+overrides so ``current`` reflects the
      // server's authoritative state (it may have normalised composite
      // shapes back to the canonical form), then clear pending.
      const refreshed = await this.dataServer.getConfigOverrides(projectId);
      this.response.set(refreshed);
      this.pendingOverridesSignal.set(new Map());
      this.compositeDirtyRows.set(new Set());
      this.toast.success('Configuration saved');
    } catch (err) {
      if (err instanceof ConfigOverridesValidationError) {
        this.fieldError.set({ field: err.field, error: err.message });
        this.toast.warning(`Some changes weren't saved: ${err.message}`);
      } else {
        const message =
          err instanceof Error ? err.message : 'Failed to save configuration';
        this.toast.error(message);
      }
    } finally {
      this.saving.set(false);
    }
  }
}
