"""Jira-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`JiraTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":         JiraProject(...),
          "users":           Iterable[JiraUser],
          "issues":          Iterable[Issue],
          "issue_statuses":  Iterable[IssueStatus],
          "issue_types":     Iterable[IssueType],
      }

   The validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 shared helper). This
   transformer only declares the bucket specs.

2. **Raw Jira JSON DTO** — a ``JsonFileFormatJira`` / dict / bytes.
   Same situation as the git domain: the wiring that walks the raw
   payload and assembles entities (the legacy
   ``jira_miner.linker.transformers`` plus its registry-building loop)
   is the processor's concern, not the per-domain transformer's. Calling
   ``transform(raw)`` with anything other than a Mapping today raises
   ``NotImplementedError`` with the explicit "Chunk 8 plumbs the linker
   rewrite" message.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people.source import SourceKind
from ..transformer import Transformer, TransformResult
from .models import Issue, IssueStatus, IssueType, JiraProject, JiraUser


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "users": (EntityKind.JIRA_USER, JiraUser),
    "issues": (EntityKind.ISSUE, Issue),
    "issue_statuses": (EntityKind.ISSUE_STATUS, IssueStatus),
    "issue_types": (EntityKind.ISSUE_TYPE, IssueType),
}


class JiraTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``jira`` source.

    See module docstring for the two accepted raw shapes. Subclassing is
    not expected — the transformer is stateless and instantiated by the
    processor (Chunk 8) once per build.
    """

    source: ClassVar[SourceKind] = SourceKind.JIRA

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, JiraProject, _BUCKET_SPECS)
        # Anything else (e.g. a parsed ``JsonFileFormatJira``) needs the
        # linker rewrite — see module docstring + Chunk 5 handoff for the
        # rationale.
        raise NotImplementedError(
            "JiraTransformer.transform(raw) accepts only an entity-bundle "
            "Mapping today (see module docstring). The raw-DTO path will "
            "be wired in Chunk 8 alongside the processor rewrite."
        )


__all__ = ["JiraTransformer"]
