"""Bridge from raw DuDe artifacts to a :class:`DuplicationTransformer` bundle.

The legacy DuDe miner produced two files per repo:

* an **external** duplication CSV — one row per pair of *distinct files*
  that share at least one duplicated block, columns::

      file_a_path , file_b_path , total_block_length

  (No header. ``total_block_length`` is the sum of duplicated-block line
  counts across all blocks shared by the two files. DuDe aggregates
  multiple blocks per file-pair into one row.)

* an **internal** duplication JSON — one object per *single file* that
  contains a self-duplication, with the legacy "Code Pulsar" metric
  shape::

      [
        {"file": "...", "name": "Internal File Duplication",
         "category": "Duplication", "value": <line_count>},
        ...
      ]

This module is the *reader* layer (no equivalent in v2 yet) and the
*bridge* that hands a Mapping to :meth:`DuplicationTransformer.transform`.
Down-stream consumer:

    bundle = build_duplication_bundle(ext_csv, int_json, repo, "ZEPPELIN")
    result = DuplicationTransformer().transform(bundle)

Pair construction rules:

* **External CSV row**       → :class:`DuplicationPair` with distinct
  ``file_a_ref`` / ``file_b_ref``. ``duplication_kind`` is
  :data:`DuplicationKind.SIBLING` when the two files share the same
  immediate parent directory, else :data:`DuplicationKind.EXTERNAL`
  (matches the convention used by ``duplication_external`` /
  ``duplication_sibling`` relation builders, which split on the same
  directory test).
* **Internal JSON entry**    → self-pair :class:`DuplicationPair` with
  ``file_a_ref == file_b_ref``, ``duplication_kind ==
  DuplicationKind.INTERNAL`` (per the module-bottom decision note in
  ``models.py``: "if needed, can be represented as a self-pair").
"""
from __future__ import annotations

import csv
import json
import posixpath
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Optional

from ...kernel import EntityKind, EntityRef
from ...people import SourceKind
from .models import DuplicationKind, DuplicationPair, DuplicationProject


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _classify_external(file_a_path: str, file_b_path: str) -> DuplicationKind:
    """Sibling vs external split — same directory => sibling.

    Matches the directory test used by
    :class:`DuplicationExternalBuilder` /
    :class:`DuplicationSiblingBuilder` so the relation builders that
    consume this domain see consistent labels.
    """
    if posixpath.dirname(file_a_path) == posixpath.dirname(file_b_path):
        return DuplicationKind.SIBLING
    return DuplicationKind.EXTERNAL


def _parse_external_csv(
    csv_path: Path,
    project_ref: EntityRef,
) -> List[DuplicationPair]:
    """Parse a DuDe external-duplication CSV into :class:`DuplicationPair`.

    The CSV has no header — three columns: ``file_a_path``,
    ``file_b_path``, ``total_block_length`` (an int, the v2
    ``token_count`` rename). Blank rows are skipped silently. Rows that
    fail to parse a numeric block-length are skipped with the row left
    out of the bundle.
    """
    pairs: List[DuplicationPair] = []
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        for row in reader:
            if not row or len(row) < 3:
                continue
            file_a_path = row[0].strip()
            file_b_path = row[1].strip()
            if not file_a_path or not file_b_path:
                continue
            try:
                token_count = int(row[2].strip())
            except (ValueError, IndexError):
                continue

            file_a_ref = EntityRef(kind=EntityKind.FILE, id=file_a_path)
            file_b_ref = EntityRef(kind=EntityKind.FILE, id=file_b_path)
            # Canonicalise the pair so (a, b) and (b, a) collapse.
            pair_id = DuplicationPair.make_id(file_a_ref.id, file_b_ref.id)
            # After canonicalisation, line up the refs to match the id
            # ordering. Keeps the registry's ``by_file_a`` / ``by_file_b``
            # indexes deterministic.
            a_id, b_id = sorted((file_a_ref.id, file_b_ref.id))
            canonical_a = EntityRef(kind=EntityKind.FILE, id=a_id)
            canonical_b = EntityRef(kind=EntityKind.FILE, id=b_id)

            pairs.append(
                DuplicationPair(
                    id=pair_id,
                    project_ref=project_ref,
                    file_a_ref=canonical_a,
                    file_b_ref=canonical_b,
                    token_count=token_count,
                    block_count=1,
                    duplication_kind=_classify_external(a_id, b_id),
                )
            )
    return pairs


def _parse_internal_json(
    json_path: Path,
    project_ref: EntityRef,
) -> List[DuplicationPair]:
    """Parse a DuDe internal-duplication JSON into self-pair entities.

    Each top-level object has ``{"file", "name", "category", "value"}``.
    ``value`` is the duplicated line-count inside that single file. We
    emit it as a self-pair :class:`DuplicationPair`
    (``file_a_ref == file_b_ref``) tagged
    :data:`DuplicationKind.INTERNAL`, per the model docstring.
    """
    with json_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Iterable):
        return []

    pairs: List[DuplicationPair] = []
    for entry in payload:
        if not isinstance(entry, Mapping):
            continue
        file_path = entry.get("file")
        value = entry.get("value")
        if not isinstance(file_path, str) or not file_path:
            continue
        try:
            token_count = int(value)
        except (TypeError, ValueError):
            continue

        file_ref = EntityRef(kind=EntityKind.FILE, id=file_path)
        pair_id = DuplicationPair.make_id(file_ref.id, file_ref.id)
        pairs.append(
            DuplicationPair(
                id=pair_id,
                project_ref=project_ref,
                file_a_ref=file_ref,
                file_b_ref=file_ref,
                token_count=token_count,
                block_count=1,
                duplication_kind=DuplicationKind.INTERNAL,
            )
        )
    return pairs


# ---------------------------------------------------------------------------
# Public bridge
# ---------------------------------------------------------------------------


def build_duplication_bundle(
    external_csv: Optional[Path],
    internal_json: Optional[Path],
    repo_name: str,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Build a transformer-ready bundle from DuDe artifacts.

    Either input may be ``None`` (the orchestrator skips missing files);
    if both are ``None`` we raise :class:`ValueError` because the
    resulting bundle would be empty and almost certainly indicates a
    caller bug.

    Parameters
    ----------
    external_csv
        Optional path to the DuDe external-duplication CSV.
    internal_json
        Optional path to the DuDe internal-duplication JSON.
    repo_name
        Repo identifier — used as the :class:`DuplicationProject` id so
        the project row is stable across re-builds and addressable from
        relation builders.
    project_name
        Human-facing project name; defaults to ``"Project"``.

    Returns
    -------
    Mapping
        ``{"project": DuplicationProject, "duplication_pairs": [...]}``
        — exactly the entity-bundle shape
        :meth:`DuplicationTransformer.transform` consumes.
    """
    if external_csv is None and internal_json is None:
        raise ValueError(
            "build_duplication_bundle requires at least one of "
            "external_csv or internal_json; both were None."
        )

    project = DuplicationProject(
        id=repo_name,
        name=project_name,
        source=SourceKind.DUPLICATION,
    )
    project_ref = project.ref()

    pairs: List[DuplicationPair] = []
    if external_csv is not None:
        pairs.extend(_parse_external_csv(external_csv, project_ref))
    if internal_json is not None:
        pairs.extend(_parse_internal_json(internal_json, project_ref))

    return {
        "project": project,
        "duplication_pairs": pairs,
    }


__all__ = ["build_duplication_bundle"]
