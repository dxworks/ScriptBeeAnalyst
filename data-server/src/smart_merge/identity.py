"""Smart-merge identity DTOs.

Per Phase-2 decision D5 (architectural_change_followup.md §1) the
smart-merge engine keeps :class:`SourceIdentity` as an **internal DTO**.
The boundary contract:

* INPUT  — a typed :class:`~src.common.kernel.Graph` (the v2 graph root)
* OUTPUT — :class:`UnifiedUser` records persisted into Supabase tables
  (``unified_users`` / ``user_identity_mappings`` / ``rejected_similarities``)

Both classes lived under :mod:`src.common.unified_author` during Phase 1
(when the smart-merge endpoints still consumed the legacy
``graph_data: dict`` global). Chunk 19 of Phase 2 moves them here so the
``src.common.*`` surface no longer carries smart-merge-specific shapes
and the v1 dict-of-projects can be deleted.

Both types are intentionally domain-agnostic — they don't import any
``src.common.domains.*`` model. :class:`UnifiedUser` carries the live
:class:`~src.common.kernel.Graph` reference only to back the
``commit_count`` / ``issue_count`` / ``pr_count`` accessors the API
response shape needs. Identity extraction itself lives in
:mod:`src.smart_merge.identity_extractor`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional
from uuid import uuid4

if TYPE_CHECKING:  # forward-only; we never import the typed Graph at runtime
    from src.common.kernel import Graph


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


class UnifiedUser:
    """A merged identity aggregating accounts across sources.

    Created when a user accepts a smart-merge suggestion. The API shape
    (:meth:`to_dict`) is what the web UI's smart-merge view consumes.

    Per Phase-2 decision D5 this stays a smart-merge DTO — it is
    NOT the typed v2 :class:`src.common.people.UnifiedUser` Entity
    (which is the persisted graph-side representation). These two
    serve different purposes:

    * v2 Entity ``UnifiedUser`` — kernel entity, indexed by
      ``UnifiedUserRegistry``, persisted via :class:`PickleStore`. Wired
      into the typed graph for cross-source navigation.
    * smart-merge ``UnifiedUser`` (this class) — API serialisation
      shape with rich ``commit_count`` / ``issue_count`` / ``pr_count``
      accessors that resolve against a bound :class:`Graph`. Persisted
      to Supabase ``unified_users`` + ``user_identity_mappings`` via
      :class:`SupabaseSmartMergeRepository`.

    The two are linked through the source-side ``unified_user_id``
    back-pointer the smart-merge endpoints write onto the typed
    accounts when applying a merge.
    """

    def __init__(
        self,
        display_name: str,
        primary_email: Optional[str] = None,
        identities: Optional[List[SourceIdentity]] = None,
        id: Optional[str] = None,
    ):
        self.id = id or str(uuid4())
        self.display_name = display_name
        self.primary_email = primary_email
        self.identities: List[SourceIdentity] = identities or []
        self._graph: Optional["Graph"] = None

    # ------------------------------------------------------------------
    # Graph binding
    # ------------------------------------------------------------------
    def bind_graph(self, graph: Optional["Graph"]) -> None:
        """Bind to a typed v2 :class:`Graph` so accessors can resolve
        live references. Pass ``None`` to clear the binding."""
        self._graph = graph

    # ------------------------------------------------------------------
    # Per-source identity filters
    # ------------------------------------------------------------------
    @property
    def git_identities(self) -> List[SourceIdentity]:
        return [i for i in self.identities if i.source == "git"]

    @property
    def github_identities(self) -> List[SourceIdentity]:
        return [i for i in self.identities if i.source == "github"]

    @property
    def jira_identities(self) -> List[SourceIdentity]:
        return [i for i in self.identities if i.source == "jira"]

    # ------------------------------------------------------------------
    # Live entity resolution against the bound typed Graph
    # ------------------------------------------------------------------
    @property
    def git_accounts(self) -> list:
        """Resolve live :class:`GitAccount` references from the bound graph.

        Empty list if no graph is bound. In v2 the account id IS the
        legacy ``"name <email>"`` composite (see
        :meth:`GitAccount.make_id`), which matches the ``source_key``
        emitted by :mod:`identity_extractor`.
        """
        if self._graph is None:
            return []
        accounts = []
        for identity in self.git_identities:
            account = self._graph.git_accounts.get(identity.source_key)
            if account is not None:
                accounts.append(account)
        return accounts

    @property
    def github_users(self) -> list:
        """Resolve live :class:`GitHubUser` references from the bound graph."""
        if self._graph is None:
            return []
        users = []
        for identity in self.github_identities:
            user = self._graph.github_users.get(identity.source_key)
            if user is not None:
                users.append(user)
        return users

    @property
    def jira_users(self) -> list:
        """Resolve live :class:`JiraUser` references from the bound graph."""
        if self._graph is None:
            return []
        users = []
        for identity in self.jira_identities:
            user = self._graph.jira_users.get(identity.source_key)
            if user is not None:
                users.append(user)
        return users

    # ------------------------------------------------------------------
    # Aggregates over all identities
    # ------------------------------------------------------------------
    @property
    def all_emails(self) -> List[str]:
        return list({i.email for i in self.identities if i.email})

    @property
    def all_names(self) -> List[str]:
        return list({i.name for i in self.identities})

    @property
    def all_logins(self) -> List[str]:
        return list({i.login for i in self.identities if i.login})

    # ------------------------------------------------------------------
    # Activity counts (resolve through the bound typed Graph)
    # ------------------------------------------------------------------
    @property
    def commit_count(self) -> int:
        """Total commits authored across all linked GitAccounts.

        Reads :pyattr:`Graph.commits.by_author` keyed on each
        account's :class:`EntityRef`. Empty graph -> 0.
        """
        if self._graph is None:
            return 0
        total = 0
        for account in self.git_accounts:
            total += len(self._graph.commits.by_author[account.ref()])
        return total

    @property
    def issue_count(self) -> int:
        """Unique issues touched (reported / created / assigned) across
        all linked JiraUsers. De-duplicates within a single user (the
        same issue can be on multiple lists)."""
        if self._graph is None:
            return 0
        count = 0
        issues_reg = self._graph.issues
        for ju in self.jira_users:
            user_ref = ju.ref()
            seen: set[str] = set()
            for index_name in ("by_reporter", "by_creator", "by_assignee"):
                bucket = getattr(issues_reg, index_name)[user_ref]
                for issue in bucket:
                    if issue.key not in seen:
                        seen.add(issue.key)
                        count += 1
        return count

    @property
    def pr_count(self) -> int:
        """Unique PRs touched (created / merged_by) across linked
        :class:`GitHubUser` accounts."""
        if self._graph is None:
            return 0
        count = 0
        pr_reg = self._graph.pull_requests
        for gu in self.github_users:
            user_ref = gu.ref()
            seen: set[int] = set()
            for index_name in ("by_author",):
                bucket = getattr(pr_reg, index_name)[user_ref]
                for pr in bucket:
                    if pr.number not in seen:
                        seen.add(pr.number)
                        count += 1
            # ``by_merged_by`` isn't a declared index on the v2
            # PullRequestRegistry (per Chunk 6 / src/common/domains/github/
            # registries.py — only ``by_author`` / ``by_state`` /
            # ``by_number`` are declared). Fall back to a scan keyed on
            # the user's ref for merged_by_ref.
            for pr in pr_reg.all():
                if pr.merged_by_ref == user_ref and pr.number not in seen:
                    seen.add(pr.number)
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict for API responses."""
        return {
            "id": self.id,
            "display_name": self.display_name,
            "primary_email": self.primary_email,
            "identities": [
                {
                    "source": i.source,
                    "source_key": i.source_key,
                    "name": i.name,
                    "email": i.email,
                    "login": i.login,
                }
                for i in self.identities
            ],
            "stats": {
                "commit_count": self.commit_count,
                "issue_count": self.issue_count,
                "pr_count": self.pr_count,
            },
        }

    def __repr__(self) -> str:
        return (
            f"UnifiedUser(id={self.id!r}, name={self.display_name!r}, "
            f"identities={len(self.identities)})"
        )


__all__ = ["SourceIdentity", "UnifiedUser"]
