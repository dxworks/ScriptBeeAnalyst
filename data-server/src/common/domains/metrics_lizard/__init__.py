"""Lizard-metrics-domain v2 entities, registries, and transformer.

See plan §4 (source-domain entities) and §9 (transformer contract).
"""
from __future__ import annotations

from .models import FileMetric, FunctionMetric, LizardMetricsProject
from .registries import (
    FileMetricRegistry,
    LizardMetricsProjectRegistry,
)


# Lazy transformer export — see Chunk 8 note in the git/__init__.py twin.
def __getattr__(name):  # PEP 562
    if name == "LizardMetricsTransformer":
        from .transformer import LizardMetricsTransformer

        return LizardMetricsTransformer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # models
    "FileMetric",
    "FunctionMetric",
    "LizardMetricsProject",
    # registries
    "FileMetricRegistry",
    "LizardMetricsProjectRegistry",
    # transformer
    "LizardMetricsTransformer",
]
