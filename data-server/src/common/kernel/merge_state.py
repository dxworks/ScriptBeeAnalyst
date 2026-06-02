"""Project lifecycle state for the UnifiedUsers redesign.

The Graph carries a ``merge_state`` field that distinguishes the two
explicit stages of a project's lifecycle:

* ``PRE_MERGE`` — setup stage. Role-typed refs (``Commit.author_ref``,
  ``PullRequest.merged_by_ref``, ``Issue.reporter_ref``, …) target the
  per-source account kinds. The user reviews author matches, exclusion
  rules, and enrichment thresholds.
* ``FINALIZED`` — query stage. Every role-typed ref has been rewritten
  to target a :class:`UnifiedUser`. The agent and any generated
  human-readable code reason about *one* kind of person.

The transition runs once via the finalize endpoint and is not
reversible (re-import is the documented recourse). See
``unified_users_change.md`` for the full design.
"""
from __future__ import annotations

from enum import StrEnum


class MergeState(StrEnum):
    """Lifecycle phase of a project's graph."""

    PRE_MERGE = "PRE_MERGE"
    FINALIZED = "FINALIZED"

    #: Transient stage persisted on the ``projects`` *row* (never on the
    #: in-memory :class:`Graph`) for the duration of a finalize run. The
    #: finalize endpoint writes it before the rebind and overwrites it with
    #: ``FINALIZED`` on success (or resets it to ``PRE_MERGE`` on failure).
    #: ``/projects/current`` surfaces it so a mid-finalize browser refresh
    #: keeps the setup tabs locked and the Analysis loading bar up instead of
    #: falling back to the editable PRE_MERGE setup.
    FINALIZING = "FINALIZING"


__all__ = ["MergeState"]
