/**
 * Path-compression for the components treemap.
 *
 * dx-platform-frontend's treemap (the visual reference at
 * `src/app/visualizations/treemap/treemap.component.ts`) renders every folder
 * segment as its own SVG group. With long Java-style nesting that buries the
 * interesting labels under many tiny labels. ScriptBee's contribution is to
 * collapse single-child folder chains into one composite label
 * (e.g. `users/retrievers/dto`) before handing the tree to d3.
 *
 * This module is intentionally d3-free so it stays unit-testable in isolation.
 */
export type FileNode = {
  kind: 'file';
  /** The leaf basename, not the full path. */
  name: string;
  /** Full original path (used by the treemap render for tooltips + emits). */
  path: string;
  /** Lines of code; null coalesces to 0 at the leaf. */
  loc: number;
  /** Component this file belongs to (null = unassigned bucket). */
  componentName: string | null;
};

export type FolderNode = {
  kind: 'folder';
  /** One or more `/`-joined folder segments. After compression this may be
   *  a composite label like `users/retrievers/dto`. */
  name: string;
  children: TreeNode[];
};

export type TreeNode = FileNode | FolderNode;

/**
 * Compress single-child folder chains in place-of-output (returns a new tree
 * — never mutates the input).
 *
 * Compression rule (verbatim from the F3 plan, "Path-compression rule —
 * explicit spec"):
 *   A folder F is compressible iff
 *     (a) F has exactly one child C, AND
 *     (b) C is a folder (not a file), AND
 *     (c) F has no file children directly.
 *   Compressing fuses F + C into one folder named `F.name + '/' + C.name`
 *   with children = C.children. Repeat until stable.
 *
 * Properties this guarantees:
 *   - A folder with multiple children NEVER collapses.
 *   - A folder with a file directly inside NEVER collapses (the file would
 *     otherwise be visually hidden under the parent label).
 *   - A linear chain `a/b/c/d/leaf.ext` collapses fully to a single label
 *     `a/b/c/d` containing one file leaf `leaf.ext`.
 *   - The function is idempotent: f(f(t)) deep-equals f(t).
 */
export function compressSingleChildChains(node: TreeNode): TreeNode {
  if (node.kind === 'file') {
    return node;
  }

  // Recurse first so children are already compressed when we evaluate the
  // current level. Without this, a chain like a -> b -> c -> file would need
  // multiple passes; with it, one bottom-up traversal is enough.
  const compressedChildren = node.children.map(compressSingleChildChains);

  // Now decide whether THIS folder collapses with its (single) child.
  // Loop because after one merge the merged child itself might still satisfy
  // the rule against the new child (we already recursed, so children are
  // stable — only the merge-with-child step can stack).
  let current: FolderNode = { kind: 'folder', name: node.name, children: compressedChildren };
  while (
    current.children.length === 1 &&
    current.children[0]!.kind === 'folder'
  ) {
    const onlyChild = current.children[0] as FolderNode;
    // Rule (c): no file children directly. We already know children.length === 1
    // and that child is a folder, so this is satisfied. Kept as an explicit
    // check for future-proofing if the loop body changes.
    const hasDirectFileChild = current.children.some((c) => c.kind === 'file');
    if (hasDirectFileChild) break;
    current = {
      kind: 'folder',
      name: `${current.name}/${onlyChild.name}`,
      children: onlyChild.children,
    };
  }
  return current;
}
