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

from typing import Any, ClassVar, Iterable, List, Mapping

from ...kernel import Entity, EntityKind
from ...people import SourceKind
from ..transformer import Transformer, TransformResult
from .models import Change, Commit, File, GitAccount, GitProject, Hunk


_REQUIRED_PROJECT_KEY = "project"
_ENTITY_BUCKETS: dict[str, EntityKind] = {
    "accounts": EntityKind.GIT_ACCOUNT,
    "commits": EntityKind.COMMIT,
    "files": EntityKind.FILE,
    "changes": EntityKind.CHANGE,
    "hunks": EntityKind.HUNK,
}
_ENTITY_BUCKET_TYPES: dict[str, type[Entity]] = {
    "accounts": GitAccount,
    "commits": Commit,
    "files": File,
    "changes": Change,
    "hunks": Hunk,
}


class GitTransformer(Transformer):
    """Concrete :class:`Transformer` for the ``git`` source.

    See module docstring for the two accepted raw shapes. Subclassing is not
    expected — the transformer is stateless and instantiated by the
    processor (Chunk 8) once per build.
    """

    source: ClassVar[SourceKind] = SourceKind.GIT

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def transform(self, raw: Any) -> TransformResult:
        """Convert ``raw`` to a :class:`TransformResult`. See module docs."""
        if isinstance(raw, Mapping):
            return self._transform_entity_bundle(raw)
        # Anything else (e.g. a ``GitLogDTO``) needs the linker rewrite —
        # see module docstring + Chunk 4 handoff for the rationale.
        raise NotImplementedError(
            "GitTransformer.transform(raw) accepts only an entity-bundle "
            "Mapping today (see module docstring). The raw-DTO path will "
            "be wired in Chunk 8 alongside the processor rewrite."
        )

    # ------------------------------------------------------------------
    # Implementation
    # ------------------------------------------------------------------
    def _transform_entity_bundle(self, raw: Mapping[str, Any]) -> TransformResult:
        if _REQUIRED_PROJECT_KEY not in raw:
            raise ValueError(
                f"GitTransformer.transform: missing required key "
                f"{_REQUIRED_PROJECT_KEY!r} in entity bundle"
            )
        project = raw[_REQUIRED_PROJECT_KEY]
        if not isinstance(project, GitProject):
            raise TypeError(
                f"GitTransformer.transform: 'project' must be a GitProject, "
                f"got {type(project).__name__}"
            )

        unknown_keys = set(raw) - {_REQUIRED_PROJECT_KEY, *_ENTITY_BUCKETS}
        if unknown_keys:
            raise ValueError(
                f"GitTransformer.transform: unknown bundle keys {sorted(unknown_keys)}"
            )

        entities: dict[EntityKind, List[Entity]] = {}
        for bucket_name, kind in _ENTITY_BUCKETS.items():
            expected_cls = _ENTITY_BUCKET_TYPES[bucket_name]
            raw_bucket: Iterable[Entity] = raw.get(bucket_name, ()) or ()
            collected: List[Entity] = []
            for item in raw_bucket:
                if not isinstance(item, expected_cls):
                    raise TypeError(
                        f"GitTransformer.transform: bucket {bucket_name!r} "
                        f"contains {type(item).__name__}, expected "
                        f"{expected_cls.__name__}"
                    )
                collected.append(item)
            entities[kind] = collected

        return TransformResult(project=project, entities=entities)


__all__ = ["GitTransformer"]
