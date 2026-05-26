"""UnifiedUser entity and registry.

See §2.2 + §2.3 of ``architectural_changes.md``.

A :class:`UnifiedUser` ties together the per-source accounts the smart-merge
UI has confirmed belong to one human. The link is bidirectional and O(1) in
both directions:

* ``UnifiedUser.account_refs`` (list of :class:`EntityRef`) → resolves to
  per-source ``Account`` entities through the graph's domain registries.
* :class:`UnifiedUserRegistry` declares a ``by_account`` index keyed on each
  entry in ``account_refs`` (multi-key fan-out — see Chunk 1's
  ``IndexSpec`` key-fn semantics).

Smart-merge API surface (UnifiedUsers redesign §L)
--------------------------------------------------

Task P4.C folded the former ``src.smart_merge.identity.UnifiedUser`` DTO
into this entity — there is now a single ``UnifiedUser`` class. The
API-shape helpers (``bind_graph``, ``commit_count``, ``pr_count``,
``issue_count``, ``to_dict``, the per-source ``*_identities`` filters,
plus the ``all_emails`` / ``all_names`` / ``all_logins`` aggregates) live
here. The ``identities`` field is a smart-merge in-memory carrier
(``exclude=True`` so it does NOT serialise through pickle / ``model_dump``)
and ``_graph`` is a Pydantic ``PrivateAttr`` set by :meth:`bind_graph`.

Auto-generated reverse resolvers (UnifiedUsers redesign §C)
-----------------------------------------------------------

Every role-typed account ref across the domain models (``Commit.author_ref``,
``PullRequest.merged_by_ref``, ``Issue.reporter_ref``, ...) is paired with
a reverse resolver auto-installed on :class:`UnifiedUser`: e.g.
``uu.commits_as_author(g)``, ``uu.pull_requests_as_merged_by(g)``,
``uu.issues_as_reporter(g)``. The method body is a one-line index read
against the owning registry's ``by_<role>`` bucket keyed on the
:class:`UnifiedUser`'s own ref.

Install timing — chosen path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The class body of :class:`UnifiedUser` finishes loading BEFORE the domain
``models.py`` files import (``unified.py`` is imported from
``kernel/graph.py`` at line 57 — the domain registries' imports come at
lines 60+). At end-of-module time, :data:`AccountRoleRegistry` may still
be empty.

To avoid the chicken-and-egg the module:

1. Calls ``_install_reverse_resolvers()`` eagerly at end-of-module — the
   three raw-provenance accessors always install; the dynamic ones land
   only if the registry happens to be populated.
2. ``src/common/kernel/__init__.py`` calls the same function once more
   AFTER ``Graph`` (and through it every domain registry / model) has
   finished loading. By that point :data:`AccountRoleRegistry` carries
   all twelve specs and the dynamic resolvers land on the class. This
   is the deterministic install path that the ``dir(...)`` inspection
   in the task verification depends on.
3. Installs a class-level ``__getattr__`` fallback on :class:`UnifiedUser`
   that runs ``_install_reverse_resolvers()`` on first access of any
   reverse-resolver name (``<plural>_as_<role>`` or one of the three raw-
   provenance helpers ``git_accounts``/``jira_users``/``github_users``).
   Belt-and-braces for any consumer importing ``unified.py`` through a
   non-``kernel/__init__`` path.

The three raw-provenance accessors (``uu.git_accounts(g)`` /
``uu.jira_users(g)`` / ``uu.github_users(g)``) thin-wrap
:meth:`UnifiedUser.accounts_of_kind` — they're hand-installed in the same
pass alongside the dynamic ones (a closure helper avoids the
late-binding loop bug on ``kind``). They are NOT derived from
``AccountRoleRegistry``.

Idempotency / collision rules mirror ``_install_ref_resolvers`` in
``kernel/entity.py``: methods we install carry a
``_generated_reverse_resolver = True`` marker. A re-install replaces a
marked method silently; a hand-written same-named method without the
marker raises :class:`TypeError`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable, List, Optional
from uuid import uuid4

from pydantic import Field, PrivateAttr

from ..kernel import Entity, EntityKind, EntityRef, IndexSpec, Registry

if TYPE_CHECKING:  # forward-only; we never import the domain Account types
    from ..kernel import Graph
    from .account import Account
    from src.smart_merge.identity import SourceIdentity


class UnifiedUser(Entity):
    """A merged identity across source-side accounts.

    Attributes
    ----------
    display_name:
        Human-friendly name chosen during smart-merge.
    primary_email:
        Most-trusted email (usually the Git one). ``None`` if unknown.
    account_refs:
        Per-source account references (kind ∈ ``{GIT_ACCOUNT, JIRA_USER,
        GITHUB_USER}``). Indexed by :class:`UnifiedUserRegistry`'s
        ``by_account`` for reverse lookups.
    """

    kind: ClassVar[EntityKind] = EntityKind.UNIFIED_USER

    # Override ``Entity.id`` to default to a fresh UUID — preserves the
    # behaviour of the former ``smart_merge.identity.UnifiedUser`` DTO
    # which auto-generated the id when callers omitted it (the smart-merge
    # ``apply`` / ``apply-batch`` endpoints rely on that — see
    # ``src/server.py``).
    id: str = Field(default_factory=lambda: str(uuid4()))

    display_name: str
    primary_email: Optional[str] = None
    account_refs: List[EntityRef] = []

    # Smart-merge in-memory carrier. ``exclude=True`` keeps it out of
    # ``model_dump`` / ``__reduce__`` so the typed-graph pickle layout is
    # unaffected: the source-of-truth for identities post-restart is
    # Supabase (``user_identity_mappings``), replayed by
    # :func:`_replay_user_mappings` in ``src/server.py``.
    identities: List["SourceIdentity"] = Field(default_factory=list, exclude=True)

    # Bound typed Graph for the per-instance stats accessors
    # (``commit_count`` / ``pr_count`` / ``issue_count``). Pydantic
    # ``PrivateAttr`` so it's not validated as a field, not serialised by
    # ``model_dump``, and not pickled by :class:`Entity`'s ``__reduce__``.
    # Set via :meth:`bind_graph`. Defaults to ``None`` — unbound instances
    # report ``0`` for every count (matches the former DTO behaviour).
    _graph: Optional["Graph"] = PrivateAttr(default=None)

    # ------------------------------------------------------------------
    # Smart-merge API: graph binding for per-instance stats accessors.
    # ------------------------------------------------------------------
    def bind_graph(self, graph: Optional["Graph"]) -> None:
        """Bind to a typed v2 :class:`Graph` so the per-instance stats
        accessors (``commit_count`` / ``pr_count`` / ``issue_count``) can
        resolve through the reverse resolvers. Pass ``None`` to clear."""
        self._graph = graph

    # ------------------------------------------------------------------
    # Per-source identity filters (trivial views over ``self.identities``).
    # ------------------------------------------------------------------
    @property
    def git_identities(self) -> List["SourceIdentity"]:
        return [i for i in self.identities if i.source == "git"]

    @property
    def github_identities(self) -> List["SourceIdentity"]:
        return [i for i in self.identities if i.source == "github"]

    @property
    def jira_identities(self) -> List["SourceIdentity"]:
        return [i for i in self.identities if i.source == "jira"]

    # ------------------------------------------------------------------
    # Aggregates over all identities.
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
    # Activity counts (resolved through the bound typed Graph via the
    # reverse resolvers installed at module-bottom; one-liners now).
    # ------------------------------------------------------------------
    @property
    def commit_count(self) -> int:
        """Commits authored by this user. ``0`` if no graph bound."""
        if self._graph is None:
            return 0
        return len(self.commits_as_author(self._graph))

    @property
    def pr_count(self) -> int:
        """Unique PRs touched (authored ∪ merged-by). ``0`` if no graph bound."""
        if self._graph is None:
            return 0
        seen: set[int] = set()
        count = 0
        for pr in self.pull_requests_as_author(self._graph):
            if pr.number not in seen:
                seen.add(pr.number)
                count += 1
        for pr in self.pull_requests_as_merged_by(self._graph):
            if pr.number not in seen:
                seen.add(pr.number)
                count += 1
        return count

    @property
    def issue_count(self) -> int:
        """Unique issues touched (reporter ∪ creator ∪ assignee).
        ``0`` if no graph bound."""
        if self._graph is None:
            return 0
        seen: set[str] = set()
        count = 0
        for role in ("issues_as_reporter", "issues_as_creator", "issues_as_assignee"):
            resolver = getattr(self, role, None)
            if resolver is None:
                continue
            for issue in resolver(self._graph):
                if issue.key not in seen:
                    seen.add(issue.key)
                    count += 1
        return count

    # ------------------------------------------------------------------
    # Serialisation — wire-compatible with the former DTO's ``to_dict``.
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        """Serialize to JSON-safe dict for smart-merge API responses.

        Output shape matches the legacy
        ``src.smart_merge.identity.UnifiedUser.to_dict`` so the web UI
        consuming ``/projects/{id}/authors/users`` (and the
        ``apply`` / ``apply-batch`` endpoints) sees an unchanged payload.
        """
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

    # ------------------------------------------------------------------
    # Generic, domain-free accessors. The auto-installed reverse resolvers
    # (see module docstring + ``_install_reverse_resolvers`` below) ride
    # on top of this one — the three raw-provenance helpers
    # (``git_accounts`` / ``jira_users`` / ``github_users``) thin-wrap it.
    #
    # ``accounts(graph)`` is auto-generated by
    # ``Entity.__pydantic_init_subclass__`` from the ``account_refs``
    # field (see ``kernel/entity.py``); behaviour is identical to the
    # earlier hand-written version — resolve every ref, drop entries that
    # don't resolve.
    # ------------------------------------------------------------------
    def accounts_of_kind(
        self, graph: "Graph", kind: EntityKind
    ) -> list["Account"]:
        """Resolve only the account_refs of a given :class:`EntityKind`.

        Use this instead of the per-domain helpers the plan sketched
        (``git_accounts`` / ``jira_users`` / ``github_users``). Example::

            git_accounts = uu.accounts_of_kind(graph, EntityKind.GIT_ACCOUNT)
        """
        resolved: list["Account"] = []
        for ref in self.account_refs:
            if ref.kind != kind:
                continue
            entity = graph.resolve(ref)
            if entity is not None:
                resolved.append(entity)  # type: ignore[arg-type]
        return resolved

    # ------------------------------------------------------------------
    # Lazy-install fallback for reverse resolvers (see module docstring).
    #
    # The eager install at end-of-module is a no-op when domain models
    # haven't loaded yet (``AccountRoleRegistry`` empty at unified.py
    # import time — see module docstring). The first access of any
    # reverse-resolver name on a UnifiedUser instance retries install,
    # then re-dispatches through the normal MRO.
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` only fires on misses — installed methods take
        # the normal path. We restrict the trigger to plausible reverse-
        # resolver names so unrelated typos still raise crisply.
        if (
            "_as_" in name
            or name in _RAW_PROVENANCE_METHOD_KINDS
        ) and not _is_reverse_install_done():
            _install_reverse_resolvers()
            # Re-attempt the lookup through the normal class machinery.
            # We bypass ``__getattr__`` to avoid an infinite loop if the
            # install failed to produce ``name``.
            attr = type(self).__dict__.get(name)
            if attr is not None:
                return attr.__get__(self, type(self))
        # Defer to Pydantic's ``BaseModel.__getattr__`` for everything
        # else — it handles ``PrivateAttr`` lookup (the ``_graph`` slot
        # set by :meth:`bind_graph`) by reading
        # ``self.__pydantic_private__``. Without this delegation, our
        # ``raise AttributeError(name)`` shadows that lookup and the
        # ``commit_count`` / ``pr_count`` / ``issue_count`` properties
        # can't read ``self._graph``.
        return super().__getattr__(name)


def _unified_by_account_keys(u: UnifiedUser) -> Iterable[EntityRef]:
    """Index key-fn: fan out across every entry in ``account_refs``.

    Chunk 1's ``_normalize_keys`` (``index.py``) special-cases ``BaseModel``
    as a *single* hashable composite key (good — EntityRef is hashable).
    Returning a plain ``list[EntityRef]`` hits the iterable branch and
    fans out — one bucket per account ref, exactly what §2.3 wants.
    """
    return u.account_refs


class UnifiedUserRegistry(Registry[UnifiedUser, str]):
    """Registry of :class:`UnifiedUser` with a reverse-lookup index.

    The ``by_account`` index makes "which UnifiedUser owns this source
    account?" an O(1) lookup. Concrete convenience method
    :meth:`for_account` wraps that index so Chunk 7 (smart-merge UI) and
    Chunk 9 (relation builder) don't need to know the index name.
    """

    indexes = [
        IndexSpec(
            name="by_account",
            key_fn=_unified_by_account_keys,
            multi=True,
        ),
    ]

    def get_id(self, entity: UnifiedUser) -> str:
        return entity.id

    # ------------------------------------------------------------------
    # Domain-facing convenience
    # ------------------------------------------------------------------
    def for_account(self, account_ref: EntityRef) -> Optional[UnifiedUser]:
        """Return the unique :class:`UnifiedUser` that owns ``account_ref``.

        Returns ``None`` if no UnifiedUser has been merged yet for that
        account. Returns the first match if (incorrectly) multiple users
        claim the same account — the smart-merge UI is responsible for
        keeping that invariant; the registry won't enforce it here so
        downstream code can detect+repair such states.
        """
        bucket = self.by_account[account_ref]  # type: ignore[attr-defined]
        if not bucket:
            return None
        # ``bucket`` is a tuple[UnifiedUser, ...] per kernel multi-index
        # semantics. We return the first; callers expecting at-most-one
        # treat it as canonical.
        return bucket[0]


# ---------------------------------------------------------------------------
# Reverse-resolver installer (UnifiedUsers redesign §C).
# ---------------------------------------------------------------------------
# Mirrors the forward-resolver pattern in ``kernel/entity.py``:
# ``_install_ref_resolvers`` walks ``cls.model_fields`` once per Entity
# subclass; here we walk the module-level ``AccountRoleRegistry`` once and
# install one reverse resolver per registered spec on ``UnifiedUser``.
# Idempotent on re-import (the marker ``_generated_reverse_resolver``
# distinguishes our installs from any hand-written method of the same
# name — a collision with an unmarked attribute raises ``TypeError``).
#
# Why this module hosts the installer instead of ``kernel/entity.py``:
# the reverse resolvers are unique to :class:`UnifiedUser` (the post-
# rebind sink kind), so the install code lives next to the class it
# patches rather than parallel to the generic forward-resolver pass.

_REVERSE_GENERATED_MARKER = "_generated_reverse_resolver"

#: Method-name → :class:`EntityKind` for the three raw-provenance
#: accessors that thin-wrap :meth:`UnifiedUser.accounts_of_kind`. These
#: are hand-installed (not derived from ``AccountRoleRegistry``).
_RAW_PROVENANCE_METHOD_KINDS: dict[str, EntityKind] = {
    "git_accounts": EntityKind.GIT_ACCOUNT,
    "jira_users":   EntityKind.JIRA_USER,
    "github_users": EntityKind.GITHUB_USER,
}


def _is_reverse_install_done() -> bool:
    """True iff the lazy installer has already run (any pass that
    actually installed something flips the flag)."""
    return getattr(UnifiedUser, "__reverse_resolvers_installed__", False)


def _entity_plural_name(owning_cls: type) -> str:
    """Map ``owning_cls.kind`` to the Graph field-name (entity-plural).

    Single source of truth: :data:`kernel.graph._KIND_TO_FIELDS`. Project-
    kind would map to 7 fields and isn't a role-ref carrier — we reject
    it explicitly to surface a clear error if anyone marks one in the
    future. Any other kind that maps to multiple fields would similarly
    be ambiguous and is rejected.
    """
    # Local import — keeps this module's eager imports free of Graph.
    from ..kernel.graph import _KIND_TO_FIELDS

    fields = _KIND_TO_FIELDS.get(owning_cls.kind, [])
    if not fields:
        raise TypeError(
            f"UnifiedUser reverse-resolver installer: no Graph field "
            f"registered for kind {owning_cls.kind!r} on "
            f"{owning_cls.__name__!r}. Update _FIELD_SPECS in "
            f"src/common/kernel/graph.py."
        )
    if len(fields) > 1:
        raise TypeError(
            f"UnifiedUser reverse-resolver installer: kind "
            f"{owning_cls.kind!r} maps to multiple Graph fields "
            f"({fields}); cannot derive an unambiguous entity-plural name."
        )
    return fields[0]


def _make_reverse_resolver(field_name_on_graph: str, role: str):
    """Build one reverse resolver — closure on ``field_name_on_graph`` and
    ``role`` to avoid the late-binding loop bug.
    """
    method_name = f"{field_name_on_graph}_as_{role}"
    index_name = f"by_{role}"

    def resolver(self, graph):
        registry = getattr(graph, field_name_on_graph)
        index = getattr(registry, index_name)
        return list(index[self.ref()])

    resolver.__name__ = method_name
    resolver.__qualname__ = f"UnifiedUser.{method_name}"
    resolver.__doc__ = (
        f"Return every :class:`{field_name_on_graph}` entity whose "
        f"``{role}`` role-ref targets this UnifiedUser. Reads the "
        f"``graph.{field_name_on_graph}.{index_name}`` registry index. "
        f"Auto-generated; see ``common/people/unified.py``."
    )
    setattr(resolver, _REVERSE_GENERATED_MARKER, True)
    return resolver


def _make_raw_provenance_resolver(method_name: str, account_kind: EntityKind):
    """Build one raw-provenance accessor — closure on ``account_kind`` to
    avoid the late-binding loop bug.
    """
    def resolver(self, graph):
        return self.accounts_of_kind(graph, account_kind)

    resolver.__name__ = method_name
    resolver.__qualname__ = f"UnifiedUser.{method_name}"
    resolver.__doc__ = (
        f"Return every :class:`Account` of kind {account_kind!r} that "
        f"belongs to this UnifiedUser. Thin wrapper over "
        f":meth:`UnifiedUser.accounts_of_kind`. "
        f"Auto-installed; see ``common/people/unified.py``."
    )
    setattr(resolver, _REVERSE_GENERATED_MARKER, True)
    return resolver


def _set_resolver(method_name: str, resolver) -> None:
    """Install one resolver on :class:`UnifiedUser` with the collision check.

    Mirrors the ``_install_ref_resolvers`` pattern: a previous generator
    install (marked) is silently overwritten; an unmarked attribute of
    the same name raises ``TypeError``.
    """
    existing = UnifiedUser.__dict__.get(method_name)
    if existing is not None and not getattr(
        existing, _REVERSE_GENERATED_MARKER, False
    ):
        raise TypeError(
            f"UnifiedUser.{method_name} would be auto-generated as a "
            f"reverse resolver, but the class already defines "
            f"{method_name!r} without the "
            f"``_generated_reverse_resolver`` marker. Rename the "
            f"colliding attribute or set the marker if you really mean "
            f"to override."
        )
    setattr(UnifiedUser, method_name, resolver)


def _install_reverse_resolvers() -> int:
    """Install every reverse resolver derived from ``AccountRoleRegistry``
    plus the three raw-provenance accessors. Returns the count of
    methods installed (dynamic + raw).

    No-op when ``AccountRoleRegistry`` is empty AND raw-provenance
    accessors are already present (the lazy path runs this on first
    instance access — by then domain models have always loaded).
    """
    # Local import — ``AccountRoleRegistry`` is in the kernel package and
    # has no Graph dependency.
    from ..kernel.role_ref import AccountRoleRegistry

    specs = AccountRoleRegistry.all()
    # The three raw-provenance accessors don't depend on the registry
    # being non-empty — install them whenever this function runs.
    installed = 0
    for spec in specs:
        plural = _entity_plural_name(spec.owning_cls)
        method_name = f"{plural}_as_{spec.role}"
        _set_resolver(method_name, _make_reverse_resolver(plural, spec.role))
        installed += 1
    for method_name, account_kind in _RAW_PROVENANCE_METHOD_KINDS.items():
        _set_resolver(
            method_name,
            _make_raw_provenance_resolver(method_name, account_kind),
        )
        installed += 1
    # Mark complete only when the dynamic specs were actually present —
    # otherwise the lazy ``__getattr__`` path needs to retry after the
    # domain models finish loading.
    if specs:
        UnifiedUser.__reverse_resolvers_installed__ = True  # type: ignore[attr-defined]
    return installed


# Eager install at end-of-module. If ``AccountRoleRegistry`` is empty
# (normal in the kernel-package import order — see module docstring),
# only the three raw-provenance accessors land; the ``__getattr__``
# fallback completes the install lazily on first instance access.
_install_reverse_resolvers()


# ---------------------------------------------------------------------------
# Resolve the forward-referenced ``SourceIdentity`` annotation on the
# ``identities`` field (UnifiedUsers redesign §L). The runtime import
# lives at module-bottom so the ``smart_merge`` namespace is touched
# only after :class:`UnifiedUser`'s class body has finished — no
# circular-import risk (``smart_merge.identity`` is dependency-free —
# only stdlib + a ``TYPE_CHECKING`` Graph forward ref).
# ---------------------------------------------------------------------------
from src.smart_merge.identity import SourceIdentity  # noqa: E402,F401

UnifiedUser.model_rebuild()


__all__ = ["UnifiedUser", "UnifiedUserRegistry"]
