"""Auto-generated ``*_ref`` / ``*_refs`` resolver methods on entities.

Covers the contract added in ``kernel/entity.py``: every concrete
``Entity`` subclass gets a same-named method per ref field that resolves
through a ``Graph`` argument. Value objects that aren't ``Entity`` opt
in via :func:`with_ref_resolvers`.
"""
from __future__ import annotations

from typing import ClassVar, List, Optional

import pytest
from pydantic import BaseModel

from src.common.kernel import Entity, EntityKind, EntityRef, Graph, Registry
from src.common.kernel.entity import with_ref_resolvers
from src.common.domains.git.models import Commit, GitAccount, GitProject
from src.common.domains.git.registries import GitAccountRegistry
from src.common.people import SourceKind


def _make_graph_with_alice() -> tuple[Graph, GitAccount, GitProject]:
    """One-account fixture used by several tests below."""
    project = GitProject(id="p1", name="t", source=SourceKind.GIT)
    reg = GitAccountRegistry()
    alice = GitAccount(
        id="alice",
        name="Alice",
        project_ref=project.ref(),
        email="alice@x",
    )
    reg.add(alice)
    graph = Graph(project_id="p1", git_accounts=reg)
    graph.add_project(project)
    return graph, alice, project


# ---- toy entities, defined once per test to keep registration local ----


class _Toy(Entity):
    kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
    name: str = ""
    friend_ref: Optional[EntityRef] = None
    follower_refs: List[EntityRef] = []


# ---- singular ref ------------------------------------------------------


def test_singular_resolver_returns_entity():
    graph, alice, _ = _make_graph_with_alice()
    toy = _Toy(id="t1", friend_ref=alice.ref())
    resolved = toy.friend(graph)
    assert resolved is not None
    assert resolved.id == "alice"


def test_singular_resolver_returns_none_when_field_is_none():
    graph, _, _ = _make_graph_with_alice()
    toy = _Toy(id="t1", friend_ref=None)
    assert toy.friend(graph) is None


def test_singular_resolver_returns_none_when_target_missing():
    graph, _, _ = _make_graph_with_alice()
    ghost = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="nobody")
    toy = _Toy(id="t1", friend_ref=ghost)
    assert toy.friend(graph) is None


# ---- list refs ---------------------------------------------------------


def test_list_resolver_returns_resolved_entities():
    graph, alice, _ = _make_graph_with_alice()
    toy = _Toy(id="t1", follower_refs=[alice.ref(), alice.ref()])
    followers = toy.followers(graph)
    assert len(followers) == 2
    assert all(f.id == "alice" for f in followers)


def test_list_resolver_drops_unresolved_entries():
    graph, alice, _ = _make_graph_with_alice()
    ghost = EntityRef(kind=EntityKind.GIT_ACCOUNT, id="nobody")
    toy = _Toy(id="t1", follower_refs=[alice.ref(), ghost, alice.ref()])
    followers = toy.followers(graph)
    assert len(followers) == 2  # ghost dropped


def test_list_resolver_empty_list_returns_empty():
    graph, _, _ = _make_graph_with_alice()
    toy = _Toy(id="t1", follower_refs=[])
    assert toy.followers(graph) == []


# ---- naming rule ------------------------------------------------------


def test_plural_naming_strips_refs_and_appends_s():
    """`parent_refs` -> `parents`, not `parent` and not `parent_refss`."""
    assert "parents" in Commit.__dict__
    assert "parent" not in Commit.__dict__
    # marker present so collision detection can distinguish ours
    assert getattr(Commit.__dict__["parents"], "_generated_ref_resolver", False)


def test_singular_naming_strips_ref():
    assert "author" in Commit.__dict__
    assert "author_ref" in Commit.model_fields


