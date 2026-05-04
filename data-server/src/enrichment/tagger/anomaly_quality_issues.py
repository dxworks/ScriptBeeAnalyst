"""anomaly.codesmell.{Category}.{Rule} — per-rule code-smell traits from Insider.

Implements §7.1 of communication/B4_sonar_insider/index_step_general.md, with
the data-format corrections from index_step_data_format.md (no severity
normalisation; surface raw `occurrence_count` via evidence).

Per orchestrator decision (B4):
  * Trait taxonomy is data-driven: emit traits dynamically based on the
    (category, rule) pairs that actually appear in the input. The Insider
    catalogue is much larger than the 5 rules Zeppelin happens to surface,
    and the registry will reflect whatever the tagger emits when it runs.
  * No severity translation: `evidence.occurrence_count` carries Insider's
    raw `value`. SonarQube (when added) can introduce its own severity
    translation in a separate tagger or by extending this one.
"""
from __future__ import annotations

from typing import Iterable

from src.enrichment.models import EntityTags, Trait
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id


class QualityIssuesTagger:
    """Emit `anomaly.codesmell.{Category}.{Rule}` traits per file.

    Reads `ctx.graph_data['quality_issues']` (a `QualityIssues` container) and
    joins each issue against the git File registry by `file_path`. Files in
    the issue payload that are not found in git are still tagged using the
    raw issue path as the entity id, so an unmatched file ends up
    discoverable via `find_files_with_trait` instead of silently dropped.

    For each (file, category, rule) bin the tagger emits one Trait whose
    severity = the summed `occurrence_count` for the bin (typically equal to
    the single record's value because Insider already aggregates per file/rule).
    Evidence carries `occurrence_count`, `record_count`, `category`,
    `rule_name`, `basis="insider"` so the AI can caveat findings.
    """

    # WHY: TRAITS is intentionally an empty list — the trait names are
    # discovered at run time from the data (§B4 orchestrator decision). The
    # registry walks this attribute when it exists, so leaving it empty keeps
    # the tagger discoverable without claiming a fixed enumeration that the
    # broader Insider rule catalogue would falsify.
    TRAITS: list[dict] = []

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        qi = ctx.graph_data.get("quality_issues")
        if qi is None or not getattr(qi, "issues", None):
            return []

        # Resolve git file ids once so we can map issue paths onto whatever
        # canonical file id `_file_id(...)` returns for the matching git File.
        # When git is absent (quality-only ingest) we still emit traits using
        # the raw Insider path as the entity id.
        git = ctx.graph_data.get("git")
        git_file_ids: set[str] = set()
        if git is not None:
            for f in git.file_registry.all:
                fid = _file_id(f)
                if fid is not None:
                    git_file_ids.add(fid)

        # Bin issues by (file, category, rule).
        bins: dict[tuple[str, str, str], list] = {}
        for issue in qi.issues:
            key = (issue.file_path, issue.category, issue.rule_name)
            bins.setdefault(key, []).append(issue)

        # Emit one EntityTags per file, with one Trait per (category, rule).
        per_file_traits: dict[str, list[Trait]] = {}
        for (file_path, category, rule_name), records in bins.items():
            occurrence_count = sum(r.occurrence_count for r in records)
            trait_name = _trait_name(category, rule_name)
            trait = make_trait(
                trait_name,
                family="codesmell",
                severity=float(occurrence_count),
                category=category,
                rule_name=rule_name,
                occurrence_count=occurrence_count,
                record_count=len(records),
                basis=qi.source,
                git_matched=file_path in git_file_ids,
            )
            per_file_traits.setdefault(file_path, []).append(trait)

        return [
            EntityTags(entity_kind="file", entity_id=fid, traits=traits)
            for fid, traits in per_file_traits.items()
        ]


def _trait_name(category: str, rule_name: str) -> str:
    """Build `anomaly.codesmell.{Category}.{Rule}` with whitespace stripped.

    Insider rule names contain spaces (e.g. "Stub Implementer"); the trait
    namespace stays dot-separated and PascalCase for parity with existing
    `anomaly.*` traits. Categories arrive PascalCase already in the
    Zeppelin run; we still sanitise to be safe across the wider catalogue.
    """
    cat = _sanitise_segment(category)
    rule = _sanitise_segment(rule_name)
    return f"anomaly.codesmell.{cat}.{rule}"


def _sanitise_segment(segment: str) -> str:
    """Strip non-alphanumeric characters from a single trait-name segment.

    Preserves the original capitalisation (so `Stub Implementer` becomes
    `StubImplementer`, not `stubimplementer`). Drops everything that is not
    a letter or digit so spaces, dots, hyphens and other punctuation cannot
    leak into the dot-separated trait namespace (e.g. Insider's
    `Catch Top-Level Exception` becomes `CatchTopLevelException`).
    """
    return "".join(ch for ch in segment if ch.isalnum())
