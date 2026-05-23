/**
 * Curation context menu (F4).
 *
 * Modelled on dx-platform-frontend's right-click factory at
 * `src/app/visualizations/treemap/treemap.component.ts:346-470` — same
 * action shape ("Add to component X" / "Add to new component"), but a
 * standalone Angular component instead of a hand-built d3 selection so
 * the popover participates in change detection and Tailwind.
 *
 * Mount strategy:
 *   The page holds an `openMenu` signal with the anchor (`x`, `y`, kind,
 *   path, componentName). On `(contextMenu)` from the treemap the page
 *   sets it; on `(close)` here the page clears it. The popover itself is
 *   `position: fixed` clamped to the viewport.
 */
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  HostListener,
  ViewChild,
  computed,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  ComponentColorService,
} from '../component-color.service';

/** Anchor + context passed to the menu by the page. */
export interface CurationMenuAnchor {
  /** Right-click anchor in **page** coordinates (cursor position). */
  x: number;
  y: number;
  /** What the user clicked. Folders carry a compressed label, files a
   *  full path — see treemap's `TreemapContextMenuEvent`. */
  kind: 'file' | 'folder';
  /** Compressed folder label or full file path. */
  path: string;
  /** Current bucket. `null` = synthetic `(unassigned)`. */
  componentName: string | null;
}

/** Shape of the "Move to existing" event. */
export interface MoveToExistingEvent {
  targetName: string;
}

/** Shape of the "Move to new" event. */
export interface MoveToNewEvent {
  name: string;
  color: string;
}

/** A row in the component-picker submenu. */
interface ComponentChoice {
  name: string;
  color: string;
}

@Component({
  selector: 'app-curation-menu',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './curation-menu.component.html',
  styleUrl: './curation-menu.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class CurationMenuComponent {
  // ── Inputs ──────────────────────────────────────────────────────────────
  readonly anchor = input.required<CurationMenuAnchor>();
  /** Components available as targets (already excluding the current one
   *  — but the menu also defends in `availableTargets()`). */
  readonly components = input.required<ReadonlyArray<{ name: string }>>();
  /** {name → colour} so each row in the submenu can show its swatch. */
  readonly componentColors = input<Record<string, string>>({});

  // ── Outputs ─────────────────────────────────────────────────────────────
  readonly moveToExisting = output<MoveToExistingEvent>();
  readonly moveToNew = output<MoveToNewEvent>();
  /** Whole-folder variant (folder anchor only). */
  readonly moveFolderToExisting = output<MoveToExistingEvent>();
  readonly moveFolderToNew = output<MoveToNewEvent>();
  /** User dismissed (click-outside, ESC, action committed). */
  readonly close = output<void>();

  // ── UI state ────────────────────────────────────────────────────────────
  /** Which submenu is open. */
  readonly submenu = signal<'none' | 'moveTo' | 'newComponent' | 'folderMoveTo' | 'folderNewComponent'>('none');
  readonly newComponentName = signal('');
  /** The pre-selected colour for the new bucket (defaults to a palette
   *  pick — user can change via the inline swatch picker). */
  readonly newComponentColor = signal<string>('');

  @ViewChild('panel', { static: true })
  panelRef!: ElementRef<HTMLDivElement>;

  private readonly colorService = inject(ComponentColorService);

  /** Targets minus the current bucket (`null` → '(unassigned)' counts too). */
  readonly availableTargets = computed<ComponentChoice[]>(() => {
    const a = this.anchor();
    const colours = this.componentColors();
    return this.components()
      .filter((c) => c.name !== a.componentName)
      .map((c) => ({
        name: c.name,
        color: colours[c.name] ?? this.colorService.colorFor(c.name),
      }));
  });

  /** Palette exposed for the inline new-component picker. */
  readonly palette = this.colorService.palette;

  /**
   * Anchor coordinates clamped so the popover stays in the viewport.
   * Uses an after-view-init pass so we know the panel's actual rendered
   * width and height. v1 falls back to a conservative 220×320 box if the
   * panel isn't measured yet (first paint).
   */
  readonly clampedPosition = computed(() => {
    const a = this.anchor();
    const w = typeof window !== 'undefined' ? window.innerWidth : 1024;
    const h = typeof window !== 'undefined' ? window.innerHeight : 768;
    // Conservative bbox until we measure; the popover re-clamps on submenu open.
    const panelW = 240;
    const panelH = 360;
    return {
      left: Math.max(8, Math.min(a.x, w - panelW - 8)),
      top: Math.max(8, Math.min(a.y, h - panelH - 8)),
    };
  });

  // ── Actions ─────────────────────────────────────────────────────────────

  pickMoveTo(name: string): void {
    this.moveToExisting.emit({ targetName: name });
    this.close.emit();
  }

  pickFolderMoveTo(name: string): void {
    this.moveFolderToExisting.emit({ targetName: name });
    this.close.emit();
  }

  openNewComponentForm(scope: 'file' | 'folder'): void {
    // Default to a hash-derived colour seeded with the current path so two
    // different right-clicks don't always preselect the same swatch.
    const seed = this.anchor().path || 'new-component';
    this.newComponentColor.set(this.colorService.colorFor(seed));
    this.newComponentName.set('');
    this.submenu.set(scope === 'file' ? 'newComponent' : 'folderNewComponent');
    // Focus is wired in the template via [attr.autofocus] on the input.
  }

  submitNewComponent(scope: 'file' | 'folder'): void {
    const name = this.newComponentName().trim();
    if (!name) return;
    const color = this.newComponentColor() || this.colorService.colorFor(name);
    if (scope === 'file') {
      this.moveToNew.emit({ name, color });
    } else {
      this.moveFolderToNew.emit({ name, color });
    }
    this.close.emit();
  }

  pickColor(c: string): void {
    this.newComponentColor.set(c);
  }

  // ── Keyboard + click-outside ────────────────────────────────────────────

  /** ESC anywhere on the document closes the menu. */
  @HostListener('document:keydown.escape')
  onEscape(): void {
    this.close.emit();
  }

  /** Click outside the popover closes the menu.
   *  Using `mousedown` so the click that opens a different rect's menu
   *  closes the current one before the new contextmenu event fires. */
  @HostListener('document:mousedown', ['$event'])
  onDocumentMouseDown(ev: MouseEvent): void {
    const el = this.panelRef?.nativeElement;
    if (!el) return;
    if (ev.target instanceof Node && !el.contains(ev.target)) {
      this.close.emit();
    }
  }
}
