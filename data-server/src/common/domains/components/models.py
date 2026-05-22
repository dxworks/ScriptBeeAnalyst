"""Components-domain entity for the v2 graph.

A :class:`Component` is a path-prefix-grouped collection of files.
Per plan §5/§7 it's the one Entity that mixes "data" and "tag-like
aggregation" — :class:`ComponentResolverMetric` (in
:mod:`src.enrichment.metrics.implementations`) walks every file and
emits one :class:`Component` per resolved group.

Field mapping vs the legacy ``src/enrichment/models.py:Component``:

* ``id``           — NEW: the component name (legacy used a list[Component]).
* ``project_ref``  — NEW: ref to the originating :class:`GitProject`
                     (or wider scope when the component spans projects).
* ``name``         — preserved (also the canonical id).
* ``path_prefix``  — preserved.
* ``file_refs``    — was ``file_paths: list[str]``; v2 carries typed
                     refs into :class:`git.File`.
* ``description``  — NEW (plan §5).
"""
from __future__ import annotations

from typing import ClassVar, List, Optional

from ...kernel import Entity, EntityKind, EntityRef


class Component(Entity):
    """A path-prefix group of files, addressable by name.

    See module docstring for the field mapping. Constructed by
    :class:`ComponentResolverMetric` from
    :mod:`src.enrichment.metrics.implementations.component_resolver`.

    Resolver methods (auto-generated, see ``kernel/entity.py``):
        ``.project(graph)`` -> ``Project | None``  (project_ref may be None)
        ``.files(graph)``   -> ``list[File]``
    """

    kind: ClassVar[EntityKind] = EntityKind.COMPONENT

    project_ref: Optional[EntityRef] = None
    name: str
    path_prefix: str = ""
    file_refs: List[EntityRef] = []
    description: Optional[str] = None


__all__ = ["Component"]
