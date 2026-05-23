"""Pydantic shapes for the filter-rules feature.

Two models:

* :class:`RuleDSL`   — the agent-emitted predicate description; mirrors the
                       JSON payload documented in ``filter_files.md`` §DSL.
                       ``all_of`` is supported at depth 1 only (a flat list
                       of leaf predicates, AND-combined).
* :class:`FilterRule` — the data-server's view of one ``project_filter_rules``
                        row.

Entity kind values use the canonical SCREAMING-CASE-then-lowercase strings
that :class:`~src.common.kernel.EntityKind` carries (``"file"``,
``"commit"``, ``"pull_request"``, …). The web-ui and OpenCode tool pass the
same strings on the wire.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

from src.common.kernel import EntityKind


SupportedOp = Literal[
    "lt", "le", "gt", "ge", "eq", "ne", "in", "not_in", "contains", "regex"
]


class Predicate(BaseModel):
    """A leaf comparison: ``<field> <op> <value>``.

    ``field`` is an entity-attribute name registered in
    :data:`src.filter_rules.engine._RESOLVERS`. ``op`` is one of
    ``lt`` / ``le`` / ``gt`` / ``ge`` / ``eq`` / ``ne`` / ``in`` / ``not_in``
    / ``contains`` / ``regex``. ``value`` shape depends on the op (scalar
    for the comparisons, list for ``in``/``not_in``, string for
    ``contains`` / ``regex``).
    """

    model_config = ConfigDict(extra="forbid")

    field: str
    op: SupportedOp
    value: Union[str, int, float, bool, List[Union[str, int, float, bool]], None] = None


class AllOf(BaseModel):
    """Depth-1 conjunction wrapper.

    ``all_of`` holds a flat list of :class:`Predicate`. No nesting:
    :func:`src.filter_rules.engine.validate_dsl` rejects nested wrappers.
    """

    model_config = ConfigDict(extra="forbid")

    all_of: List[Predicate]


class RuleDSL(BaseModel):
    """One rule: a single entity_kind and either a leaf predicate or an ``all_of`` block."""

    model_config = ConfigDict(extra="forbid")

    entity_kind: EntityKind
    predicate: Union[Predicate, AllOf]


class FilterRule(BaseModel):
    """In-memory mirror of one ``project_filter_rules`` row.

    ``user_id`` is ``None`` for dev-mode rows persisted without a JWT
    (see :mod:`src.filter_rules.repository`).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    project_id: str
    user_id: Optional[str] = None
    entity_kind: EntityKind
    name: str
    nl_description: str
    dsl: RuleDSL
    created_at: Optional[datetime] = None


class CreateFilterRuleRequest(BaseModel):
    """POST /projects/{id}/rules body."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    nl_description: str = Field(min_length=1)
    dsl: RuleDSL


__all__ = [
    "AllOf",
    "CreateFilterRuleRequest",
    "FilterRule",
    "Predicate",
    "RuleDSL",
]
