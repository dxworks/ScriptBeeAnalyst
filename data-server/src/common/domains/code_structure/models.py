"""Code-structure-domain entities for the v2 graph.

Faithful port of ``src/common/codestructure_models.py`` (legacy). Every
cross-entity reference uses :class:`EntityRef`, never a Python object
reference — per plan §4 (and the Chunk 4/5 pattern).

The plan §3 explicitly calls out :pyattr:`CodeStructureProject.kind_of_source`
as a ``Literal["jafax", "codeframe"]`` switch (task 8 = flip from
``"jafax"`` to ``"codeframe"`` without touching entity shapes). The entity
classes themselves are tool-agnostic — the only difference between the two
formats is the raw-DTO path that feeds them, which the transformer routes on.

Entity-vs-value-object decisions (plan §1.1):

* :class:`CodeStructureProject`, :class:`CodeType`, :class:`CodeMethod`,
  :class:`CodeField`, :class:`CodeReference` are all real :class:`Entity`
  subclasses (the kernel ``EntityKind`` enum already lists ``CODE_TYPE`` /
  ``CODE_METHOD`` / ``CODE_FIELD`` / ``CODE_REF``).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, List, Literal, Optional

from ...kernel import Entity, EntityKind, EntityRef
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import CodeStructureTransformer  # noqa: F401


# Closed set of source tools that can emit a `CodeStructureProject`. The
# entity shape is identical for both; the transformer branches on this when
# the raw-DTO path is wired (Chunk 8).
KindOfSource = Literal["jafax", "codeframe"]


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class CodeStructureProject(Project):
    """A single code-structure project's metadata.

    Per plan §3 the Project is metadata only — entities live in flat
    registries on :class:`Graph`. The legacy ``CodeStructureProject`` owned
    four registries (types / methods / fields / references); that ownership
    moves to :class:`Graph` in Chunk 8.

    The :pyattr:`kind_of_source` Literal lets a single project carry data
    from either JaFax or CodeFrame; task 8 (legacy ``voyager-changes-tasks``)
    is exactly this flip — entities unchanged.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    kind_of_source: KindOfSource = "jafax"

    def transformer_class(self) -> type["CodeStructureTransformer"]:  # type: ignore[override]
        # Lazy import — same pattern as :class:`git.GitProject` /
        # :class:`jira.JiraProject` / :class:`github.GitHubProject`.
        from .transformer import CodeStructureTransformer

        return CodeStructureTransformer


class CodeType(Entity):
    """A class / interface / enum / record from the parsed sources.

    Field mapping vs legacy ``codestructure_models.CodeType``:

    * ``id``                  — unchanged (globally unique synthetic id,
                                e.g. ``"jafax:19"`` in the legacy
                                transformer).
    * ``project_ref``         — NEW: typed ref to the owning
                                :class:`CodeStructureProject`.
    * ``file_ref``            — was implicit through ``file_path`` (a plain
                                string). v2 carries a typed
                                :class:`EntityRef` to the
                                :class:`git.File` Entity. ``None`` is
                                allowed because some types come from
                                external sources (jdk classes etc.) for
                                which no project-local File exists.
    * ``fully_qualified_name``— was ``qualified_name``; renamed to match the
                                plan §4 example and standard FAMIX naming.
    * ``simple_name``         — was ``name``; renamed for clarity (we keep
                                ``fully_qualified_name`` too, so it's the
                                "short name" of the type).
    * ``type_category``       — was ``kind: str``; renamed to dodge the
                                inherited :pyattr:`Entity.kind` ClassVar.
                                Values: ``"class"`` / ``"interface"`` /
                                ``"enum"`` / ``"record"`` /
                                ``"annotation"``.
    * ``is_external``         — preserved.
    * ``is_type_parameter``   — preserved.
    * ``parent_refs``         — was ``super_class_id`` + ``interface_ids``
                                (two parallel fields); v2 collapses both
                                into a single list of typed refs (plan §4
                                "parent_refs"). Order: superclass first,
                                interfaces after. The relation kind
                                (extends vs implements) lives on
                                :class:`CodeReference` rows of kind
                                ``"inheritance"`` / ``"interface"``.
    * ``method_refs``         — NEW: typed refs to the
                                :class:`CodeMethod` entities owned by this
                                type. Plan §4 explicit. Reverse lookup is
                                also available via
                                :class:`CodeMethodRegistry.by_type`.
    * ``field_refs``          — NEW: typed refs to the :class:`CodeField`
                                entities owned by this type. Same shape
                                as ``method_refs``.
    * ``modifiers``           — NEW: optional set of modifier strings
                                (``"public"`` / ``"static"`` / ``"final"``
                                / …). Legacy didn't carry them, but plan
                                §4 mentions them; transformers may stay
                                empty when the source format doesn't
                                emit them.
    """

    kind: ClassVar[EntityKind] = EntityKind.CODE_TYPE

    project_ref: EntityRef
    fully_qualified_name: str
    simple_name: str
    type_category: str
    file_ref: Optional[EntityRef] = None
    is_external: bool = False
    is_type_parameter: bool = False
    parent_refs: List[EntityRef] = []
    method_refs: List[EntityRef] = []
    field_refs: List[EntityRef] = []
    modifiers: List[str] = []


