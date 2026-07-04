"""Registries for every duplication-domain :class:`Entity` subclass.

Per plan §1.5, indexes are declared as a ``ClassVar[list[IndexSpec]]`` and
rebuilt on every mutation / on :meth:`Registry.load` — they are NOT pickled.
"""
from __future__ import annotations

from ...kernel import IndexSpec, Registry
from .models import DuplicationPair, DuplicationProject


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class DuplicationProjectRegistry(Registry[DuplicationProject, str]):
    """Holds every :class:`DuplicationProject` in the graph."""

    indexes = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name, multi=True),
    ]

    def get_id(self, entity: DuplicationProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Duplication pairs
# ---------------------------------------------------------------------------


def _pair_file_fanout(p: DuplicationPair):
    """Fan-out key: a pair touches BOTH ``file_a_ref`` AND ``file_b_ref``.

    Returning a list makes the kernel's multi-key index fan the row out
    so a single lookup ``by_file[ref]`` returns every pair touching that
    file regardless of which side it sat on.
    """
    if p.file_a_ref == p.file_b_ref:
        # Internal duplications (self-pair) only fan out once — otherwise
        # the row would appear twice in the same bucket.
        return [p.file_a_ref]
    return [p.file_a_ref, p.file_b_ref]


class DuplicationPairRegistry(Registry[DuplicationPair, str]):
    """Every :class:`DuplicationPair` in the graph.

    Indexes:

    * ``by_file_a``  — fast "duplications where file F is listed as the
                       'a' side" lookup. Useful only when the relation
                       builder cares about the canonical ordering.
    * ``by_file_b``  — same, for the 'b' side.
    * ``by_file``    — *combined* fan-out: "all duplications touching
                       file F regardless of which side it sat on". This
                       is what the brief explicitly calls out: most
                       consumers don't care about the canonical side, so
                       a single lookup wins.
    * ``by_project`` — one bucket per :class:`DuplicationProject`.
    * ``by_kind``    — group by ``DuplicationKind`` (external / sibling
                       / internal).
    """

    indexes = [
        IndexSpec(name="by_file_a", key_fn=lambda p: p.file_a_ref, multi=True),
        IndexSpec(name="by_file_b", key_fn=lambda p: p.file_b_ref, multi=True),
        IndexSpec(name="by_file", key_fn=_pair_file_fanout, multi=True),
        IndexSpec(name="by_project", key_fn=lambda p: p.project_ref, multi=True),
        IndexSpec(
            name="by_kind", key_fn=lambda p: p.duplication_kind, multi=True
        ),
    ]

    def get_id(self, entity: DuplicationPair) -> str:
        return entity.id


__all__ = [
    "DuplicationPairRegistry",
    "DuplicationProjectRegistry",
]
