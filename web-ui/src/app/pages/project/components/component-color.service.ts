/**
 * Per-component palette service (F5).
 *
 * Thin Angular wrapper over `component-color.ts`. The pure logic lives in
 * the sibling module so the unit test can import it under `node --test`
 * without dragging in the Angular runtime (decorators don't strip cleanly
 * with the experimental TS loader).
 *
 * Picks a stable, perceptually-distinct colour per component name so the
 * treemap (a) repaints identically across reloads and (b) agrees with the
 * swatch in the components list.
 */
import { Injectable } from '@angular/core';

import { ComponentSummaryDto } from '../../../core/services/data-server.service';
import {
  COMPONENT_PALETTE,
  buildColorMap as buildColorMapPure,
  colorForName,
} from './component-color';

@Injectable({ providedIn: 'root' })
export class ComponentColorService {
  /** The palette exposed read-only for the inline picker in the F4 menu. */
  readonly palette: ReadonlyArray<string> = COMPONENT_PALETTE;

  /** Stable colour for a component name. See `colorForName` for details. */
  colorFor(name: string): string {
    return colorForName(name);
  }

  /**
   * Build `{name → colour}` for the treemap input. See the pure helper
   * for the precedence rules (overrides > explicit `color` > hash).
   */
  buildColorMap(
    components: ReadonlyArray<ComponentSummaryDto>,
    overrides: Readonly<Record<string, string>> = {},
  ): Record<string, string> {
    return buildColorMapPure(components, overrides);
  }
}
