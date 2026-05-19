"""JaFax → v2 entity-bundle bridge for the code-structure domain.

The v2 :class:`CodeStructureTransformer` (see ``transformer.py``) consumes an
*already-built* entity bundle. This module is the single seam that walks a
raw JaFax JSON dump and instantiates the v2 code-structure-domain entities
documented in ``models.py``.

JaFax JSON layout
-----------------

The JaFax dump is a flat JSON array of objects. Each object has a
``type`` discriminator and a small ``id`` integer. The five known types
and the keys they carry (across the zeppelin fixture):

* ``File``             — ``id``, ``name``, ``imports`` (list of int ids).
* ``ImportStatement``  — ``id``, ``importedClass`` (FQN string),
                         ``name``, ``onDemand``.
* ``Class``            — ``id``, ``name``, ``pack``, ``fileName``,
                         ``modifiers``, ``containedClasses``,
                         ``containedMethods``, ``containedFields``,
                         ``calledMethods``, ``superClass``, ``interfaces``,
                         ``isInterface``, ``isExternal``,
                         ``isTypeParameter``, ``typeParameters``,
                         ``accessedFields``, ``instances``, ``container``.
* ``Method``           — ``id``, ``name``, ``signature``, ``container``
                         (owning class id), ``modifiers``, ``returnType``
                         (class id), ``cyclomaticComplexity``,
                         ``parameters``, ``calledMethods``,
                         ``accessedFields``, ``isConstructor``,
                         ``isDefaultConstructor``, ``containedClasses``,
                         ``containedMethods``, ``typeParameters``,
                         ``localVariables``.
* ``Attribute``        — ``id``, ``name``, ``container`` (owning class id),
                         ``class`` (declared-type class id),
                         ``kind`` (``"Field"`` or other), ``modifiers``.

Mapping to v2 entities
----------------------

* ``Class``     → :class:`CodeType`.
  ``type_category`` derives from ``isInterface``; ``parent_refs`` puts
  ``superClass`` first, then ``interfaces``.
* ``Method``    → :class:`CodeMethod`. ``type_ref`` resolves
  ``container``; ``return_type`` resolves the FQN of the ``returnType``
  class id (best-effort — None for unknown).
* ``Attribute`` (with ``kind == "Field"``) → :class:`CodeField`.
  ``declared_type`` resolves the FQN of the ``class`` id.
* References extracted into :class:`CodeReference` rows:
   - Inheritance ``superClass`` edges (``reference_kind = "inheritance"``).
   - Interface implementation edges (``reference_kind = "interface"``).
   - Method-level ``calledMethods`` edges (``reference_kind = "call"``).
   - Method-level ``accessedFields`` edges
     (``reference_kind = "field_read"`` — JaFax doesn't split reads
     from writes).

Entity ids
----------

Every v2 entity id is built as ``f"jafax:{raw_id}"`` so it stays
deterministic across re-runs and never collides with ids from other
sources. The :class:`CodeStructureProject` id is the ``repo_name`` the
caller supplies.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ...kernel import EntityKind, EntityRef
from ...people import SourceKind
from ..git.models import File
from .models import (
    CodeField,
    CodeMethod,
    CodeReference,
    CodeStructureProject,
    CodeType,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ID helpers
# ---------------------------------------------------------------------------


def _type_id(raw_id: int) -> str:
    return f"jafax:{raw_id}"


def _method_id(raw_id: int) -> str:
    return f"jafax:{raw_id}"


def _field_id(raw_id: int) -> str:
    return f"jafax:{raw_id}"


def _ref_id(seq: int) -> str:
    return f"jafax:ref:{seq}"


def _type_ref(raw_id: int) -> EntityRef:
    return EntityRef(kind=EntityKind.CODE_TYPE, id=_type_id(raw_id))


def _method_ref(raw_id: int) -> EntityRef:
    return EntityRef(kind=EntityKind.CODE_METHOD, id=_method_id(raw_id))


def _field_ref(raw_id: int) -> EntityRef:
    return EntityRef(kind=EntityKind.CODE_FIELD, id=_field_id(raw_id))


# ---------------------------------------------------------------------------
# Modifier / category helpers
# ---------------------------------------------------------------------------


def _normalize_modifiers(modifiers: Any) -> List[str]:
    """Lowercase JaFax modifier strings, preserving order, dropping blanks."""
    if not modifiers:
        return []
    out: List[str] = []
    for raw in modifiers:
        if not isinstance(raw, str) or not raw:
            continue
        out.append(raw.lower())
    return out


def _type_category(raw: Mapping[str, Any]) -> str:
    """Classify a JaFax ``Class`` row by its boolean flags."""
    if raw.get("isInterface"):
        return "interface"
    # JaFax doesn't emit enum / record / annotation flags in the fixture,
    # so we fall back to "class". Modifiers may still encode "Enum" /
    # "Annotation" on some shapes — honour them when present.
    modifiers = raw.get("modifiers") or []
    lowered = {m.lower() for m in modifiers if isinstance(m, str)}
    if "enum" in lowered:
        return "enum"
    if "annotation" in lowered:
        return "annotation"
    if "record" in lowered:
        return "record"
    return "class"


def _fully_qualified_name(raw: Mapping[str, Any]) -> str:
    """Build ``pack.SimpleName`` from a JaFax ``Class`` row."""
    name = raw.get("name") or ""
    pack = raw.get("pack") or ""
    if pack and name:
        return f"{pack}.{name}"
    return name or pack


def _normalize_file_path(absolute_path: str, repo_name: str) -> str:
    """Reduce a JaFax absolute path to the repo-relative form used by git.

    Mirrors the lizard bridge's ``_normalize_file_path`` (see
    ``metrics_lizard/bridge.py``): strips everything up to and including
    the **last** ``/<repo_name>/<repo_name>/`` segment, falling back to
    a single ``/<repo_name>/`` strip. Returns the input verbatim if
    neither anchor matches.
    """
    doubled = f"/{repo_name}/{repo_name}/"
    idx = absolute_path.rfind(doubled)
    if idx != -1:
        return absolute_path[idx + len(doubled):]
    single = f"/{repo_name}/"
    idx = absolute_path.rfind(single)
    if idx != -1:
        return absolute_path[idx + len(single):]
    return absolute_path


# ---------------------------------------------------------------------------
# Public bridge entry point
# ---------------------------------------------------------------------------


def build_code_structure_bundle(
    file_path: Path,
    repo_name: str,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Parse a JaFax JSON dump and return a v2 entity bundle.

    Parameters
    ----------
    file_path:
        Filesystem path to the JaFax ``*-layout.json`` dump.
    repo_name:
        Stable identifier for the project (used as the
        :class:`CodeStructureProject` ``id``). The bridge never invents a
        uuid — callers pick this so it stays stable across re-runs.
    project_name:
        Display ``name`` on the :class:`CodeStructureProject`. Defaults
        to ``"Project"``.

    Returns
    -------
    Mapping with the keys :class:`CodeStructureTransformer` expects:
    ``{"project", "code_types", "code_methods", "code_fields", "code_refs"}``.
    """
    raw_entries = _read_jafax(file_path)

    project = CodeStructureProject(
        id=repo_name,
        name=project_name or repo_name,
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="jafax",
    )
    project_ref = project.ref()

    # First pass: split entries by type so we can resolve FQNs from
    # class ids before any method is built.
    classes_by_id: Dict[int, Mapping[str, Any]] = {}
    methods_by_id: Dict[int, Mapping[str, Any]] = {}
    attributes_by_id: Dict[int, Mapping[str, Any]] = {}

    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            continue
        kind = raw.get("type")
        rid = raw.get("id")
        if rid is None:
            continue
        if kind == "Class":
            classes_by_id[rid] = raw
        elif kind == "Method":
            methods_by_id[rid] = raw
        elif kind == "Attribute":
            attributes_by_id[rid] = raw

    # FQN lookup for resolving return-type / declared-type class ids.
    class_fqn_by_id: Dict[int, str] = {
        cid: _fully_qualified_name(raw) for cid, raw in classes_by_id.items()
    }

    code_types, file_ref_by_class_id, all_classes_self_repo = _build_code_types(
        classes_by_id, project_ref, repo_name
    )
    code_methods = _build_code_methods(
        methods_by_id, class_fqn_by_id, file_ref_by_class_id, project_ref
    )
    code_fields = _build_code_fields(
        attributes_by_id,
        class_fqn_by_id,
        file_ref_by_class_id,
        project_ref,
    )
    code_refs = _build_code_refs(
        classes_by_id,
        methods_by_id,
        attributes_by_id,
        project_ref,
    )

    return {
        "project": project,
        "code_types": code_types,
        "code_methods": code_methods,
        "code_fields": code_fields,
        "code_refs": code_refs,
        "_meta": {"all_rows_self_repo": all_classes_self_repo},
    }


