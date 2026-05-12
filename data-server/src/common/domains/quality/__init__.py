"""Quality-domain v2 entities, registries, and transformer.

See plan §4 (source-domain entities) and §9 (Sonar replaces Insider).
"""
from __future__ import annotations

from .models import QualityIssue, QualityProject, QualitySourceTool
from .registries import (
    QualityIssueRegistry,
    QualityProjectRegistry,
)
from .transformer import QualityTransformer

__all__ = [
    # models
    "QualityIssue",
    "QualityProject",
    "QualitySourceTool",
    # registries
    "QualityIssueRegistry",
    "QualityProjectRegistry",
    # transformer
    "QualityTransformer",
]
