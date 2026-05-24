"""Catalogue introspection for the per-project enrichment-config editor.

Builds the wire shape the editor consumes: a list of family-grouped
``CatalogueField`` entries, each one describing an editable knob (its
declared type, default, current resolved value, the list of metric names
that read it, and the metadata flags the UI needs to render it).

This module is **pure introspection** — it reads :data:`METRICS` (the
metric catalog) and :class:`EnrichmentConfig` (the dataclass schema) and
produces a JSON-shaped response. No Supabase, no FastAPI, no side
effects. The router supplies the per-project ``overrides`` dict and
this module overlays it onto the defaults to resolve ``current``.

Three things are intentionally **hidden** from the editor (see
``_HIDDEN_FIELDS``):

* ``components_mapping_data`` and ``components_mapping_path`` — already
  edited via the existing per-project Components page; surfacing them
  twice would create two ways to edit the same data.
* ``idle_threshold_days`` — declared on :class:`EnrichmentConfig` but
  never read by any active metric (a no-op knob today).

Decisions deferred to the orchestrator's per-thesis design call:

* ``recent_window_days`` stays a single global knob (referenced by
  three families). Surfaced once under the ``classifiers`` family which
  is where the file/author classifier consumers live.
* The seven ScriptBee-only traits (Cathedral, BusFactor1,
  SharedKnowledge, Awakening, Erosion, StalledReview, TestOrphan) carry
  a per-field ``dx_baseline=False`` flag so the UI can render a "no dx
  baseline" badge. Detection is by field-name prefix — see
  ``_NO_DX_BASELINE_PREFIXES``.
"""
from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields
from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.enrichment.config import DEFAULT_CONFIG, EnrichmentConfig
from src.enrichment.metrics import METRICS
from src.logger import get_logger

LOG = get_logger(__name__)


# Knobs that exist on EnrichmentConfig but the editor must NOT surface.
# Filtered at catalogue-build time so the editor can never reach them.
_HIDDEN_FIELDS: frozenset[str] = frozenset({
    "components_mapping_data",
    "components_mapping_path",
    "idle_threshold_days",
})


# Knobs whose field-name prefix marks them as feeding a ScriptBee-only
# trait (no dx baseline). See default_values.md §"Present in only one side".
_NO_DX_BASELINE_PREFIXES: tuple[str, ...] = (
    "cathedral_",
    "busfactor1_",
    "shared_knowledge_",
    "awakening_",
    "erosion_",
    "stalled_review_",
    "test_orphan_",
)


# Metric-name → family bucket. Each family becomes a section in the editor.
# A knob lands under the family of its FIRST declaring metric in METRICS
# registration order; cross-family consumers surface via the
# ``metric_names`` list on the field.
_METRIC_FAMILY: dict[str, str] = {
    "anomaly.cohesion": "cohesion",
    "anomaly.timezone": "cohesion",
    "anomaly.complexity": "cohesion",
    "anomaly.knowledge": "knowledge",
    "pr.traits": "review",
    "anomaly.quality_issues": "smell",
    "anomaly.structuring": "structuring",
    "anomaly.coupling": "structuring",
    "anomaly.testing": "testing",
    "author.classifiers": "classifiers",
    "commit.classifiers": "classifiers",
    "file.classifiers": "classifiers",
    "issue_pr.classifiers": "classifiers",
    "commit_task_prefixes": "classifiers",
    "component.resolver": "classifiers",
}


# Display order of the families in the editor.
_FAMILY_ORDER: tuple[str, ...] = (
    "cohesion",
    "knowledge",
    "review",
    "smell",
    "structuring",
    "testing",
    "classifiers",
)