# ---------------------------------------------------------------------------
# Internals — readers / builders
# ---------------------------------------------------------------------------


def _read_jafax(file_path: Path) -> List[Mapping[str, Any]]:
    """Open the JaFax dump and return its top-level list verbatim."""
    with open(file_path, "r", encoding="utf-8") as stream:
        data = json.load(stream)
    if not isinstance(data, list):
        raise ValueError(
            f"JaFax dump at {file_path} must be a JSON array; "
            f"got {type(data).__name__}."
        )
    return data


def _build_code_types(
    classes_by_id: Mapping[int, Mapping[str, Any]],
    project_ref: EntityRef,
    repo_name: str,
) -> tuple[List[CodeType], Dict[str, EntityRef], bool]:
    out: List[CodeType] = []
    file_ref_by_class_id: Dict[str, EntityRef] = {}
    missing_file_name_warned = False
    any_class_seen = False
    all_classes_self_repo = True
    for rid, raw in classes_by_id.items():
        any_class_seen = True
        parent_refs: List[EntityRef] = []
        super_class = raw.get("superClass")
        if isinstance(super_class, int) and super_class in classes_by_id:
            parent_refs.append(_type_ref(super_class))
        for iface in raw.get("interfaces") or []:
            if isinstance(iface, int) and iface in classes_by_id:
                parent_refs.append(_type_ref(iface))

        method_refs = [
            _method_ref(mid)
            for mid in raw.get("containedMethods") or []
            if isinstance(mid, int)
        ]
        field_refs = [
            _field_ref(fid)
            for fid in raw.get("containedFields") or []
            if isinstance(fid, int)
        ]

        # Per-row repo override: Class["repo"] wins if non-empty,
        # otherwise we fall back to the function-level anchor.
        raw_repo = raw.get("repo")
        if isinstance(raw_repo, str) and raw_repo.strip():
            repo_used = raw_repo.strip()
        else:
            repo_used = repo_name
            all_classes_self_repo = False

        file_ref: Optional[EntityRef] = None
        file_name = raw.get("fileName")
        if isinstance(file_name, str) and file_name:
            rel_path = _normalize_file_path(file_name, repo_used)
            file_ref = EntityRef(
                kind=EntityKind.FILE,
                id=File.make_id(repo_used, rel_path),
            )
        elif not missing_file_name_warned:
            logger.warning(
                "JaFax Class rows missing 'fileName' for repo %r; "
                "CodeType.file_ref will be None for affected classes.",
                repo_name,
            )
            missing_file_name_warned = True

        type_id = _type_id(rid)
        if file_ref is not None:
            file_ref_by_class_id[type_id] = file_ref

        out.append(
            CodeType(
                id=type_id,
                project_ref=project_ref,
                fully_qualified_name=_fully_qualified_name(raw),
                simple_name=raw.get("name") or "",
                type_category=_type_category(raw),
                file_ref=file_ref,
                is_external=bool(raw.get("isExternal", False)),
                is_type_parameter=bool(raw.get("isTypeParameter", False)),
                parent_refs=parent_refs,
                method_refs=method_refs,
                field_refs=field_refs,
                modifiers=_normalize_modifiers(raw.get("modifiers")),
            )
        )
    return out, file_ref_by_class_id, bool(any_class_seen and all_classes_self_repo)


