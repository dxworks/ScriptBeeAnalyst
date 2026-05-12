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
from .transformer import GitTransformer

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
