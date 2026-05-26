"""Filter-rules layer: a subclass of :class:`QuerySandboxView` whose
registry properties hide excluded entities.

Filter rules apply at the **query stage** only (per the unified-users
redesign §J decision: filter rules stay editable in the query stage,
not setup). So this view subclasses the post-finalize
:class:`QuerySandboxView`, never :class:`SetupSandboxView`.

Two layers:

* :class:`FilteredRegistry`         — typed entity registries (commits, files, …).
* :class:`FilteredTagRegistry`      — :class:`TraitRegistry`, :class:`ClassifierRegistry`.
* :class:`FilteredRelationRegistry` — :class:`RelationRegistry` (dual source/target check).

:class:`FilteredSandboxView` inherits from :class:`QuerySandboxView`
and overrides only the registry properties (``commits`` / ``files`` /
``issues`` / ``pull_requests`` / ``traits`` / ``classifiers`` /
``relations`` / ``components`` / ``file_metrics``). Helpers on the base
view (``tags_for``, ``find_files_with_trait``, ``cochange_neighbors``,
``list_file_metrics``, summary helpers, …) route through ``self.<name>``
and inherit filtering automatically.

Components are special-cased: each :class:`Component` is returned as a
copy with :attr:`Component.file_refs` stripped of excluded FILE ids;
components that lose every member are dropped from the view.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Iterator, List, Optional, Set

from src.common.kernel import EntityKind, EntityRef
from src.sandbox.inject import QuerySandboxView

if TYPE_CHECKING:
    from src.common.kernel.graph import Graph
    from src.common.kernel.registry import Registry


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


def _declared_index_names(registry: Any) -> Set[str]:
    """Index names declared on the registry class via ``IndexSpec``.

    Uses the public ``Registry.indexes`` ClassVar (see
    ``data-server/src/common/kernel/registry.py``) so we don't depend on
    the index instance's private ``_add`` symbol.
    """
    specs = getattr(type(registry), "indexes", None) or []
    return {spec.name for spec in specs}


class _FilteredIndex:
    """Wrap a kernel :class:`Index` so bucket reads drop excluded entities."""

    __slots__ = ("_index", "_excluded_ids")

    def __init__(self, index: object, excluded_ids: Set[str]) -> None:
        self._index = index
        self._excluded_ids = excluded_ids

    def _filter_entities(self, value: Any) -> Any:
        if value is None:
            return None
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

    __slots__ = ("_registry", "_excluded_ids", "_index_names")

    def __init__(self, registry: "Registry", excluded_ids: Set[str]) -> None:
        self._registry = registry
        self._excluded_ids = excluded_ids
        self._index_names = _declared_index_names(registry)

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
        # Don't recurse on slot attribute access during __init__.
        if name in ("_registry", "_excluded_ids", "_index_names"):
            raise AttributeError(name)
        if name in self._index_names:
            return _FilteredIndex(
                getattr(self._registry, name), self._excluded_ids
            )
        return getattr(self._registry, name)


class FilteredTagRegistry:
    """Wrap :class:`TraitRegistry` / :class:`ClassifierRegistry`.

    A tag is dropped iff its ``target`` ref points to an excluded entity
    (per the v1 strict rule — no cascade beyond direct target).
    """

    __slots__ = ("_registry", "_excluded", "_index_names")

    def __init__(
        self, registry: Any, excluded: Dict[EntityKind, Set[str]]
    ) -> None:
        self._registry = registry
        self._excluded = excluded
        self._index_names = _declared_index_names(registry)

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
        if name in ("_registry", "_excluded", "_index_names"):
            raise AttributeError(name)
        if name in self._index_names:
            return _FilteredTagIndex(
                getattr(self._registry, name), self._excluded
            )
        return getattr(self._registry, name)


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

    __slots__ = ("_registry", "_excluded", "_index_names")

    def __init__(
        self, registry: Any, excluded: Dict[EntityKind, Set[str]]
    ) -> None:
        self._registry = registry
        self._excluded = excluded
        self._index_names = _declared_index_names(registry)

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
        if name in ("_registry", "_excluded", "_index_names"):
            raise AttributeError(name)
        if name in self._index_names:
            return _FilteredRelationIndex(
                getattr(self._registry, name), self._excluded
            )
        return getattr(self._registry, name)


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
        if isinstance(key, EntityRef) and _ref_is_excluded(key, self._excluded):
            return _EMPTY
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


# Map view-property name -> the EntityKind whose excluded-id set we apply.
# Only entries whose kind is a v1 target (FILE/COMMIT/ISSUE/PULL_REQUEST)
# can ever be active; the wider list is here so adding a new rule-supported
# kind later only takes an engine entry, not a view edit.
_FILTERED_PROPERTIES: Dict[str, EntityKind] = {
    "commits": EntityKind.COMMIT,
    "files": EntityKind.FILE,
    "issues": EntityKind.ISSUE,
    "pull_requests": EntityKind.PULL_REQUEST,
}


class FilteredSandboxView(QuerySandboxView):
    """A :class:`QuerySandboxView` whose registry properties hide excluded entities.

    Subclassing buys us automatic helper filtering: every helper on
    :class:`QuerySandboxView` reads through ``self.<registry>``, so an
    override of ``self.commits`` etc. transparently filters the helper's
    output.
    """

    # QuerySandboxView declares __slots__; we add our own.
    __slots__ = ("_excluded",)

    def __init__(
        self,
        graph: "Graph",
        excluded_ids: Dict[EntityKind, Set[str]],
    ) -> None:
        super().__init__(graph)
        self._excluded = {k: set(v) for k, v in excluded_ids.items()}

    # ------------------------------------------------------------------
    # Filter-aware registry overrides.
    # ------------------------------------------------------------------
    @property
    def commits(self):
        return self._wrap_entity_registry(EntityKind.COMMIT, self._graph.commits)

    @property
    def files(self):
        return self._wrap_entity_registry(EntityKind.FILE, self._graph.files)

    @property
    def issues(self):
        return self._wrap_entity_registry(EntityKind.ISSUE, self._graph.issues)

    @property
    def pull_requests(self):
        return self._wrap_entity_registry(
            EntityKind.PULL_REQUEST, self._graph.pull_requests
        )

    @property
    def traits(self):
        return FilteredTagRegistry(self._graph.traits, self._excluded)

    @property
    def classifiers(self):
        return FilteredTagRegistry(self._graph.classifiers, self._excluded)

    @property
    def relations(self):
        return FilteredRelationRegistry(self._graph.relations, self._excluded)

    @property
    def components(self):
        return _FilteredComponents(
            self._graph.components,
            self._excluded.get(EntityKind.FILE, set()),
        )

    @property
    def file_metrics(self):
        # FileMetric rows aren't a rule target in v1, but a metric whose
        # file_ref points at an excluded file should not surface. We use a
        # post-hoc filter on iteration / lookup.
        excluded_files = self._excluded.get(EntityKind.FILE)
        if not excluded_files:
            return self._graph.file_metrics
        return _FilteredFileMetricRegistry(self._graph.file_metrics, excluded_files)

    # ------------------------------------------------------------------
    # __getattr__ — wrap any other registry whose kind has an excluded set.
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> Any:
        graph = object.__getattribute__(self, "_graph")
        attr = getattr(graph, name)
        kind = _EXTRA_REGISTRY_KINDS.get(name)
        if kind is not None and _looks_like_registry(attr):
            excluded = self._excluded.get(kind)
            if excluded:
                return FilteredRegistry(attr, excluded)
        return attr

    # ------------------------------------------------------------------
    # Overview-row filtering hook (called by QuerySandboxView.overview_as_dict).
    # ------------------------------------------------------------------
    def _keep_overview_row(self, entity_kind: str, entity_id: str) -> bool:
        kind = _OVERVIEW_KIND_TO_ENTITY_KIND.get(entity_kind)
        if kind is None:
            return True
        excluded = self._excluded.get(kind)
        if not excluded:
            return True
        return entity_id not in excluded

    def __repr__(self) -> str:
        return (
            f"FilteredSandboxView(project_id={self._graph.project_id!r}, "
            f"excluded_kinds={sorted(k.value for k in self._excluded)})"
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _wrap_entity_registry(self, kind: EntityKind, registry: Any) -> Any:
        excluded = self._excluded.get(kind)
        if not excluded:
            return registry
        return FilteredRegistry(registry, excluded)


class _FilteredFileMetricRegistry:
    """Hide :class:`FileMetric` rows whose ``file_ref`` is excluded.

    FileMetric isn't a rule target itself, but its parent file may be —
    when a FILE rule excludes a file, every metric for that file must
    also disappear from view-time iteration.
    """

    __slots__ = ("_registry", "_excluded_files", "_index_names")

    def __init__(self, registry: Any, excluded_files: Set[str]) -> None:
        self._registry = registry
        self._excluded_files = excluded_files
        self._index_names = _declared_index_names(registry)

    def _keep(self, fm: Any) -> bool:
        ref = getattr(fm, "file_ref", None)
        if ref is None:
            return True
        return ref.id not in self._excluded_files

    def get(self, id: str):
        fm = self._registry.get(id)
        if fm is None:
            return None
        return fm if self._keep(fm) else None

    def all(self):
        return tuple(fm for fm in self._registry.all() if self._keep(fm))

    def __iter__(self) -> Iterator:
        for fm in self._registry:
            if self._keep(fm):
                yield fm

    def __len__(self) -> int:
        return sum(1 for _ in self.__iter__())

    def __contains__(self, key: object) -> bool:
        """Membership test by composite FileMetric id (matches ``Registry`` contract).

        Returns ``False`` for ids whose file is excluded. Callers asking
        "does the registry have a non-excluded entry for this file ref?"
        should use ``self.by_file[ref]`` instead — passing a file ref or
        file id here will always be ``False`` because the underlying
        registry keys on the composite ``{file_path}#{metric_name}`` id.
        """
        if not isinstance(key, str):
            return False
        fm = self._registry.get(key)
        return fm is not None and self._keep(fm)

    def __getattr__(self, name: str) -> Any:
        if name in ("_registry", "_excluded_files", "_index_names"):
            raise AttributeError(name)
        if name in self._index_names:
            # by_file index keyed by EntityRef → short-circuit excluded refs.
            return _FilteredFileMetricIndex(
                getattr(self._registry, name), self._excluded_files
            )
        return getattr(self._registry, name)


class _FilteredFileMetricIndex:
    """Index wrapper for :class:`FileMetricRegistry` — short-circuits excluded file refs."""

    __slots__ = ("_index", "_excluded_files")

    def __init__(self, index: Any, excluded_files: Set[str]) -> None:
        self._index = index
        self._excluded_files = excluded_files

    def _filter(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, tuple):
            return tuple(
                fm for fm in value
                if getattr(fm, "file_ref", None) is None
                or fm.file_ref.id not in self._excluded_files
            )
        ref = getattr(value, "file_ref", None)
        if ref is not None and ref.id in self._excluded_files:
            return None
        return value

    def __getitem__(self, key: Any) -> Any:
        if (
            isinstance(key, EntityRef)
            and key.kind is EntityKind.FILE
            and key.id in self._excluded_files
        ):
            return _EMPTY
        return self._filter(self._index[key])

    def get(self, key: Any, default: Any = None):
        if (
            isinstance(key, EntityRef)
            and key.kind is EntityKind.FILE
            and key.id in self._excluded_files
        ):
            return _EMPTY if default is None else default
        value = self._index.get(key, default)
        return self._filter(value) if value is not default else default

    def keys(self):
        return self._index.keys()

    def __contains__(self, key: object) -> bool:
        return key in self._index

    def __iter__(self):
        return iter(self._index)


# Registries beyond the four primary entity surfaces where a v1+future
# rule might land. Wrapped only when an excluded set exists for the kind.
_EXTRA_REGISTRY_KINDS: Dict[str, EntityKind] = {
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
    "duplications": EntityKind.DUPLICATION_PAIR,
    "quality_issues": EntityKind.QUALITY_ISSUE,
}


# OverviewTable.entity_kind is a legacy DX label ("file" / "component" /
# "author" / "issue" / "pr") — not the EntityKind enum. Map the ones that
# can carry a v1 rule. "author" is unmapped: unified-user rules aren't a
# v1 target (see filter_files.md §weird-interactions).
_OVERVIEW_KIND_TO_ENTITY_KIND: Dict[str, EntityKind] = {
    "file": EntityKind.FILE,
    "component": EntityKind.COMPONENT,
    "issue": EntityKind.ISSUE,
    "pr": EntityKind.PULL_REQUEST,
    "commit": EntityKind.COMMIT,
}


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
