"""Insider code-smells JSON → quality-domain entity bundle.

This bridge reads a raw Insider ``*-code_smells.json`` file from disk and
produces the entity-bundle Mapping that :class:`QualityTransformer` accepts
on its already-built-bundle path::

    {
        "project":        QualityProject(..., source_tool="insider"),
        "quality_issues": [QualityIssue, ...],
    }

The legacy reader (``src/quality_miner/``) is being retired per Chunk 10's
cleanup brief. The v2 build path consumes pre-assembled entity bundles via
:func:`src.processor.build_graph_from_bundles`, so this module is the one
place that knows the Insider on-disk shape.

Insider's code-smells file is an array of plain objects with the keys:

* ``name``     — the rule identifier with spaces, e.g. ``"Stub Implementer"``.
* ``category`` — rule family / bucket, e.g. ``"Inheritance"`` /
                 ``"Traceability"``.
* ``file``     — path of the offending file (the project-relative path that
                 the git-domain :class:`File.id` uses; we forward it raw as
                 :class:`EntityRef.id`).
* ``value``    — occurrence count for that ``(rule, file)`` pair.

Insider emits no severity, no message, no line numbers, and no language —
those :class:`QualityIssue` fields stay ``None`` and the by_severity /
by_language indexes simply skip the row (per kernel normalize_keys
semantics).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Mapping

from ...kernel import EntityKind, EntityRef
from ...people import SourceKind
from ..git.models import File
from .models import QualityIssue, QualityProject


# Top-level Insider rows we know how to map. Defensive: extra keys are
# ignored (Insider has historically added derived fields between releases),
# missing keys raise a clear error rather than silently producing nulls.
_REQUIRED_ROW_KEYS = ("name", "category", "file")


def _coerce_rows(payload: Any, source: Path) -> Iterable[Mapping[str, Any]]:
    """Validate the top-level JSON shape and yield row mappings.

    Insider's file is *always* a JSON array at the top level. We accept a
    nested ``{"issues": [...]}`` envelope too — defensive against
    downstream wrappers occasionally seen in tests — but otherwise reject
    anything that isn't a list of dicts.
    """
    if isinstance(payload, Mapping):
        for key in ("issues", "smells", "code_smells", "data"):
            inner = payload.get(key)
            if isinstance(inner, list):
                payload = inner
                break
        else:
            raise ValueError(
                f"Insider code-smells file {source} is a dict but has no "
                f"recognized array key (issues/smells/code_smells/data)."
            )
    if not isinstance(payload, list):
        raise ValueError(
            f"Insider code-smells file {source} must contain a top-level "
            f"JSON array; got {type(payload).__name__}."
        )
    return payload


def _issue_id(project_id: str, file_path: str, rule_id: str, idx: int) -> str:
    """Synthetic stable id per (project, file, rule, idx).

    Mirrors the convention noted in :class:`QualityIssue` docstring. ``idx``
    is the row position in the source JSON, which gives us stable ids even
    when the same ``(file, rule)`` pair appears more than once (legacy
    Insider has been known to do that across runs).
    """
    return f"{project_id}::{file_path}::{rule_id}::{idx}"


def build_quality_bundle(
    file_path: Path, repo_name: str, project_name: str = "Project"
) -> Mapping[str, Any]:
    """Read an Insider code-smells JSON and build the v2 entity bundle.

    Parameters
    ----------
    file_path
        Filesystem path to the Insider ``*-code_smells.json`` artifact.
    repo_name
        Repository identifier — used as the :class:`QualityProject.id` so
        the project's stable id is independent of any mutable display
        name. Mirrors the per-source-project id convention used by the
        git / jira / github bridges.
    project_name
        Human-facing project name. Defaults to ``"Project"`` to match the
        sibling bridge signatures.

    Returns
    -------
    Mapping with ``project`` + ``quality_issues`` keys, ready to hand to
    :meth:`QualityTransformer.transform`.
    """
    file_path = Path(file_path)
    with file_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    project = QualityProject(
        id=repo_name,
        name=project_name,
        source=SourceKind.QUALITY,
        source_tool="insider",
    )
    project_ref = project.ref()

    issues: List[QualityIssue] = []
    for idx, row in enumerate(_coerce_rows(payload, file_path)):
        if not isinstance(row, Mapping):
            raise ValueError(
                f"Insider row #{idx} in {file_path} is not an object: "
                f"got {type(row).__name__}."
            )
        missing = [k for k in _REQUIRED_ROW_KEYS if k not in row]
        if missing:
            raise ValueError(
                f"Insider row #{idx} in {file_path} missing required "
                f"keys: {missing}. Row keys: {list(row.keys())}."
            )

        rule_id = str(row["name"])
        category = str(row["category"])
        raw_file = str(row["file"])

        # Insider's "value" is the occurrence count for the (rule, file)
        # pair. Default to 1 to keep parity with the entity contract when
        # the field is absent. Coerce defensively — Insider has emitted
        # the count as a string in past releases.
        raw_value = row.get("value", 1)
        try:
            occurrence_count = int(raw_value)
        except (TypeError, ValueError):
            occurrence_count = 1

        issues.append(
            QualityIssue(
                id=_issue_id(project.id, raw_file, rule_id, idx),
                project_ref=project_ref,
                file_ref=EntityRef(
                    kind=EntityKind.FILE, id=File.make_id(repo_name, raw_file)
                ),
                rule_id=rule_id,
                category=category,
                source_tool="insider",
                occurrence_count=occurrence_count,
                # Insider emits no severity / message / line range /
                # language; leave these None so the typed indexes
                # (by_severity etc.) auto-skip the row.
                severity=None,
                message=None,
                line_start=None,
                line_end=None,
                language=None,
            )
        )

    return {"project": project, "quality_issues": issues}


__all__ = ["build_quality_bundle"]
