"""Overlay per-project overrides onto :class:`EnrichmentConfig`.

The override JSONB stores values in their JSON-native shape (no tuples,
no compiled regex), but ``EnrichmentConfig`` declares typed Python
shapes (``tuple[str, ...]``, ``list[tuple[str, int]]``,
``list[Pattern[str]]`` …). This module bridges the two: one tiny
coercion function per shape, composed by :func:`apply_overrides`.

Coercion is **strict**. If an override's shape disagrees with the
declared type — e.g. a string where an int is expected, a regex source
that fails to compile, a tuple-bucket that isn't ``[name, int]`` —
:class:`OverrideCoercionError` is raised with the offending field name
in the message. Silent fallback to the default would mask user-visible
data corruption; the router translates the error into a 422.

The merge sits in front of the pipeline: the build path calls
:func:`apply_overrides`, hands the returned :class:`EnrichmentConfig`
to ``run_pipeline``, and every existing metric reads the field via
``getattr`` with zero changes.
"""
from __future__ import annotations

import re
from dataclasses import fields as dataclass_fields, replace
from typing import Any, Mapping

from src.enrichment.config import EnrichmentConfig
from src.logger import get_logger

LOG = get_logger(__name__)


class OverrideCoercionError(ValueError):
    """Raised when an override value's shape doesn't match its declared type.

    Carries the offending ``field`` name so the router can build a 422
    payload pointing the UI at the specific input.
    """

    def __init__(self, field: str, message: str) -> None:
        super().__init__(f"{field}: {message}")
        self.field = field


