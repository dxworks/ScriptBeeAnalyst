"""file_path -> component_name resolution.

Decision rule (first match wins):
  1. Explicit `ComponentMapping` entry — longest-prefix wins across all
     (path_prefix, *extra_paths) prefixes registered.
  2. Top-folder fallback (path before the first '/').
  3. `(other)` synthetic component for paths that have no separator and no
     explicit mapping (rare — root-level files).
"""
from __future__ import annotations

from typing import Optional

from src.enrichment.components.mapping import ComponentMapping
from src.enrichment.models import Component


OTHER_COMPONENT = "(other)"


def top_folder_of(file_id: Optional[str]) -> Optional[str]:
    """Top-level folder for a path, or the path itself if there's no '/'."""
    if not file_id:
        return None
    return file_id.split("/", 1)[0] if "/" in file_id else file_id


class ComponentResolver:
    """Resolves a file path to a component name and tracks `Component.file_paths`."""

    def __init__(self, mapping: ComponentMapping):
        self._mapping = mapping
        # Pre-compute (prefix, name) pairs sorted by prefix length desc so the
        # longest-prefix match wins.
        prefixes: list[tuple[str, str]] = []
        for name, spec in mapping.components.items():
            prefixes.append((spec.path_prefix, name))
            for extra in spec.extra_paths:
                prefixes.append((extra, name))
        self._prefixes = sorted(prefixes, key=lambda p: -len(p[0]))

    def resolve(self, file_id: Optional[str]) -> Optional[str]:
        if not file_id:
            return None
        for prefix, name in self._prefixes:
            if file_id.startswith(prefix):
                return name
        top = top_folder_of(file_id)
        if top is None:
            return None
        # If the path has no separator AND we have an explicit mapping
        # the file simply doesn't fit, return (other) — otherwise the top
        # itself becomes the component name (heuristic).
        if "/" not in file_id and not self._mapping.is_empty():
            return OTHER_COMPONENT
        return top

    def build_components(self, file_paths: list[str]) -> list[Component]:
        """Group `file_paths` by component and return Component models.

        `path_prefix` for heuristic-only components is the top folder (with no
        trailing slash). For mapped components it's the explicit prefix.
        """
        groups: dict[str, list[str]] = {}
        for fid in file_paths:
            comp = self.resolve(fid)
            if comp is None:
                continue
            groups.setdefault(comp, []).append(fid)

        # Stable ordering: alphabetic by component name, with `(other)` last.
        names = sorted(groups.keys(), key=lambda n: (n == OTHER_COMPONENT, n))

        out: list[Component] = []
        for name in names:
            prefix = self._prefix_for(name)
            out.append(Component(
                name=name,
                path_prefix=prefix,
                file_paths=sorted(groups[name]),
            ))
        return out

    def _prefix_for(self, name: str) -> str:
        spec = self._mapping.components.get(name)
        if spec is not None:
            return spec.path_prefix
        if name == OTHER_COMPONENT:
            return ""
        return name
