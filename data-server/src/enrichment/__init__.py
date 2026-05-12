"""Enrichment layer: tags, relations, metrics, overview tables.

The v2 pipeline (``run_pipeline``) walks the typed :class:`~src.common.kernel.Graph`,
populating ``graph.traits`` / ``graph.classifiers`` / ``graph.relations`` and
``graph.components`` via the :mod:`metric` / :mod:`relation` registries.
Overview tables (``src.enrichment.overviews``) consume the enriched graph.
"""

from src.enrichment.pipeline import (
    PipelineError,
    PipelineHost,
    PipelineResult,
    run_pipeline,
)

__all__ = [
    "PipelineError",
    "PipelineHost",
    "PipelineResult",
    "run_pipeline",
]