def _coerce_bool(field: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise OverrideCoercionError(field, f"expected bool, got {type(value).__name__}")
    return value


def _coerce_int(field: str, value: Any) -> int:
    # JSON has no separate int type; pydantic-decoded ints come in as int.
    # bool is an int subclass in Python — reject explicitly.
    if isinstance(value, bool) or not isinstance(value, int):
        raise OverrideCoercionError(field, f"expected int, got {type(value).__name__}")
    return value


def _coerce_float(field: str, value: Any) -> float:
    # Accept ints as floats (JSON 0.8 stays 0.8; JSON 1 widens to 1.0).
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise OverrideCoercionError(field, f"expected number, got {type(value).__name__}")
    return float(value)


def _coerce_str(field: str, value: Any) -> str:
    if not isinstance(value, str):
        raise OverrideCoercionError(field, f"expected string, got {type(value).__name__}")
    return value


def _coerce_str_tuple(field: str, value: Any) -> tuple[str, ...]:
    """``tuple[str, ...]`` — JSON arrives as ``list[str]``."""
    if not isinstance(value, (list, tuple)):
        raise OverrideCoercionError(field, f"expected array, got {type(value).__name__}")
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise OverrideCoercionError(
                field, f"element {i} expected string, got {type(item).__name__}"
            )
        out.append(item)
    return tuple(out)


def _coerce_issue_age_buckets(field: str, value: Any) -> list[tuple[str, int]]:
    """``list[tuple[str, int]]`` — JSON arrives as ``list[list]`` or ``list[dict]``.

    Accept both: ``[["<1w", 7], ...]`` (compact wire shape) and
    ``[{"label": "<1w", "max_days": 7}, ...]`` (form-editor shape). The
    UI sends the dict shape; the wire-shape branch keeps round-trip
    sanity in tests.
    """
    if not isinstance(value, list):
        raise OverrideCoercionError(field, f"expected array, got {type(value).__name__}")
    out: list[tuple[str, int]] = []
    for i, item in enumerate(value):
        label: Any
        bound: Any
        if isinstance(item, dict):
            label = item.get("label")
            bound = item.get("max_days")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            label, bound = item[0], item[1]
        else:
            raise OverrideCoercionError(
                field, f"element {i} expected [label, max_days] or {{label, max_days}}"
            )
        if not isinstance(label, str):
            raise OverrideCoercionError(
                field, f"element {i} 'label' expected string, got {type(label).__name__}"
            )
        if isinstance(bound, bool) or not isinstance(bound, int):
            raise OverrideCoercionError(
                field, f"element {i} 'max_days' expected int, got {type(bound).__name__}"
            )
        out.append((label, bound))
    return out


def _coerce_daytime_buckets(field: str, value: Any) -> dict[str, tuple[int, int]]:
    """``dict[str, tuple[int, int]]`` — JSON object → dict of two-int tuples."""
    if not isinstance(value, dict):
        raise OverrideCoercionError(field, f"expected object, got {type(value).__name__}")
    out: dict[str, tuple[int, int]] = {}
    for label, bounds in value.items():
        if not isinstance(label, str):
            raise OverrideCoercionError(field, f"bucket key expected string, got {type(label).__name__}")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 2:
            raise OverrideCoercionError(
                field, f"bucket {label!r} expected [start_hour, end_hour]"
            )
        start, end = bounds
        if isinstance(start, bool) or not isinstance(start, int):
            raise OverrideCoercionError(field, f"bucket {label!r} start_hour expected int")
        if isinstance(end, bool) or not isinstance(end, int):
            raise OverrideCoercionError(field, f"bucket {label!r} end_hour expected int")
        out[label] = (start, end)
    return out


def _coerce_regex_list(field: str, value: Any) -> list[re.Pattern[str]]:
    """``list[Pattern[str]]`` — JSON arrives as ``list[str]``; recompile each."""
    if not isinstance(value, list):
        raise OverrideCoercionError(field, f"expected array of regex strings, got {type(value).__name__}")
    out: list[re.Pattern[str]] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise OverrideCoercionError(
                field, f"element {i} expected regex string, got {type(item).__name__}"
            )
        try:
            out.append(re.compile(item))
        except re.error as exc:
            raise OverrideCoercionError(
                field, f"element {i} is not a valid regex: {exc}"
            ) from exc
    return out


def _coerce_nature_patterns(field: str, value: Any) -> list[tuple[str, re.Pattern[str]]]:
    """``list[tuple[str, Pattern[str]]]`` — JSON as ``list[{label, regex}]`` or ``list[[label, regex]]``."""
    if not isinstance(value, list):
        raise OverrideCoercionError(field, f"expected array, got {type(value).__name__}")
    out: list[tuple[str, re.Pattern[str]]] = []
    for i, item in enumerate(value):
        label: Any
        pattern: Any
        if isinstance(item, dict):
            label = item.get("label")
            pattern = item.get("regex")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            label, pattern = item[0], item[1]
        else:
            raise OverrideCoercionError(
                field, f"element {i} expected [label, regex] or {{label, regex}}"
            )
        if not isinstance(label, str):
            raise OverrideCoercionError(
                field, f"element {i} 'label' expected string, got {type(label).__name__}"
            )
        if not isinstance(pattern, str):
            raise OverrideCoercionError(
                field, f"element {i} 'regex' expected string, got {type(pattern).__name__}"
            )
        try:
            out.append((label, re.compile(pattern)))
        except re.error as exc:
            raise OverrideCoercionError(
                field, f"element {i} is not a valid regex: {exc}"
            ) from exc
    return out


# Field-name → coercion function. Dataclass `f.type` is a string (because
# config.py uses `from __future__ import annotations`), so we route by the
# normalised type-string with a few field-name-specific carve-outs for the
# composite shapes.
_COERCERS_BY_TYPE: dict[str, Any] = {
    "bool": _coerce_bool,
    "int": _coerce_int,
    "float": _coerce_float,
    "str": _coerce_str,
    "Optional[str]": _coerce_str,
    "tuple[str, ...]": _coerce_str_tuple,
    "list[tuple[str, int]]": _coerce_issue_age_buckets,
    "dict[str, tuple[int, int]]": _coerce_daytime_buckets,
    "list[Pattern[str]]": _coerce_regex_list,
    "list[tuple[str, Pattern[str]]]": _coerce_nature_patterns,
}


def _coerce_value(field_name: str, declared_type: str, value: Any) -> Any:
    coercer = _COERCERS_BY_TYPE.get(declared_type)
    if coercer is None:
        raise OverrideCoercionError(
            field_name, f"no coercer for declared type {declared_type!r}"
        )
    return coercer(field_name, value)


def apply_overrides(
    base: EnrichmentConfig, overrides: Mapping[str, Any]
) -> EnrichmentConfig:
    """Return a clone of ``base`` with each ``overrides[name]`` applied.

    Unknown keys are skipped with a warning — defensive in case a field
    was removed from :class:`EnrichmentConfig` without the override row
    being migrated. The build path must stay alive on stale data;
    validation belongs in the PUT endpoint.

    Shape mismatches on KNOWN keys raise :class:`OverrideCoercionError`
    — that signals genuine data corruption (somebody hand-edited the
    JSONB outside the PUT validator).
    """
    if not overrides:
        return base

    declared: dict[str, Any] = {f.name: f for f in dataclass_fields(EnrichmentConfig)}
    coerced: dict[str, Any] = {}

    for name, raw_value in overrides.items():
        dc_field = declared.get(name)
        if dc_field is None:
            LOG.warning(
                "config override references unknown field %r — skipping", name
            )
            continue
        coerced[name] = _coerce_value(name, str(dc_field.type), raw_value)

    if not coerced:
        return base
    return replace(base, **coerced)


__all__ = [
    "OverrideCoercionError",
    "apply_overrides",
]
