"""Git-domain v2 entities, registries, and transformer.

See plan §4 (Source-domain entities) and §9 (Recipe — adding a new data
source). The public surface here is intentionally narrow: every concrete
class downstream code (Chunks 7/8 + the MCP sandbox) needs is re-exported.
"""
from __future__ import annotations

from .models import (
    Change,
    ChangeType,
    Commit,
    File,
    GitAccount,
    GitProject,
    Hunk,
    LineChange,
    LineOperation,
)
from .registries import (
    ChangeRegistry,
    CommitRegistry,
    FileRegistry,
    GitAccountRegistry,
    GitProjectRegistry,
    HunkRegistry,
)


# Lazy export of the transformer (PEP 562). Chunk 8's typed Graph imports
# this package's registries during the kernel-package initialization
# chain; if the transformer were imported eagerly here, that would cycle
# back through ``common.domains.transformer`` while it's mid-load. The
# lazy hook keeps ``from src.common.domains.git import GitTransformer``
# working for tests / external callers without forcing the transformer
# onto the kernel boot path.
def __getattr__(name):  # PEP 562
    if name == "GitTransformer":
        from .transformer import GitTransformer

        return GitTransformer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # models
    "Change",
    "ChangeType",
    "Commit",
    "File",
    "GitAccount",
    "GitProject",
    "Hunk",
    "LineChange",
    "LineOperation",
    # registries
    "ChangeRegistry",
    "CommitRegistry",
    "FileRegistry",
    "GitAccountRegistry",
    "GitProjectRegistry",
    "HunkRegistry",
    # transformer
    "GitTransformer",
]
