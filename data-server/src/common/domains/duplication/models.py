"""Duplication-domain entities for the v2 graph.

Faithful port of ``src/common/duplication_models.py`` (legacy DuDe miner).
Every cross-entity reference uses :class:`EntityRef`, never a Python object
reference — per plan §4 (and the Chunk 4/5 pattern).

Entity-vs-value-object decisions (plan §1.1):

* :class:`DuplicationProject`, :class:`DuplicationPair` are real
  :class:`Entity` subclasses (``EntityKind.DUPLICATION_PAIR`` is already in
  the kernel enum set).
* The legacy ``DuplicationInternal`` model (per-file internal duplication
  scalar) is **not** a separate Entity in v2 — see module-bottom decision
  notes. Per-file internal duplication is a :class:`FileMetric` (handled
  by the ``metrics_lizard`` / quality layers), or, if needed, can be
  represented as a self-pair :class:`DuplicationPair` with ``file_a_ref ==
  file_b_ref``. Documented in handoff.
"""
from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Optional

from ...kernel import Entity, EntityKind, EntityRef
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import DuplicationTransformer  # noqa: F401


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DuplicationKind(StrEnum):
    """DuDe duplication relationship kinds (legacy ``DuplicationKind`` port).

    Promoted to :class:`StrEnum` for consistency with the rest of the v2
    kernel (``EntityKind``, ``SourceKind``).
    """

    EXTERNAL = "external"  # files in different immediate parent directories
    SIBLING = "sibling"  # files in the same immediate parent directory
    INTERNAL = "internal"  # within a single file


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class DuplicationProject(Project):
    """A single duplication project's metadata.

    Per plan §3 the Project is metadata only — entities live in flat
    registries on :class:`Graph`. The legacy ``Duplication`` container
    owned ``external_pairs`` + ``internal_by_file``; that ownership moves
    to :class:`Graph` (Chunk 8) plus the metrics layer for the
    file-level scalar.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    def transformer_class(self) -> type["DuplicationTransformer"]:  # type: ignore[override]
        from .transformer import DuplicationTransformer

        return DuplicationTransformer


class DuplicationPair(Entity):
    """An aggregated duplication relationship between two files.

    Field mapping vs legacy ``duplication_models.DuplicationPair``:

    * ``id``                  — NEW: composite of the (canonicalised)
                                file paths, ``"{file_a}::{file_b}"``. The
                                legacy model had no explicit id and the
                                pair was the registry key in tuple form;
                                v2's :class:`Entity` requires a string id.
                                :meth:`DuplicationPair.make_id` performs
                                the canonicalisation (sorted file paths)
                                so ``(a, b)`` and ``(b, a)`` collapse onto
                                the same row, matching legacy semantics.
    * ``project_ref``         — NEW: typed ref to
                                :class:`DuplicationProject`.
    * ``file_a_ref`` /
      ``file_b_ref``          — were ``file_a_path`` / ``file_b_path``
                                (plain strings); v2 carries typed
                                :class:`EntityRef` values into
                                :class:`git.File`. Canonicalisation
                                (``file_a_ref.id < file_b_ref.id`` by
                                path id) is the transformer's
                                responsibility.
    * ``token_count``         — was ``total_block_length: int`` — renamed
                                because the plan §4 example uses
                                ``token_count``, and what DuDe actually
                                reports is the LINE-count summed across
                                blocks (it does not literally tokenise).
                                We preserve the semantic via the field
                                name plan §4 mandates; consumers reading
                                the legacy ``total_block_length`` rename
                                to ``token_count`` in v2. The Chunk-6
                                handoff documents this.
    * ``block_count``         — preserved (count of duplicated blocks).
    * ``duplication_kind``    — was ``kind: DuplicationKind``; renamed to
                                dodge the inherited :pyattr:`Entity.kind`
                                ClassVar. Default ``EXTERNAL``.
    * ``line_range_a`` /
      ``line_range_b``        — NEW (plan §4); typed (start, end) tuples
                                for the range inside each file. Optional
                                because the legacy DuDe aggregation
                                collapses multiple blocks into one row
                                and the per-row ranges are not always
                                meaningful at the aggregate level; v2
                                makes them available for tools that DO
                                emit them per-row (e.g. a future
                                Sonar-driven duplicate detector).
    * ``fingerprint``         — NEW (plan §4); optional content-hash
                                identifier. Legacy didn't carry one.
    * ``similarity_score``    — NEW (plan §4 — "if legacy has it"). DuDe
                                does NOT emit a similarity score (it's a
                                binary "duplicated or not" tool); kept
                                optional so future tools (CPD / Sonar
                                duplications) can populate it.

    Resolver methods (auto-generated, see ``kernel/entity.py``):
        ``.project(graph)`` -> ``DuplicationProject | None``
        ``.file_a(graph)``  -> ``File | None``
        ``.file_b(graph)``  -> ``File | None``
    """

    kind: ClassVar[EntityKind] = EntityKind.DUPLICATION_PAIR

    project_ref: EntityRef
    file_a_ref: EntityRef
    file_b_ref: EntityRef
    token_count: int
    block_count: int = 1
    duplication_kind: DuplicationKind = DuplicationKind.EXTERNAL
    line_range_a: Optional[tuple[int, int]] = None
    line_range_b: Optional[tuple[int, int]] = None
    fingerprint: Optional[str] = None
    similarity_score: Optional[float] = None

    @staticmethod
    def make_id(file_a_id: str, file_b_id: str) -> str:
        """Composite registry id with deterministic canonical ordering.

        DuDe's external pair table is symmetric. We sort the two file ids
        lexically before composing so ``(a, b)`` and ``(b, a)`` always
        produce the same registry row — matching the legacy
        canonicalisation note (``duplication_models.DuplicationPair``
        docstring).
        """
        a, b = sorted((file_a_id, file_b_id))
        return f"{a}::{b}"


__all__ = [
    "DuplicationKind",
    "DuplicationPair",
    "DuplicationProject",
]
