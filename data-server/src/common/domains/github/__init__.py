"""GitHub-domain v2 entities, registries, and transformer.

See plan §4 (Source-domain entities) and §9 (Recipe — adding a new data
source). The public surface here is intentionally narrow: every concrete
class downstream code (Chunks 7/8 + the MCP sandbox) needs is re-exported.
"""
from __future__ import annotations

from .models import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
    Review,
    ReviewComment,
)
from .registries import (
    GitHubCommitRegistry,
    GitHubProjectRegistry,
    GitHubUserRegistry,
    PullRequestRegistry,
    ReviewCommentRegistry,
    ReviewRegistry,
)


# Lazy transformer export — see Chunk 8 note in the git/__init__.py twin.
def __getattr__(name):  # PEP 562
    if name == "GitHubTransformer":
        from .transformer import GitHubTransformer

        return GitHubTransformer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # models
    "GitHubCommit",
    "GitHubProject",
    "GitHubUser",
    "PullRequest",
    "Review",
    "ReviewComment",
    # registries
    "GitHubCommitRegistry",
    "GitHubProjectRegistry",
    "GitHubUserRegistry",
    "PullRequestRegistry",
    "ReviewCommentRegistry",
    "ReviewRegistry",
    # transformer
    "GitHubTransformer",
]
