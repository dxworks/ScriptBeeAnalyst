"""Git-domain :class:`Transformer` implementation.

Per plan §9, every domain ships a transformer that converts a raw mined
payload into a :class:`TransformResult` the processor (Chunk 8) routes into
graph registries.

Two raw input shapes are recognized by :meth:`GitTransformer.transform`:

1. **Already-built entity bundle** — a ``dict`` of the form::

      {
          "project":   GitProject(...),
          "accounts":  Iterable[GitAccount],
          "commits":   Iterable[Commit],
          "files":     Iterable[File],
          "changes":   Iterable[Change],
          "hunks":     Iterable[Hunk],   # optional
      }

   This is the form Chunk 8 (and tests) hand to the transformer once the
   raw ``GitLogDTO`` has been folded into entities by the dedicated
   reader+linker stage that lives in ``src/inspector_git/``. The
   transformer just regroups the entities into a typed
   :class:`TransformResult`. This is the supported v2 entry point.

   The actual validation + regrouping is delegated to
   :meth:`Transformer.collect_bundle` (Chunk 5 factored the per-domain
   skeleton up the hierarchy so Jira/GitHub don't clone it). This
   transformer just declares its bucket specs and forwards.

2. **Raw inspector-git DTO** — a ``GitLogDTO`` (or its raw bytes) directly.
   The wiring that walks the DTO and builds entities is not part of Chunk
   4; it requires plumbing the legacy ``CommitTransformer`` /
   ``ChangeTransformer`` traversal (1300+ lines under
   ``inspector_git/linker/``) and the rename-chain tracking that
   :class:`File`'s new path-as-id contract demands. Calling this with a
   ``GitLogDTO`` today raises ``NotImplementedError`` with the explicit
   "Chunk 8 plumbs the linker rewrite" message — the Chunk 4 handoff
   documents the rationale.
"""
from __future__ import annotations

from typing import Any, ClassVar, Mapping

from ...kernel import Entity, EntityKind
from ...people.source import SourceKind
from ..transformer import Transformer, TransformResult
from .models import Change, Commit, File, GitAccount, GitProject, Hunk


_BUCKET_SPECS: dict[str, tuple[EntityKind, type[Entity]]] = {
    "accounts": (EntityKind.GIT_ACCOUNT, GitAccount),
    "commits": (EntityKind.COMMIT, Commit),
    "files": (EntityKind.FILE, File),
    "changes": (EntityKind.CHANGE, Change),
    "hunks": (EntityKind.HUNK, Hunk),
}


class GitTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``git`` source.

    See module docstring for the two accepted raw shapes. Subclassing is not
    expected — the transformer is stateless and instantiated by the
    processor (Chunk 8) once per build.
    """

    source: ClassVar[SourceKind] = SourceKind.GIT

    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self.collect_bundle(raw, GitProject, _BUCKET_SPECS)
        # Anything else (e.g. a ``GitLogDTO``) needs the linker rewrite —
        # see module docstring + Chunk 4 handoff for the rationale.
        raise NotImplementedError(
            "GitTransformer.transform(raw) accepts only an entity-bundle "
            "Mapping today (see module docstring). The raw-DTO path will "
            "be wired in Chunk 8 alongside the processor rewrite."
        )


__all__ = ["GitTransformer"]
