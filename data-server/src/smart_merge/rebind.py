"""One-shot rebind pass: account refs → unified-user refs.

Implements task P3.B (UnifiedUsers redesign §D + §E + §M). See
``unified_users_change.md`` for the full design.

When a project transitions from ``PRE_MERGE`` to ``FINALIZED`` (the user
clicks "Finish Configuration" in the web UI), this module performs the
once-only rewrite that flips every role-typed account ref on a domain
entity (``Commit.author_ref``, ``PullRequest.merged_by_ref``,
``Issue.reporter_ref``, ...) to target the matching
:class:`UnifiedUser` instead of the per-source ``Account``.

The pass is destructive in-memory but deterministic — re-import is the
documented recourse (no inverse). It is guarded by
:attr:`Graph.merge_state`: running it twice on the same graph raises.

Algorithm
---------

1. **State guard** — assert ``graph.merge_state == MergeState.PRE_MERGE``.
2. **Auto-create singleton UUs** — every per-source account whose
   ``unified_user_id`` is ``None`` gets a fresh :class:`UnifiedUser`
   carrying just that one account. This guarantees the post-finalize
   invariant "every role ref points at UNIFIED_USER" with no mixed
   states.
3. **Rewrite role refs** — iterate :class:`AccountRoleRegistry` and for
   each spec walk every entity in the owning registry, resolving the
   current account ref to its ``unified_user_id`` and replacing the
   field value with a fresh ``EntityRef(kind=UNIFIED_USER, id=uu_id)``.
4. **Rebuild indexes** — :meth:`Graph.rebuild_indexes` so every
   ``by_author`` / ``by_reporter`` / etc. is re-keyed on the new UU
   refs.
5. **Flip state** — ``graph.merge_state = MergeState.FINALIZED``.

Returns a small :class:`RebindStats` for the caller (the finalize
endpoint surfaces the counts to the UI).

Edge cases
----------

* A ref pointing at a missing (deleted) account: dropped for plural
  fields, set to ``None`` for optional singular fields, and raises for
  required singular fields. Logged as a warning in either case.
* Nested value objects (``IssueTransition`` / ``Comment`` — Jira's
  ``user_ref`` / ``author_ref`` / ``updated_by_ref``) are NOT touched
  by this pass. The ``account_role_ref`` marker is not applied to
  fields on nested :class:`BaseModel` value objects today (the
  collector only walks ``Entity`` subclasses). A separate hook is
  planned; until then those refs continue to target per-source
  accounts even after finalize. Documented as a TODO on the affected
  fields in ``src/common/domains/jira/models.py``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from ..common.kernel import EntityKind, EntityRef
from ..common.kernel.graph import _KIND_TO_FIELDS
from ..common.kernel.merge_state import MergeState
from ..common.kernel.role_ref import AccountRoleRegistry, RoleRefSpec
from ..common.people.unified import UnifiedUser

if TYPE_CHECKING:
    from ..common.kernel import Graph
    from ..common.people.account import Account


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebindStats:
    """Summary of a rebind pass — surfaced by the finalize endpoint."""

    #: How many singleton :class:`UnifiedUser` entities the pass
    #: synthesized (one per orphan per-source account).
    unified_users_created: int

    #: Total number of ``*_ref`` values rewritten across all domain
    #: entities (counts both singular and individual list entries).
    refs_rewritten: int


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def rebind_account_refs_to_unified(graph: "Graph") -> RebindStats:
    """Run the once-only account-ref → unified-user rebind on ``graph``.

    Mutates ``graph`` in place. Idempotency is enforced via
    :attr:`Graph.merge_state`: a second call raises :class:`ValueError`
    because finalize is one-way (re-import to redo).
    """
    # --- 1. State guard ---------------------------------------------------
    if graph.merge_state == MergeState.FINALIZED:
        raise ValueError(
            "rebind already applied; reset the graph to re-run "
            "(merge_state == FINALIZED)"
        )
    if graph.merge_state != MergeState.PRE_MERGE:
        raise ValueError(
            f"rebind requires merge_state == PRE_MERGE, "
            f"got {graph.merge_state!r}"
        )

    # --- 2. Auto-create singleton UUs for orphan accounts -----------------
    unified_users_created = _autocreate_singleton_unified_users(graph)

    # --- 3. Rewrite role refs ---------------------------------------------
    refs_rewritten = _rewrite_role_refs(graph)

    # --- 4. Rebuild indexes -----------------------------------------------
    graph.rebuild_indexes()

    # --- 5. Flip state ----------------------------------------------------
    graph.merge_state = MergeState.FINALIZED

    return RebindStats(
        unified_users_created=unified_users_created,
        refs_rewritten=refs_rewritten,
    )


# ---------------------------------------------------------------------------
# Step 2 — singleton auto-creation
# ---------------------------------------------------------------------------


#: Field names on :class:`Graph` that own per-source ``Account`` entities.
#: Walked in declaration order so singleton creation is deterministic.
_ACCOUNT_FIELDS: tuple[str, ...] = ("git_accounts", "jira_users", "github_users")


def _autocreate_singleton_unified_users(graph: "Graph") -> int:
    """Synthesize a :class:`UnifiedUser` for every orphan account.

    "Orphan" = ``account.unified_user_id is None``. After this pass,
    every per-source account on the graph has a non-``None``
    ``unified_user_id``, so the rewrite pass (step 3) can always
    resolve a target UU id without a fallback branch.
    """
    import uuid

    created = 0
    for field_name in _ACCOUNT_FIELDS:
        registry = getattr(graph, field_name)
        for account in registry.all():
            if account.unified_user_id is not None:
                continue
            uu = UnifiedUser(
                id=str(uuid.uuid4()),
                display_name=account.name,
                primary_email=getattr(account, "email", None),
                account_refs=[account.ref()],
            )
            graph.unified_users.add(uu)
            account.unified_user_id = uu.id
            created += 1
    return created


# ---------------------------------------------------------------------------
# Step 3 — rewrite role refs
# ---------------------------------------------------------------------------


def _rewrite_role_refs(graph: "Graph") -> int:
    """Walk :data:`AccountRoleRegistry` and rewrite every marked field.

    Returns the number of individual ref values that were replaced
    (counts each list entry separately).
    """
    rewritten = 0
    for spec in AccountRoleRegistry.all():
        registry = _registry_for_spec(graph, spec)
        if registry is None:
            # Unknown kind — should be impossible (the role-ref collector
            # only fires from ``__pydantic_init_subclass__``, so every
            # registered spec's owning_cls is a real Entity subclass with
            # a Graph slot). Defensive guard: skip + warn.
            logger.warning(
                "rebind: no Graph registry for kind %r (owning_cls=%s, "
                "field=%s); skipping",
                spec.owning_cls.kind,
                spec.owning_cls.__name__,
                spec.field_name,
            )
            continue
        for entity in registry.all():
            if spec.plural:
                rewritten += _rewrite_plural_field(graph, entity, spec)
            else:
                rewritten += _rewrite_singular_field(graph, entity, spec)
    return rewritten


def _registry_for_spec(graph: "Graph", spec: RoleRefSpec):
    """Return the registry on ``graph`` that owns ``spec.owning_cls``.

    Uses :data:`_KIND_TO_FIELDS` from ``kernel/graph.py`` directly —
    ``Graph.registry_for(kind)`` would also work for the non-PROJECT
    kinds we care about, but the role-ref carriers (Commit / PR /
    Issue / Review / ...) all map 1:1 to a Graph field, and going
    through the field-name table keeps the lookup explicit.
    """
    kind = spec.owning_cls.kind
    fields = _KIND_TO_FIELDS.get(kind, [])
    if len(fields) != 1:
        # A role-ref carrier must have an unambiguous Graph slot —
        # PROJECT-kind (7 fields) wouldn't have role refs anyway.
        return None
    return getattr(graph, fields[0])


def _rewrite_plural_field(graph: "Graph", entity, spec: RoleRefSpec) -> int:
    """Rewrite a ``list[EntityRef]`` field in place.

    Drops entries whose account no longer resolves (logged as a
    warning — typical cause is a deleted account that some entity
    still points at, which is a data-quality issue but not fatal for
    a list-shaped field).
    """
    current = getattr(entity, spec.field_name) or []
    new_refs: list[EntityRef] = []
    changed = 0
    for ref in current:
        if ref.kind == EntityKind.UNIFIED_USER:
            # Defensive: already rewritten (shouldn't happen pre-finalize).
            new_refs.append(ref)
            continue
        uu_ref = _account_ref_to_unified_user_ref(graph, ref, spec, entity)
        if uu_ref is None:
            # Dropped — account didn't resolve. Warning was logged in
            # the helper; nothing more to do.
            changed += 1  # still count as a "ref changed" (removed)
            continue
        new_refs.append(uu_ref)
        changed += 1
    setattr(entity, spec.field_name, new_refs)
    return changed


def _rewrite_singular_field(graph: "Graph", entity, spec: RoleRefSpec) -> int:
    """Rewrite a single ``EntityRef`` field in place.

    For ``spec.optional == True`` a ``None`` value is left alone (returns
    0). For required fields a missing/unresolvable account raises —
    the data is broken and the caller should surface it.
    """
    current: Optional[EntityRef] = getattr(entity, spec.field_name)
    if current is None:
        if spec.optional:
            return 0
        raise ValueError(
            f"rebind: required role-ref {spec.owning_cls.__name__}."
            f"{spec.field_name} is None on entity id={entity.id!r}"
        )
    if current.kind == EntityKind.UNIFIED_USER:
        # Defensive: already rewritten.
        return 0
    uu_ref = _account_ref_to_unified_user_ref(graph, current, spec, entity)
    if uu_ref is None:
        if spec.optional:
            # Account didn't resolve — drop the ref by setting None.
            setattr(entity, spec.field_name, None)
            return 1
        raise ValueError(
            f"rebind: required role-ref {spec.owning_cls.__name__}."
            f"{spec.field_name} on entity id={entity.id!r} points at "
            f"{current!r} which does not resolve to an account"
        )
    setattr(entity, spec.field_name, uu_ref)
    return 1


def _account_ref_to_unified_user_ref(
    graph: "Graph",
    ref: EntityRef,
    spec: RoleRefSpec,
    entity,
) -> Optional[EntityRef]:
    """Resolve ``ref`` to an Account, then build a UU ref from its
    ``unified_user_id``.

    Returns ``None`` when:

    * the ref doesn't resolve (deleted entity), or
    * the resolved account has ``unified_user_id is None``
      (should be impossible after step 2 — raises instead).

    The caller decides what to do with a ``None`` return (drop for
    plural fields, raise / None-out for singular).
    """
    account: Optional["Account"] = graph.resolve(ref)  # type: ignore[assignment]
    if account is None:
        logger.warning(
            "rebind: %s.%s on entity id=%r points at %r which does not "
            "resolve; dropping",
            spec.owning_cls.__name__,
            spec.field_name,
            getattr(entity, "id", "?"),
            ref,
        )
        return None
    uu_id = getattr(account, "unified_user_id", None)
    if uu_id is None:
        # Step 2 should have set this for every account on the graph.
        # If we land here, either the account isn't in a graph registry
        # we walked, or step 2 had a bug — surface it loudly.
        raise RuntimeError(
            f"rebind: account {type(account).__name__} id={account.id!r} "
            f"has unified_user_id=None after singleton auto-creation; "
            f"this indicates a bug in step 2 (or an account outside the "
            f"three known per-source registries)"
        )
    return EntityRef(kind=EntityKind.UNIFIED_USER, id=uu_id)


__all__ = ["RebindStats", "rebind_account_refs_to_unified"]