class CatalogueField(BaseModel):
    """One editable knob in the catalogue response.

    ``current`` is the value the build path will see right now — either
    the user's override (when present in the overrides dict) or the
    dataclass default. The UI renders ``current`` into the input and
    flags it as "modified" when it differs from ``default``.

    ``metric_names`` lists every metric that reads this knob, so the
    editor can show a "Used by: …" badge.

    ``dx_baseline`` is ``False`` for knobs that feed a ScriptBee-only
    trait (Cathedral / BusFactor1 / SharedKnowledge / Awakening /
    Erosion / StalledReview / TestOrphan).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: str
    default: Any
    current: Any
    metric_names: List[str] = Field(default_factory=list)
    dx_baseline: bool = True


class CatalogueFamily(BaseModel):
    """One family section in the catalogue response."""

    model_config = ConfigDict(extra="forbid")

    name: str
    fields: List[CatalogueField] = Field(default_factory=list)


class CatalogueResponse(BaseModel):
    """GET /projects/{id}/config/catalogue response shape."""

    model_config = ConfigDict(extra="forbid")

    families: List[CatalogueFamily] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Introspection helpers
# ---------------------------------------------------------------------------

def _metric_index() -> dict[str, list[str]]:
    """Build ``field_name → [metric_name, ...]`` from the live registry.

    Metrics appear in their METRICS registration order (insertion-ordered
    dict). The first metric to reference a field is the field's "home"
    family for grouping; the full list is reported in ``metric_names``.
    """
    index: dict[str, list[str]] = {}
    for cls in METRICS:
        for field_name in cls.config_fields:
            index.setdefault(field_name, []).append(cls.name)
    return index


def _home_family(metric_names: list[str]) -> str:
    """Return the family bucket of the first declaring metric.

    Falls back to ``"classifiers"`` for unknown metric names — defensive,
    so adding a metric without updating ``_METRIC_FAMILY`` still produces
    a renderable catalogue (visible misplacement is better than a 500).
    The misconfig is logged so it surfaces in normal operations.
    """
    for metric_name in metric_names:
        family = _METRIC_FAMILY.get(metric_name)
        if family is not None:
            return family
    LOG.warning(
        "no _METRIC_FAMILY entry for any of %r — defaulting to 'classifiers'",
        metric_names,
    )
    return "classifiers"


def _has_no_dx_baseline(field_name: str) -> bool:
    return any(field_name.startswith(p) for p in _NO_DX_BASELINE_PREFIXES)


# Inline-flag glyphs for re flags that survive the JSON round-trip. UNICODE
# is the engine default in Python 3 and never needs to be re-encoded.
_INLINE_FLAG_CHARS: tuple[tuple[int, str], ...] = (
    (re.IGNORECASE, "i"),
    (re.MULTILINE, "m"),
    (re.DOTALL, "s"),
    (re.VERBOSE, "x"),
)


def _serialise_regex(pattern: re.Pattern[str]) -> str:
    """Render a compiled pattern as a self-contained source string.

    Non-default flags (everything except :data:`re.UNICODE`) are encoded
    as an inline ``(?flags)`` prefix so :func:`re.compile` on the round-
    tripped string reproduces the original flag set. Without this the
    default ``NATURE_PATTERNS`` (all ``re.IGNORECASE``) would silently
    become case-sensitive after a save-and-load cycle.
    """
    flags = pattern.flags & ~re.UNICODE
    glyphs = "".join(ch for bit, ch in _INLINE_FLAG_CHARS if flags & bit)
    if not glyphs:
        return pattern.pattern
    return f"(?{glyphs}){pattern.pattern}"


def _serialise_default(value: Any) -> Any:
    """Coerce a dataclass default into a JSON-safe shape for the wire.

    Regex patterns become their source string with non-default flags
    encoded inline (see :func:`_serialise_regex`); tuples become lists.
    The catalogue is read-only metadata, so we ship a representation the
    browser can render — the merge layer does the inverse coercion on
    PUT.
    """
    if isinstance(value, re.Pattern):
        return _serialise_regex(value)
    if isinstance(value, tuple):
        return [_serialise_default(v) for v in value]
    if isinstance(value, list):
        return [_serialise_default(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialise_default(v) for k, v in value.items()}
    return value


def editable_field_names() -> frozenset[str]:
    """Names the PUT validator accepts.

    A field is editable iff (a) some metric's ``config_fields`` references
    it, (b) it exists on :class:`EnrichmentConfig`, AND (c) it isn't in
    ``_HIDDEN_FIELDS``. The router uses this set to 400 on unknown fields.
    """
    referenced = set(_metric_index().keys())
    declared = {f.name for f in dataclass_fields(EnrichmentConfig)}
    return frozenset((referenced & declared) - _HIDDEN_FIELDS)


def build_catalogue(overrides: Optional[Mapping[str, Any]] = None) -> CatalogueResponse:
    """Build the catalogue, resolving ``current`` against ``overrides``.

    ``overrides`` may be ``None`` or empty — in that case ``current``
    equals ``default`` for every field. Unknown fields in ``overrides``
    are ignored here (the catalogue does not validate writes — the
    router does).

    Raises :class:`ValueError` if some metric references a field name
    that doesn't exist on :class:`EnrichmentConfig`. That's a code-level
    contract violation, not user data, so it crashes the catalogue build
    rather than producing a half-empty UI.
    """
    overrides = overrides or {}
    declared: Dict[str, Any] = {f.name: f for f in dataclass_fields(EnrichmentConfig)}
    index = _metric_index()

    fields_by_family: Dict[str, List[CatalogueField]] = {
        name: [] for name in _FAMILY_ORDER
    }

    # Iterate the metric→fields index so families come out in the
    # registration order documented in §3 of the implementation plan.
    # The index is already keyed uniquely by field name (setdefault).
    for field_name, metric_names in index.items():
        if field_name in _HIDDEN_FIELDS:
            continue

        dc_field = declared.get(field_name)
        if dc_field is None:
            raise ValueError(
                f"Metric references unknown EnrichmentConfig field "
                f"{field_name!r} (used by: {metric_names!r}). Add the field "
                f"to EnrichmentConfig or remove it from config_fields."
            )

        default_value = getattr(DEFAULT_CONFIG, field_name)
        current_value = (
            overrides[field_name] if field_name in overrides else default_value
        )

        catalogue_field = CatalogueField(
            name=field_name,
            type=str(dc_field.type),
            default=_serialise_default(default_value),
            current=_serialise_default(current_value),
            metric_names=list(metric_names),
            dx_baseline=not _has_no_dx_baseline(field_name),
        )
        family = _home_family(metric_names)
        fields_by_family.setdefault(family, []).append(catalogue_field)

    return CatalogueResponse(
        families=[
            CatalogueFamily(name=family_name, fields=fields_by_family[family_name])
            for family_name in _FAMILY_ORDER
            if fields_by_family[family_name]  # drop empty families
        ]
    )


__all__ = [
    "CatalogueField",
    "CatalogueFamily",
    "CatalogueResponse",
    "build_catalogue",
    "editable_field_names",
]
