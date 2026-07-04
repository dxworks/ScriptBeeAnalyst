"""Tag hierarchy — first-class graph entities for traits and classifiers.

See §5 of ``architectural_changes.md``. The hierarchy::

    Tag                 (abstract, Entity subclass — carries ``target``)
      ├── Trait         (kind=TRAIT,      family + name + severity + evidence)
      └── Classifier    (kind=CLASSIFIER, dimension + value)

Both subclasses store the entity they tag via :class:`EntityRef`. The
registries in :mod:`src.enrichment.tags.registries` declare reverse indexes
(`by_target`, `by_family`, `by_name`, `by_dimension`, `by_dim_value`) so
"which traits is target X carrying?" / "which entities have trait Y?" /
"which entities have classifier (dim, value)?" are O(1) lookups.

Typed evidence
--------------

``Trait.evidence`` is a typed mapping (NO ``Any`` in declared types). Each
evidence value is one of:

* ``str | int | float | bool`` — scalar primitives.
* :class:`EntityRef`                — typed cross-entity pointer.
* ``list[EvidenceValue]``           — recursive list.
* ``dict[str, EvidenceValue]``      — recursive map.

This mirrors the legacy ``dict[str, Any]`` shape while enforcing structure
the AI sandbox can reason about. The same union shape (re-declared
independently) appears on :class:`Relation.extras` as ``RelationExtra`` so
the two surfaces can diverge later without ripple edits — see the Chunk-3
handoff's "Design choices" section.
"""
from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from typing import TypeAliasType

from src.common.kernel import Entity, EntityKind, EntityRef


class TraitFamily(StrEnum):
    """Closed set of trait family discriminators.

    Members marked ``# legacy`` correspond to ``family=`` values currently
    emitted by ``src/enrichment/tagger/`` (verified by grep). Members
    marked ``# forward`` are listed in plan §5 but not yet emitted by any
    legacy tagger; they are reserved for the metric port in Chunk 7
    onwards. See the Chunk-3 handoff for the membership audit.
    """

    # ---- emitted by legacy taggers (verified by grep at chunk-3 time) ----
    KNOWLEDGE   = "knowledge"   # anomaly_knowledge.py
    COHESION    = "cohesion"    # anomaly_cohesion.py / _complexity.py / _timezone.py
    REVIEW      = "review"      # pr_traits.py
    STRUCTURING = "structuring" # anomaly_structuring.py
    TESTING     = "testing"     # anomaly_testing.py
    SMELL       = "smell"       # anomaly_quality_issues.py emits ``"codesmell"``
                                # today; plan §5 names this family ``SMELL``.
                                # The legacy ``"codesmell"`` string is NOT a
                                # member here — Chunk 7's port will map it.

    # ---- plan §5 forward-looking, not yet emitted ----
    HOTSPOT     = "hotspot"     # forward (planned)
    RECENCY     = "recency"     # forward (planned)
    COUPLING    = "coupling"    # forward (planned) — note: legacy uses
                                # ``basis="coupling"`` as a sub-field on
                                # structuring traits today.
    OWNERSHIP   = "ownership"   # forward (planned)
    GOVERNANCE  = "governance"  # forward (planned)


# ----------------------------------------------------------------------
# Evidence values — typed recursive union, no ``Any`` in public signatures.
#
# Pydantic v2 needs ``TypeAliasType`` (PEP 695-style) to terminate
# recursive-union schema generation correctly. A plain module-level
# ``X = A | list["X"] | ...`` triggers infinite recursion inside
# ``_generate_schema``. Using ``TypeAliasType`` keeps the alias a single
# object that Pydantic can fold; the str forward-ref ``"EvidenceValue"``
# inside resolves through the alias's own scope. See the Chunk-3 handoff
# for the failure mode and the citation.
# ----------------------------------------------------------------------
EvidenceValue = TypeAliasType(
    "EvidenceValue",
    str
    | int
    | float
    | bool
    | EntityRef
    | list["EvidenceValue"]
    | dict[str, "EvidenceValue"],
)


class Tag(Entity, abstract=True):
    """Abstract base for every first-class tag entity.

    Both subclasses (:class:`Trait`, :class:`Classifier`) carry the
    ``target`` :class:`EntityRef` so reverse indexes can be declared on the
    base ``target`` field.

    Concrete subclasses MUST declare ``kind: ClassVar[EntityKind]`` —
    enforced by the kernel's ``__init_subclass__``.
    """

    target: EntityRef


class Trait(Tag):
    """A 0..N concern attached to an entity (the v2 of dx's ``Anomaly``).

    Examples (from the legacy taggers): ``anomaly.knowledge.Orphan``,
    ``anomaly.review.StalledReview``, ``anomaly.cohesion.size.Supernova``.

    Attributes
    ----------
    family:
        Closed-set :class:`TraitFamily` discriminator.
    name:
        Free-form trait name (e.g. ``"anomaly.knowledge.Orphan"``).
    severity:
        Float (typically 0.0–1.0+); mirrors dx's ``Anomaly.severity``.
    evidence:
        Typed mapping of evidence values (no ``Any``). The plan §5 calls
        this out: every evidence entry must be a primitive, an
        :class:`EntityRef`, or a recursive list/map of the same.
    is_proxy:
        Promoted from the legacy ``evidence['proxy']`` boolean flag. ``True``
        when this trait is a *proxy* derivation (e.g. an indirectly-inferred
        anomaly). The legacy ``evidence`` carried it under a string key; we
        hoist it to a typed field so metric authors can branch on it cheaply.
    """

    kind: ClassVar[EntityKind] = EntityKind.TRAIT

    family: TraitFamily
    name: str
    severity: float = 1.0
    evidence: dict[str, EvidenceValue] = {}
    is_proxy: bool = False


class Classifier(Tag):
    """A mandatory 1:1 attribute on an entity along a single dimension.

    Examples (from the legacy taggers): ``(dimension="role", value="test")``
    on a File; ``(dimension="nature", value="bugfix")`` on a Commit.

    The plan §5 calls out the at-most-one-per-(target, dimension)
    invariant. The :class:`ClassifierRegistry`'s ``by_dim_value`` index
    enforces it on lookup; the smart-merge / metric pipeline is
    responsible for not adding two entries that conflict on the same pair.
    """

    kind: ClassVar[EntityKind] = EntityKind.CLASSIFIER

    dimension: str
    value: str


__all__ = [
    "EvidenceValue",
    "Tag",
    "Trait",
    "Classifier",
    "TraitFamily",
]
