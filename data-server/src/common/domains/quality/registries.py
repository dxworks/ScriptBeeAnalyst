"""Registries for every quality-domain :class:`Entity` subclass.

Per plan §1.5, indexes are declared as a ``ClassVar[list[IndexSpec]]`` and
rebuilt on every mutation / on :meth:`Registry.load` — they are NOT pickled.
"""
from __future__ import annotations

from ...kernel import IndexSpec, Registry
from .models import QualityIssue, QualityProject


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------


class QualityProjectRegistry(Registry[QualityProject, str]):
    """Holds every :class:`QualityProject` in the graph.

    Indexes:

    * ``by_name``        — name lookup, parallels other domains.
    * ``by_source_tool`` — quick "all Insider projects" / "all Sonar
                           projects" filter. Useful when the metric layer
                           wants to skip tool-specific metrics
                           (Sonar-severity-only ones etc.).
    """

    indexes = [
        IndexSpec(name="by_name", key_fn=lambda p: p.name, multi=True),
        IndexSpec(
            name="by_source_tool", key_fn=lambda p: p.source_tool, multi=True
        ),
    ]

    def get_id(self, entity: QualityProject) -> str:
        return entity.id


# ---------------------------------------------------------------------------
# Quality issues
# ---------------------------------------------------------------------------


def _severity_key(i: QualityIssue):
    """Skip ``None`` severities from the by_severity index.

    Insider issues never carry a severity, so they'd otherwise all land
    in a ``None`` bucket, making the index near-useless for Sonar-driven
    queries. ``None`` keys are auto-skipped per kernel
    ``_normalize_keys`` semantics.
    """
    return i.severity


class QualityIssueRegistry(Registry[QualityIssue, str]):
    """Every :class:`QualityIssue` in the graph.

    Indexes:

    * ``by_file``     — fast "all issues on file F" — the most common
                        query for the file-level dashboard.
    * ``by_project``  — one bucket per :class:`QualityProject`.
    * ``by_rule_id``  — "all violations of rule X across the project"
                        (the metric layer uses this for hotspot
                        detection).
    * ``by_severity`` — Sonar's severity ordinal; ``None`` keys skipped.
    * ``by_category`` — group by category bucket (Insider's family or
                        Sonar's rule category).
    """

    indexes = [
        IndexSpec(name="by_file", key_fn=lambda i: i.file_ref, multi=True),
        IndexSpec(name="by_project", key_fn=lambda i: i.project_ref, multi=True),
        IndexSpec(name="by_rule_id", key_fn=lambda i: i.rule_id, multi=True),
        IndexSpec(name="by_severity", key_fn=_severity_key, multi=True),
        IndexSpec(name="by_category", key_fn=lambda i: i.category, multi=True),
    ]

    def get_id(self, entity: QualityIssue) -> str:
        return entity.id


__all__ = [
    "QualityIssueRegistry",
    "QualityProjectRegistry",
]