def test_multi_registry_kind_resolves_via_graph_resolve():
    """``project_ref`` (kind=PROJECT) has 7 candidate registries; the
    resolver must walk them via ``Graph.resolve``, not the single-
    registry shortcut ``EntityRef.resolve``."""
    from datetime import datetime, timezone
    graph, alice, project = _make_graph_with_alice()
    c = Commit(
        id="c1",
        sha="c1",
        project_ref=project.ref(),
        message="m",
        author_date=datetime.now(timezone.utc),
        committer_date=datetime.now(timezone.utc),
        author_ref=alice.ref(),
        committer_ref=alice.ref(),
    )
    graph.commits.add(c)
    resolved = c.project(graph)
    assert resolved is not None
    assert resolved.id == "p1"


# ---- collision detection ----------------------------------------------


def test_collision_raises_typeerror():
    with pytest.raises(TypeError) as excinfo:
        class _Bad(Entity):
            kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
            author_ref: EntityRef = EntityRef(
                kind=EntityKind.GIT_ACCOUNT, id="x"
            )

            def author(self, graph):  # noqa: D401
                return "conflict"

    msg = str(excinfo.value)
    assert "author" in msg
    assert "author_ref" in msg


def test_property_collision_raises_typeerror():
    """A ``@property`` named like the auto-generated resolver must also
    collide — properties are stored in ``cls.__dict__`` just like methods
    (``Hunk.added_lines`` is the precedent in the codebase)."""
    with pytest.raises(TypeError) as excinfo:
        class _Bad(Entity):
            kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
            author_ref: EntityRef = EntityRef(
                kind=EntityKind.GIT_ACCOUNT, id="x"
            )

            @property
            def author(self):  # noqa: D401
                return "conflict"

    msg = str(excinfo.value)
    assert "author" in msg
    assert "author_ref" in msg


# ---- pluralization rule guard rail ------------------------------------


def test_double_pluralization_raises_typeerror():
    """A field already ending in ``s`` (e.g. ``processes_refs``) would
    yield ``processess`` — the generator refuses rather than silently
    producing a misspelled method."""
    with pytest.raises(TypeError) as excinfo:
        class _Bad(Entity):
            kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
            processes_refs: List[EntityRef] = []

    msg = str(excinfo.value)
    assert "processes_refs" in msg
    assert "processess" in msg


# ---- abstract intermediates -------------------------------------------


def test_abstract_base_does_not_get_resolvers():
    class _Mid(Entity, abstract=True):
        friend_ref: Optional[EntityRef] = None

    assert "friend" not in _Mid.__dict__

    class _Leaf(_Mid):
        kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT

    assert "friend" in _Leaf.__dict__


# ---- value-object decorator -------------------------------------------


def test_with_ref_resolvers_decorator_on_plain_basemodel():
    @with_ref_resolvers
    class _Memo(BaseModel):
        author_ref: EntityRef
        watcher_refs: List[EntityRef] = []

    graph, alice, _ = _make_graph_with_alice()
    memo = _Memo(
        author_ref=alice.ref(),
        watcher_refs=[alice.ref()],
    )
    assert memo.author(graph).id == "alice"
    assert [w.id for w in memo.watchers(graph)] == ["alice"]


# ---- introspection ----------------------------------------------------


def test_generated_resolver_has_docstring_and_marker():
    method = Commit.__dict__["author"]
    assert method.__doc__ is not None
    assert "author_ref" in method.__doc__
    assert getattr(method, "_generated_ref_resolver", False) is True


def test_generated_resolver_appears_in_dir():
    """`dir()` is what IDE / `inspect` / the agent looks at."""
    names = dir(Commit)
    assert "author" in names
    assert "parents" in names
    assert "project" in names


# ---- field types that are NOT refs are skipped silently ---------------


def test_field_ending_in_ref_but_not_typed_entityref_is_skipped():
    """A field named ``x_ref`` but typed ``str`` (or anything non-EntityRef)
    must NOT trigger generation — it's a custom contract."""

    class _Quirky(Entity):
        kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
        external_ref: str = ""  # not an EntityRef!

    assert "external" not in _Quirky.__dict__
