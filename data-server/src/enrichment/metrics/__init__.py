"""Metrics — pluggable graph analytics.

Public API for Chunks 7+ (metric port + MCP sandbox)::

    from src.enrichment.metrics import (
        Metric, MetricInputs, MetricOutputs, MetricOutput,
        MetricRegistry, METRICS,
    )

See §7 of ``architectural_changes.md`` and the recipe in §10.
"""
from __future__ import annotations

from .base import Metric, MetricInputs, MetricOutput, MetricOutputs
from .registry import METRICS, MetricRegistry

__all__ = [
    "Metric",
    "MetricInputs",
    "MetricOutput",
    "MetricOutputs",
    "MetricRegistry",
    "METRICS",
]
