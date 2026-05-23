import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  ViewChild,
  effect,
  inject,
  input,
  output,
  signal,
} from '@angular/core';
import * as d3 from 'd3';

import {
  ComponentFileDto,
  ComponentSummaryDto,
} from '../../../../core/services/data-server.service';

import {
  compressSingleChildChains,
  type FileNode,
  type FolderNode,
  type TreeNode,
} from './path-compression';

/** Synthetic bucket name for files whose `component_name` is null. */
const UNASSIGNED_BUCKET = '(unassigned)';

/** Min file size used for the d3.sum() weight; mirrors `sumBySize` (~30) at
 *  line 139 of the dx-platform-frontend treemap reference — tiny files would
 *  otherwise collapse to invisible slivers in the treemap. */
const MIN_FILE_WEIGHT = 30;

/** Fallback colour when `componentColors` has no entry for a bucket (F5 will
 *  provide a real palette). Matches the swatch fallback used in the page
 *  shell (`#9ca3af`-ish neutral). */
const FALLBACK_COMPONENT_COLOR = '#94a3b8';

/** Neutral fill for file rects, design-token-aligned (light surface tone). */
const FILE_FILL = '#ffffff';

/** A d3.hierarchy node carrying our TreeNode data. */
type HierarchyDatum = TreeNode & { __value?: number };

/** Output payload for the right-click context-menu hook (F4 will consume). */
export interface TreemapContextMenuEvent {
  kind: 'file' | 'folder';
  /** For files: full file path. For folders: the composite folder label
   *  (e.g. `users/retrievers/dto`) — F4 can rebuild the prefix from this. */
  path: string;
  /** The component this anchor belongs to. `null` only when the anchor is in
   *  the synthetic `(unassigned)` bucket. */
  componentName: string | null;
  /** Page coordinates of the cursor (for popover positioning). */
  x: number;
  y: number;
}

export interface TreemapFileClickEvent {
  path: string;
  componentName: string | null;
}

interface TooltipState {
  visible: boolean;
  /** Page-relative pixel position; the tooltip is `position: fixed`. */
  x: number;
  y: number;
  path: string;
  loc: number;
  componentName: string;
}