def _build_code_methods(
    methods_by_id: Mapping[int, Mapping[str, Any]],
    class_fqn_by_id: Mapping[int, str],
    file_ref_by_class_id: Mapping[str, EntityRef],
    project_ref: EntityRef,
) -> List[CodeMethod]:
    out: List[CodeMethod] = []
    for rid, raw in methods_by_id.items():
        type_ref: Optional[EntityRef] = None
        file_ref: Optional[EntityRef] = None
        container = raw.get("container")
        if isinstance(container, int) and container in class_fqn_by_id:
            type_ref = _type_ref(container)
            file_ref = file_ref_by_class_id.get(_type_id(container))

        return_type: Optional[str] = None
        rt = raw.get("returnType")
        if isinstance(rt, int):
            return_type = class_fqn_by_id.get(rt)

        called_refs = [
            _method_ref(mid)
            for mid in raw.get("calledMethods") or []
            if isinstance(mid, int) and mid in methods_by_id
        ]

        # JaFax ``parameters`` is a list of ids referencing local-variable
        # rows that aren't part of the layout fixture; surface raw string
        # ids so the field is non-empty when present without inventing
        # a fake type. Tolerate missing keys by emitting an empty list.
        parameters: List[str] = []
        for param in raw.get("parameters") or []:
            if isinstance(param, str):
                parameters.append(param)
            elif isinstance(param, int):
                parameters.append(str(param))

        out.append(
            CodeMethod(
                id=_method_id(rid),
                project_ref=project_ref,
                name=raw.get("name") or "",
                type_ref=type_ref,
                file_ref=file_ref,
                signature=raw.get("signature") or "",
                return_type=return_type,
                parameters=parameters,
                modifiers=_normalize_modifiers(raw.get("modifiers")),
                line_start=None,
                line_end=None,
                cyclomatic_complexity=int(raw.get("cyclomaticComplexity") or 0),
                is_constructor=bool(raw.get("isConstructor", False)),
                called_method_refs=called_refs,
            )
        )
    return out


