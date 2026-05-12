"""Source identifiers for people and projects.

Each data source ingested by ScriptBee has a stable string id (``"git"``,
``"jira"``, ``"github"``, …) used both as the discriminator on
``Project.source`` and indirectly via ``Account`` subclasses. See §2 / §3 of
``architectural_changes.md``.

Defined as a ``StrEnum`` for consistency with :class:`EntityKind` (Chunk 1).
"""
from __future__ import annotations

from enum import StrEnum


class SourceKind(StrEnum):
    """Closed set of supported source kinds.

    Adding a new data source = adding a new member here AND a matching
    ``Project`` / entity / transformer triple under ``common/domains/``
    (see §9 of the plan).
    """

    GIT = "git"
    JIRA = "jira"
    GITHUB = "github"
    CODE_STRUCTURE = "code_structure"
    DUPLICATION = "duplication"
    QUALITY = "quality"
    LIZARD = "lizard"
    APP_INSPECTOR = "app_inspector"


__all__ = ["SourceKind"]
