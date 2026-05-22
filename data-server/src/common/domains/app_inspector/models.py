"""App-Inspector domain entities for the v2 graph.

Mirrors :mod:`src.common.domains.quality.models`. App Inspector produces a
hierarchical "tag" taxonomy per source file (e.g.
``appinspector.OS.Network.Connection.Socket``). Each tag firing for a file
becomes an :class:`AppTag` entity referencing the host :class:`AppInspectorProject`
and the source :class:`git.File`.

Per plan §4, every cross-entity reference uses :class:`EntityRef`, never a
Python object reference.

Entity-vs-value-object decisions (plan §1.1):

* :class:`AppInspectorProject` and :class:`AppTag` are real
  :class:`Entity` subclasses (``EntityKind.APP_TAG`` is in the kernel enum
  set; see ``common/kernel/kinds.py:55``).

The :pyattr:`AppInspectorProject.source_tool` Literal mirrors
:class:`QualityProject.source_tool`. Today only ``"appinspector"`` is
supported; a future tool with the same tag-taxonomy shape (e.g.
``"chronos"``) would extend the Literal without changing the entity shape.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from ...kernel import Entity, EntityKind, EntityRef
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import AppInspectorTransformer  # noqa: F401


# Closed set of app-inspector-style source identifiers. The entity shape
# is identical across tools; the transformer branches on this when more
# than one is wired.
AppInspectorSourceTool = Literal["appinspector"]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class AppInspectorProject(Project):
    """A single App-Inspector project's metadata.

    Per plan §3 the Project is metadata only — entities live in flat
    registries on :class:`Graph`. The per-file concern list moves to the
    flat :class:`AppTagRegistry` on the graph.

    :pyattr:`source_tool` is a typed switch matching the
    :class:`QualityProject` precedent. Default ``"appinspector"`` is the
    only value supported today.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    source_tool: AppInspectorSourceTool = "appinspector"

    def transformer_class(self) -> type["AppInspectorTransformer"]:  # type: ignore[override]
        from .transformer import AppInspectorTransformer

        return AppInspectorTransformer


class AppTag(Entity):
    """A single App-Inspector tag firing for one file.

    Field mapping vs the raw JSON shape (``file.concerns[*]``):

    * ``id``           — synthetic stable id per ``(project_id, file_path,
                         tag)`` built via :meth:`AppTag.make_id`.
    * ``project_ref``  — typed ref to :class:`AppInspectorProject`.
    * ``file_ref``     — typed ref to a :class:`git.File`.
    * ``file_path``    — the raw repo-relative file path as it appears in
                         the source JSON's ``entity`` key. Kept alongside
                         ``file_ref`` so consumers can group / display the
                         path without having to dereference the file.
    * ``tag``          — full dotted taxonomy string, e.g.
                         ``appinspector.OS.Network.Connection.Socket``.
    * ``strength``     — integer strength as emitted by App Inspector.
    """

    kind: ClassVar[EntityKind] = EntityKind.APP_TAG

    project_ref: EntityRef
    file_ref: EntityRef
    file_path: str
    tag: str
    strength: int

    @staticmethod
    def make_id(project_id: str, file_path: str, tag: str) -> str:
        """Synthetic stable id per ``(project, file, tag)``.

        Mirrors the convention used by :class:`QualityIssue`'s bridge
        (see ``quality/bridge.py::_issue_id``). The double-colon
        separator keeps the three segments visually distinct and avoids
        ambiguity when a path or tag contains a single colon.
        """
        return f"{project_id}::{file_path}::{tag}"


__all__ = [
    "AppInspectorProject",
    "AppTag",
    "AppInspectorSourceTool",
]
