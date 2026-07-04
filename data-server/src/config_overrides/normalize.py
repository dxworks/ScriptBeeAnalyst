"""Canonicalise router-bound override payloads before persistence.

The merge layer accepts two shapes for composite fields (compact
``[label, value]`` lists AND descriptive ``{label, value}`` dicts) as
defense-in-depth for hand-edited JSONB. The router writes ONE shape so
storage stays consistent: the compact list form, which matches what
:func:`catalogue._serialise_default` ships on the way out — meaning the
catalogue's ``current`` field round-trips through storage without any
client-side re-shaping.

This module is intentionally separate from :mod:`merge`: merge stays
tolerant on read, normalize stays strict on write. Keeping them apart
prevents drift where someone "simplifies" the merge layer and
accidentally also removes the defensive read path.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping


# Field names whose canonical wire shape is ``list[[label, value]]``.
# Both keys take the same {label, regex/max_days} → [label, value] flattening.
_LIST_OF_PAIRS_FIELDS: frozenset[str] = frozenset({
    "issue_age_buckets",  # dict key: "max_days"
    "nature_patterns",    # dict key: "regex"
})


def _pair_dict_to_list(item: Any) -> Any:
    """Flatten ``{label, regex|max_days}`` into ``[label, value]``.

    Lists and other shapes pass through unchanged — the coercion layer
    in :mod:`merge` will reject anything not matching the two expected
    shapes when the merged config is materialised.
    """
    if not isinstance(item, dict):
        return item
    if "label" not in item:
        return item
    if "max_days" in item:
        return [item["label"], item["max_days"]]
    if "regex" in item:
        return [item["label"], item["regex"]]
    return item


def normalize_for_storage(overrides: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a deep-shallow copy of ``overrides`` with composite shapes flattened.

    Only the two known list-of-pair fields are reshaped; every other
    field passes through verbatim. The router calls this once on the way
    in; downstream layers (repository, merge, catalogue) only ever see
    the compact list form for these two fields.
    """
    normalized: Dict[str, Any] = {}
    for name, value in overrides.items():
        if name in _LIST_OF_PAIRS_FIELDS and isinstance(value, list):
            normalized[name] = [_pair_dict_to_list(item) for item in value]
        else:
            normalized[name] = value
    return normalized


__all__ = ["normalize_for_storage"]
