"""CodeFrame → v2 entity-bundle bridge for the code-structure domain.

The v2 :class:`CodeStructureTransformer` (see ``transformer.py``) consumes
an *already-built* entity bundle. This module is the single seam that
walks a CodeFrame ``*.jsonl`` dump and instantiates the v2
code-structure-domain entities documented in ``models.py``.

CodeFrame JSONL layout (v0.6.6)
-------------------------------

The CodeFrame dump is a streaming JSON-lines file. Each line is one
self-contained JSON object. Three record shapes appear:

* ``{"kind": "run", "total_files": ..., "started_at": ..., "input_path": ...}``
  — single header line.
* ``{"filePath", "language", "packageName"?, "imports"?, "types"?, "methods"?, "methodCalls"?}``
  — one record per analyzed file.
* ``{"kind": "done", "files_analyzed", "files_with_errors", "duration_seconds", "ended_at"}``
  — single footer line.

A file record may carry:

* ``types[]`` — declared classes / interfaces / enums. Each type carries
  ``{kind, name, visibility, modifiers, implementsInterfaces?, annotations?, methods[], fields[]?, types[]?}``.
  ``types[].types[]`` recurses for inner classes.
* ``methods[]`` at the file scope — JS / TS / Python free functions /
  module-level methods (no owning type).
* ``methodCalls[]`` at the file scope — calls from free expression
  positions (currently unused for edges; see §4 below).

Each type-scope ``methods[].methodCalls[]`` carries:
``{methodName, objectName?, objectType?, callCount, parameterCount}``.

Mapping to v2 entities
----------------------

* ``types[i]``               → :class:`CodeType`. ``type_category`` is
  the ``kind`` field verbatim (``"class"`` / ``"interface"`` / etc.).
  ``parent_refs`` is populated from ``implementsInterfaces`` only —
  CodeFrame v0.6.6 does not emit ``extendsClass``, so inheritance edges
  vanish.
* ``methods[j]``             → :class:`CodeMethod`. For file-scope free
  functions ``type_ref`` is ``None``.
* ``fields[]`` (Java mostly) → :class:`CodeField`.
* ``CodeReference`` rows are emitted for:
   - resolved ``implementsInterfaces`` (``reference_kind = "interface"``).
   - resolved method calls (``reference_kind = "call"``). Unresolved
     calls are silently dropped; the bridge logs a single
     ``resolved X / Y method calls`` INFO line per run.

Entity ids
----------

CodeFrame has no integer ids, so we synthesize stable, deterministic ids
from the source fields (see §5 in the Task 8 plan):

* ``CodeType.id``      = ``codeframe:type:{filePath}#{packageName}.{simpleName}``
* ``CodeMethod.id``    = ``codeframe:method:{filePath}#{ownerFqn}.{methodName}({paramTypesCSV})``
* ``CodeField.id``     = ``codeframe:field:{filePath}#{ownerFqn}.{fieldName}``
* ``CodeReference.id`` = ``codeframe:ref:{seq}``

``filePath`` in the id is the *raw* absolute path from the CodeFrame
record so the keys stay equal across all records that mention the same
file. File-relative-path normalization for :class:`File` refs happens
separately via :func:`_normalize_file_path`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple

from ...kernel import EntityKind, EntityRef
from ...people.source import SourceKind
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
# Public bridge entry point
# ---------------------------------------------------------------------------


def build_code_structure_bundle(
    file_path: Path,
    repo_name: str,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Parse a CodeFrame JSONL dump and return a v2 entity bundle.

    Parameters
    ----------
    file_path:
        Filesystem path to the CodeFrame ``*-codeframe.jsonl`` dump.
    repo_name:
        Stable identifier for the project (used as the
        :class:`CodeStructureProject` ``id``).
    project_name:
        Display ``name`` on the :class:`CodeStructureProject`.

    Returns
    -------
    Mapping with the keys :class:`CodeStructureTransformer` expects:
    ``{"project", "code_types", "code_methods", "code_fields",
    "code_refs", "_meta"}``.
    """
    project = CodeStructureProject(
        id=repo_name,
        name=project_name or repo_name,
        source=SourceKind.CODE_STRUCTURE,
        kind_of_source="codeframe",
    )
    project_ref = project.ref()

    # Materialize the JSONL stream once — we need two passes (index +
    # emit) and the file is on local disk, so the memory cost is fine.
    file_records: List[Mapping[str, Any]] = []
    unknown_kinds_seen = 0
    run_seen = False
    done_seen = False
    for rec in _iter_codeframe_records(file_path):
        cls = _classify_record(rec)
        if cls == "file":
            file_records.append(rec)
        elif cls == "run":
            run_seen = True
        elif cls == "done":
            done_seen = True
        else:
            unknown_kinds_seen += 1

    if unknown_kinds_seen:
        logger.warning(
            "CodeFrame dump at %s carried %d unrecognised record(s); skipped.",
            file_path,
            unknown_kinds_seen,
        )
    if not run_seen:
        logger.debug("CodeFrame dump at %s had no 'run' header record.", file_path)
    if not done_seen:
        logger.debug("CodeFrame dump at %s had no 'done' footer record.", file_path)

    # --- Pass 1: build the FQN / simple-name / type-FQN indexes ----------
    fqn_index: Dict[str, EntityRef] = {}
    simple_name_index: Dict[str, List[EntityRef]] = {}
    type_fqn_index: Dict[str, List[EntityRef]] = {}

    for rec in file_records:
        package = rec.get("packageName") or ""
        rec_path = rec.get("filePath") or ""
        for tnode, _depth in _walk_types(rec.get("types") or []):
            owner_fqn = _join_fqn(package, tnode.get("name") or "")
            type_id = _codeframe_id(
                "type", rec_path, owner_fqn, "", ""
            )
            type_ref = EntityRef(kind=EntityKind.CODE_TYPE, id=type_id)
            _index_type(type_fqn_index, owner_fqn, tnode.get("name") or "", type_ref)
            for m in tnode.get("methods") or []:
                _index_method(
                    fqn_index,
                    simple_name_index,
                    rec_path,
                    owner_fqn,
                    m,
                )
        # File-scope free functions: owner FQN derived from the file
        # stem so call edges still resolve within the same file.
        free_owner = _file_stem_owner(rec_path)
        for m in rec.get("methods") or []:
            _index_method(
                fqn_index,
                simple_name_index,
                rec_path,
                free_owner,
                m,
            )

    # --- Pass 2: emit entities + refs ------------------------------------
    code_types: List[CodeType] = []
    code_methods: List[CodeMethod] = []
    code_fields: List[CodeField] = []
    code_refs: List[CodeReference] = []
    ref_seq = 0

    resolved_calls = 0
    total_calls = 0

    for rec in file_records:
        package = rec.get("packageName") or ""
        rec_path = rec.get("filePath") or ""
        file_ref = _file_ref_for(rec_path, repo_name)

        # Types (recursing into nested types[]) ---------------------------
        for tnode, _depth in _walk_types(rec.get("types") or []):
            simple_name = tnode.get("name") or ""
            owner_fqn = _join_fqn(package, simple_name)
            type_id = _codeframe_id("type", rec_path, owner_fqn, "", "")
            type_ref = EntityRef(kind=EntityKind.CODE_TYPE, id=type_id)

            method_refs: List[EntityRef] = []
            field_refs: List[EntityRef] = []
            parent_refs: List[EntityRef] = []

            # Resolved implementsInterfaces -----------------------------
            for iface_name in tnode.get("implementsInterfaces") or []:
                if not isinstance(iface_name, str) or not iface_name:
                    continue
                target = _resolve_type_name(iface_name, type_fqn_index)
                if target is None:
                    continue
                parent_refs.append(target)
                code_refs.append(
                    CodeReference(
                        id=_ref_id(ref_seq),
                        project_ref=project_ref,
                        reference_kind="interface",
                        source_type_ref=type_ref,
                        target_type_ref=target,
                    )
                )
                ref_seq += 1

            # Methods ---------------------------------------------------
            for m in tnode.get("methods") or []:
                method_entity, calls_total, calls_resolved, ref_seq = _emit_method(
                    m,
                    project_ref=project_ref,
                    owner_simple_name=simple_name,
                    owner_fqn=owner_fqn,
                    owner_type_ref=type_ref,
                    file_path_key=rec_path,
                    file_ref=file_ref,
                    fqn_index=fqn_index,
                    simple_name_index=simple_name_index,
                    out_refs=code_refs,
                    ref_seq=ref_seq,
                )
                code_methods.append(method_entity)
                method_refs.append(method_entity.ref())
                total_calls += calls_total
                resolved_calls += calls_resolved

            # Fields ----------------------------------------------------
            for f in tnode.get("fields") or []:
                if not isinstance(f, Mapping):
                    continue
                fname = f.get("name") or ""
                if not fname:
                    continue
                field_id = _codeframe_id("field", rec_path, owner_fqn, fname, "")
                modifiers = _normalize_modifiers(f.get("modifiers"))
                vis_mod = _visibility_to_modifier(f.get("visibility"))
                if vis_mod and vis_mod not in modifiers:
                    modifiers.append(vis_mod)
                field = CodeField(
                    id=field_id,
                    project_ref=project_ref,
                    name=fname,
                    type_ref=type_ref,
                    file_ref=file_ref,
                    declared_type=f.get("type"),
                    modifiers=modifiers,
                )
                code_fields.append(field)
                field_refs.append(field.ref())

            type_modifiers = _normalize_modifiers(tnode.get("modifiers"))
            vis_mod = _visibility_to_modifier(tnode.get("visibility"))
            if vis_mod and vis_mod not in type_modifiers:
                type_modifiers.append(vis_mod)

            code_types.append(
                CodeType(
                    id=type_id,
                    project_ref=project_ref,
                    fully_qualified_name=owner_fqn,
                    simple_name=simple_name,
                    type_category=str(tnode.get("kind") or "class"),
                    file_ref=file_ref,
                    is_external=False,
                    is_type_parameter=False,
                    parent_refs=parent_refs,
                    method_refs=method_refs,
                    field_refs=field_refs,
                    modifiers=type_modifiers,
                )
            )

        # File-scope free functions (JS / TS / Python module-level) ------
        free_owner = _file_stem_owner(rec_path)
        for m in rec.get("methods") or []:
            method_entity, calls_total, calls_resolved, ref_seq = _emit_method(
                m,
                project_ref=project_ref,
                owner_simple_name=None,
                owner_fqn=free_owner,
                owner_type_ref=None,
                file_path_key=rec_path,
                file_ref=file_ref,
                fqn_index=fqn_index,
                simple_name_index=simple_name_index,
                out_refs=code_refs,
                ref_seq=ref_seq,
            )
            code_methods.append(method_entity)
            total_calls += calls_total
            resolved_calls += calls_resolved

    logger.info(
        "CodeFrame bridge resolved %d / %d method calls (repo=%s)",
        resolved_calls,
        total_calls,
        repo_name,
    )

    return {
        "project": project,
        "code_types": code_types,
        "code_methods": code_methods,
        "code_fields": code_fields,
        "code_refs": code_refs,
        # CodeFrame doesn't carry per-record repo info, so every row is
        # taken to belong to the caller-supplied repo anchor. The
        # processor uses this to suppress its "rows from a different
        # repo" warning. Stay False: True is reserved for the case where
        # every row carries an explicit repo tag matching the anchor,
        # and CodeFrame has no such tag.
        "_meta": {"all_rows_self_repo": False},
    }


