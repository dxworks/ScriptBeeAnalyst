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
    """One role-typed account-ref field on a domain entity.

    Attributes
    ----------
    owning_cls:
        The ``Entity`` subclass that declares the field. Stored as the
        class itself so the rebind pass can navigate to its registry
        through the graph.
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
    """

    owning_cls: type
    field_name: str
    role: str
    plural: bool


def account_role_ref(role: str) -> Any:
    """Mark a singular ``EntityRef`` field as a role-typed account ref.

    Returns a Pydantic :class:`FieldInfo` with no default — the field is
    required, matching the existing ``Commit.author_ref`` /
    ``Commit.committer_ref`` semantics where every commit has an author.

    Use :func:`account_role_refs` for ``list[EntityRef]`` fields.
    """
    return Field(..., json_schema_extra={_MARKER_KEY: {"role": role, "plural": False}})


def account_role_refs(role: str) -> Any:
    """Mark a ``list[EntityRef]`` field as a role-typed account-ref list.

    Default is an empty list (via ``default_factory``) so a field with
    no assignees deserializes cleanly.
    """
    return Field(
        default_factory=list,
        json_schema_extra={_MARKER_KEY: {"role": role, "plural": True}},
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
    ) -> RoleRefSpec:
        """Register one role-typed field. Idempotent on re-registration.

        The key is ``(owning_cls.__qualname__, field_name)`` — same
        idempotency contract as ``_install_ref_resolvers`` in
        ``kernel/entity.py``, where re-importing a module silently
        replaces the previous generator install.
        """
        spec = RoleRefSpec(
            owning_cls=owning_cls,
            field_name=field_name,
            role=role,
            plural=plural,
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
        )
        registered.append(spec)
    return registered


__all__ = [
    "AccountRoleRegistry",
    "RoleRefSpec",
    "account_role_ref",
    "account_role_refs",
    "collect_role_refs",
]
