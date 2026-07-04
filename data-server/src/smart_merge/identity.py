"""Smart-merge identity DTO (:class:`SourceIdentity`).

Per Phase-2 decision D5 (architectural_change_followup.md §1) the
smart-merge engine keeps :class:`SourceIdentity` as an **internal DTO**.
The boundary contract:

* INPUT  — a typed :class:`~src.common.kernel.Graph` (the v2 graph root)
* OUTPUT — :class:`~src.common.people.UnifiedUser` entities persisted
  into Supabase tables (``unified_users`` / ``user_identity_mappings`` /
  ``rejected_similarities``).

Chunk 19 of Phase 2 moved this module here from
:mod:`src.common.unified_author` so the ``src.common.*`` surface no
longer carries smart-merge-specific shapes.

UnifiedUsers redesign §L (P4.C) collapsed the former
``smart_merge.identity.UnifiedUser`` API-shape class into the canonical
graph entity :class:`src.common.people.UnifiedUser`. Only
:class:`SourceIdentity` remains here — it is intentionally domain-
agnostic and doesn't import any ``src.common.domains.*`` model.
Identity extraction lives in :mod:`src.smart_merge.identity_extractor`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SourceIdentity:
    """A single identity from one data source, normalized for cross-source
    similarity comparison.

    This is the adapter between the typed v2 graph (per-domain account
    registries — :pyattr:`Graph.git_accounts` / :pyattr:`Graph.jira_users`
    / :pyattr:`Graph.github_users`) and the smart-merge engine. Constructed
    by :func:`src.smart_merge.identity_extractor.extract_all_identities`.

    Field shapes match the legacy v1 ``SourceIdentity`` exactly so the
    similarity-engine internals (token blocking, edge weights, persisted
    rejected pairs in Supabase) remain wire-compatible.
    """

    source: str            # "git", "github", "jira"
    name: str              # display name (always present)
    email: Optional[str]   # email (git always has, github/jira may not)
    login: Optional[str]   # github login or jira key
    source_key: str        # unique key within source registry

    @property
    def key(self) -> str:
        """Globally unique key: ``"{source}:{source_key}"``.

        Used as the cluster-graph node id throughout the engine and
        as the partial-key for the persisted ``rejected_similarities``
        table (which stores ``source`` + ``source_key`` separately).
        """
        return f"{self.source}:{self.source_key}"

    @property
    def display_label(self) -> str:
        """Human-readable label for UI display."""
        parts = [self.name]
        if self.email:
            parts.append(f"<{self.email}>")
        if self.login:
            parts.append(f"@{self.login}")
        return " ".join(parts)


__all__ = ["SourceIdentity"]