# ---------------------------------------------------------------------------
# JSONL reader
# ---------------------------------------------------------------------------


def _iter_codeframe_records(file_path: Path) -> Iterator[Mapping[str, Any]]:
    """Stream JSONL records from ``file_path``.

    Tolerates trailing whitespace and blank lines. Raises ``ValueError``
    on malformed JSON, surfacing the offending line number to help
    operators triage corrupt dumps.
    """
    with open(file_path, "r", encoding="utf-8") as stream:
        for lineno, raw in enumerate(stream, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"CodeFrame dump at {file_path} line {lineno} is not valid JSON: {exc}"
                ) from exc
            if isinstance(rec, Mapping):
                yield rec
            # Non-object records (lists, numbers, …) are silently skipped;
            # CodeFrame never emits them today, but we don't want to
            # explode on a future header change.


def _classify_record(rec: Mapping[str, Any]) -> str:
    """Classify a parsed JSONL record as run / file / done / unknown."""
    kind = rec.get("kind")
    if kind == "run":
        return "run"
    if kind == "done":
        return "done"
    if isinstance(rec.get("filePath"), str):
        return "file"
    return "unknown"


# ---------------------------------------------------------------------------
# ID + ref helpers
# ---------------------------------------------------------------------------


def _codeframe_id(
    entity: str,
    file_path: str,
    owner_fqn: str,
    member_name: str,
    suffix: str,
) -> str:
    """Build a deterministic CodeFrame entity id.

    Format:
      - type:   ``codeframe:type:{filePath}#{ownerFqn}``
      - method: ``codeframe:method:{filePath}#{ownerFqn}.{methodName}({paramTypesCSV})``
      - field:  ``codeframe:field:{filePath}#{ownerFqn}.{fieldName}``
    """
    body = file_path or ""
    fragment = owner_fqn or ""
    if member_name:
        fragment = f"{fragment}.{member_name}" if fragment else member_name
    if suffix:
        fragment = f"{fragment}{suffix}"
    return f"codeframe:{entity}:{body}#{fragment}"


