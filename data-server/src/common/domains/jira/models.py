"""Jira-domain entities for the v2 graph.

Faithful port of ``src/common/jira_models.py`` (legacy). Every cross-entity
reference uses :class:`EntityRef`, never a Python object reference — per
plan §4 (and the Chunk 4 git domain that established the pattern).

Entity-vs-value-object decisions:

* :class:`Issue`, :class:`IssueStatus`, :class:`IssueType`,
  :class:`JiraUser`, :class:`JiraProject` are all real :class:`Entity`
  subclasses (plan §1.1 + §4.1).
* :class:`IssueTransition` and :class:`TransitionItem` are **value objects**
  nested inside :class:`Issue.transitions`. The plan's closed
  :class:`EntityKind` enum reserves ``CHANGE`` for git's file-level change;
  there's no ``ISSUE_TRANSITION`` member. Promoting transitions to entities
  would require a kernel-touching enum extension; option (b) from the
  Chunk 5 brief was selected because the legacy code never queries
  transitions across issues — every consumer reads them as a stream tied
  to an issue's life. See handoff "IssueTransition decision".
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, ClassVar, List, Optional

from pydantic import BaseModel, ConfigDict

from ...kernel import Entity, EntityKind, EntityRef
from ...people import Account, SourceKind
from ...projects import Project

if TYPE_CHECKING:  # forward-only — keeps cycles broken
    from .transformer import JiraTransformer  # noqa: F401


# ---------------------------------------------------------------------------
# Value objects (NOT entities — nested inside `Issue.transitions`)
# ---------------------------------------------------------------------------


class TransitionItem(BaseModel):
    """A single field-level diff inside an :class:`IssueTransition`.

    Faithful port of legacy ``ChangeItem``. Frozen value object — no
    cross-issue identity is needed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    from_value: Optional[str] = None
    from_string: Optional[str] = None
    to_value: Optional[str] = None
    to_string: Optional[str] = None


class IssueTransition(BaseModel):
    """A single transition entry in an :class:`Issue`'s history.

    Faithful port of legacy ``jira_models.Change``. Renamed to
    ``IssueTransition`` because the kernel's :class:`EntityKind.CHANGE`
    member is already used by git's file-level :class:`git.Change`. The
    plan §4.1 explicitly directs this name (``jira.IssueTransition``).

    Value object — :class:`EntityKind` has no ``ISSUE_TRANSITION``
    member; promoting it would require a kernel enum edit. See module
    docstring + handoff for the decision rationale.

    The legacy ``user: Optional[JiraUser]`` is replaced by
    ``user_ref: Optional[EntityRef]`` to match the typed-ref discipline
    every cross-entity link follows in v2.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    created: datetime
    changed_fields: List[str] = []
    items: List[TransitionItem] = []
    user_ref: Optional[EntityRef] = None


class Comment(BaseModel):
    """A single comment on an :class:`Issue`.

    Value object — :class:`EntityKind` does not list ``COMMENT`` as a
    member, and no downstream consumer (Chunks 7/8 + the MCP sandbox)
    indexes comments cross-issue today. Nested inside
    :class:`Issue.comments`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    body: str
    created: datetime
    updated: datetime
    author_ref: Optional[EntityRef] = None
    updated_by_ref: Optional[EntityRef] = None


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class JiraProject(Project):
    """A single Jira project's metadata.

    Per plan §3 the Project is metadata only — entities live in flat
    registries on :class:`Graph`. The legacy ``JiraProject`` owned five
    registries (status-category / status / type / issue / user); that
    ownership moves to :class:`Graph` in Chunk 8.
    """

    kind: ClassVar[EntityKind] = EntityKind.PROJECT

    def transformer_class(self) -> type["JiraTransformer"]:  # type: ignore[override]
        # Lazy import keeps the project module free of the transformer's own
        # kernel/people dependencies (mirrors the Chunk 2 + Chunk 4 pattern).
        from .transformer import JiraTransformer

        return JiraTransformer


class JiraUser(Account):
    """A Jira user account.

    Field mapping vs legacy ``jira_models.JiraUser``:

    * ``id``              — was the legacy ``link`` field (used as registry
                            id). v2 uses the same value as the canonical
                            :class:`Entity.id` so cross-refs stay stable.
    * ``key``             — preserved (per-project user identifier from
                            Jira's API).
    * ``name``            — inherited from :class:`Account` base.
    * ``link``            — preserved (the user-facing URL).
    * ``project_ref``     — was implicit through ``JiraProject``'s
                            ``jira_user_registry`` ownership; now a typed
                            ref.
    * ``unified_user_id`` — inherited from :class:`Account`; replaces the
                            legacy ``Developer.accounts`` link.
    * ``issues_as_*``     — DROPPED. Reverse lookup via
                            :class:`IssueRegistry.by_assignee` /
                            ``by_reporter`` / ``by_creator``.
    """

    kind: ClassVar[EntityKind] = EntityKind.JIRA_USER
    # ``source`` is declared on :class:`Account` as an abstract property.
    # ClassVar override matches the Chunk 4 git-account pattern — cheap
    # access, no property dispatch on a per-class constant.
    source: ClassVar[SourceKind] = SourceKind.JIRA  # type: ignore[misc]

    key: str
    link: Optional[str] = None


