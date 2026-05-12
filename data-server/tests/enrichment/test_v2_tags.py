"""Chunk-3 tests for :mod:`src.enrichment.tags`.

Covers:

* Trait add / remove / index behaviour (``by_target``, ``by_family``,
  ``by_name``) plus convenience methods (``for_target``, ``of_name``,
  ``of_family``).
* Classifier add / remove / index behaviour (``by_target``, ``by_dimension``,
  ``by_dim_value``) plus convenience methods (``for_target``,
  ``with_value``, ``of_dimension``).
* Trait.is_proxy default + override.
* Trait.evidence accepts a deep typed structure (str / int / float /
  bool / EntityRef / nested list / nested dict) and rejects untyped junk.
* The ``Tag`` abstract base — declared on subclass works, direct
  instantiation refused (no ``kind``).
* Classifier dimension/value tuple index uses ``(dim,)`` and
  ``(dim, value)`` keys per plan §5.2.

The test filename uses the ``test_v2_`` prefix instead of ``test_tags.py``
because ``tests/enrichment/`` already contains a legacy ``test_relations.py``;
the prefix keeps pytest's module-id namespace unique while honouring the
chunk-3 spec's "tests live in ``tests/enrichment/``" directive AND pytest's
default ``test_*.py`` collection pattern. See the Chunk-3 handoff.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.tags import (
    Classifier,
    ClassifierRegistry,
    EvidenceValue,
    Tag,
    Trait,
    TraitFamily,
    TraitRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _file_ref(fid: str) -> EntityRef:
    return EntityRef(kind=EntityKind.FILE, id=fid)


def _commit_ref(cid: str) -> EntityRef:
    return EntityRef(kind=EntityKind.COMMIT, id=cid)


def _make_trait(
    *,
    id: str,
    target: EntityRef,
    family: TraitFamily = TraitFamily.KNOWLEDGE,
    name: str = "anomaly.knowledge.Orphan",
    severity: float = 1.0,
    is_proxy: bool = False,
    evidence: dict | None = None,
) -> Trait:
    return Trait(
        id=id,
        target=target,
        family=family,
        name=name,
        severity=severity,
        is_proxy=is_proxy,
        evidence=evidence or {},
    )


def _make_classifier(
    *,
    id: str,
    target: EntityRef,
    dimension: str,
    value: str,
) -> Classifier:
    return Classifier(
        id=id, target=target, dimension=dimension, value=value
    )


# ---------------------------------------------------------------------------
# Tag abstract base
# ---------------------------------------------------------------------------
def test_tag_is_abstract_cannot_be_instantiated_directly() -> None:
    """``Tag`` is declared ``abstract=True``; only Trait / Classifier are
    concrete leaves the kernel will instantiate."""
    # ``Tag`` has __entity_abstract__ set per kernel contract.
    assert getattr(Tag, "__entity_abstract__", False) is True
    # And Tag itself has no ``kind`` ClassVar.
    assert "kind" not in Tag.__dict__


def test_trait_and_classifier_declare_their_kind() -> None:
    """Both concrete leaves expose ``kind`` per the kernel's enforcement
    rule (Chunk 1 ``__init_subclass__``)."""
    assert Trait.kind == EntityKind.TRAIT
    assert Classifier.kind == EntityKind.CLASSIFIER


# ---------------------------------------------------------------------------
# Trait registry — indexes + convenience accessors
# ---------------------------------------------------------------------------
def test_trait_registry_add_get_remove_and_indexes_update() -> None:
    reg = TraitRegistry()
    target_a = _file_ref("a.py")
    target_b = _file_ref("b.py")

    t1 = _make_trait(id="t1", target=target_a, family=TraitFamily.KNOWLEDGE,
                     name="anomaly.knowledge.Orphan")
    t2 = _make_trait(id="t2", target=target_a, family=TraitFamily.COHESION,
                     name="anomaly.cohesion.size.Supernova")
    t3 = _make_trait(id="t3", target=target_b, family=TraitFamily.KNOWLEDGE,
                     name="anomaly.knowledge.Orphan")

    reg.add(t1)
    reg.add(t2)
    reg.add(t3)

    assert reg.get("t1") is t1
    assert len(reg) == 3

    # by_target
    assert {t.id for t in reg.for_target(target_a)} == {"t1", "t2"}
    assert {t.id for t in reg.for_target(target_b)} == {"t3"}

    # by_family
    assert {t.id for t in reg.of_family(TraitFamily.KNOWLEDGE)} == {"t1", "t3"}
    assert {t.id for t in reg.of_family(TraitFamily.COHESION)} == {"t2"}

    # by_name
    assert {t.id for t in reg.of_name("anomaly.knowledge.Orphan")} == {"t1", "t3"}
    assert reg.of_name("does.not.exist") == ()

    # remove updates indexes
    reg.remove("t2")
    assert reg.of_family(TraitFamily.COHESION) == ()
    assert {t.id for t in reg.for_target(target_a)} == {"t1"}


def test_trait_registry_for_target_on_unknown_returns_empty_tuple() -> None:
    reg = TraitRegistry()
    reg.add(_make_trait(id="t1", target=_file_ref("a.py")))
    assert reg.for_target(_file_ref("nope.py")) == ()


def test_trait_registry_indexes_match_plan_names() -> None:
    """Plan §5.2 spells the three index names; make sure none drifted."""
    names = [spec.name for spec in TraitRegistry.indexes]
    assert names == ["by_target", "by_family", "by_name"]


# ---------------------------------------------------------------------------
# Trait — typed evidence + is_proxy
# ---------------------------------------------------------------------------
def test_trait_is_proxy_defaults_false_and_can_be_overridden() -> None:
    t_default = _make_trait(id="t1", target=_file_ref("a.py"))
    assert t_default.is_proxy is False

    t_proxy = _make_trait(id="t2", target=_file_ref("a.py"), is_proxy=True)
    assert t_proxy.is_proxy is True


def test_trait_evidence_accepts_typed_recursive_union() -> None:
    """Every leaf shape in :data:`EvidenceValue` validates: primitives,
    :class:`EntityRef`, nested list, nested dict."""
    target = _file_ref("a.py")
    nested_ref = _commit_ref("abc123")
    evidence: dict[str, EvidenceValue] = {
        "string":    "hello",
        "int":       42,
        "float":     3.14,
        "bool":      True,
        "entity":    nested_ref,
        "list":      ["x", 1, False, [3, 4]],
        "dict":      {"k": 1, "nested": {"deep": True}, "ref": nested_ref},
    }
    t = _make_trait(id="t1", target=target, evidence=evidence)
    # JSON round-trip preserves the typed structure.
    restored = Trait.model_validate(t.model_dump())
    assert restored.evidence["string"] == "hello"
    assert restored.evidence["entity"] == nested_ref
    assert restored.evidence["list"] == ["x", 1, False, [3, 4]]
    assert restored.evidence["dict"]["nested"]["deep"] is True


def test_trait_evidence_rejects_untyped_junk() -> None:
    """``EvidenceValue`` is a closed union — random objects must fail
    validation. This is the "no ``Any``" guard the plan §5 calls out."""

    class _NotAllowed:
        pass

    with pytest.raises(ValidationError):
        Trait(
            id="t1",
            target=_file_ref("a.py"),
            family=TraitFamily.KNOWLEDGE,
            name="anomaly.knowledge.Orphan",
            evidence={"bad": _NotAllowed()},  # type: ignore[dict-item]
        )


# ---------------------------------------------------------------------------
# Classifier registry — indexes + convenience accessors
# ---------------------------------------------------------------------------
def test_classifier_registry_add_get_remove_and_indexes_update() -> None:
    reg = ClassifierRegistry()
    target_a = _file_ref("a.py")
    target_b = _file_ref("b.py")

    c1 = _make_classifier(id="c1", target=target_a, dimension="role", value="test")
    c2 = _make_classifier(id="c2", target=target_a, dimension="layer", value="ui")
    c3 = _make_classifier(id="c3", target=target_b, dimension="role", value="prod")
    c4 = _make_classifier(id="c4", target=_file_ref("c.py"), dimension="role", value="test")

    for c in (c1, c2, c3, c4):
        reg.add(c)
    assert len(reg) == 4

    # for_target — returns a dict keyed by dimension.
    on_a = reg.for_target(target_a)
    assert set(on_a.keys()) == {"role", "layer"}
    assert on_a["role"] is c1
    assert on_a["layer"] is c2

    on_b = reg.for_target(target_b)
    assert set(on_b.keys()) == {"role"}
    assert on_b["role"] is c3

    # with_value — every classifier matching ``(dim, value)``.
    role_tests = reg.with_value("role", "test")
    assert {c.id for c in role_tests} == {"c1", "c4"}
    assert reg.with_value("role", "unknown") == ()

    # of_dimension — every classifier along that dimension regardless of value.
    role_any = reg.of_dimension("role")
    assert {c.id for c in role_any} == {"c1", "c3", "c4"}

    # remove updates indexes
    reg.remove("c1")
    after_remove = reg.with_value("role", "test")
    assert len(after_remove) == 1 and after_remove[0].id == "c4"
    assert "role" not in reg.for_target(target_a)


def test_classifier_by_dimension_key_is_one_tuple_per_plan() -> None:
    """Plan §5.2 specifies ``(c.dimension,)`` — a 1-element tuple — as the
    ``by_dimension`` index key. The convenience method
    :meth:`ClassifierRegistry.of_dimension` matches; raw access via the
    index uses the tuple form."""
    reg = ClassifierRegistry()
    c1 = _make_classifier(id="c1", target=_file_ref("a.py"),
                          dimension="role", value="test")
    reg.add(c1)

    # Raw index access (the kernel exposes by_dimension as an attribute)
    idx = reg.by_dimension  # type: ignore[attr-defined]
    assert idx[("role",)] == (c1,)
    # The bare-string key is NOT what plan §5.2 specifies.
    assert idx["role"] == ()


def test_classifier_by_dim_value_key_is_two_tuple_per_plan() -> None:
    """Plan §5.2: key = ``(dimension, value)``."""
    reg = ClassifierRegistry()
    c1 = _make_classifier(id="c1", target=_file_ref("a.py"),
                          dimension="role", value="test")
    reg.add(c1)

    idx = reg.by_dim_value  # type: ignore[attr-defined]
    assert idx[("role", "test")] == (c1,)
    assert idx[("role", "prod")] == ()


def test_classifier_registry_indexes_match_plan_names() -> None:
    names = [spec.name for spec in ClassifierRegistry.indexes]
    assert names == ["by_target", "by_dimension", "by_dim_value"]


# ---------------------------------------------------------------------------
# Trait family membership audit (catches accidental enum reordering)
# ---------------------------------------------------------------------------
def test_trait_family_includes_legacy_and_forward_members() -> None:
    """Chunk-3 ships these members; the handoff documents which are
    legacy-verified vs. forward-looking. This test pins the set so a
    refactor doesn't silently drop one."""
    members = {f.value for f in TraitFamily}
    legacy = {"knowledge", "cohesion", "review", "structuring", "testing", "smell"}
    forward = {"hotspot", "recency", "coupling", "ownership", "governance"}
    assert legacy <= members
    assert forward <= members


# ---------------------------------------------------------------------------
# Tag.target is mandatory + typed
# ---------------------------------------------------------------------------
def test_tag_target_is_required_entity_ref() -> None:
    """``Tag.target: EntityRef`` is the plan-mandated shared field. Trait
    and Classifier both inherit it; instantiation without it must fail."""
    with pytest.raises(ValidationError):
        Trait(id="t1", family=TraitFamily.KNOWLEDGE, name="x")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        Classifier(id="c1", dimension="role", value="test")  # type: ignore[call-arg]
