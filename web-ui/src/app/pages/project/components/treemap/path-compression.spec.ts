/**
 * Unit tests for the path-compression helper.
 *
 * This is the contribution that distinguishes ScriptBee's treemap from the
 * dx-platform-frontend reference (which renders every folder segment as its
 * own group). The plan flags these tests as non-negotiable, so they cover the
 * full compressibility decision matrix plus idempotency.
 *
 * The repo doesn't have a configured Angular/Jasmine/Karma/Vitest runner yet
 * (angular.json declares no `test` architect target, package.json has no
 * jasmine/karma/vitest deps). Rather than introduce one, this spec uses the
 * Node 24 built-in test runner (`node --test`) with the experimental TS
 * type-stripping that ships enabled in Node 24.x — meaning it runs the
 * source `.ts` directly with zero extra tooling. Run with:
 *
 *   node --test --experimental-strip-types --no-warnings \
 *     src/app/pages/project/components/treemap/path-compression.spec.ts
 */
import { describe, it } from 'node:test';
import { strict as assert } from 'node:assert';

import {
  compressSingleChildChains,
  type FileNode,
  type FolderNode,
  type TreeNode,
} from './path-compression.ts';

// Tiny builders so the test trees stay readable.
function folder(name: string, children: TreeNode[]): FolderNode {
  return { kind: 'folder', name, children };
}

function file(name: string, path = name, loc = 10): FileNode {
  return { kind: 'file', name, path, loc, componentName: null };
}

describe('compressSingleChildChains', () => {
  it('leaves a file leaf untouched', () => {
    const leaf = file('main.ts', 'src/main.ts', 42);
    const out = compressSingleChildChains(leaf);
    assert.deepEqual(out, leaf);
  });

  it('does not compress a folder with multiple children', () => {
    //   root
    //   ├── a.ts
    //   └── b.ts
    const tree = folder('root', [file('a.ts'), file('b.ts')]);
    const out = compressSingleChildChains(tree);
    assert.equal(out.kind, 'folder');
    assert.equal((out as FolderNode).name, 'root');
    assert.equal((out as FolderNode).children.length, 2);
  });

  it('does not compress a folder whose single child is a file', () => {
    //   pkg
    //   └── lonely.ts   <-- file, so pkg stays a separate label
    const tree = folder('pkg', [file('lonely.ts', 'pkg/lonely.ts')]);
    const out = compressSingleChildChains(tree);
    assert.equal((out as FolderNode).name, 'pkg');
    assert.equal((out as FolderNode).children.length, 1);
    assert.equal((out as FolderNode).children[0]!.kind, 'file');
  });

  it('fully collapses a linear folder chain ending in a file', () => {
    //   a
    //   └── b
    //       └── c
    //           └── d
    //               └── leaf.ext
    // The rule says compress F when F has exactly one folder child and no
    // direct file child. The final 'd' folder has a single file child so it
    // does NOT collapse with leaf.ext — it stays a folder holding the file.
    // So a/b/c collapses to one composite folder 'a/b/c/d' holding leaf.ext.
    const tree = folder('a', [
      folder('b', [
        folder('c', [folder('d', [file('leaf.ext', 'a/b/c/d/leaf.ext')])]),
      ]),
    ]);
    const out = compressSingleChildChains(tree) as FolderNode;
    assert.equal(out.kind, 'folder');
    assert.equal(out.name, 'a/b/c/d');
    assert.equal(out.children.length, 1);
    assert.equal(out.children[0]!.kind, 'file');
    assert.equal((out.children[0] as FileNode).name, 'leaf.ext');
  });

  it('does not collapse across a folder that has a sibling file', () => {
    //   src
    //   ├── README.md   <-- direct file → src must not be merged into its
    //   │                   one folder neighbour
    //   └── inner
    //       └── deep.ts
    const tree = folder('src', [
      file('README.md', 'src/README.md'),
      folder('inner', [file('deep.ts', 'src/inner/deep.ts')]),
    ]);
    const out = compressSingleChildChains(tree) as FolderNode;
    assert.equal(out.name, 'src');
    assert.equal(out.children.length, 2);
    // The inner folder has a single file child → must stay as `inner`,
    // not merge with `deep.ts`.
    const innerChild = out.children.find(
      (c) => c.kind === 'folder',
    ) as FolderNode;
    assert.equal(innerChild.name, 'inner');
    assert.equal(innerChild.children[0]!.kind, 'file');
  });

  it('compresses the worked example users/retrievers/dto from the plan', () => {
    //   users
    //   └── retrievers
    //       └── dto
    //           ├── UserDto.java
    //           └── UserRequestDto.java
    // Two children → 'dto' itself doesn't collapse with them, but the chain
    // above 'dto' DOES collapse into one composite label.
    const tree = folder('users', [
      folder('retrievers', [
        folder('dto', [
          file('UserDto.java', 'users/retrievers/dto/UserDto.java'),
          file(
            'UserRequestDto.java',
            'users/retrievers/dto/UserRequestDto.java',
          ),
        ]),
      ]),
    ]);
    const out = compressSingleChildChains(tree) as FolderNode;
    assert.equal(out.name, 'users/retrievers/dto');
    assert.equal(out.children.length, 2);
    assert.ok(out.children.every((c) => c.kind === 'file'));
  });

  it('is idempotent — applying twice yields the same tree', () => {
    const tree = folder('a', [
      folder('b', [
        folder('c', [
          file('one.ts', 'a/b/c/one.ts'),
          file('two.ts', 'a/b/c/two.ts'),
        ]),
      ]),
      folder('q', [folder('r', [file('z.ts', 'a/q/r/z.ts')])]),
    ]);
    const once = compressSingleChildChains(tree);
    const twice = compressSingleChildChains(once);
    assert.deepEqual(twice, once);
  });

  it('does not mutate the input tree', () => {
    const inner = folder('inner', [file('deep.ts', 'src/inner/deep.ts')]);
    const tree = folder('src', [inner]);
    const snapshot = JSON.parse(JSON.stringify(tree));
    compressSingleChildChains(tree);
    assert.deepEqual(tree, snapshot);
  });
});
