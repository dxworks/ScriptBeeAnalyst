"""Role-typed account-ref markers and registry.

This module is part of the UnifiedUsers redesign (task P1.B; see
``unified_users_change.md`` §A).

Domain entities today carry references to per-source accounts
(``GitAccount`` / ``JiraUser`` / ``GitHubUser``) under role-named ref
fields — ``Commit.author_ref``, ``PullRequest.merged_by_ref``,
``Issue.reporter_ref``, etc. After the project's lifecycle transitions
to ``FINALIZED``, every such ref is rewritten to point at the matching
``UnifiedUser`` and the reverse-resolvers
(``uu.commits_as_author(g)`` / ``uu.pull_requests_as_merged_by(g)`` /
``uu.issues_as_reporter(g)`` / ...) become available on
``UnifiedUser``.

Both passes — the rebind pass (§D) and the reverse-resolver installer
(§C) — need to enumerate the role-typed ref fields without per-domain
code. They read :data:`AccountRoleRegistry`.

Domain usage::

    from src.common.kernel import account_role_ref, account_role_refs

    class Commit(Entity):
        kind: ClassVar[EntityKind] = EntityKind.COMMIT
        author_ref: EntityRef = account_role_ref("author")
        committer_ref: EntityRef = account_role_ref("committer")
        parent_refs: list[EntityRef] = []   # not a role-ref — left as-is

    class PullRequest(Entity):
        kind: ClassVar[EntityKind] = EntityKind.PULL_REQUEST
        assignee_refs: list[EntityRef] = account_role_refs("assignee")

The markers produce ordinary Pydantic :class:`FieldInfo` objects whose
``json_schema_extra`` carries the ``_account_role`` payload. The
kernel's class-init pass (``_install_ref_resolvers`` in
``kernel/entity.py``) walks ``model_fields`` and registers every
marker it finds with :data:`AccountRoleRegistry`. Idempotent on
re-import: a re-registration with the same
``(owning_cls.__qualname__, field_name)`` key overwrites the entry
instead of duplicating it.

This phase (P1.B) only collects the metadata. Reverse resolvers and
the rebind pass that consume the registry land in later tasks.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import Field
from pydantic.fields import FieldInfo


_MARKER_KEY = "_account_role"


@dataclass(frozen=True)
class RoleRefSpec:
    """One role-typed account-ref field on a domain entity or value object.

    Attributes
    ----------
    owning_cls:
        The ``Entity`` subclass (or value-object :class:`BaseModel`)
        that declares the field. Stored as the class itself so the
        rebind pass can navigate to its registry through the graph
        (Entities) or to its parent Entity's list field (value objects;
        see :data:`_VALUE_OBJECT_PARENTS` in ``smart_merge/rebind.py``).
    field_name:
        The Pydantic field name on ``owning_cls`` (e.g. ``"author_ref"``
        or ``"assignee_refs"``).
    role:
        The role label used to derive the reverse-resolver name
        (``commits_as_<role>``). Free-form within a domain; conventionally
        ``"author"``, ``"committer"``, ``"reporter"``, ``"creator"``,
        ``"assignee"``, ``"merged_by"``, ``"requested_reviewer"``, ...
    plural:
        ``True`` for ``list[EntityRef]`` fields, ``False`` for singular
        ``EntityRef`` (or ``Optional[EntityRef]``). The rebind pass uses
        this to know whether to walk a list or replace a single value;
        the reverse-resolver installer doesn't care — both flavours
        produce ``uu.<entities>_as_<role>(g) -> list[Entity]``.
    optional:
        ``True`` for singular fields that may legitimately be ``None``
        at construction time (``Optional[EntityRef] = None`` semantics —
        e.g. ``PullRequest.merged_by_ref`` for an unmerged PR, or
        ``Review.author_ref`` for a ghost-user-deleted review). The
        rebind pass (P3.B) uses this to know whether to tolerate a
        ``None`` value when iterating field values, and the marker
        factory uses it to emit ``Field(default=None, ...)`` instead
        of ``Field(...)``. Meaningless for plural fields (lists always
        default to ``[]``); always ``False`` for plurals.
    value_object:
        ``True`` when ``owning_cls`` is a nested value-object
        :class:`BaseModel` (no ``kind`` ClassVar, no Graph registry
        slot), reached only via a parent Entity's list field. The
        rebind pass (P3.C) uses this to switch from registry-walking
        to parent-list traversal, and the reverse-resolver installer
        skips these specs (value-object refs are queryable via Issue
        traversal, not via ``UnifiedUser.<plural>_as_<role>``).
        Set by :func:`register_value_object_role_refs`; ``False`` for
        the Entity-side collector path (:func:`collect_role_refs`).
    """

    owning_cls: type
    field_name: str
    role: str
    plural: bool
    optional: bool = False
    value_object: bool = False


def account_role_ref(role: str, *, optional: bool = False) -> Any:
    """Mark a singular ``EntityRef`` field as a role-typed account ref.

    By default returns a Pydantic :class:`FieldInfo` with no default —
    the field is required, matching the existing ``Commit.author_ref``
    / ``Commit.committer_ref`` semantics where every commit has an
    author.

    Pass ``optional=True`` for ``Optional[EntityRef] = None`` fields
    (e.g. ``PullRequest.merged_by_ref`` for an unmerged PR or
    ``Review.author_ref`` for a ghost-user-deleted review). In that
    case the factory emits ``Field(default=None, ...)`` so existing
    constructor sites that omit the kwarg keep working. The
    ``optional`` flag is preserved on the registered
    :class:`RoleRefSpec` so the rebind pass (P3.B) can tolerate a
    ``None`` value when iterating field values.

    Use :func:`account_role_refs` for ``list[EntityRef]`` fields.
    """
    payload = {"role": role, "plural": False, "optional": optional}
    if optional:
        return Field(default=None, json_schema_extra={_MARKER_KEY: payload})
    return Field(..., json_schema_extra={_MARKER_KEY: payload})


def account_role_refs(role: str, *, optional: bool = False) -> Any:
    """Mark a ``list[EntityRef]`` field as a role-typed account-ref list.

    Default is an empty list (via ``default_factory``) so a field with
    no assignees deserializes cleanly.

    The ``optional`` keyword is accepted for signature symmetry with
    :func:`account_role_ref` but is meaningless for plural fields — a
    list field's natural "empty" representation is ``[]``, never
    ``None``. Passing ``optional=True`` is silently ignored (the
    registered :class:`RoleRefSpec` will carry ``optional=False`` for
    plurals).
    """
    del optional  # plural fields ignore the flag; see docstring
    payload = {"role": role, "plural": True, "optional": False}
    return Field(
        default_factory=list,
        json_schema_extra={_MARKER_KEY: payload},
    )


def _extract_role_metadata(finfo: FieldInfo) -> dict[str, Any] | None:
    """Return the ``_account_role`` payload on a field, or ``None``.

    Tolerant of Pydantic v2's ``json_schema_extra`` shapes: ``dict`` or
    callable. Callables (rare in this codebase, but supported by
    Pydantic) are NOT invoked — the marker convention is to store a
    plain dict, and a callable shape is treated as "not marked".
    """
    extra = finfo.json_schema_extra
    if not isinstance(extra, dict):
        return None
    payload = extra.get(_MARKER_KEY)
    if not isinstance(payload, dict):
        return None
    return payload


class AccountRoleRegistry:
    """Process-wide registry of role-typed account-ref fields.

    Populated at class-init time (see ``kernel/entity.py``'s
    ``__pydantic_init_subclass__``) for every ``Entity`` subclass that
    declares a field built with :func:`account_role_ref` or
    :func:`account_role_refs`.

    The registry is module-level (a class with class-attribute state)
    rather than an instance — there's exactly one set of role-typed
    fields in the process, parallel to ``EntityKind`` itself.
    """

    # Maps (owning_cls.__qualname__, field_name) -> RoleRefSpec. Keyed
    # by name rather than by class identity so a re-imported subclass
    # (e.g. test reload) overwrites its previous registration instead
    # of leaving a dangling spec pointing at the stale class object.
    _entries: dict[tuple[str, str], RoleRefSpec] = {}

    @classmethod
    def register(
        cls,
        *,
        owning_cls: type,
        field_name: str,
        role: str,
        plural: bool,
        optional: bool = False,
        value_object: bool = False,
    ) -> RoleRefSpec:
        """Register one role-typed field. Idempotent on re-registration.

        The key is ``(owning_cls.__qualname__, field_name)`` — same
        idempotency contract as ``_install_ref_resolvers`` in
        ``kernel/entity.py``, where re-importing a module silently
        replaces the previous generator install.

        ``value_object=True`` flags a nested-BaseModel owning_cls
        (e.g. ``IssueTransition`` / ``Comment``). See the field doc on
        :class:`RoleRefSpec` for the semantics.
        """
        spec = RoleRefSpec(
            owning_cls=owning_cls,
            field_name=field_name,
            role=role,
            plural=plural,
            optional=optional,
            value_object=value_object,
        )
        cls._entries[(owning_cls.__qualname__, field_name)] = spec
        return spec

    @classmethod
    def all(cls) -> list[RoleRefSpec]:
        """Return every registered spec, in insertion order."""
        return list(cls._entries.values())

    @classmethod
    def for_entity(cls, entity_cls: type) -> list[RoleRefSpec]:
        """Return every spec whose ``owning_cls`` is ``entity_cls``.

        Matched by class identity, not by qualname — callers passing
        a re-imported class get only entries pointing at the same
        object they hold.
        """
        return [
            spec
            for spec in cls._entries.values()
            if spec.owning_cls is entity_cls
        ]

    @classmethod
    def clear(cls) -> None:
        """Drop every registered spec. Intended for tests."""
        cls._entries.clear()


def collect_role_refs(entity_cls: type) -> list[RoleRefSpec]:
    """Walk ``entity_cls.model_fields`` and register any role-typed refs.

    Called from ``kernel/entity.py``'s ``__pydantic_init_subclass__``
    after :func:`_install_ref_resolvers`. Returns the specs registered
    for this class — useful for tests; production callers don't read
    the return value.
    """
    model_fields = getattr(entity_cls, "model_fields", None)
    if not model_fields:
        return []
    registered: list[RoleRefSpec] = []
    for fname, finfo in model_fields.items():
        payload = _extract_role_metadata(finfo)
        if payload is None:
            continue
        spec = AccountRoleRegistry.register(
            owning_cls=entity_cls,
            field_name=fname,
            role=payload["role"],
            plural=bool(payload.get("plural", False)),
            optional=bool(payload.get("optional", False)),
        )
        registered.append(spec)
    return registered


def register_value_object_role_refs(model_cls: type) -> list[RoleRefSpec]:
    """Explicit registration helper for nested value-object ``BaseModel``s.

    The kernel's automatic ``__pydantic_init_subclass__`` hook (see
    ``kernel/entity.py``) fires only on :class:`Entity` subclasses, so
    role-typed refs declared on a nested value object (a plain
    :class:`BaseModel` reached via a parent Entity's list field — e.g.
    ``IssueTransition`` inside ``Issue.transitions``, ``Comment`` inside
    ``Issue.comments``) are NOT picked up automatically. Domain modules
    that mark such fields must call this helper at module-import time
    once per value-object class. Example::

        from src.common.kernel.role_ref import (
            account_role_ref,
            register_value_object_role_refs,
        )

        class Comment(BaseModel):
            author_ref: Optional[EntityRef] = account_role_ref(
                "author", optional=True
            )

        register_value_object_role_refs(Comment)

    Specs registered through this helper carry ``value_object=True``;
    the rebind pass switches to parent-list traversal for them and the
    :class:`UnifiedUser` reverse-resolver installer skips them entirely
    (value-object refs are queryable via parent-Entity traversal, not
    via a ``UnifiedUser.<plural>_as_<role>`` reverse method).

    Idempotent on re-import. Returns the specs registered for this
    class — useful for tests; production callers don't read it.
    """
    model_fields = getattr(model_cls, "model_fields", None)
    if not model_fields:
        return []
    registered: list[RoleRefSpec] = []
    for fname, finfo in model_fields.items():
        payload = _extract_role_metadata(finfo)
        if payload is None:
            continue
        spec = AccountRoleRegistry.register(
            owning_cls=model_cls,
            field_name=fname,
            role=payload["role"],
            plural=bool(payload.get("plural", False)),
            optional=bool(payload.get("optional", False)),
            value_object=True,
        )
        registered.append(spec)
    return registered


__all__ = [
    "AccountRoleRegistry",
    "RoleRefSpec",
    "account_role_ref",
    "account_role_refs",
    "collect_role_refs",
    "register_value_object_role_refs",
]
