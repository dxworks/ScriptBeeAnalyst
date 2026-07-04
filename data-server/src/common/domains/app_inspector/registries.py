"""Registries for every app-inspector-domain :class:`Entity` subclass.

Per plan §1.5, indexes are declared as a ``ClassVar[list[IndexSpec]]`` and
rebuilt on every mutation / on :meth:`Registry.load` — they are NOT pickled.
"""
from __future__ import annotations

from typing import ClassVar

from ...kernel import IndexSpec, Registry
from .models import AppInspectorProject, AppTag


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class AppInspectorProjectRegistry(Registry[AppInspectorProject, str]):
    """Holds every :class:`AppInspectorProject` in the graph.

    Indexes:

    * ``by_name``        — name lookup, parallels other domains.
    * ``by_source_tool`` — quick "all AppInspector projects" filter. Today
                           only ``"appinspector"`` is emitted, but the
                           index is kept for symmetry with
                           :class:`QualityProjectRegistry` and to support
                           future sibling tools.
    """

    indexes: ClassVar[list[IndexSpec]] = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name),
        IndexSpec(name="by_source_tool", key_fn=lambda p: p.source_tool),
    ]

    def get_id(self, entity: AppInspectorProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# App tags
# ---------------------------------------------------------------------------


def _tag_root(tag: str) -> str:
    """Return the first segment of the taxonomy after the ``appinspector.`` prefix.

    Examples
    --------
    >>> _tag_root("appinspector.OS.Network.Connection.Socket")
    'OS'
    >>> _tag_root("appinspector.Cryptography.CryptoCurrency")
    'Cryptography'

    Tags that do not start with the ``appinspector.`` prefix fall back to
    their leading dotted segment so the index never produces an empty
    bucket.
    """
    if tag.startswith("appinspector."):
        return tag.split(".", 2)[1]
    return tag.split(".", 1)[0]


class AppTagRegistry(Registry[AppTag, str]):
    """Every :class:`AppTag` in the graph.

    Indexes:

    * ``by_file``      — fast "all tags on file F" — supports per-file
                         drill-downs in the dashboard.
    * ``by_project``   — one bucket per :class:`AppInspectorProject`.
    * ``by_tag``       — full dotted taxonomy lookup (e.g. "which files
                         carry ``appinspector.OS.Network.Connection.Socket``?").
    * ``by_tag_root``  — broad category lookup keyed by the first segment
                         after ``appinspector.`` (``OS`` / ``Cryptography``
                         / etc.). Useful when an analysis only cares
                         about the top-level taxonomy bucket.
    """

    indexes: ClassVar[list[IndexSpec]] = [
        IndexSpec(name="by_file", key_fn=lambda t: t.file_ref, multi=True),
        IndexSpec(name="by_project", key_fn=lambda t: t.project_ref, multi=True),
        IndexSpec(name="by_tag", key_fn=lambda t: t.tag, multi=True),
        IndexSpec(
            name="by_tag_root", key_fn=lambda t: _tag_root(t.tag), multi=True
        ),
    ]

    def get_id(self, entity: AppTag) -> str:
        return entity.id


__all__ = [
    "AppInspectorProjectRegistry",
    "AppTagRegistry",
]
