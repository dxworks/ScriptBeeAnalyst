/**
 * Unit tests for the component colour palette (F5).
 *
 * Same runner pattern as path-compression.spec.ts: Node's built-in test
 * runner with experimental TS strip-types. Run with:
 *
 *   node --test --experimental-strip-types --no-warnings \
 *     src/app/pages/project/components/component-color.spec.ts
 *
 * (Or via the `npm run test:unit` script, which globs both spec files.)
 *
 * The Angular `@Injectable` service in `component-color.service.ts` is a
 * thin wrapper over the pure `component-color.ts` module — decorators
 * don't strip cleanly under the experimental loader, so the spec talks to
 * the pure module directly. Same logic, runnable without TestBed.
 */
import { describe, it } from 'node:test';
import { strict as assert } from 'node:assert';

import {
  COMPONENT_PALETTE,
  FALLBACK_COMPONENT_COLOR,
  buildColorMap,
  colorForName,
} from './component-color.ts';

describe('colorForName', () => {
  it('returns a colour from the palette', () => {
    const c = colorForName('api');
    assert.ok(COMPONENT_PALETTE.includes(c), `expected ${c} to be in the palette`);
  });

  it('is deterministic — same name → same colour across calls', () => {
    const a = colorForName('users');
    const b = colorForName('users');
    const c = colorForName('users');
    assert.equal(a, b);
    assert.equal(b, c);
  });

  it('distributes a small set of names across the palette', () => {
    // With 20 colours and 20 distinct names we don't expect a perfect
    // bijection (the birthday bound says ~50% chance of *any* collision),
    // but we should see meaningful spread (>= half the palette size in
    // distinct outputs). This catches a hash that degenerates to one slot.
    const names = [
      'api', 'web', 'core', 'auth', 'billing', 'data', 'ui', 'shared',
      'storage', 'graph', 'metrics', 'pipeline', 'sandbox', 'tests',
      'docs', 'config', 'utils', 'models', 'services', 'guards',
    ];
    const colours = new Set(names.map(colorForName));
    assert.ok(
      colours.size >= COMPONENT_PALETTE.length / 2,
      `expected at least ${COMPONENT_PALETTE.length / 2} distinct colours, got ${colours.size}`,
    );
  });

  it('falls back when given an empty name', () => {
    assert.equal(colorForName(''), FALLBACK_COMPONENT_COLOR);
  });
});

describe('buildColorMap', () => {
  it('builds a {name → colour} for every component', () => {
    const map = buildColorMap([
      { name: 'api', color: null },
      { name: 'web', color: null },
    ]);
    assert.equal(typeof map['api'], 'string');
    assert.equal(typeof map['web'], 'string');
    assert.equal(map['api'], colorForName('api'));
  });

  it('honours an explicit `color` on the summary over the hash', () => {
    const map = buildColorMap([{ name: 'api', color: '#abcdef' }]);
    assert.equal(map['api'], '#abcdef');
  });

  it('lets overrides win for new draft buckets', () => {
    const map = buildColorMap(
      [{ name: 'api', color: null }],
      { 'new-bucket': '#123456', api: '#fedcba' },
    );
    assert.equal(map['api'], '#fedcba');
    assert.equal(map['new-bucket'], '#123456');
  });
});
