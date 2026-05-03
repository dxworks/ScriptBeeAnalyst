"""Static metric catalog — reflects on the enrichment subpackages.

The AI assistant calls `/enrichments/catalog` (or `list_metrics()` on the MCP
server) to get a live, code-derived inventory of every classifier slot,
anomaly trait, relation kind, and overview table the system computes. Built
by walking `src.enrichment.{tagger, relations, overview}` and reading the
class attributes (`TRAITS` / `CLASSIFIERS` / `KIND` / `NAME` / `ENTITY_KIND`)
plus class docstrings — so a new metric appears in the catalog as soon as
the class lands in code.

The catalog does NOT require a project to be loaded — it's pure
introspection. Use it to discover what exists; use the per-metric source file
(linked in each entry) to learn what the metric *means* and the
`config_fields` list to find threshold values in `EnrichmentConfig`.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import re
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, Iterator

from src.enrichment import config as enrichment_config
from src.enrichment.config import EnrichmentConfig


# Repo-relative anchor for source_file paths. Resolves to data-server/.
_DATA_SERVER_ROOT = Path(__file__).resolve().parents[2]


def build_metric_catalog() -> dict[str, Any]:
    """Walk the enrichment subpackages and return the live metric catalog."""
    classifiers: list[dict] = []
    traits: list[dict] = []
    relations: list[dict] = []
    overviews: list[dict] = []

    cfg_field_names = [f.name for f in dataclass_fields(EnrichmentConfig)]

    for cls in _iter_metric_classes("src.enrichment.tagger"):
        decl_classifiers = getattr(cls, "CLASSIFIERS", None)
        decl_traits = getattr(cls, "TRAITS", None)

        if decl_classifiers:
            for entry in decl_classifiers:
                classifiers.append({
                    "slot": entry["slot"],
                    "entity": entry["entity"],
                    "values": list(entry.get("values", [])),
                    "tagger": cls.__name__,
                    "source_file": _source_path(cls),
                    "docstring": _doc_for(cls),
                })

        if decl_traits:
            for entry in decl_traits:
                trait_name = entry["name"]
                traits.append({
                    "name": trait_name,
                    "entity": entry["entity"],
                    "family": entry.get("family"),
                    "tagger": cls.__name__,
                    "source_file": _source_path(cls),
                    "docstring": _doc_for(cls),
                    "config_fields": _config_fields_for_trait(
                        trait_name, cfg_field_names,
                    ),
                })

    for cls in _iter_metric_classes("src.enrichment.relations"):
        kind = getattr(cls, "KIND", None)
        if not kind:
            continue
        source, target = _parse_relation_endpoints(kind)
        relations.append({
            "kind": kind,
            "source_kind": source,
            "target_kind": target,
            "extractor": cls.__name__,
            "source_file": _source_path(cls),
            "docstring": _doc_for(cls),
        })

    for cls in _iter_metric_classes("src.enrichment.overview"):
        name = getattr(cls, "NAME", None)
        if not name:
            continue
        overviews.append({
            "name": name,
            "entity_kind": getattr(cls, "ENTITY_KIND", None),
            "builder": cls.__name__,
            "source_file": _source_path(cls),
            "docstring": _doc_for(cls),
            "columns": _module_columns(cls),
        })

    sandbox_helpers = [
        {
            "name": "find_files_with_trait",
            "signature": "(trait_name: str) -> list[str]",
            "purpose": "Return file ids carrying a trait, e.g. 'anomaly.testing.BugMagnet'.",
        },
        {
            "name": "cochange_neighbors",
            "signature": "(file_id: str, window: str = 'lifetime', limit: int = 10) -> list[tuple[str, float]]",
            "purpose": "Top-N files most often co-changed with the given file (kind='cochange.file-file').",
        },
        {
            "name": "overview_as_dict",
            "signature": "(name: str) -> dict",
            "purpose": "Fetch an OverviewTable as nested dict for in-sandbox querying.",
        },
    ]

    classifiers.sort(key=lambda c: (c["entity"], c["slot"]))
    traits.sort(key=lambda t: (t.get("family") or "", t["name"], t["entity"]))
    relations.sort(key=lambda r: r["kind"])
    overviews.sort(key=lambda o: o["name"])

    return {
        "classifiers": classifiers,
        "traits": traits,
        "relations": relations,
        "overviews": overviews,
        "helpers": sandbox_helpers,
        "source_roots": {
            "data_models": "src/common/",
            "enrichment_taggers": "src/enrichment/tagger/",
            "enrichment_relations": "src/enrichment/relations/",
            "enrichment_overviews": "src/enrichment/overview/",
            "pipeline": "src/enrichment/pipeline.py",
            "config": "src/enrichment/config.py",
        },
        "counts": {
            "classifiers": len(classifiers),
            "traits": len(traits),
            "relations": len(relations),
            "overviews": len(overviews),
        },
    }


def _iter_metric_classes(package_name: str) -> Iterator[type]:
    """Yield every class defined inside any submodule of `package_name`."""
    package = importlib.import_module(package_name)
    package_path = getattr(package, "__path__", None)
    if package_path is None:
        return

    for module_info in pkgutil.iter_modules(package_path):
        if module_info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package_name}.{module_info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            # Skip classes re-imported from other modules — only yield those
            # defined in the module we're walking.
            if obj.__module__ != module.__name__:
                continue
            yield obj


def _source_path(cls: type) -> str:
    try:
        path = Path(inspect.getsourcefile(cls) or "").resolve()
    except (TypeError, OSError):
        return ""
    try:
        return str(path.relative_to(_DATA_SERVER_ROOT))
    except ValueError:
        return str(path)


def _doc_for(cls: type) -> str:
    """Class docstring, falling back to the module docstring.

    Many metric files keep their conceptual docs at module level (one paragraph
    explaining the family) rather than repeating them on the class. The
    registry surfaces whichever is present so the AI sees real prose either way.
    """
    if cls.__doc__:
        return inspect.cleandoc(cls.__doc__)
    module = inspect.getmodule(cls)
    if module and module.__doc__:
        return inspect.cleandoc(module.__doc__)
    return ""


def _module_columns(cls: type) -> list[str]:
    """Return the module-level COLUMNS constant if present (overview tables)."""
    module = inspect.getmodule(cls)
    if module is None:
        return []
    cols = getattr(module, "COLUMNS", None)
    if isinstance(cols, list):
        return list(cols)
    return []


def _parse_relation_endpoints(kind: str) -> tuple[str | None, str | None]:
    """Parse a relation kind into (source, target) entity types.

    Handles two conventions:
      `family.<source>-<target>[.modifier]` (e.g. cochange.file-file.shared-devs)
      `<source>.<target>`                   (e.g. issue.file, pr.reviewer)

    Returns (None, None) if neither shape matches. Used for UI hints; the
    canonical truth lives in each Relation row's source_kind / target_kind.
    """
    parts = kind.split(".")
    for part in parts:
        if "-" in part:
            left, right = part.split("-", 1)
            return _normalise_endpoint(left), _normalise_endpoint(right)
    if len(parts) >= 2:
        return _normalise_endpoint(parts[0]), _normalise_endpoint(parts[1])
    return None, None


_ENDPOINT_ALIASES = {
    "files": "file", "authors": "author", "issues": "issue",
    "prs": "pr", "components": "component", "users": "author",
    "reviewer": "author",
}


def _normalise_endpoint(token: str) -> str:
    token = token.strip().lower()
    return _ENDPOINT_ALIASES.get(token, token)


def _config_fields_for_trait(
    trait_name: str, cfg_field_names: list[str],
) -> list[str]:
    """Best-effort match of trait name → EnrichmentConfig fields.

    Strategy (first non-empty result wins):
      1. snake_case prefix of the leaf trait name (`PolarisedOwnership`
         → fields starting with `polarised_ownership_`).
      2. contiguous lowercase prefix (`busfactor1_*` matches `BusFactor1`).
      3. depluralised contiguous prefix (`OrphanCausers` → `orphancauser_*`).
      4. head-token prefix (`PolarisedOwnership` → `polarised_*`) — only when
         the head is at least 4 characters so single-word noise like `pr_*`
         doesn't get pulled in.

    Returns [] if nothing matches. The result is a hint to the AI, not a
    guarantee — some traits (e.g. BusFactor1 also reads `hermit_dominance_ratio`)
    use config fields whose names don't share a token with the trait.
    """
    leaf = trait_name.rsplit(".", 1)[-1]
    if not leaf:
        return []

    snake = _camel_to_snake(leaf)
    contiguous = leaf.lower()

    candidates: list[str] = [snake, contiguous]
    if contiguous.endswith("s"):
        candidates.append(contiguous[:-1])
    if snake.endswith("s"):
        candidates.append(snake[:-1])

    for cand in candidates:
        if not cand:
            continue
        matches = [
            f for f in cfg_field_names
            if f == cand or f.startswith(f"{cand}_")
        ]
        if matches:
            return matches

    head = snake.split("_", 1)[0]
    if len(head) >= 4:
        return [
            f for f in cfg_field_names
            if f == head or f.startswith(f"{head}_")
        ]

    return []


_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def _camel_to_snake(name: str) -> str:
    return _CAMEL_BOUNDARY.sub("_", name).lower()