class CodeMethod(Entity):
    """A method (or function) declared inside a :class:`CodeType`.

    Field mapping vs legacy ``codestructure_models.CodeMethod``:

    * ``id``                       — unchanged (synthetic id).
    * ``project_ref``              — NEW: typed ref to
                                     :class:`CodeStructureProject`.
    * ``type_ref``                 — was ``parent_type_id`` (a plain id
                                     str). v2 carries a typed
                                     :class:`EntityRef` to the owning
                                     :class:`CodeType`. ``None`` is
                                     allowed because some legacy JaFax
                                     methods arrive with no container.
    * ``name``                     — unchanged.
    * ``signature``                — unchanged.
    * ``file_ref``                 — was ``file_path`` (a plain string);
                                     v2 carries the typed :class:`EntityRef`
                                     to a :class:`git.File`. ``None``
                                     until the transformer resolves it.
    * ``return_type``              — NEW (plan §4); legacy didn't carry
                                     it, but Codeframe will. Optional with
                                     ``None`` default.
    * ``parameters``               — NEW (plan §4); list of parameter
                                     names / types as raw strings.
                                     Optional and may stay empty.
    * ``modifiers``                — NEW (plan §4); set of modifier
                                     strings.
    * ``line_start`` / ``line_end``— NEW (plan §4 "line range").
                                     ``None`` when source data lacks them.
    * ``cyclomatic_complexity``    — preserved.
    * ``is_constructor``           — preserved.
    * ``called_method_refs``       — NEW (plan §4 "call graph"); typed
                                     refs into the callees. Legacy stored
                                     the call graph as a separate
                                     :class:`CodeReference` collection;
                                     v2 keeps that path AS WELL because
                                     :class:`CodeReference` rows carry
                                     extra metadata
                                     (kind/location/weight). This list is
                                     a cached fast path for "who does X
                                     call" queries.
    """

    kind: ClassVar[EntityKind] = EntityKind.CODE_METHOD

    project_ref: EntityRef
    name: str
    type_ref: Optional[EntityRef] = None
    file_ref: Optional[EntityRef] = None
    signature: str = ""
    return_type: Optional[str] = None
    parameters: List[str] = []
    modifiers: List[str] = []
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    cyclomatic_complexity: int = 0
    is_constructor: bool = False
    called_method_refs: List[EntityRef] = []


class CodeField(Entity):
    """A field / property declared inside a :class:`CodeType`.

    Field mapping vs legacy ``codestructure_models.CodeField``:

    * ``id``                  — unchanged (synthetic id).
    * ``project_ref``         — NEW.
    * ``type_ref``            — was ``parent_type_id``; typed ref now.
                                ``None`` allowed for the same reason as
                                :class:`CodeMethod.type_ref`.
    * ``name``                — unchanged.
    * ``file_ref``            — was ``file_path``; typed ref now.
    * ``declared_type``       — NEW (plan §4). ``Optional`` because
                                JaFax does not always report it.
    * ``modifiers``           — NEW (plan §4); set of modifier strings.
    """

    kind: ClassVar[EntityKind] = EntityKind.CODE_FIELD

    project_ref: EntityRef
    name: str
    type_ref: Optional[EntityRef] = None
    file_ref: Optional[EntityRef] = None
    declared_type: Optional[str] = None
    modifiers: List[str] = []


class CodeReference(Entity):
    """A directed code-level reference between two entities.

    Field mapping vs legacy ``codestructure_models.CodeReference``:

    * ``id``                       — unchanged (synthetic id).
    * ``project_ref``              — NEW.
    * ``reference_kind``           — was ``kind: str``; renamed to dodge
                                     the inherited :pyattr:`Entity.kind`
                                     ClassVar. Values:
                                     ``"call"`` / ``"field_read"`` /
                                     ``"field_write"`` / ``"inheritance"``
                                     / ``"interface"`` / ``"import"``.
                                     The legacy collapsed
                                     ``fieldRead`` / ``fieldWrite`` into
                                     a single ``"fieldAccess"`` value; v2
                                     keeps the split so metrics can
                                     attribute reads vs writes (additive
                                     vs the legacy).
    * ``source_method_ref`` /
      ``source_type_ref``          — was ``from_entity_id`` (a plain id
                                     str). v2 splits into two typed refs
                                     so consumers don't have to inspect
                                     the kind discriminator on the ref to
                                     decide what entity to resolve. One
                                     is set, the other is ``None`` —
                                     calls / field reads / writes set
                                     ``source_method_ref``; inheritance /
                                     interface / import set
                                     ``source_type_ref``.
    * ``target_method_ref`` /
      ``target_type_ref`` /
      ``target_field_ref``         — was ``to_entity_id``. Same shape as
                                     above: exactly one is set per row.
                                     ``target_field_ref`` is new — legacy
                                     didn't have a code-field Entity; v2
                                     does, so field reads/writes resolve
                                     to a typed :class:`CodeField` ref.
    * ``location``                 — NEW (plan §4); a frozen value object
                                     carrying ``file_ref`` + ``line``.
                                     Optional because external / synthetic
                                     references may not carry a location.
    * ``weight``                   — preserved (count of occurrences).
    """

    kind: ClassVar[EntityKind] = EntityKind.CODE_REF

    project_ref: EntityRef
    reference_kind: str
    source_method_ref: Optional[EntityRef] = None
    source_type_ref: Optional[EntityRef] = None
    target_method_ref: Optional[EntityRef] = None
    target_type_ref: Optional[EntityRef] = None
    target_field_ref: Optional[EntityRef] = None
    location_file_ref: Optional[EntityRef] = None
    location_line: Optional[int] = None
    weight: int = 1


__all__ = [
    "CodeField",
    "CodeMethod",
    "CodeReference",
    "CodeStructureProject",
    "CodeType",
    "KindOfSource",
]