def _ref_id(seq: int) -> str:
    return f"codeframe:ref:{seq}"


def _file_ref_for(absolute_path: str, repo_name: str) -> Optional[EntityRef]:
    if not absolute_path:
        return None
    rel_path = _normalize_file_path(absolute_path, repo_name)
    return EntityRef(kind=EntityKind.FILE, id=File.make_id(repo_name, rel_path))


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def _normalize_modifiers(modifiers: Any) -> List[str]:
    """Lowercase CodeFrame modifier strings, preserving order, dropping blanks."""
    if not modifiers:
        return []
    out: List[str] = []
    for raw in modifiers:
        if not isinstance(raw, str) or not raw:
            continue
        out.append(raw.lower())
    return out


def _visibility_to_modifier(visibility: Any) -> Optional[str]:
    """Fold CodeFrame's separate ``visibility`` string into ``modifiers``.

    Returns the lowercased visibility token (``"public"`` / ``"private"``
    / ``"protected"`` / …) or ``None`` when the source omits it.
    """
    if not isinstance(visibility, str):
        return None
    token = visibility.strip().lower()
    return token or None


def _normalize_file_path(absolute_path: str, repo_name: str) -> str:
    """Reduce a raw absolute path to the repo-relative form used by git.

    Mirrors the lizard bridge's ``_normalize_file_path``: strips
    everything up to and including the **last**
    ``/<repo_name>/<repo_name>/`` segment, falling back to a single
    ``/<repo_name>/`` strip. Returns the input verbatim if neither
    anchor matches.
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
# Indexing helpers
# ---------------------------------------------------------------------------


def _walk_types(
    types: Iterable[Mapping[str, Any]],
    depth: int = 0,
) -> Iterator[Tuple[Mapping[str, Any], int]]:
    """Depth-first walk over ``types[]`` honouring nested ``types[]``."""
    for t in types or ():
        if not isinstance(t, Mapping):
            continue
        yield t, depth
        nested = t.get("types") or ()
        if nested:
            yield from _walk_types(nested, depth + 1)


def _join_fqn(package: str, simple_name: str) -> str:
    if package and simple_name:
        return f"{package}.{simple_name}"
    return simple_name or package


def _file_stem_owner(file_path: str) -> str:
    """Owner FQN used for file-scope (no-type) free functions.

    Uses the file stem so call edges within the same file resolve, but
    keeps the path so two files with the same stem don't collide.
    """
    if not file_path:
        return ""
    return Path(file_path).stem or file_path


def _param_type_csv(method: Mapping[str, Any]) -> str:
    parts: List[str] = []
    for p in method.get("parameters") or ():
        if not isinstance(p, Mapping):
            continue
        ptype = p.get("type")
        parts.append(str(ptype) if ptype is not None else "?")
    return ",".join(parts)


def _index_method(
    fqn_index: Dict[str, EntityRef],
    simple_name_index: Dict[str, List[EntityRef]],
    file_path: str,
    owner_fqn: str,
    method: Mapping[str, Any],
) -> None:
    name = method.get("name") if isinstance(method, Mapping) else None
    if not isinstance(name, str) or not name:
        return
    csv = _param_type_csv(method)
    method_id = _codeframe_id("method", file_path, owner_fqn, name, f"({csv})")
    ref = EntityRef(kind=EntityKind.CODE_METHOD, id=method_id)

    # FQN key — owner-qualified, ignoring overload disambiguation so the
    # high-fidelity resolver (objectType.method) hits regardless of how
    # many params the caller passed.
    fqn_key = f"{owner_fqn}.{name}" if owner_fqn else name
    fqn_index.setdefault(fqn_key, ref)

    simple_name_index.setdefault(name, []).append(ref)


def _index_type(
    type_fqn_index: Dict[str, List[EntityRef]],
    fqn: str,
    simple_name: str,
    ref: EntityRef,
) -> None:
    if fqn:
        type_fqn_index.setdefault(fqn, []).append(ref)
    if simple_name and simple_name != fqn:
        type_fqn_index.setdefault(simple_name, []).append(ref)


def _resolve_type_name(
    raw_name: str,
    type_fqn_index: Mapping[str, List[EntityRef]],
) -> Optional[EntityRef]:
    candidates = type_fqn_index.get(raw_name)
    if not candidates:
        return None
    # Ambiguous bare-name hits are dropped — we only resolve when unique.
    if len(candidates) > 1:
        return None
    return candidates[0]


def _resolve_call(
    call: Mapping[str, Any],
    owner_file_path: str,
    fqn_index: Mapping[str, EntityRef],
    simple_name_index: Mapping[str, List[EntityRef]],
) -> Optional[EntityRef]:
    """Best-effort FQN resolution for a single ``methodCalls[]`` entry.

    Resolution order:
    1. ``objectType.methodName`` (CodeFrame's highest-fidelity hint).
    2. ``objectName.methodName`` (treats ``objectName`` as a bare FQN).
    3. Unique simple-name hit in ``simple_name_index``.
    """
    if not isinstance(call, Mapping):
        return None
    method_name = call.get("methodName")
    if not isinstance(method_name, str) or not method_name:
        return None

    object_type = call.get("objectType")
    if isinstance(object_type, str) and object_type:
        ref = fqn_index.get(f"{object_type}.{method_name}")
        if ref is not None:
            return ref

    object_name = call.get("objectName")
    if isinstance(object_name, str) and object_name:
        ref = fqn_index.get(f"{object_name}.{method_name}")
        if ref is not None:
            return ref

    candidates = simple_name_index.get(method_name) or []
    if len(candidates) == 1:
        return candidates[0]
    return None


# ---------------------------------------------------------------------------
# Emit helpers
# ---------------------------------------------------------------------------


def _emit_method(
    method: Mapping[str, Any],
    *,
    project_ref: EntityRef,
    owner_simple_name: Optional[str],
    owner_fqn: str,
    owner_type_ref: Optional[EntityRef],
    file_path_key: str,
    file_ref: Optional[EntityRef],
    fqn_index: Mapping[str, EntityRef],
    simple_name_index: Mapping[str, List[EntityRef]],
    out_refs: List[CodeReference],
    ref_seq: int,
) -> Tuple[CodeMethod, int, int, int]:
    """Build one :class:`CodeMethod` + its call-edge :class:`CodeReference` rows.

    Returns ``(method, calls_total, calls_resolved, next_ref_seq)``.
    """
    name = method.get("name") if isinstance(method, Mapping) else ""
    name = name if isinstance(name, str) else ""

    csv = _param_type_csv(method)
    method_id = _codeframe_id("method", file_path_key, owner_fqn, name, f"({csv})")
    method_ref = EntityRef(kind=EntityKind.CODE_METHOD, id=method_id)

    parameters: List[str] = []
    for p in method.get("parameters") or ():
        if not isinstance(p, Mapping):
            continue
        p_name = p.get("name") or ""
        p_type = p.get("type")
        if p_type is not None:
            parameters.append(f"{p_name}: {p_type}" if p_name else str(p_type))
        elif p_name:
            parameters.append(p_name)

    signature = f"{name}({csv})"

    modifiers = _normalize_modifiers(method.get("modifiers"))
    vis_mod = _visibility_to_modifier(method.get("visibility"))
    if vis_mod and vis_mod not in modifiers:
        modifiers.append(vis_mod)

    is_constructor = bool(owner_simple_name and name == owner_simple_name)
    # CodeFrame's JS / TS / Python constructors don't share the type
    # simple-name. Honour the common ``"constructor"`` literal too.
    if not is_constructor and name in {"constructor", "__init__"} and owner_type_ref is not None:
        is_constructor = True

    called_method_refs: List[EntityRef] = []
    calls_total = 0
    calls_resolved = 0
    for call in method.get("methodCalls") or ():
        calls_total += 1
        target_ref = _resolve_call(call, file_path_key, fqn_index, simple_name_index)
        if target_ref is None:
            continue
        calls_resolved += 1
        called_method_refs.append(target_ref)
        weight_raw = call.get("callCount") if isinstance(call, Mapping) else None
        try:
            weight = int(weight_raw) if weight_raw is not None else 1
        except (TypeError, ValueError):
            weight = 1
        out_refs.append(
            CodeReference(
                id=_ref_id(ref_seq),
                project_ref=project_ref,
                reference_kind="call",
                source_method_ref=method_ref,
                target_method_ref=target_ref,
                weight=max(1, weight),
            )
        )
        ref_seq += 1

    method_entity = CodeMethod(
        id=method_id,
        project_ref=project_ref,
        name=name,
        type_ref=owner_type_ref,
        file_ref=file_ref,
        signature=signature,
        return_type=method.get("returnType") if isinstance(method.get("returnType"), str) else None,
        parameters=parameters,
        modifiers=modifiers,
        line_start=None,
        line_end=None,
        cyclomatic_complexity=0,
        is_constructor=is_constructor,
        called_method_refs=called_method_refs,
    )
    return method_entity, calls_total, calls_resolved, ref_seq


__all__ = ["build_code_structure_bundle"]