@Component({
  selector: 'app-components-treemap',
  standalone: true,
  templateUrl: './components-treemap.component.html',
  styleUrl: './components-treemap.component.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ComponentsTreemapComponent {
  // ── Inputs ────────────────────────────────────────────────────────────────
  readonly files = input.required<ComponentFileDto[]>();
  readonly components = input.required<ComponentSummaryDto[]>();
  readonly componentColors = input<Record<string, string>>({});
  /** Selected component (driven by the list pane). Currently used to dim
   *  non-selected buckets; null = no selection / show everything. */
  readonly selectedComponentName = input<string | null>(null);

  // ── Outputs ───────────────────────────────────────────────────────────────
  readonly fileClick = output<TreemapFileClickEvent>();
  readonly contextMenu = output<TreemapContextMenuEvent>();

  // ── Template refs ─────────────────────────────────────────────────────────
  @ViewChild('host', { static: true })
  hostRef!: ElementRef<HTMLDivElement>;
  @ViewChild('svg', { static: true })
  svgRef!: ElementRef<SVGSVGElement>;

  // ── Reactive state for the lightweight HTML tooltip ───────────────────────
  readonly tooltip = signal<TooltipState>({
    visible: false,
    x: 0,
    y: 0,
    path: '',
    loc: 0,
    componentName: '',
  });

  private readonly destroyRef = inject(DestroyRef);

  // Size signal so the render effect re-runs on ResizeObserver ticks too.
  private readonly size = signal<{ w: number; h: number }>({ w: 0, h: 0 });

  private resizeObserver: ResizeObserver | null = null;

  constructor() {
    // Re-render whenever inputs or size change. d3 is allowed to read DOM
    // inside the effect — the effect is a side-effect step, not a derivation.
    effect(() => {
      const f = this.files();
      const c = this.components();
      const colors = this.componentColors();
      const selected = this.selectedComponentName();
      const { w, h } = this.size();
      if (w <= 0 || h <= 0) {
        return;
      }
      this.render(f, c, colors, selected, w, h);
    });

    // ResizeObserver is attached after the view is initialised so hostRef is
    // guaranteed to exist. Using a small bootstrap effect keeps the wiring
    // local to the constructor.
    queueMicrotask(() => this.attachResizeObserver());

    this.destroyRef.onDestroy(() => {
      this.resizeObserver?.disconnect();
    });
  }

  private attachResizeObserver(): void {
    const host = this.hostRef?.nativeElement;
    if (!host) return;

    const update = () => {
      const rect = host.getBoundingClientRect();
      this.size.set({ w: Math.floor(rect.width), h: Math.floor(rect.height) });
    };
    update();

    this.resizeObserver = new ResizeObserver(() => update());
    this.resizeObserver.observe(host);
  }

  // ── Pipeline: files → buckets → tree → compress → d3 layout → SVG ────────
  private render(
    files: ComponentFileDto[],
    components: ComponentSummaryDto[],
    componentColors: Record<string, string>,
    selectedComponentName: string | null,
    width: number,
    height: number,
  ): void {
    const svg = d3.select(this.svgRef.nativeElement);
    svg.selectAll('*').remove();
    svg.attr('width', width).attr('height', height);

    if (files.length === 0) {
      return;
    }

    // 1. Group files by component (null → UNASSIGNED_BUCKET).
    const buckets = this.groupByComponent(files);

    // 2-3. Build folder tree per bucket and run path-compression.
    // The synthetic root holds one folder per component. We use a stable
    // ordering: the canonical `components` order (largest LOC first as
    // sorted by the page), then unassigned at the end.
    const componentOrder = [
      ...components.map((c) => c.name),
      UNASSIGNED_BUCKET,
    ];
    const seen = new Set<string>();
    const rootChildren: TreeNode[] = [];
    for (const name of componentOrder) {
      if (seen.has(name)) continue;
      seen.add(name);
      const bucket = buckets.get(name);
      if (!bucket || bucket.length === 0) continue;
      const tree = this.buildFolderTree(name, bucket);
      // NOTE: dx-platform-frontend's `transformData()` at lines 473-504 of
      // `treemap/treemap.component.ts` uses a flat `map[path]` lookup keyed
      // on substring path prefixes. We use the same conceptual split-on-`/`
      // approach but materialise nodes via a recursive walk so that
      // compressSingleChildChains can run on a clean tree.
      const compressed = compressSingleChildChains(tree) as FolderNode;
      rootChildren.push(compressed);
    }
    // Any extra buckets that aren't in `components` (defensive — should be
    // none in v1, but the contract doesn't forbid them).
    for (const [name, bucket] of buckets) {
      if (seen.has(name)) continue;
      const tree = this.buildFolderTree(name, bucket);
      rootChildren.push(compressSingleChildChains(tree));
    }

    const root: FolderNode = {
      kind: 'folder',
      name: '__root__',
      children: rootChildren,
    };

    // 4. d3 hierarchy + sum. Mirror sumBySize threshold of ~30 (reference
    // line 139) so tiny files don't collapse into nothing.
    const hierarchy = d3
      .hierarchy<HierarchyDatum>(root as HierarchyDatum, (d) =>
        d.kind === 'folder' ? d.children : null,
      )
      .sum((d) => (d.kind === 'file' ? Math.max(d.loc, MIN_FILE_WEIGHT) : 0))
      .sort((a, b) => (b.value ?? 0) - (a.value ?? 0));

    // 5. Layout. Mirrors line 224 of the reference, but with `round(true)`
    // (the reference passes `round(false)`; rounded layouts give crisper
    // borders, the F3 plan asks for `round(true)`).
    const layout = d3
      .treemap<HierarchyDatum>()
      .tile(d3.treemapSquarify)
      .size([width, height])
      .padding(2)
      .round(true);
    layout(hierarchy);

    // 6. Render — nested SVG <g> per descendant.
    type LeafOrNode = d3.HierarchyRectangularNode<HierarchyDatum>;
    const descendants = hierarchy.descendants() as LeafOrNode[];

    const cell = svg
      .selectAll<SVGGElement, LeafOrNode>('g.treemap-cell')
      // skip the synthetic root (depth 0)
      .data(descendants.filter((d) => d.depth > 0))
      .enter()
      .append('g')
      .attr('class', (d) =>
        d.data.kind === 'file' ? 'treemap-cell file' : 'treemap-cell folder',
      )
      .attr('transform', (d) => `translate(${d.x0},${d.y0})`);

    // Component-level groups (depth 1) carry the canonical component name on
    // a dataset attribute so F4 can find them in the DOM if needed.
    cell
      .filter((d) => d.depth === 1)
      .attr('data-component-name', (d) => d.data.name);

    cell
      .append('rect')
      .attr('width', (d) => Math.max(0.1, d.x1 - d.x0))
      .attr('height', (d) => Math.max(0.1, d.y1 - d.y0))
      .attr('fill', (d) =>
        this.fillForNode(d, componentColors, selectedComponentName),
      )
      .attr('stroke', (d) =>
        this.strokeForNode(d, componentColors, selectedComponentName),
      )
      .attr('stroke-width', (d) => (d.depth === 1 ? 3 : 1))
      .attr('rx', (d) => (d.depth === 1 ? 4 : 2))
      // Right-click handler on every rect (file or folder); component-level
      // rects also get it because F4's "Move whole folder" applies there too.
      .on('contextmenu', (event: MouseEvent, d) => {
        event.preventDefault();
        this.emitContextMenu(event, d);
      });

    // Click on a file rect: emit fileClick (inspector is deferred).
    cell
      .filter((d) => d.data.kind === 'file')
      .on('click', (_event, d) => {
        const file = d.data as FileNode;
        this.fileClick.emit({
          path: file.path,
          componentName: file.componentName,
        });
      });

    // Tooltips on file rects.
    cell
      .filter((d) => d.data.kind === 'file')
      .on('mousemove', (event: MouseEvent, d) => {
        const file = d.data as FileNode;
        this.tooltip.set({
          visible: true,
          x: event.clientX,
          y: event.clientY,
          path: file.path,
          loc: file.loc,
          componentName: file.componentName ?? UNASSIGNED_BUCKET,
        });
      })
      .on('mouseleave', () => {
        this.tooltip.update((t) => ({ ...t, visible: false }));
      });

    // 7. Folder labels — the compressed name renders inside the folder rect.
    // Component (depth 1) labels are bolder and live at top-left of the rect.
    cell
      .filter((d) => d.data.kind === 'folder' && d.depth === 1)
      .append('text')
      .attr('class', 'treemap-label component-label')
      .attr('x', 6)
      .attr('y', 14)
      .text((d) => this.truncateLabel(d.data.name, d.x1 - d.x0));

    // Inner folder labels (the compressed path-segments like
    // `users/retrievers/dto`). Only render if there is room.
    cell
      .filter((d) => d.data.kind === 'folder' && d.depth > 1)
      .append('text')
      .attr('class', 'treemap-label folder-label')
      .attr('x', 4)
      .attr('y', 12)
      .text((d) => this.truncateLabel(d.data.name, d.x1 - d.x0))
      .attr('opacity', (d) => (d.x1 - d.x0 < 40 || d.y1 - d.y0 < 16 ? 0 : 1));

    // File labels — only show if the rect is wide enough to fit a basename.
    cell
      .filter((d) => d.data.kind === 'file')
      .append('text')
      .attr('class', 'treemap-label file-label')
      .attr('x', 4)
      .attr('y', 12)
      .text((d) => this.truncateLabel((d.data as FileNode).name, d.x1 - d.x0))
      .attr('opacity', (d) => (d.x1 - d.x0 < 36 || d.y1 - d.y0 < 16 ? 0 : 1));
  }

  // ── Helpers ───────────────────────────────────────────────────────────────

  private groupByComponent(
    files: ComponentFileDto[],
  ): Map<string, ComponentFileDto[]> {
    const buckets = new Map<string, ComponentFileDto[]>();
    for (const f of files) {
      const key = f.component_name ?? UNASSIGNED_BUCKET;
      const arr = buckets.get(key);
      if (arr) arr.push(f);
      else buckets.set(key, [f]);
    }
    return buckets;
  }

  /**
   * Build a folder tree by splitting each file path on `/`. Conceptually
   * matches dx-platform-frontend's `transformData()` (lines 473-504 of the
   * reference) but materialised top-down so the result is a clean tree of
   * `TreeNode` values that `compressSingleChildChains` can rewrite.
   */
  private buildFolderTree(
    componentName: string,
    files: ComponentFileDto[],
  ): FolderNode {
    const root: FolderNode = {
      kind: 'folder',
      name: componentName,
      children: [],
    };
    const componentNameOrNull =
      componentName === UNASSIGNED_BUCKET ? null : componentName;

    for (const f of files) {
      const segments = f.path.split('/').filter((s) => s.length > 0);
      if (segments.length === 0) continue;
      let cursor: FolderNode = root;
      for (let i = 0; i < segments.length - 1; i++) {
        const seg = segments[i]!;
        let next = cursor.children.find(
          (c) => c.kind === 'folder' && c.name === seg,
        ) as FolderNode | undefined;
        if (!next) {
          next = { kind: 'folder', name: seg, children: [] };
          cursor.children.push(next);
        }
        cursor = next;
      }
      const fileName = segments[segments.length - 1]!;
      cursor.children.push({
        kind: 'file',
        name: fileName,
        path: f.path,
        loc: f.loc ?? 0,
        componentName: componentNameOrNull,
      });
    }
    return root;
  }

  /**
   * Walk up from the descendant to depth-1 (the component bucket) to find
   * which component this rect belongs to. The component name lives on the
   * top-level folder node's `name`.
   */
  private componentForDescendant(
    d: d3.HierarchyRectangularNode<HierarchyDatum>,
  ): string {
    let cur: d3.HierarchyNode<HierarchyDatum> | null = d;
    while (cur && cur.depth > 1) {
      cur = cur.parent;
    }
    return cur?.data?.name ?? '';
  }

  private fillForNode(
    d: d3.HierarchyRectangularNode<HierarchyDatum>,
    componentColors: Record<string, string>,
    selectedComponentName: string | null,
  ): string {
    const componentName = this.componentForDescendant(d);
    const dim =
      selectedComponentName !== null && componentName !== selectedComponentName;

    if (d.data.kind === 'file') {
      // File rects use a near-white neutral; the surrounding folder shading
      // gives the visual hierarchy. Dim slightly when a different component
      // is selected.
      return dim ? '#f4f4f5' : FILE_FILL;
    }

    // Folder fill: depth-shaded brighter from the component's base colour.
    // Mirrors `colorByDepth` at line 156 of the reference.
    const base =
      componentColors[componentName] ?? FALLBACK_COMPONENT_COLOR;
    const c = d3.color(base);
    if (!c) return base;
    const depthStep = 0.25;
    // Depth 1 = the component rect itself → no brighter shift; deeper folders
    // get progressively brighter so labels stay legible.
    const shaded = c.brighter(Math.max(0, d.depth - 1) * depthStep).toString();
    if (dim) {
      const grey = d3.color('#e5e7eb')?.toString() ?? '#e5e7eb';
      // Blend back toward grey for unselected components.
      return d3.interpolateRgb(shaded, grey)(0.6);
    }
    return shaded;
  }

  private strokeForNode(
    d: d3.HierarchyRectangularNode<HierarchyDatum>,
    componentColors: Record<string, string>,
    _selectedComponentName: string | null,
  ): string {
    if (d.depth === 1) {
      // Thick coloured border around each component rect.
      const componentName = this.componentForDescendant(d);
      return componentColors[componentName] ?? FALLBACK_COMPONENT_COLOR;
    }
    if (d.data.kind === 'file') {
      return '#d1d5db';
    }
    return 'rgba(0,0,0,0.08)';
  }

  private truncateLabel(text: string, availableWidth: number): string {
    // Rough heuristic: ~7px per char. Keeps the labels readable without a
    // measure pass per render. Adjust empirically if needed.
    const maxChars = Math.max(0, Math.floor((availableWidth - 8) / 7));
    if (maxChars <= 0) return '';
    if (text.length <= maxChars) return text;
    if (maxChars <= 1) return '…';
    return text.slice(0, maxChars - 1) + '…';
  }

  private emitContextMenu(
    event: MouseEvent,
    d: d3.HierarchyRectangularNode<HierarchyDatum>,
  ): void {
    const componentName = this.componentForDescendant(d);
    const isUnassigned = componentName === UNASSIGNED_BUCKET;
    if (d.data.kind === 'file') {
      this.contextMenu.emit({
        kind: 'file',
        path: (d.data as FileNode).path,
        componentName: isUnassigned ? null : componentName,
        x: event.clientX,
        y: event.clientY,
      });
      return;
    }
    // Folder anchor: emit the (possibly compressed) folder label as `path`.
    // F4 will recombine this with the parent component name to reconstruct
    // the on-disk prefix when issuing the "Move whole folder" action.
    this.contextMenu.emit({
      kind: 'folder',
      path: d.data.name,
      componentName: isUnassigned ? null : componentName,
      x: event.clientX,
      y: event.clientY,
    });
  }
}
