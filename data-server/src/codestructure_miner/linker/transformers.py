"""Transform JaFax layout dicts into a CodeStructureProject.

Implements §3-§4 of communication/B2_codeframe/index_step_general.md.

The JaFax layout file is a flat array of polymorphic entity dicts referenced
by integer ids. The transformer:
  1. Indexes all entities by id.
  2. Re-attaches Methods whose `container` is None via the back-link
     `Class.containedMethods` (the data-format reference reports 3 244 of
     13 036 methods need this fallback).
  3. Resolves every Method/Field to its declaring Class -> file path. The
     "fileName" we use is the union of `File.name` and `Class.fileName`
     strings — 77 `Class.fileName` values are NOT present as `File.name`
     entities, and we do not want to lose that source coverage.
  4. Drops external targets (Class.isExternal=True or unresolvable owner)
     before emitting CodeReferences — dx's default file-level edge behaviour.
  5. Emits CodeReferences for: method calls, field accesses, inheritance,
     interface implementation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from src.codestructure_miner.reader_dto.jafax_dto import (
    AttributeJaFax,
    ClassJaFax,
    FileJaFax,
    ImportStatementJaFax,
    MethodJaFax,
)
from src.common.codestructure_models import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeType,
)
from src.logger import get_logger

LOG = get_logger(__name__)


# Sentinel for the synthetic id namespace — keeps int ids from JaFax distinct
# across formats so the reference-registry can hold rows from both later.
ID_PREFIX = "jafax"


class JaFaxTransformer:
    """Build a `CodeStructureProject` from a JaFax layout entity list.

    `path_prefix` (optional) is stripped from the leading segment of each
    file path so paths join with the iglog-side `repo_prefix` convention used
    in `processor.py`. Default behaviour is to leave the path untouched.
    """

    def __init__(
        self,
        entities: List[Dict[str, Any]],
        path_prefix: Optional[str] = None,
        source: str = "jafax",
    ):
        self.entities = entities
        self.path_prefix = path_prefix.strip("/") if path_prefix else None
        self.source = source

        self._files: Dict[int, FileJaFax] = {}
        self._classes: Dict[int, ClassJaFax] = {}
        self._methods: Dict[int, MethodJaFax] = {}
        self._attributes: Dict[int, AttributeJaFax] = {}
        self._imports: Dict[int, ImportStatementJaFax] = {}
        # method_id -> owning class id (filled from Class.containedMethods so
        # methods with container=None are still resolvable).
        self._method_to_class: Dict[int, int] = {}
        self._field_to_class: Dict[int, int] = {}

    # ── entry point ─────────────────────────────────────────────────────────

    def transform(self) -> CodeStructureProject:
        self._index_entities()
        self._build_back_links()

        project = CodeStructureProject(source=self.source)

        # Types
        for cls in self._classes.values():
            if cls.is_external or cls.is_type_parameter:
                continue
            file_path = self._normalise(cls.file_name)
            if not file_path:
                continue
            project.type_registry.add(self._to_code_type(cls, file_path))

        # Methods
        for m in self._methods.values():
            file_path = self._file_for_method(m)
            if not file_path:
                continue
            project.method_registry.add(self._to_code_method(m, file_path))

        # Fields (Attribute.kind == 'Field' only — params/locals are not file-level)
        for a in self._attributes.values():
            if a.kind != "Field":
                continue
            file_path = self._file_for_field(a)
            if not file_path:
                continue
            project.field_registry.add(self._to_code_field(a, file_path))

        # References — drop edges where source or target file is unresolved
        # (external targets, type parameters, methods with no class back-link).
        self._emit_references(project)

        LOG.info(
            "JaFax transform: %d types, %d methods, %d fields, %d references "
            "(from %d entities)",
            len(project.type_registry.all),
            len(project.method_registry.all),
            len(project.field_registry.all),
            len(project.reference_registry.all),
            len(self.entities),
        )
        return project

    # ── indexing + back-links ───────────────────────────────────────────────

    def _index_entities(self) -> None:
        for raw in self.entities:
            t = raw.get("type")
            try:
                if t == "File":
                    e = FileJaFax.model_validate(raw)
                    self._files[e.id] = e
                elif t == "Class":
                    e = ClassJaFax.model_validate(raw)
                    self._classes[e.id] = e
                elif t == "Method":
                    e = MethodJaFax.model_validate(raw)
                    self._methods[e.id] = e
                elif t == "Attribute":
                    e = AttributeJaFax.model_validate(raw)
                    self._attributes[e.id] = e
                elif t == "ImportStatement":
                    e = ImportStatementJaFax.model_validate(raw)
                    self._imports[e.id] = e
                # Unknown types are skipped silently — JaFax may add new ones.
            except ValidationError as exc:
                # Per orchestrator rule: log + skip malformed rows, never bury.
                LOG.warning(
                    "Skipping malformed JaFax %s entity (id=%s): %s",
                    t, raw.get("id"), exc,
                )

    def _build_back_links(self) -> None:
        # Class.containedMethods -> method_to_class. This recovers the ~3 244
        # methods whose `container` field is None.
        for cls in self._classes.values():
            for mid in cls.contained_methods:
                self._method_to_class.setdefault(mid, cls.id)
            for fid in cls.contained_fields:
                self._field_to_class.setdefault(fid, cls.id)

    # ── lookups ─────────────────────────────────────────────────────────────

    def _file_for_method(self, m: MethodJaFax) -> Optional[str]:
        cls_id = self._owning_class_id_for_method(m)
        if cls_id is None:
            return None
        cls = self._classes.get(cls_id)
        if cls is None or cls.is_external or cls.is_type_parameter:
            return None
        return self._normalise(cls.file_name)

    def _file_for_field(self, a: AttributeJaFax) -> Optional[str]:
        # `container` for Field-kind Attribute points to the declaring Class.
        cls_id: Optional[int] = a.container if a.container is not None else None
        if cls_id is None or cls_id not in self._classes:
            cls_id = self._field_to_class.get(a.id)
        if cls_id is None:
            return None
        cls = self._classes.get(cls_id)
        if cls is None or cls.is_external or cls.is_type_parameter:
            return None
        return self._normalise(cls.file_name)

    def _owning_class_id_for_method(self, m: MethodJaFax) -> Optional[int]:
        """Walk container chain (Method -> Method -> ... -> Class) and fall
        back to the back-link table when container is None or a Method id."""
        cur: Optional[int] = m.container
        while cur is not None:
            if cur in self._classes:
                return cur
            parent = self._methods.get(cur)
            if parent is None or parent.container is None:
                break
            cur = parent.container
        return self._method_to_class.get(m.id)

    # ── emitters ────────────────────────────────────────────────────────────

    def _to_code_type(self, cls: ClassJaFax, file_path: str) -> CodeType:
        kind = "interface" if cls.is_interface else "class"
        # We don't have explicit enum/record markers from JaFax — Java enums
        # and records show up as regular Class entities. Leave kind as "class"
        # for them; downstream consumers that want finer granularity can read
        # the modifiers.
        qualified = f"{cls.pack}.{cls.name}" if cls.pack else cls.name
        return CodeType(
            id=_jid(cls.id),
            name=cls.name,
            qualified_name=qualified,
            file_path=file_path,
            kind=kind,
            is_external=cls.is_external,
            is_type_parameter=cls.is_type_parameter,
            super_class_id=_jid(cls.super_class) if cls.super_class is not None else None,
            interface_ids=[_jid(i) for i in cls.interfaces],
        )

    def _to_code_method(self, m: MethodJaFax, file_path: str) -> CodeMethod:
        cls_id = self._owning_class_id_for_method(m)
        return CodeMethod(
            id=_jid(m.id),
            name=m.name,
            signature=m.signature,
            parent_type_id=_jid(cls_id) if cls_id is not None else None,
            file_path=file_path,
            cyclomatic_complexity=m.cyclomatic_complexity,
            is_constructor=m.is_constructor,
        )

    def _to_code_field(self, a: AttributeJaFax, file_path: str) -> CodeField:
        cls_id = self._field_to_class.get(a.id) or a.container
        return CodeField(
            id=_jid(a.id),
            name=a.name,
            parent_type_id=_jid(cls_id) if cls_id is not None else None,
            file_path=file_path,
        )

    def _emit_references(self, project: CodeStructureProject) -> None:
        """Walk Method.calledMethods / accessedFields and Class.{superClass,interfaces}
        and aggregate into per-edge CodeReference rows. Edges with unresolved
        endpoints (external classes, type parameters, no file path) are dropped.
        """
        # Calls — Method -> Method
        for m in self._methods.values():
            if not m.called_methods:
                continue
            src_file = self._file_for_method(m)
            if src_file is None:
                continue
            for tid in m.called_methods:
                target = self._methods.get(tid)
                if target is None:
                    continue
                tgt_file = self._file_for_method(target)
                if tgt_file is None:
                    continue
                project.reference_registry.add(CodeReference(
                    id=f"call:{m.id}->{tid}",
                    kind="call",
                    from_entity_id=_jid(m.id),
                    from_file_path=src_file,
                    to_entity_id=_jid(tid),
                    to_file_path=tgt_file,
                    weight=1,
                ))

        # Field accesses — Method -> Attribute (kind=Field)
        for m in self._methods.values():
            if not m.accessed_fields:
                continue
            src_file = self._file_for_method(m)
            if src_file is None:
                continue
            for aid in m.accessed_fields:
                attr = self._attributes.get(aid)
                if attr is None or attr.kind != "Field":
                    continue
                tgt_file = self._file_for_field(attr)
                if tgt_file is None:
                    continue
                project.reference_registry.add(CodeReference(
                    id=f"fieldAccess:{m.id}->{aid}",
                    kind="fieldAccess",
                    from_entity_id=_jid(m.id),
                    from_file_path=src_file,
                    to_entity_id=_jid(aid),
                    to_file_path=tgt_file,
                    weight=1,
                ))

        # Inheritance + interfaces — Class -> Class
        for cls in self._classes.values():
            if cls.is_external or cls.is_type_parameter:
                continue
            src_file = self._normalise(cls.file_name)
            if src_file is None:
                continue
            if cls.super_class is not None:
                target = self._classes.get(cls.super_class)
                if target is not None and not target.is_external and not target.is_type_parameter:
                    tgt_file = self._normalise(target.file_name)
                    if tgt_file is not None and tgt_file != src_file:
                        project.reference_registry.add(CodeReference(
                            id=f"inheritance:{cls.id}->{cls.super_class}",
                            kind="inheritance",
                            from_entity_id=_jid(cls.id),
                            from_file_path=src_file,
                            to_entity_id=_jid(cls.super_class),
                            to_file_path=tgt_file,
                            weight=1,
                        ))
            for iid in cls.interfaces:
                target = self._classes.get(iid)
                if target is None or target.is_external or target.is_type_parameter:
                    continue
                tgt_file = self._normalise(target.file_name)
                if tgt_file is None or tgt_file == src_file:
                    continue
                project.reference_registry.add(CodeReference(
                    id=f"interface:{cls.id}->{iid}",
                    kind="interface",
                    from_entity_id=_jid(cls.id),
                    from_file_path=src_file,
                    to_entity_id=_jid(iid),
                    to_file_path=tgt_file,
                    weight=1,
                ))

    # ── path normalisation ──────────────────────────────────────────────────

    def _normalise(self, raw: Optional[str]) -> Optional[str]:
        if not raw:
            return None
        path = raw.replace("\\", "/").lstrip("./").lstrip("/")
        if self.path_prefix:
            prefix = self.path_prefix
            if path.startswith(prefix + "/"):
                path = path[len(prefix) + 1:]
        return path or None


def _jid(int_id: Optional[int]) -> str:
    if int_id is None:
        return ""
    return f"{ID_PREFIX}:{int_id}"
