"""App-Inspector domain v2 entities, registries, and transformer.

See plan §4 (source-domain entities) and Task 7 (App Inspector ingestion).
"""
from __future__ import annotations

from .models import AppInspectorProject, AppTag, AppInspectorSourceTool
from .registries import (
    AppInspectorProjectRegistry,
    AppTagRegistry,
)


# Lazy transformer export — see Chunk 8 note in the git/__init__.py twin.
def __getattr__(name):  # PEP 562
    if name == "AppInspectorTransformer":
        from .transformer import AppInspectorTransformer

        return AppInspectorTransformer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # models
    "AppInspectorProject",
    "AppTag",
    "AppInspectorSourceTool",
    # registries
    "AppInspectorProjectRegistry",
    "AppTagRegistry",
    # transformer
    "AppInspectorTransformer",
]
