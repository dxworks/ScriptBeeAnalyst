"""Chunk-3 tests for :mod:`src.enrichment.relations_v2`.

Covers:

* Relation add / remove / index behaviour for all 5 indexes.
* All 5 convenience methods (``for_source``, ``for_target``, ``of_kind``,
  ``of_kind_in_window``, ``between``).
* :meth:`Relation.canonical_id` determinism + format.
* Duplicate-by-canonical-id collapses to a single registry entry.
* :class:`WindowKind` membership audit.
* :class:`RelationBuilder` is an ABC requiring ``build``.

Test filename uses the ``test_v2_`` prefix to avoid colliding with the
legacy ``tests/enrichment/test_relations.py`` while still matching pytest's
default ``test_*.py`` collection pattern. See the Chunk-3 handoff.
"""
from __future__ import annotations

from typing import ClassVar, Iterable

import pytest
from pydantic import ValidationError

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.relations_v2 import (
    Relation,
    RelationBuilder,
    RelationRegistry,
    WindowKind,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _ref(kind: EntityKind, id_: str) -> EntityRef:
    return EntityRef(kind=kind, id=id_)


def _make_relation(
    *,
    source: EntityRef,
    target: EntityRef,
    relation_kind: str = "cochange",
    window: WindowKind = WindowKind.LIFETIME,
    strength: float = 1.0,
    extras: dict | None = None,
    id: str | None = None,
) -> Relation:
    rid = id or Relation.canonical_id(source, target, relation_kind, window)
    return Relation(
        id=rid,
        source=source,
        target=target,
        relation_kind=relation_kind,
        window=window,
        strength=strength,
        extras=extras or {},
    )


# ---------------------------------------------------------------------------
# canonical_id
# ---------------------------------------------------------------------------
def test_canonical_id_is_deterministic_and_uses_plan_format() -> None:
    src = _ref(EntityKind.FILE, "a.py")
    tgt = _ref(EntityKind.FILE, "b.py")
    rid1 = Relation.canonical_id(src, tgt, "cochange", WindowKind.RECENT)
    rid2 = Relation.canonical_id(src, tgt, "cochange", WindowKind.RECENT)
    assert rid1 == rid2
    # Format mandated by the chunk-3 spec.
    assert rid1 == "cochange:recent:file/a.py->file/b.py"


def test_canonical_id_is_asymmetric_in_source_target() -> None:
    a = _ref(EntityKind.FILE, "a.py")
    b = _ref(EntityKind.FILE, "b.py")
    rid_ab = Relation.canonical_id(a, b, "cochange", WindowKind.LIFETIME)
    rid_ba = Relation.canonical_id(b, a, "cochange", WindowKind.LIFETIME)
    assert rid_ab != rid_ba


def test_canonical_id_window_defaults_to_lifetime() -> None:
    src = _ref(EntityKind.FILE, "a")
    tgt = _ref(EntityKind.FILE, "b")
    assert Relation.canonical_id(src, tgt, "cochange") == \
        "cochange:lifetime:file/a->file/b"


def test_canonical_id_accepts_str_or_enum() -> None:
    src = _ref(EntityKind.FILE, "a")
    tgt = _ref(EntityKind.FILE, "b")
    from_enum = Relation.canonical_id(src, tgt, "x", WindowKind.RECENT)
    from_str = Relation.canonical_id(src, tgt, "x", "recent")
    assert from_enum == from_str


# ---------------------------------------------------------------------------
# Registry CRUD + indexes
# ---------------------------------------------------------------------------
def test_relation_registry_add_get_remove_basic() -> None:
    reg = RelationRegistry()
    a = _ref(EntityKind.FILE, "a")
    b = _ref(EntityKind.FILE, "b")
    rel = _make_relation(source=a, target=b)
    reg.add(rel)
    assert reg.get(rel.id) is rel
    assert len(reg) == 1
    reg.remove(rel.id)
    assert reg.get(rel.id) is None
    assert reg.for_source(a) == ()


def test_relation_registry_indexes_match_plan_names() -> None:
    """Plan §6 spells the five index names explicitly."""
    names = [spec.name for spec in RelationRegistry.indexes]
    assert names == [
        "by_source",
        "by_target",
        "by_kind",
        "by_kind_window",
        "by_pair",
    ]


def test_relation_registry_by_source_and_by_target_indexes() -> None:
    reg = RelationRegistry()
    a, b, c = (_ref(EntityKind.FILE, x) for x in ("a", "b", "c"))

    r_ab = _make_relation(source=a, target=b)
    r_ac = _make_relation(source=a, target=c, relation_kind="other")
    r_bc = _make_relation(source=b, target=c)

    for r in (r_ab, r_ac, r_bc):
        reg.add(r)

    assert {r.id for r in reg.for_source(a)} == {r_ab.id, r_ac.id}
    assert {r.id for r in reg.for_source(b)} == {r_bc.id}
    assert {r.id for r in reg.for_target(c)} == {r_ac.id, r_bc.id}
    assert reg.for_source(c) == ()  # nothing originates from c


def test_relation_registry_by_kind_and_by_kind_window_indexes() -> None:
    reg = RelationRegistry()
    a, b, c = (_ref(EntityKind.FILE, x) for x in ("a", "b", "c"))

    r_co_life = _make_relation(source=a, target=b, relation_kind="cochange",
                               window=WindowKind.LIFETIME)
    r_co_rec  = _make_relation(source=a, target=b, relation_kind="cochange",
                               window=WindowKind.RECENT)
    r_pr      = _make_relation(source=a, target=c, relation_kind="pr_commit",
                               window=WindowKind.LIFETIME)

    for r in (r_co_life, r_co_rec, r_pr):
        reg.add(r)

    assert {r.id for r in reg.of_kind("cochange")} == {r_co_life.id, r_co_rec.id}
    assert {r.id for r in reg.of_kind("pr_commit")} == {r_pr.id}
    assert reg.of_kind("missing") == ()

    only_recent = reg.of_kind_in_window("cochange", WindowKind.RECENT)
    assert len(only_recent) == 1 and only_recent[0].id == r_co_rec.id
    only_life = reg.of_kind_in_window("cochange", WindowKind.LIFETIME)
    assert len(only_life) == 1 and only_life[0].id == r_co_life.id
    assert reg.of_kind_in_window("pr_commit", WindowKind.RECENT) == ()


def test_of_kind_in_window_accepts_str_or_enum_consistently() -> None:
    """:meth:`RelationRegistry.of_kind_in_window` is lenient like
    :meth:`Relation.canonical_id`: ``window`` may be either a
    :class:`WindowKind` enum member or its bare string value. Passing the
    string ``"recent"`` must match the same bucket as passing
    ``WindowKind.RECENT`` — single source of truth."""
    reg = RelationRegistry()
    a, b = (_ref(EntityKind.FILE, x) for x in ("a", "b"))

    r = _make_relation(source=a, target=b, relation_kind="cochange",
                       window=WindowKind.RECENT)
    reg.add(r)

    from_enum = reg.of_kind_in_window("cochange", WindowKind.RECENT)
    from_str  = reg.of_kind_in_window("cochange", "recent")

    assert from_enum == from_str
    assert len(from_str) == 1
    assert from_str[0].id == r.id


def test_of_kind_in_window_rejects_invalid_string_window() -> None:
    """A string that isn't a valid :class:`WindowKind` value raises
    :class:`ValueError` (Python ``StrEnum`` coercion)."""
    reg = RelationRegistry()
    with pytest.raises(ValueError):
        reg.of_kind_in_window("cochange", "not_a_real_window")


def test_relation_registry_by_pair_index() -> None:
    reg = RelationRegistry()
    a, b = (_ref(EntityKind.FILE, x) for x in ("a", "b"))
    r1 = _make_relation(source=a, target=b, relation_kind="cochange",
                        window=WindowKind.LIFETIME)
    r2 = _make_relation(source=a, target=b, relation_kind="pr_commit",
                        window=WindowKind.LIFETIME)
    reg.add(r1)
    reg.add(r2)
    assert {r.id for r in reg.between(a, b)} == {r1.id, r2.id}
    assert reg.between(b, a) == ()  # asymmetric


def test_relation_registry_remove_updates_all_indexes() -> None:
    reg = RelationRegistry()
    a, b = (_ref(EntityKind.FILE, x) for x in ("a", "b"))
    r = _make_relation(source=a, target=b, relation_kind="cochange",
                       window=WindowKind.RECENT)
    reg.add(r)
    reg.remove(r.id)
    assert reg.for_source(a) == ()
    assert reg.for_target(b) == ()
    assert reg.of_kind("cochange") == ()
    assert reg.of_kind_in_window("cochange", WindowKind.RECENT) == ()
    assert reg.between(a, b) == ()


# ---------------------------------------------------------------------------
# Duplicate-by-canonical-id collapses
# ---------------------------------------------------------------------------
def test_two_relations_with_same_canonical_id_collapse_to_one() -> None:
    """The duplicate-collapse property is critical for RelationBuilders:
    re-emitting the same logical relation must not double-count in
    indexes.

    Two builders compute strength independently; the second add() wins
    (kernel ``Registry.add`` replaces by primary id and re-keys indexes)."""
    reg = RelationRegistry()
    a, b = (_ref(EntityKind.FILE, x) for x in ("a", "b"))

    r_first = _make_relation(
        source=a, target=b, relation_kind="cochange",
        window=WindowKind.RECENT, strength=0.5,
    )
    r_second = _make_relation(
        source=a, target=b, relation_kind="cochange",
        window=WindowKind.RECENT, strength=0.9,  # different strength
    )

    # Same canonical id (same source/target/kind/window).
    assert r_first.id == r_second.id

    reg.add(r_first)
    reg.add(r_second)
    assert len(reg) == 1
    # The later add replaced; we got r_second's strength.
    assert reg.get(r_first.id).strength == 0.9

    # Indexes don't double-count.
    pair = reg.between(a, b)
    assert len(pair) == 1 and pair[0].strength == 0.9
    by_window = reg.of_kind_in_window("cochange", WindowKind.RECENT)
    assert len(by_window) == 1 and by_window[0].strength == 0.9


# ---------------------------------------------------------------------------
# Extras typed union
# ---------------------------------------------------------------------------
def test_relation_extras_accepts_typed_recursive_union() -> None:
    a, b = (_ref(EntityKind.FILE, x) for x in ("a", "b"))
    r = _make_relation(
        source=a, target=b,
        extras={
            "string": "x",
            "int":    1,
            "float":  3.14,
            "bool":   True,
            "ref":    a,
            "list":   [1, "a", [True]],
            "dict":   {"k": 1, "deep": {"v": 2}},
        },
    )
    # JSON round-trip preserves shape.
    restored = Relation.model_validate(r.model_dump())
    assert restored.extras["ref"] == a
    assert restored.extras["list"] == [1, "a", [True]]
    assert restored.extras["dict"]["deep"]["v"] == 2


def test_relation_extras_rejects_untyped_junk() -> None:
    a, b = (_ref(EntityKind.FILE, x) for x in ("a", "b"))

    class _NotAllowed:
        pass

    with pytest.raises(ValidationError):
        _make_relation(source=a, target=b, extras={"bad": _NotAllowed()})  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# WindowKind membership audit
# ---------------------------------------------------------------------------
def test_window_kind_includes_legacy_and_forward_members() -> None:
    members = {w.value for w in WindowKind}
    legacy = {"lifetime", "recent"}
    forward = {"last_30_days", "last_90_days"}
    assert legacy <= members
    assert forward <= members


# ---------------------------------------------------------------------------
# RelationBuilder ABC
# ---------------------------------------------------------------------------
def test_relation_builder_is_abstract_and_requires_build() -> None:
    """Cannot instantiate :class:`RelationBuilder` directly nor a subclass
    that forgets ``build``."""
    with pytest.raises(TypeError):
        RelationBuilder()  # type: ignore[abstract]

    class _IncompleteBuilder(RelationBuilder):
        name: ClassVar[str] = "incomplete"
        relation_kind: ClassVar[str] = "x"
        # no ``build`` override

    with pytest.raises(TypeError):
        _IncompleteBuilder()  # type: ignore[abstract]


def test_relation_builder_concrete_subclass_runs() -> None:
    """Worked example for downstream chunks (the handoff cites this)."""

    class IssueCommitLinker(RelationBuilder):
        name: ClassVar[str] = "issue.commit"
        relation_kind: ClassVar[str] = "issue_commit"

        def build(self, graph) -> Iterable[Relation]:  # noqa: ARG002
            src = _ref(EntityKind.ISSUE, "ISSUE-1")
            tgt = _ref(EntityKind.COMMIT, "abc123")
            yield Relation(
                id=Relation.canonical_id(src, tgt, self.relation_kind),
                source=src,
                target=tgt,
                relation_kind=self.relation_kind,
                window=WindowKind.LIFETIME,
                strength=1.0,
            )

    builder = IssueCommitLinker()
    produced = list(builder.build(graph=None))
    assert len(produced) == 1
    assert produced[0].relation_kind == "issue_commit"
    assert produced[0].source.kind == EntityKind.ISSUE
