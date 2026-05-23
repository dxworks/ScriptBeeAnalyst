"""Wrap :class:`MCPSandboxView` so reads skip excluded entities.

Three wrapper classes carry the work:

* :class:`FilteredRegistry`         — typed entity registries (commits, files, …).
* :class:`FilteredTagRegistry`      — :class:`TraitRegistry`, :class:`ClassifierRegistry`.
* :class:`FilteredRelationRegistry` — :class:`RelationRegistry` (dual source/target check).

The view also intercepts ``graph.components`` to strip excluded FILE
refs from each component's :attr:`Component.file_refs`, dropping the
component entirely when it goes empty.

Construction is per-request — keep it cheap. The wrappers hold references
to the underlying registry and to the excluded-ids map; iteration filters
on the fly with O(N) scans (N = entity count of that registry).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Set

from src.common.kernel import EntityKind, EntityRef

if TYPE_CHECKING:
    from src.common.domains.components.models import Component
    from src.common.kernel.graph import Graph
    from src.common.kernel.registry import Registry
    from src.enrichment.relations.registries import RelationRegistry
    from src.enrichment.tags.registries import (
        ClassifierRegistry,
        TraitRegistry,
    )
    from src.sandbox import MCPSandboxView


# Stand-in tuple for "missing key" returns from multi-key indexes — same
# empty-tuple sentinel the kernel :class:`Index` uses.
_EMPTY: tuple = tuple()


def _ref_is_excluded(
    ref: Optional[EntityRef], excluded: Dict[EntityKind, Set[str]]
) -> bool:
    """``True`` iff ``ref`` points to an entity flagged for exclusion."""
    if ref is None:
        return False
    bucket = excluded.get(ref.kind)
    if not bucket:
        return False
    return ref.id in bucket


class _FilteredIndex:
    """Wrap a kernel :class:`Index` so bucket reads drop excluded entities.

    Only the read surface used by ``MCPSandboxView`` (``__getitem__``,
    ``get``, ``keys``, ``__contains__``, iteration) is implemented; the
    private ``_add`` / ``_remove`` mutators stay on the wrapped index.
    """

    __slots__ = ("_index", "_excluded_ids")

    def __init__(self, index: object, excluded_ids: Set[str]) -> None:
        self._index = index
        self._excluded_ids = excluded_ids

    def _filter_entities(self, value: Any) -> Any:
        if value is None:
            return None
        # multi-key buckets are tuples; multi=False returns a single entity
        if isinstance(value, tuple):
            return tuple(
                e for e in value if getattr(e, "id", None) not in self._excluded_ids
            )
        if getattr(value, "id", None) in self._excluded_ids:
            return None
        return value

    def __getitem__(self, key: Any) -> Any:
        return self._filter_entities(self._index[key])  # type: ignore[index]

    def get(self, key: Any, default: Any = None) -> Any:
        value = self._index.get(key, default)  # type: ignore[attr-defined]
        return self._filter_entities(value)

    def keys(self):
        return self._index.keys()  # type: ignore[attr-defined]

    def __contains__(self, key: object) -> bool:
        return key in self._index  # type: ignore[operator]

    def __iter__(self):
        return iter(self._index)  # type: ignore[arg-type]


class FilteredRegistry:
    """Read-only view of a typed :class:`Registry` skipping excluded ids."""

    __slots__ = ("_registry", "_excluded_ids")

    def __init__(self, registry: "Registry", excluded_ids: Set[str]) -> None:
        self._registry = registry
        self._excluded_ids = excluded_ids

    def get(self, id: str):
        if id in self._excluded_ids:
            return None
        return self._registry.get(id)

    def all(self):
        return tuple(
            e for e in self._registry.all() if e.id not in self._excluded_ids
        )

    def ids(self) -> Set[str]:
        return self._registry.ids() - self._excluded_ids

    def __iter__(self) -> Iterator:
        for e in self._registry:
            if e.id in self._excluded_ids:
                continue
            yield e

    def __len__(self) -> int:
        return len(self._registry) - sum(
            1 for eid in self._excluded_ids if eid in self._registry
        )

    def __contains__(self, id: object) -> bool:
        if id in self._excluded_ids:
            return False
        return id in self._registry

    def __getattr__(self, name: str) -> Any:
        # Wrap declared indexes; otherwise read-through. Index access is the
        # interesting hot path — ``commits.by_author[ref]`` must skip
        # excluded commits without the caller noticing.
        attr = getattr(self._registry, name)
        # Heuristic: registry indexes have ``_add`` / ``_remove`` and a ``name`` attribute.
        if (
            hasattr(attr, "__getitem__")
            and hasattr(attr, "keys")
            and hasattr(attr, "_add")
        ):
            return _FilteredIndex(attr, self._excluded_ids)
        return attr


class FilteredTagRegistry:
    """Wrap :class:`TraitRegistry` / :class:`ClassifierRegistry`.

    A tag is dropped iff its ``target`` ref points to an excluded entity
    (per the v1 strict rule — no cascade beyond direct target).
    """

    __slots__ = ("_registry", "_excluded")

    def __init__(
        self, registry: Any, excluded: Dict[EntityKind, Set[str]]
    ) -> None:
        self._registry = registry
        self._excluded = excluded

    def _keep(self, tag: Any) -> bool:
        target = getattr(tag, "target", None)
        return not _ref_is_excluded(target, self._excluded)

    def get(self, id: str):
        entity = self._registry.get(id)
        if entity is None:
            return None
        return entity if self._keep(entity) else None

    def all(self):
        return tuple(t for t in self._registry.all() if self._keep(t))

    def __iter__(self) -> Iterator:
        for t in self._registry:
            if self._keep(t):
                yield t

    def __len__(self) -> int:
        return sum(1 for _ in self.__iter__())

    def __contains__(self, id: object) -> bool:
        entity = self._registry.get(id) if hasattr(self._registry, "get") else None
        return entity is not None and self._keep(entity)

    # --- registry-method passthroughs that filter the result ---
    def for_target(self, ref: EntityRef):
        if _ref_is_excluded(ref, self._excluded):
            return _EMPTY
        result = self._registry.for_target(ref)
        if isinstance(result, dict):
            return {k: v for k, v in result.items() if self._keep(v)}
        return tuple(t for t in result if self._keep(t))

    def of_name(self, name: str):
        return tuple(t for t in self._registry.of_name(name) if self._keep(t))

    def with_value(self, dim: str, value: str):
        return tuple(
            t for t in self._registry.with_value(dim, value) if self._keep(t)
        )

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._registry, name)
        if (
            hasattr(attr, "__getitem__")
            and hasattr(attr, "keys")
            and hasattr(attr, "_add")
        ):
            # An index keyed by EntityRef (by_target) needs filtering by
            # both bucket-key (the target ref) and by tag identity.
            return _FilteredTagIndex(attr, self._excluded)
        return attr


class _FilteredTagIndex:
    """Index wrapper for tag registries.

    Buckets keyed by ``target`` short-circuit to empty when that target is
    excluded. Other index slices filter element-by-element on tag.target.
    """

    __slots__ = ("_index", "_excluded")

    def __init__(self, index: Any, excluded: Dict[EntityKind, Set[str]]) -> None:
        self._index = index
        self._excluded = excluded

    def _keep(self, tag: Any) -> bool:
        target = getattr(tag, "target", None)
        return not _ref_is_excluded(target, self._excluded)

    def __getitem__(self, key: Any) -> Any:
        # Fast path: the by_target index uses an EntityRef key.
        if isinstance(key, EntityRef) and _ref_is_excluded(key, self._excluded):
            return _EMPTY
        value = self._index[key]
        if isinstance(value, tuple):
            return tuple(t for t in value if self._keep(t))
        if value is None:
            return None
        return value if self._keep(value) else None

    def get(self, key: Any, default: Any = None):
        if isinstance(key, EntityRef) and _ref_is_excluded(key, self._excluded):
            return _EMPTY if default is None else default
        value = self._index.get(key, default)
        if isinstance(value, tuple):
            return tuple(t for t in value if self._keep(t))
        if value is None:
            return default
        return value if self._keep(value) else default

    def keys(self):
        return self._index.keys()

    def __contains__(self, key: object) -> bool:
        return key in self._index

    def __iter__(self):
        return iter(self._index)


class FilteredRelationRegistry:
    """Wrap :class:`RelationRegistry`.

    A relation is dropped iff EITHER its ``source`` OR its ``target`` refs
    an excluded entity (strict — no cascade beyond the relation itself).
    """

    __slots__ = ("_registry", "_excluded")

    def __init__(
        self, registry: "RelationRegistry", excluded: Dict[EntityKind, Set[str]]
    ) -> None:
        self._registry = registry
        self._excluded = excluded

    def _keep(self, rel: Any) -> bool:
        return not (
            _ref_is_excluded(getattr(rel, "source", None), self._excluded)
            or _ref_is_excluded(getattr(rel, "target", None), self._excluded)
        )

    def get(self, id: str):
        entity = self._registry.get(id)
        if entity is None:
            return None
        return entity if self._keep(entity) else None

    def all(self):
        return tuple(r for r in self._registry.all() if self._keep(r))

    def __iter__(self) -> Iterator:
        for r in self._registry:
            if self._keep(r):
                yield r

    def __len__(self) -> int:
        return sum(1 for _ in self.__iter__())

    def __contains__(self, id: object) -> bool:
        entity = self._registry.get(id) if hasattr(self._registry, "get") else None
        return entity is not None and self._keep(entity)

    def for_source(self, ref: EntityRef):
        if _ref_is_excluded(ref, self._excluded):
            return _EMPTY
        return tuple(r for r in self._registry.for_source(ref) if self._keep(r))

    def for_target(self, ref: EntityRef):
        if _ref_is_excluded(ref, self._excluded):
            return _EMPTY
        return tuple(r for r in self._registry.for_target(ref) if self._keep(r))

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._registry, name)
        if (
            hasattr(attr, "__getitem__")
            and hasattr(attr, "keys")
            and hasattr(attr, "_add")
        ):
            return _FilteredRelationIndex(attr, self._excluded)
        return attr


class _FilteredRelationIndex:
    """Index wrapper for relation registry indexes."""

    __slots__ = ("_index", "_excluded")

    def __init__(self, index: Any, excluded: Dict[EntityKind, Set[str]]) -> None:
        self._index = index
        self._excluded = excluded

    def _keep(self, rel: Any) -> bool:
        return not (
            _ref_is_excluded(getattr(rel, "source", None), self._excluded)
            or _ref_is_excluded(getattr(rel, "target", None), self._excluded)
        )

    def _filter_value(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, tuple):
            return tuple(r for r in value if self._keep(r))
        return value if self._keep(value) else None

    def __getitem__(self, key: Any) -> Any:
        # Short-circuit by_source/by_target buckets whose key is an
        # excluded EntityRef — no need to walk the bucket.
        if isinstance(key, EntityRef) and _ref_is_excluded(key, self._excluded):
            return _EMPTY
        # by_pair keys are (source, target) tuples of EntityRef.
        if (
            isinstance(key, tuple)
            and len(key) == 2
            and all(isinstance(k, EntityRef) for k in key)
            and any(_ref_is_excluded(k, self._excluded) for k in key)
        ):
            return _EMPTY
        return self._filter_value(self._index[key])

    def get(self, key: Any, default: Any = None):
        if isinstance(key, EntityRef) and _ref_is_excluded(key, self._excluded):
            return _EMPTY if default is None else default
        value = self._index.get(key, default)
        return self._filter_value(value) if value is not default else default

    def keys(self):
        return self._index.keys()

    def __contains__(self, key: object) -> bool:
        return key in self._index

    def __iter__(self):
        return iter(self._index)


class _FilteredComponents:
    """Wrap :attr:`Graph.components` so FILE refs of excluded files vanish.

    Components keep their identity; only :attr:`Component.file_refs` is
    rewritten. Components that lose every member are dropped from the view.
    """

    __slots__ = ("_registry", "_excluded_files")

    def __init__(self, registry: Any, excluded_files: Set[str]) -> None:
        self._registry = registry
        self._excluded_files = excluded_files

    def _filter_component(self, component: Any) -> Optional[Any]:
        if component is None:
            return None
        refs = getattr(component, "file_refs", None)
        if refs is None:
            return component
        kept_refs = [
            r for r in refs
            if not (r.kind is EntityKind.FILE and r.id in self._excluded_files)
        ]
        if not kept_refs:
            return None
        # Copy so we don't mutate the underlying entity.
        return component.model_copy(update={"file_refs": kept_refs})

    def get(self, id: str):
        return self._filter_component(self._registry.get(id))

    def all(self):
        out: List[Any] = []
        for c in self._registry.all():
            filtered = self._filter_component(c)
            if filtered is not None:
                out.append(filtered)
        return tuple(out)

    def __iter__(self) -> Iterator:
        for c in self._registry:
            filtered = self._filter_component(c)
            if filtered is not None:
                yield filtered

    def __len__(self) -> int:
        return sum(1 for _ in self.__iter__())

    def __contains__(self, id: object) -> bool:
        c = self._registry.get(id) if hasattr(self._registry, "get") else None
        return self._filter_component(c) is not None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._registry, name)


class FilteredSandboxView:
    """Wrap :class:`MCPSandboxView` so every read drops excluded entities.

    The wrapper holds references to the underlying view + the excluded-ids
    map; it overrides the four explicit registry properties
    (``commits`` / ``files`` / ``issues`` / ``pull_requests``) and
    intercepts ``__getattr__`` to wrap any other registry (or the
    components surface) the agent reaches for.
    """

    _ENTITY_KIND_BY_REGISTRY = {
        "commits": EntityKind.COMMIT,
        "files": EntityKind.FILE,
        "issues": EntityKind.ISSUE,
        "pull_requests": EntityKind.PULL_REQUEST,
        "changes": EntityKind.CHANGE,
        "hunks": EntityKind.HUNK,
        "git_accounts": EntityKind.GIT_ACCOUNT,
        "jira_users": EntityKind.JIRA_USER,
        "github_users": EntityKind.GITHUB_USER,
        "issue_statuses": EntityKind.ISSUE_STATUS,
        "issue_types": EntityKind.ISSUE_TYPE,
        "reviews": EntityKind.REVIEW,
        "review_comments": EntityKind.REVIEW_COMMENT,
        "github_commits": EntityKind.GITHUB_COMMIT,
        "code_types": EntityKind.CODE_TYPE,
        "code_methods": EntityKind.CODE_METHOD,
        "code_fields": EntityKind.CODE_FIELD,
        "code_refs": EntityKind.CODE_REF,
        "file_metrics": EntityKind.FILE_METRIC,
        "duplications": EntityKind.DUPLICATION_PAIR,
        "quality_issues": EntityKind.QUALITY_ISSUE,
    }

    def __init__(
        self,
        inner: "MCPSandboxView",
        excluded_ids: Dict[EntityKind, Set[str]],
    ) -> None:
        self._inner = inner
        self._excluded = {k: set(v) for k, v in excluded_ids.items()}

    # ------------------------------------------------------------------
    # Explicit registry properties — direct overrides of the four
    # ``MCPSandboxView`` properties documented in §11 of the plan.
    # ------------------------------------------------------------------
    @property
    def commits(self):
        return self._wrap_registry("commits", self._inner.commits)

    @property
    def files(self):
        return self._wrap_registry("files", self._inner.files)

    @property
    def issues(self):
        return self._wrap_registry("issues", self._inner.issues)

    @property
    def pull_requests(self):
        return self._wrap_registry("pull_requests", self._inner.pull_requests)

    # ------------------------------------------------------------------
    # Components — special-case: filter file_refs inside each component.
    # ------------------------------------------------------------------
    @property
    def components(self):
        return _FilteredComponents(
            self._inner._graph.components,
            self._excluded.get(EntityKind.FILE, set()),
        )

    # ------------------------------------------------------------------
    # Generic fallthrough.
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        inner = object.__getattribute__(self, "_inner")
        attr = getattr(inner, name)
        if name == "traits":
            return FilteredTagRegistry(attr, self._excluded)
        if name == "classifiers":
            return FilteredTagRegistry(attr, self._excluded)
        if name == "relations":
            return FilteredRelationRegistry(attr, self._excluded)
        # Wrap any other typed Registry by inferring its EntityKind.
        kind = self._ENTITY_KIND_BY_REGISTRY.get(name)
        if kind is not None and _looks_like_registry(attr):
            excluded = self._excluded.get(kind)
            if excluded:
                return FilteredRegistry(attr, excluded)
        return attr

    def __repr__(self) -> str:
        return (
            f"FilteredSandboxView(inner={self._inner!r}, "
            f"excluded_kinds={sorted(k.value for k in self._excluded)})"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _wrap_registry(self, name: str, registry: Any) -> Any:
        kind = self._ENTITY_KIND_BY_REGISTRY.get(name)
        if kind is None:
            return registry
        excluded = self._excluded.get(kind)
        if not excluded:
            return registry
        return FilteredRegistry(registry, excluded)


def _looks_like_registry(obj: Any) -> bool:
    """Duck-type check: a typed registry exposes ``.all`` / ``.get`` / iteration."""
    return (
        hasattr(obj, "all")
        and hasattr(obj, "get")
        and hasattr(obj, "__iter__")
        and hasattr(obj, "__len__")
    )


__all__ = [
    "FilteredRegistry",
    "FilteredRelationRegistry",
    "FilteredSandboxView",
    "FilteredTagRegistry",
]
