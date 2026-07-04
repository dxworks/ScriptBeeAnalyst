/**
 * Pure palette logic for the components page (F5).
 *
 * Lives in a side module (no Angular imports) so the unit test can
 * `import` it under `node --test --experimental-strip-types`. The Angular
 * `@Injectable` service in `component-color.service.ts` is a thin wrapper
 * over these functions; everything that needs testing is here.
 *
 * Reference: dx-platform-frontend's `services/color-palette.service.ts`
 * ships a 20+ colour `COLOR_SCHEME_20` array. We follow the *idea* (a
 * fixed palette indexed by a name-hash) but pick our own palette tuned
 * for ScriptBee's light surface tone — dropping near-black entries that
 * would clash with the file-rect borders. Hash is a tiny hand-rolled djb2.
 */

/**
 * 20-entry palette. Hand-picked to be (a) reasonably distinct against a
 * white file fill, (b) all in the same lightness band so depth-shading
 * (`d3.color(base).brighter(depth*step)`) produces legible inner folders,
 * and (c) WCAG-friendly when used as a thick component border on top of a
 * 1pt grey card divider.
 */
export const COMPONENT_PALETTE: ReadonlyArray<string> = [
  '#3b82f6', // blue-500
  '#10b981', // emerald-500
  '#f59e0b', // amber-500
  '#ef4444', // red-500
  '#8b5cf6', // violet-500
  '#ec4899', // pink-500
  '#06b6d4', // cyan-500
  '#84cc16', // lime-500
  '#f97316', // orange-500
  '#14b8a6', // teal-500
  '#a855f7', // purple-500
  '#0ea5e9', // sky-500
  '#22c55e', // green-500
  '#eab308', // yellow-500
  '#d946ef', // fuchsia-500
  '#6366f1', // indigo-500
  '#f43f5e', // rose-500
  '#64748b', // slate-500
  '#0891b2', // cyan-600
  '#ca8a04', // yellow-600
];

/**
 * Centralised greys carried over from the treemap (F3 review carry-over).
 * Hosted here so they live next to the per-component palette and the
 * treemap can import from one source. SVG fills can't be CSS variables
 * without a `getComputedStyle` pass, so they have to be JS constants.
 */
export const FALLBACK_COMPONENT_COLOR = '#94a3b8'; // slate-400
export const FILE_FILL = '#ffffff';
export const FILE_FILL_DIM = '#f4f4f5';
export const FOLDER_STROKE = 'rgba(0,0,0,0.08)';
export const FILE_STROKE = '#d1d5db'; // gray-300
export const FOLDER_DIM_GREY = '#e5e7eb'; // gray-200

/** djb2: small, branch-free, decent distribution for short strings. */
export function djb2(input: string): number {
  let hash = 5381;
  for (let i = 0; i < input.length; i++) {
    // (hash * 33) ^ char — keep as unsigned 32-bit via `>>> 0`.
    hash = ((hash << 5) + hash + input.charCodeAt(i)) >>> 0;
  }
  return hash;
}

/**
 * Stable colour for a component name. Same input → same output across
 * reloads; different names spread across the palette by `djb2 % length`.
 */
export function colorForName(name: string): string {
  if (!name) return FALLBACK_COMPONENT_COLOR;
  const idx = djb2(name) % COMPONENT_PALETTE.length;
  return COMPONENT_PALETTE[idx]!;
}

/**
 * Minimal shape required from `ComponentSummaryDto` for the palette layer.
 * Defined here to keep this module Angular-free (no data-server.service
 * import — that file pulls in HttpClient and would explode the test).
 */
export interface PalettableComponent {
  readonly name: string;
  readonly color: string | null;
}

/**
 * Build `{name → colour}` for the treemap input. Respects an explicit
 * `color` already on the summary (B3 has a column; v1 always sends null,
 * but the contract allows it), otherwise falls back to the hash-derived
 * colour from `colorForName`. The optional `overrides` map carries
 * draft-only colours picked in "Move to new component…" — these win so
 * the treemap paints the new bucket immediately, before the backend
 * rebuild.
 */
export function buildColorMap(
  components: ReadonlyArray<PalettableComponent>,
  overrides: Readonly<Record<string, string>> = {},
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const c of components) {
    out[c.name] = overrides[c.name] ?? c.color ?? colorForName(c.name);
  }
  // Draft-only buckets (created via "Move to new component…") might not be
  // in `components` yet — they appear in `overrides`. Carry them over.
  for (const name of Object.keys(overrides)) {
    if (!(name in out)) {
      out[name] = overrides[name]!;
    }
  }
  return out;
}
