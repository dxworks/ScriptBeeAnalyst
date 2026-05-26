"""App-Inspector tags JSON → app-inspector-domain entity bundle.

This bridge reads a raw App-Inspector ``*-chronos-tags.json`` file from
disk and produces the entity-bundle Mapping that
:class:`AppInspectorTransformer` accepts on its already-built-bundle path::

    {
        "project":  AppInspectorProject(..., source_tool="appinspector"),
        "app_tags": [AppTag, ...],
    }

The v2 build path consumes pre-assembled entity bundles via
:func:`src.processor.build_graph_from_bundles`, so this module is the one
place that knows the App-Inspector on-disk shape.

The on-disk JSON is a single object with a nested ``file.concerns`` list,
each entry shaped like::

    {
        "entity":   "<repo>/<repo-relative file path>",
        "tag":      "appinspector.<dotted.taxonomy>",
        "strength": <int>
    }

The ``entity`` value is repo-rooted: its first path segment is the repo
name (e.g. ``zeppelin/...``). Mirroring :class:`QualityBridge`'s
``repo_name`` handling, we strip a leading ``<repo_name>/`` from the path
when present so the stored ``file_path`` is repo-relative — that matches
the convention used everywhere else in the graph (``File.path``,
:meth:`File.make_id`, …).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, List, Mapping

from ...kernel import EntityKind, EntityRef
from ...people.source import SourceKind
from ..git.models import File
from .models import AppInspectorProject, AppTag


# Required keys on every concern row. Defensive: extra keys are ignored
# (App Inspector / Chronos has historically added derived fields between
# releases), missing keys raise a clear error rather than silently
# producing nulls.
_REQUIRED_ROW_KEYS = ("entity", "tag", "strength")


def _coerce_rows(payload: Any, source: Path) -> Iterable[Mapping[str, Any]]:
    """Validate the top-level JSON shape and yield row mappings.

    App-Inspector / Chronos's file is *always* a JSON object with a
    nested ``file.concerns`` array. We reject anything that isn't shaped
    that way so a malformed file fails loudly instead of silently
    producing an empty bundle.
    """
    if not isinstance(payload, Mapping):
        raise ValueError(
            f"App-Inspector tags file {source} must contain a top-level "
            f"JSON object; got {type(payload).__name__}."
        )
    file_section = payload.get("file")
    if not isinstance(file_section, Mapping):
        raise ValueError(
            f"App-Inspector tags file {source} is missing a top-level "
            f"'file' object; got {type(file_section).__name__}."
        )
    concerns = file_section.get("concerns")
    if not isinstance(concerns, list):
        raise ValueError(
            f"App-Inspector tags file {source} is missing a "
            f"'file.concerns' array; got {type(concerns).__name__}."
        )
    return concerns


def _strip_repo_prefix(raw_entity: str, repo_name: str) -> str:
    """Strip a leading ``<repo_name>/`` segment from a repo-rooted path.

    Mirrors :func:`build_quality_bundle`'s repo-name handling: the
    on-disk path is repo-rooted (its first segment is the repo name),
    but every other domain stores repo-relative paths, so we normalize
    here before constructing the :class:`File` reference.

    If the path does not start with ``<repo_name>/`` it is returned
    verbatim — the caller's downstream `File.make_id` will still work,
    we just won't have an anchored repo to strip.
    """
    prefix = f"{repo_name}/"
    if raw_entity.startswith(prefix):
        return raw_entity[len(prefix):]
    return raw_entity


def build_app_inspector_bundle(
    file_path: Path,
    repo_name: str,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Read an App-Inspector tags JSON and build the v2 entity bundle.

    Parameters
    ----------
    file_path
        Filesystem path to the App-Inspector ``*-chronos-tags.json``
        artifact.
    repo_name
        Repository identifier — used as the :class:`AppInspectorProject.id`
        so the project's stable id is independent of any mutable display
        name. Also used to strip the leading ``<repo_name>/`` segment
        from repo-rooted ``entity`` paths before building the file
        reference, so the resulting :class:`AppTag.file_path` is
        repo-relative (matching every other domain's convention).
    project_name
        Human-facing project name. Defaults to ``"Project"`` to match
        the sibling bridge signatures.

    Returns
    -------
    Mapping with ``project`` + ``app_tags`` keys, ready to hand to
    :meth:`AppInspectorTransformer.transform`. The ``_meta`` slot
    mirrors :func:`build_quality_bundle`'s shape — it carries an
    ``all_rows_self_repo`` flag set to ``True`` when every observed
    concern's ``entity`` was prefixed with ``<repo_name>/``.
    """
    file_path = Path(file_path)
    with file_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)

    project = AppInspectorProject(
        id=repo_name,
        name=project_name,
        source=SourceKind.APP_INSPECTOR,
        source_tool="appinspector",
    )
    project_ref = project.ref()

    tags: List[AppTag] = []
    any_row_seen = False
    all_rows_self_repo = True
    repo_prefix = f"{repo_name}/"
    for idx, row in enumerate(_coerce_rows(payload, file_path)):
        if not isinstance(row, Mapping):
            raise ValueError(
                f"App-Inspector row #{idx} in {file_path} is not an "
                f"object: got {type(row).__name__}."
            )
        missing = [k for k in _REQUIRED_ROW_KEYS if k not in row]
        if missing:
            raise ValueError(
                f"App-Inspector row #{idx} in {file_path} missing "
                f"required keys: {missing}. Row keys: {list(row.keys())}."
            )

        any_row_seen = True
        raw_entity = str(row["entity"])
        tag = str(row["tag"])

        # Chronos's "strength" is the per-(file, tag) magnitude. Coerce
        # defensively — if the field is non-numeric (string, null, …)
        # we default to 1 the same way the quality bridge does for its
        # ``value`` field. This keeps a single bad row from killing the
        # whole bundle.
        raw_strength = row["strength"]
        try:
            strength = int(raw_strength)
        except (TypeError, ValueError):
            strength = 1

        if raw_entity.startswith(repo_prefix):
            file_rel_path = raw_entity[len(repo_prefix):]
        else:
            file_rel_path = raw_entity
            all_rows_self_repo = False

        tags.append(
            AppTag(
                id=AppTag.make_id(project.id, file_rel_path, tag),
                project_ref=project_ref,
                file_ref=EntityRef(
                    kind=EntityKind.FILE,
                    id=File.make_id(repo_name, file_rel_path),
                ),
                file_path=file_rel_path,
                tag=tag,
                strength=strength,
            )
        )

    return {
        "project": project,
        "app_tags": tags,
        "_meta": {"all_rows_self_repo": bool(any_row_seen and all_rows_self_repo)},
    }


__all__ = ["build_app_inspector_bundle"]
