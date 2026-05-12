"""Quality-domain entities for the v2 graph.

Faithful port of ``src/common/quality_models.py`` (legacy Insider; Sonar
later). Every cross-entity reference uses :class:`EntityRef`, never a
Python object reference — per plan §4.

Entity-vs-value-object decisions (plan §1.1):

* :class:`QualityProject` and :class:`QualityIssue` are real
  :class:`Entity` subclasses (``EntityKind.QUALITY_ISSUE`` is in the
  kernel enum set).

The plan §9 mentions Sonar replacing Insider. Mirroring the
``CodeStructureProject.kind_of_source`` precedent, we put a typed
``source_tool: Literal["insider", "sonar"]`` on
:class:`QualityProject`. A single :class:`QualityTransformer` handles
both formats; Chunk 8 picks the right raw-DTO walker by inspecting
``project.source_tool``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal, Optional

from ...kernel import Entity, EntityKind, EntityRef
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import QualityTransformer  # noqa: F401


# Closed set of quality-tool source identifiers. The entity shape is
# identical across tools; the transformer branches on this when the
# raw-DTO path is wired (Chunk 8).
QualitySourceTool = Literal["insider", "sonar"]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class QualityProject(Project):
    """A single quality project's metadata.

    Per plan §3 the Project is metadata only — entities live in flat
    registries on :class:`Graph`. The legacy ``QualityIssues`` container
    owned ``issues`` + ``by_file``; that ownership moves to :class:`Graph`
    in Chunk 8.

    :pyattr:`source_tool` (Literal) is the per-project switch between
    Insider and Sonar. Mirrors :class:`CodeStructureProject.kind_of_source`
    and plan §9 ("Sonar replaces Insider via a new transformer"). Default
    ``"insider"`` matches the legacy default.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    source_tool: QualitySourceTool = "insider"

    def transformer_class(self) -> type["QualityTransformer"]:  # type: ignore[override]
        from .transformer import QualityTransformer

        return QualityTransformer


class QualityIssue(Entity):
    """A single rule violation reported by a quality tool for one file.

    Field mapping vs legacy ``quality_models.QualityIssue``:

    * ``id``               — unchanged (synthetic stable id per
                             ``(source, file, rule, idx)`` produced by the
                             transformer).
    * ``project_ref``      — NEW: typed ref to
                             :class:`QualityProject`.
    * ``file_ref``         — was ``file_path`` (a plain string); v2 carries
                             the typed :class:`EntityRef` to a
                             :class:`git.File`.
    * ``rule_id``          — was ``rule_name`` (Insider keeps spaces, e.g.
                             ``"Stub Implementer"``). Renamed to match the
                             plan §4 example. The semantics are identical:
                             the raw rule identifier the tool emits.
    * ``severity``         — was ``severity_label`` (Sonar-only on the
                             legacy model). Renamed to the plan-§4 name.
                             ``Optional`` because Insider doesn't emit a
                             severity ordinal.
    * ``message``          — preserved (Sonar populates, Insider leaves
                             ``None``).
    * ``line_start``       — was ``line_number`` (the legacy carried a
                             single integer, Sonar may emit a range). v2
                             splits into ``line_start`` + ``line_end``;
                             a single-line issue sets both to the same
                             value or leaves ``line_end=None``.
    * ``line_end``         — NEW (plan §4); ``None`` until a tool emits
                             a multi-line range.
    * ``category``         — was ``category`` (rule family/bucket).
                             Preserved.
    * ``occurrence_count`` — preserved (Insider's raw firing count;
                             Sonar leaves ``1``).
    * ``language``         — preserved (Sonar-only on the legacy).
    * ``source_tool``      — NEW redundant copy of
                             :pyattr:`QualityProject.source_tool` carried
                             on each issue. Cheap, lets cross-project
                             scans answer "all Sonar-only severity
                             ranges" without dereferencing
                             ``project_ref``. The transformer copies the
                             project's value when emitting each row.
    """

    kind: ClassVar[EntityKind] = EntityKind.QUALITY_ISSUE

    project_ref: EntityRef
    file_ref: EntityRef
    rule_id: str
    category: str
    source_tool: QualitySourceTool = "insider"
    occurrence_count: int = 1
    severity: Optional[str] = None
    message: Optional[str] = None
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    language: Optional[str] = None


__all__ = [
    "QualityIssue",
    "QualityProject",
    "QualitySourceTool",
]