class IssueStatus(Entity):
    """A status value an :class:`Issue` may take (e.g. ``Open`` / ``Done``).

    Field mapping vs legacy ``jira_models.IssueStatus``:

    * ``id``                  — unchanged (Jira's stable status id).
    * ``name``                — unchanged (display name).
    * ``category``            — was nested ``IssueStatus.statusCategory`` (a
                                whole entity in legacy). Flattened to the
                                category's key (``"new"`` / ``"indeterminate"``
                                / ``"done"``) — the only field anything ever
                                read off the legacy category entity.
    * ``project_ref``         — NEW: typed ref to the owning
                                :class:`JiraProject`.
    * ``issues`` back-pointer — DROPPED. Reverse lookup via
                                :class:`IssueRegistry.by_status`.

    The legacy ``IssueStatusCategory`` entity is collapsed into this
    field because the only fields anyone read off it were ``key``
    (the category id) and ``name``. We carry the key as ``category``
    here; if a metric ever needs the human-readable name we can either
    map keys → names statically or re-promote :class:`IssueStatusCategory`
    to an Entity in a future chunk (additive change).
    """

    kind: ClassVar[EntityKind] = EntityKind.ISSUE_STATUS

    project_ref: EntityRef
    name: str
    category: str


class IssueType(Entity):
    """A type an :class:`Issue` may have (e.g. ``Bug`` / ``Task``).

    Field mapping vs legacy ``jira_models.IssueType``:

    * ``id``                  — unchanged (Jira's stable type id).
    * ``name``                — unchanged.
    * ``description``         — unchanged.
    * ``is_sub_task``         — was ``isSubTask`` (snake-case'd; the legacy
                                camelCase was a thin Pydantic field alias of
                                the wire-format key, not user-facing).
    * ``project_ref``         — NEW: typed ref to the owning
                                :class:`JiraProject`.
    * ``issues`` back-pointer — DROPPED. Reverse lookup via
                                :class:`IssueRegistry.by_type`.
    """

    kind: ClassVar[EntityKind] = EntityKind.ISSUE_TYPE

    project_ref: EntityRef
    name: str
    description: str = ""
    is_sub_task: bool = False


class Issue(Entity):
    """A single Jira issue.

    Field mapping vs legacy ``jira_models.Issue``:

    * ``id``                          — was the legacy ``key`` field (used as
                                        registry id, e.g. ``"PROJ-123"``).
                                        v2 uses ``key`` as the canonical
                                        :class:`Entity.id` so cross-refs are
                                        stable across re-ingests.
    * ``key``                         — preserved (same value as ``id``,
                                        kept as an explicit field because
                                        consumers reach for ``issue.key``).
    * ``numeric_id``                  — was ``id: int`` (Jira's internal
                                        numeric id). Preserved as a separate
                                        field because some Jira APIs key on
                                        it; kept optional in case the
                                        ingest didn't carry it.
    * ``project_ref``                 — NEW: typed ref to
                                        :class:`JiraProject`.
    * ``summary``                     — unchanged.
    * ``description``                 — unchanged (kept optional — legacy
                                        DTO already allowed ``None``).
    * ``status_ref``                  — was ``issue_statuses: List[IssueStatus]``
                                        (legacy collapsed Jira's
                                        single-status-at-a-time API into a
                                        list). v2 carries the current
                                        status as a single typed ref; the
                                        full history is in ``transitions``.
    * ``type_ref``                    — was ``issue_types: List[IssueType]``
                                        (same legacy List flattening); v2
                                        carries the single current type.
    * ``creator_ref``                 — was ``creator: Optional[JiraUser]``.
    * ``reporter_ref``                — was ``reporter: Optional[JiraUser]``.
    * ``assignee_refs``               — was ``jira_users_as_assignee:
                                        List[JiraUser]`` (kept multi
                                        because Jira's API exposes
                                        multi-assignee in some configs).
    * ``parent_ref``                  — was ``parent: Optional[Issue]``.
    * ``children``                    — DROPPED. Reverse of ``parent_ref``
                                        via :class:`IssueRegistry.by_parent`.
    * ``comments``                    — preserved as value-object list (no
                                        kernel EntityKind for comments).
    * ``transitions``                 — was the legacy ``transitions``
                                        (promoted from raw DTO ``changes``).
                                        Stays a value-object list — see
                                        the ``IssueTransition`` decision
                                        in the module docstring + handoff.
    * ``git_commits`` /
      ``pull_requests``               — DROPPED. Cross-source links move
                                        to :class:`RelationRegistry`
                                        (Chunk 7).
    * ``priority`` / ``resolution`` /
      ``time_estimate`` /
      ``time_spent``                  — preserved as plain fields (legacy
                                        carried them on the DTO; v2 keeps
                                        them for the metrics layer).
    """

    kind: ClassVar[EntityKind] = EntityKind.ISSUE

    project_ref: EntityRef
    key: str
    summary: str
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    numeric_id: Optional[int] = None

    status_ref: EntityRef
    type_ref: EntityRef
    creator_ref: Optional[EntityRef] = None
    reporter_ref: Optional[EntityRef] = None
    assignee_refs: List[EntityRef] = []
    parent_ref: Optional[EntityRef] = None

    priority: Optional[str] = None
    resolution: Optional[str] = None
    resolution_date: Optional[datetime] = None
    time_estimate: Optional[int] = None
    time_spent: Optional[int] = None

    comments: List[Comment] = []
    transitions: List[IssueTransition] = []


__all__ = [
    "Comment",
    "Issue",
    "IssueStatus",
    "IssueTransition",
    "IssueType",
    "JiraProject",
    "JiraUser",
    "TransitionItem",
]