def _build_code_fields(
    attributes_by_id: Mapping[int, Mapping[str, Any]],
    class_fqn_by_id: Mapping[int, str],
    file_ref_by_class_id: Mapping[str, EntityRef],
    project_ref: EntityRef,
) -> List[CodeField]:
    out: List[CodeField] = []
    for rid, raw in attributes_by_id.items():
        # Only ``kind == "Field"`` attributes map to CodeField. Other kinds
        # (e.g. local variables, parameters) are not modelled at the v2
        # graph level. JaFax sometimes omits the key — default to "Field"
        # so we don't silently drop everything when the upstream emitter
        # changes its mind about that field.
        kind = raw.get("kind", "Field")
        if kind != "Field":
            continue

        type_ref: Optional[EntityRef] = None
        file_ref: Optional[EntityRef] = None
        container = raw.get("container")
        if isinstance(container, int) and container in class_fqn_by_id:
            type_ref = _type_ref(container)
            file_ref = file_ref_by_class_id.get(_type_id(container))

        declared_type: Optional[str] = None
        cls_id = raw.get("class")
        if isinstance(cls_id, int):
            declared_type = class_fqn_by_id.get(cls_id)

        out.append(
            CodeField(
                id=_field_id(rid),
                project_ref=project_ref,
                name=raw.get("name") or "",
                type_ref=type_ref,
                file_ref=file_ref,
                declared_type=declared_type,
                modifiers=_normalize_modifiers(raw.get("modifiers")),
            )
        )
    return out


def _build_code_refs(
    classes_by_id: Mapping[int, Mapping[str, Any]],
    methods_by_id: Mapping[int, Mapping[str, Any]],
    attributes_by_id: Mapping[int, Mapping[str, Any]],
    project_ref: EntityRef,
) -> List[CodeReference]:
    """Walk classes/methods to extract directed reference rows.

    JaFax doesn't carry per-call line locations, so ``location_*`` stays
    ``None``. Each emitted reference has a synthetic id so the
    :class:`CodeReferenceRegistry` can index it.
    """
    out: List[CodeReference] = []
    seq = 0

    # Class-level edges: inheritance + interface implementation.
    for cid, raw in classes_by_id.items():
        source_ref = _type_ref(cid)

        super_class = raw.get("superClass")
        if isinstance(super_class, int) and super_class in classes_by_id:
            out.append(
                CodeReference(
                    id=_ref_id(seq),
                    project_ref=project_ref,
                    reference_kind="inheritance",
                    source_type_ref=source_ref,
                    target_type_ref=_type_ref(super_class),
                )
            )
            seq += 1

        for iface in raw.get("interfaces") or []:
            if isinstance(iface, int) and iface in classes_by_id:
                out.append(
                    CodeReference(
                        id=_ref_id(seq),
                        project_ref=project_ref,
                        reference_kind="interface",
                        source_type_ref=source_ref,
                        target_type_ref=_type_ref(iface),
                    )
                )
                seq += 1

    # Method-level edges: calls + field reads.
    for mid, raw in methods_by_id.items():
        source_ref = _method_ref(mid)

        for callee in raw.get("calledMethods") or []:
            if isinstance(callee, int) and callee in methods_by_id:
                out.append(
                    CodeReference(
                        id=_ref_id(seq),
                        project_ref=project_ref,
                        reference_kind="call",
                        source_method_ref=source_ref,
                        target_method_ref=_method_ref(callee),
                    )
                )
                seq += 1

        for fid in raw.get("accessedFields") or []:
            if not isinstance(fid, int):
                continue
            if fid not in attributes_by_id:
                continue
            # Only emit a field_read row when the accessed attribute is
            # actually a Field (not a local variable / parameter).
            if attributes_by_id[fid].get("kind", "Field") != "Field":
                continue
            out.append(
                CodeReference(
                    id=_ref_id(seq),
                    project_ref=project_ref,
                    reference_kind="field_read",
                    source_method_ref=source_ref,
                    target_field_ref=_field_ref(fid),
                )
            )
            seq += 1

    return out


__all__ = ["build_code_structure_bundle"]
