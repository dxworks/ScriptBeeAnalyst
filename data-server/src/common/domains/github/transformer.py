"""GitHub-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`GitHubTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":         GitHubProject(...),
          "users":           Iterable[GitHubUser],
          "pull_requests":   Iterable[PullRequest],
          "reviews":         Iterable[Review],
          "review_comments": Iterable[ReviewComment],
          "commits":         Iterable[GitHubCommit],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper). This
   transformer only declares the bucket specs.

2. **Raw GitHub JSON DTO** — a ``JsonFileFormatGithub`` / dict / bytes.
   Same situation as the git and jira domains: the wiring that walks
   the raw payload and assembles entities is the processor's concern,
   not the per-domain transformer's. Calling ``transform(raw)`` with
   anything other than a Mapping today raises
   ``NotImplementedError`` with the explicit "Chunk 8 plumbs the
   linker rewrite" message.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people import SourceKind
from ..transformer import Transformer, TransformResult
from .models import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
    Review,
    ReviewComment,
)


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "users": (EntityKind.GITHUB_USER, GitHubUser),
    "pull_requests": (EntityKind.PULL_REQUEST, PullRequest),
    "reviews": (EntityKind.REVIEW, Review),
    "review_comments": (EntityKind.REVIEW_COMMENT, ReviewComment),
    "commits": (EntityKind.GITHUB_COMMIT, GitHubCommit),
}


class GitHubTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``github`` source.

    See module docstring for the two accepted raw shapes. Subclassing is
    not expected — the transformer is stateless and instantiated by the
    processor (Chunk 8) once per build.
    """

    source: ClassVar[SourceKind] = SourceKind.GITHUB

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, GitHubProject, _BUCKET_SPECS)
        # Anything else (e.g. a parsed ``JsonFileFormatGithub``) needs the
        # linker rewrite — see module docstring + Chunk 5 handoff for the
        # rationale.
        raise NotImplementedError(
            "GitHubTransformer.transform(raw) accepts only an entity-bundle "
            "Mapping today (see module docstring). The raw-DTO path will "
            "be wired in Chunk 8 alongside the processor rewrite."
        )


__all__ = ["GitHubTransformer"]
