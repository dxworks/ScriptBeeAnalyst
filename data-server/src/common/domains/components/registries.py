"""Components registry.

Indexes per plan §5/§7:

* ``by_project`` — every component in a project (when ``project_ref`` is set).
* ``by_file``    — fan-out over ``component.file_refs``; given a file ref,
                   "which component(s) own this file?".
* ``by_name``    — lookup by human-readable name (same as primary id, but
                   exposed as an explicit index for symmetry with the
                   other domain registries).
"""
from __future__ import annotations

from ...kernel import IndexSpec, Registry
from .models import Component


class ComponentRegistry(Registry[Component, str]):
    """Reverse-indexed registry of :class:`Component` entities."""

    indexes = [
        IndexSpec(name="by_project", key_fn=lambda c: c.project_ref, multi=True),
        IndexSpec(name="by_file", key_fn=lambda c: c.file_refs, multi=True),
        IndexSpec(name="by_name", key_fn=lambda c: c.name, multi=True),
    ]

    def get_id(self, entity: Component) -> str:
        return entity.id


__all__ = ["ComponentRegistry"]
