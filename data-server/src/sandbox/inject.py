"""MCP sandbox façade — adapt the typed v2 :class:`Graph` to the legacy
agent-facing names.

See §11 of ``architectural_changes.md`` (lines 690–740). The MCP server
sends Python code to ``POST /execute``; the sandbox today injects a
single object called ``graph_data``. After the v2 refactor the loaded
project is a typed :class:`Graph`, not a dict-of-projects, so the
injection layer becomes::

    graph_data = MCPSandboxView(graph)

:class:`MCPSandboxView` is a thin read-side façade that exposes the
names the agent code (and the documented examples in
``analyzed_projects/instructions/*``) expects, mapped onto the new
typed :class:`Graph` registries. The mapping table at plan §11 lines
712–723 is the spec — every "Today" expression on the left works as-is
on a :class:`MCPSandboxView` instance, and produces the right thing
via the "After" expression on the right.

Design notes
------------

* :class:`MCPSandboxView` is **not** a Pydantic model — it's a thin
  Python class wrapping a single :class:`Graph` reference. Pydantic
  models stay pydantic; the view does not mutate them.
* Read-through ``__getattr__`` falls back to the underlying graph for
  any attribute the view does not declare explicitly (so e.g.
  ``graph_data.unified_users`` works without explicit boilerplate).
* No ``Any`` in declared public signatures — every method/return is
  typed against the v2 surface.
* The view does not expose write-side helpers (no ``add()`` /
  ``dump()`` / ``add_project()``); it is read-only by surface (Python
  cannot enforce that strictly without proxies, but the plan calls out
  "do NOT mutate ``graph_data``" in the agent-facing instructions).

Sandbox helpers (``find_files_with_trait``, ``cochange_neighbors``,
``overview_as_dict``) are exposed both as standalone keyword bindings
in the ``/execute`` scope (per the Chunk-7 / Chunk-8 helper plumbing)
AND as methods on this view, so legacy snippets that call e.g.
``graph_data.find_files_with_trait(name)`` still work.

Entity-side ``*_ref`` resolvers
-------------------------------

Every typed entity carries auto-generated resolver methods for its
``*_ref`` / ``*_refs`` fields — ``commit.author(graph_data)``,
``pr.commits(graph_data)``, etc. (see
``src/common/kernel/entity.py``). Prefer those over
``graph_data.<registry>.get(ref.id)`` inside ``/execute`` snippets.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING, Iterator, Literal, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.relations.models import Relation, WindowKind
from src.enrichment.tags.base import Classifier, Tag, Trait

if TYPE_CHECKING:
    from src.common.domains.git.models import Commit, File
    from src.common.domains.git.registries import CommitRegistry, FileRegistry
    from src.common.domains.github.models import PullRequest
    from src.common.domains.github.registries import PullRequestRegistry
    from src.common.domains.jira.models import Issue
    from src.common.domains.jira.registries import IssueRegistry
    from src.common.kernel import Entity, Graph


# Map EntityKind -> the MCPSandboxView property that owns the registry.
# Used by ``_resolve`` so the view-level override (filter-rules layer)
# takes precedence over a direct ``graph.resolve(ref)`` call.
_KIND_TO_VIEW_PROPERTY: dict[EntityKind, str] = {
    EntityKind.COMMIT: "commits",
    EntityKind.FILE: "files",
    EntityKind.ISSUE: "issues",
    EntityKind.PULL_REQUEST: "pull_requests",
    EntityKind.TRAIT: "traits",
    EntityKind.CLASSIFIER: "classifiers",
    EntityKind.RELATION: "relations",
    EntityKind.COMPONENT: "components",
    EntityKind.FILE_METRIC: "file_metrics",
}


class MCPSandboxView:
    """Read-side façade for the typed v2 :class:`Graph`.

    Wraps a single :class:`Graph` reference and exposes the legacy
    agent-facing surface (``graph_data.commits.all()``,
    ``graph_data.tags_for(ref)``, etc.) on top of the v2 typed
    registries. See module docstring for the mapping spec.

    Construction::

        view = MCPSandboxView(graph)
        # then in the /execute sandbox:
        exec_globals = {"graph_data": view, ...}
    """

    __slots__ = ("_graph", "_commit_issue_link_cache")

    def __init__(self, graph: "Graph") -> None:
        self._graph = graph
        # Lazily populated by :meth:`_commit_issue_links` on first use.
        # Stored as a single tuple so the cache is set atomically and
        # the underscore name keeps it out of the agent-facing surface.
        self._commit_issue_link_cache: Optional[
            tuple[dict[str, list["Issue"]], dict[str, list["Commit"]]]
        ] = None

    # ------------------------------------------------------------------
    # Direct registry hand-offs (the four "main" entity surfaces)
    #
    # Per the plan §11 mapping table, the legacy agent code did
    # ``graph_data['git'].git_commit_registry.all`` etc. The new
    # ``CommitRegistry`` (and friends) already expose ``.all()`` (a
    # snapshot tuple) and ``.get(id)`` directly — see
    # ``src/common/kernel/registry.py`` — so we just hand the registry
    # back and let the agent call those built-in methods. Iteration also
    # works: ``for c in view.commits``.
    # ------------------------------------------------------------------
    @property
    def commits(self) -> "CommitRegistry":
        """The :class:`CommitRegistry` of the underlying graph.

        Supports ``.all()``, ``.get(id)``, iteration, and the registry
        indexes (``by_author``, ``by_file``, …). Mapping table row 1.
        """
        return self._graph.commits

    @property
    def issues(self) -> "IssueRegistry":
        """The :class:`IssueRegistry` of the underlying graph.

        Supports ``.all()``, ``.get(id)``, iteration. Mapping table
        row 3.
        """
        return self._graph.issues

    @property
    def pull_requests(self) -> "PullRequestRegistry":
        """The :class:`PullRequestRegistry` of the underlying graph.

        Supports ``.all()``, ``.get(id)``, iteration.
        """
        return self._graph.pull_requests

    @property
    def files(self) -> "FileRegistry":
        """The :class:`FileRegistry` of the underlying graph.

        Supports ``.all()``, ``.get(id)``, iteration, and the
        ``by_extension`` index.
        """
        return self._graph.files

    @property
    def duplication_pairs(self):
        """Alias for duplications (legacy compat)."""
        return self._graph.duplications

    # ------------------------------------------------------------------
    # Filter-aware registry accessors.
    #
    # The filter-rules layer (``src.filter_rules.views.FilteredSandboxView``)
    # subclasses this view and overrides these properties to return wrapped
    # registries that skip excluded entities. Every helper below that reads
    # traits / classifiers / relations / components / file_metrics goes
    # through ``self.<name>`` (not ``self._graph.<name>``) so the override
    # transparently filters every helper.
    # ------------------------------------------------------------------
    @property
    def traits(self):
        return self._graph.traits

    @property
    def classifiers(self):
        return self._graph.classifiers

    @property
    def relations(self):
        return self._graph.relations

    @property
    def components(self):
        return self._graph.components

    @property
    def file_metrics(self):
        return self._graph.file_metrics

    def _resolve(self, ref: EntityRef) -> Optional["Entity"]:
        """Resolve a ref through this view's registry properties.

        When a subclass overrides a registry property to filter excluded
        entities, this method honours that override — a ref pointing at
        an excluded entity returns ``None`` even though the underlying
        graph still holds the row.
        """
        registry = self._registry_for_kind(ref.kind)
        if registry is None:
            return self._graph.resolve(ref)
        return registry.get(ref.id)

    def _registry_for_kind(self, kind: EntityKind):
        """Pick the view-level property that owns ``kind`` (or ``None``)."""
        name = _KIND_TO_VIEW_PROPERTY.get(kind)
        if name is None:
            return None
        return getattr(self, name, None)

    # ------------------------------------------------------------------
    # Read-through to the underlying graph
    #
    # Anything not declared above (e.g. ``unified_users``,
    # ``components``, ``traits``, ``classifiers``, ``relations``,
    # ``changes``, ``hunks``, ``code_methods``, …) flows through to the
    # typed :class:`Graph` field. This keeps the surface forward-
    # compatible: when Chunk 10 ports a new domain, agent code can read
    # ``graph_data.<new_field>`` without touching the view.
    # ------------------------------------------------------------------
    def __getattr__(self, name: str) -> object:
        """Fall through to the underlying :class:`Graph` for unknown
        attributes (read-through pattern).

        ``__getattr__`` only runs when normal attribute lookup fails,
        so the explicit properties above always take precedence. Raises
        :class:`AttributeError` if neither the view nor the wrapped
        graph has the attribute.
        """
        # Slot-only class — no instance __dict__ for a recursion trap.
        graph = object.__getattribute__(self, "_graph")
        try:
            return getattr(graph, name)
        except AttributeError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no attribute {name!r} "
                f"(neither does the underlying Graph)"
            ) from None

    def __dir__(self) -> list[str]:
        """Include the wrapped graph's attributes for tab-completion."""
        own = list(super().__dir__())
        return sorted(set(own) | set(dir(self._graph)))

    # ------------------------------------------------------------------
    # Internal: cached commit↔issue regex join
    #
    # ``commit_issues`` / ``issue_commits`` (see ``src.sandbox.helpers``)
    # used to rebuild the full issue-key dict + alternation regex on
    # every call. That made the natural agent pattern (call helper in a
    # loop over commits) O(N·M) and easily blew past the 60 s MCP
    # timeout on real-size projects (Zeppelin: 5.6 k commits × 6.3 k
    # issues). The maps below are built in one pass on first access and
    # reused for the lifetime of the view (which is reconstructed per
    # ``/execute`` request — see ``src/server.py`` — so there's no
    # cross-request staleness window).
    # ------------------------------------------------------------------
    def _commit_issue_links(
        self,
    ) -> tuple[dict[str, list["Issue"]], dict[str, list["Commit"]]]:
        """Lazily compute ``(commit_id -> issues, issue_key_upper -> commits)``.

        Mirrors the ``\\b<KEY>\\b`` case-insensitive match semantics used
        by :class:`~src.enrichment.relations.implementations.issue_file.IssueFileBuilder`
        and the legacy free helpers. Empty maps when no issues are
        loaded; built entries are entity lists (not ids).
        """
        cached = self._commit_issue_link_cache
        if cached is not None:
            return cached

        issue_by_key: dict[str, "Issue"] = {}
        for issue in self.issues.all():
            key = getattr(issue, "key", None)
            if key:
                issue_by_key[key.upper()] = issue

        commit_to_issues: dict[str, list["Issue"]] = {}
        key_to_commits: dict[str, list["Commit"]] = {}

        if issue_by_key:
            pattern = re.compile(
                r"\b(" + "|".join(re.escape(k) for k in issue_by_key) + r")\b",
                re.IGNORECASE,
            )
            for commit in self.commits.all():
                msg = getattr(commit, "message", "") or ""
                if not msg:
                    continue
                seen: set[str] = set()
                linked: list["Issue"] = []
                for match in pattern.findall(msg):
                    ku = match.upper()
                    if ku in seen:
                        continue
                    seen.add(ku)
                    iss = issue_by_key.get(ku)
                    if iss is None:
                        continue
                    linked.append(iss)
                    key_to_commits.setdefault(ku, []).append(commit)
                if linked:
                    commit_to_issues[commit.id] = linked

        self._commit_issue_link_cache = (commit_to_issues, key_to_commits)
        return self._commit_issue_link_cache

    # ------------------------------------------------------------------
    # Tag / classifier accessors (mapping table rows 7, 8, 10)
    # ------------------------------------------------------------------
    def tags_for(self, ref: EntityRef) -> list[Tag]:
        """Concatenate every :class:`Trait` and :class:`Classifier`
        whose ``target == ref``. Mapping table row 7.

        Replaces the legacy ``enrichments.find_tags(kind, id)``. The
        order is traits-first then classifiers — matches the legacy
        ``Tags`` payload shape (``Tags.traits`` listed before
        ``Tags.classifiers``) so snippets that iterate the result and
        early-out on traits get the same items first.
        """
        traits_result = self.traits.for_target(ref)
        traits: tuple[Trait, ...] = tuple(traits_result)
        classifiers_result = self.classifiers.for_target(ref)
        if isinstance(classifiers_result, dict):
            classifiers: list[Classifier] = list(classifiers_result.values())
        else:
            classifiers = list(classifiers_result)
        return [*traits, *classifiers]

    def find_files_with_trait(self, name: str) -> list["File"]:
        """Resolve every :class:`File` carrying the named trait.

        Reads :meth:`TraitRegistry.of_name`, filters to
        ``trait.target.kind == EntityKind.FILE``, and resolves each
        target via :meth:`Graph.resolve` to a concrete :class:`File`.
        Mapping table row 8 (re-shaped to return File objects, not
        bare ids — agent code wants the entity for further walks).

        Note: legacy callers received bare ``str`` file ids. To keep
        the legacy shape, agents can write
        ``[f.id for f in view.find_files_with_trait(...)]``. The
        examples in ``analyzed_projects/instructions/`` are updated
        accordingly.
        """
        out: list["File"] = []
        for trait in self.traits.of_name(name):
            tgt = trait.target
            if tgt.kind is not EntityKind.FILE:
                continue
            resolved = self._resolve(tgt)
            if resolved is None:
                continue
            out.append(resolved)  # type: ignore[arg-type]
        return out

    def find_files_with_classifier(
        self, dim: str, value: str
    ) -> list["File"]:
        """Resolve every :class:`File` carrying classifier
        ``(dim, value)``.

        Reads :meth:`ClassifierRegistry.with_value`, filters to file
        targets, resolves to concrete :class:`File`. Mapping table
        row 10.
        """
        out: list["File"] = []
        for classifier in self.classifiers.with_value(dim, value):
            tgt = classifier.target
            if tgt.kind is not EntityKind.FILE:
                continue
            resolved = self._resolve(tgt)
            if resolved is None:
                continue
            out.append(resolved)  # type: ignore[arg-type]
        return out

    # ------------------------------------------------------------------
    # Co-change neighbours (mapping table row 9)
    # ------------------------------------------------------------------
    def cochange_neighbors(
        self,
        file_id: str,
        window: WindowKind | str = WindowKind.LIFETIME,
        limit: Optional[int] = None,
    ) -> list["File"]:
        """Files co-changing with ``file_id`` in the given window.

        Walks :attr:`RelationRegistry.by_source` from the ``file_id``
        :class:`EntityRef` (so the per-call scan is bounded by the
        number of outgoing relations of that file, not the whole
        cochange slice), filters to ``relation_kind == "cochange"`` and
        the requested window AND target kind FILE, resolves each target
        to a concrete :class:`File`, and returns at most ``limit``
        results. Order is by_source iteration order; callers that need
        strict ordering should sort by ``strength`` themselves via
        ``view.relations.by_pair[...]``.

        ``window`` accepts both :class:`WindowKind` enum members and
        bare strings (``"lifetime"``, ``"recent"``).

        Perf: using ``by_source`` (vs the old ``by_kind_window`` slice
        with a Python-side ``source.id == file_id`` filter) keeps a
        per-file agent loop O(R) total instead of O(F·R) — same trap
        :func:`src.sandbox.helpers.commit_issues` hit with the
        commit↔issue regex join.
        """
        out: list["File"] = []
        if limit is not None and limit <= 0:
            return out
        file_ref = EntityRef(kind=EntityKind.FILE, id=file_id)
        win = (
            WindowKind(window)
            if isinstance(window, str) and not isinstance(window, WindowKind)
            else window
        )
        for rel in self.relations.by_source[file_ref]:
            if rel.relation_kind != "cochange":
                continue
            if rel.window != win:
                continue
            tgt = rel.target
            if tgt.kind is not EntityKind.FILE:
                continue
            resolved = self._resolve(tgt)
            if resolved is None:
                continue
            out.append(resolved)  # type: ignore[arg-type]
            if limit is not None and len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------
    # Overviews (mapping table row 11)
    # ------------------------------------------------------------------
    def overview_as_dict(self, name: str) -> Optional[dict[str, object]]:
        """Build the named overview table and return its dict form.

        Reads from the module-level :data:`OVERVIEWS` singleton (in
        ``src.enrichment.overviews.registries``). The returned dict
        shape mirrors the legacy ``overview_as_dict`` (entity_id ->
        column -> cell-as-dict)::

            {
              "name": "...",
              "columns": [...],
              "rows": {entity_id: {col: cell.model_dump()}}
            }

        **Stub fallback.** Chunk-7 ships skeletal stubs for every
        :class:`OverviewTableBuilder` — most ``build()`` methods raise
        :class:`NotImplementedError`. To keep the agent-facing surface
        usable today, those errors are caught here and returned as
        ``None`` (the legacy shape for "table not loaded"). When
        Chunk 10 (or later) ports the real ``build()``, this method
        will start returning the materialised table without any change
        to callers.
        """
        # Local import to avoid forcing the overviews package to load
        # at server-start; the chunk-8 server doesn't auto-import the
        # implementations module either.
        from src.enrichment.overviews.implementations import (  # noqa: F401
            authorship_table as _eager_load,  # side-effect: register builders
        )
        from src.enrichment.overviews.registries import OVERVIEWS

        try:
            builder_cls = OVERVIEWS.get(name)
        except KeyError:
            return None

        try:
            table = builder_cls().build(self._graph, None)
        except NotImplementedError:
            return None

        # Subclasses (FilteredSandboxView) can drop rows whose entity_id
        # is excluded. ``_keep_overview_row`` is a no-op on the base view.
        kept_rows = [
            row for row in table.rows
            if self._keep_overview_row(table.entity_kind, row.entity_id)
        ]

        return {
            "name": table.name,
            "columns": list(table.columns),
            "rows": {
                row.entity_id: {
                    col: cell.model_dump() for col, cell in row.cells.items()
                }
                for row in kept_rows
            },
        }

    def _keep_overview_row(self, entity_kind: str, entity_id: str) -> bool:
        """Hook for filter-rules: return ``False`` to drop a row.

        Base view keeps every row. :class:`FilteredSandboxView` overrides
        this to honour the project's excluded-id sets.
        """
        return True

    # ------------------------------------------------------------------
    # Catalog helpers (mapping table rows 12, 13)
    # ------------------------------------------------------------------
    def list_metrics(self) -> list[dict[str, object]]:
        """Catalog of every registered :class:`Metric` subclass.

        Reads the module-level :data:`METRICS` singleton. Side-loads
        ``metrics.implementations`` so the catalog is populated even
        if no caller imported the v2 pipeline yet.

        Each entry::

            {
              "name":              "<Metric.name>",
              "family":            "<short module-tail (e.g. 'anomaly_testing')>",
              "emits_traits":      [...],
              "emits_classifiers": [...],
              "emits_relations":   [...],
              "config_fields":     [...],
            }

        Replaces the broken ``GET /enrichments/catalog`` REST surface;
        Chunk 20 wires the MCP ``list_metrics()`` tool through here via
        ``/execute``. Read-only — never touches registry state.
        """
        # Side-load implementations so the catalog is populated.
        from src.enrichment.metrics import (  # noqa: F401
            implementations as _impls,
        )
        from src.enrichment.metrics import METRICS

        out: list[dict[str, object]] = []
        for cls in METRICS.all():
            outputs = getattr(cls, "outputs", None)
            family = cls.__module__.rsplit(".", 1)[-1]
            out.append(
                {
                    "name": cls.name,
                    "family": family,
                    "emits_traits": list(getattr(outputs, "emits_traits", []) or []),
                    "emits_classifiers": list(
                        getattr(outputs, "emits_classifiers", []) or []
                    ),
                    "emits_relations": list(
                        getattr(outputs, "emits_relations", []) or []
                    ),
                    "config_fields": list(
                        getattr(cls, "config_fields", []) or []
                    ),
                }
            )
        return out

    def list_overviews(self) -> list[str]:
        """Names of every registered :class:`OverviewTableBuilder`."""
        # Side-load the implementations so all 11 stubs are registered
        # even if no caller imported them yet.
        from src.enrichment.overviews import implementations as _impls  # noqa: F401
        from src.enrichment.overviews.registries import OVERVIEWS

        return list(OVERVIEWS.names())

    # ------------------------------------------------------------------
    # Per-file Lizard metrics (Chunk 20 — was GET /enrichments/metrics/files)
    # ------------------------------------------------------------------
    def list_file_metrics(
        self,
        min_loc: float = 0.0,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> dict[str, object]:
        """Per-file Lizard complexity rows, sorted by ``sum_nloc`` desc.

        Reads :attr:`Graph.file_metrics` (the Chunk-8
        :class:`FileMetricRegistry`). One :class:`FileMetric` row exists
        per ``(file, metric_name)`` pair; this helper pivots them back
        into the file-level shape the legacy
        ``GET /enrichments/metrics/files`` payload emitted::

            {
              "count": <int>,
              "files": [
                {
                  "file_path":             "src/A.java",
                  "source":                "lizard",
                  "sum_nloc":              <float|None>,
                  "max_ccn":               <float|None>,
                  "avg_ccn":               <float|None>,
                  "function_count":        <float|None>,
                  "longest_function_nloc": <float|None>,
                },
                ...
              ],
            }

        Empty result (``{"count": 0, "files": []}``) when no Lizard CSV
        was ingested for the loaded project.

        ``min_loc`` filters by ``sum_nloc``; ``limit`` / ``offset``
        paginate the (post-sort) list. Pagination is honoured because
        a project with thousands of files would otherwise overrun a
        single ``/execute`` stdout buffer.
        """
        # Pivot one row per file: file_path -> {metric_name: value}, plus a
        # single ``source`` string per file. Multiple sources on the same
        # file collapse to the first one observed (Lizard is the only
        # source today; the loop tolerates future tools.).
        per_file: dict[str, dict[str, float]] = {}
        sources: dict[str, str] = {}
        for fm in self.file_metrics:
            file_path = fm.file_ref.id
            # Skip metrics whose file is filtered out at the view level.
            if self.files.get(file_path) is None:
                continue
            bucket = per_file.setdefault(file_path, {})
            bucket[fm.metric_name] = fm.value
            sources.setdefault(file_path, fm.source)

        rows: list[dict[str, object]] = []
        for file_path, metrics in per_file.items():
            sum_nloc = metrics.get("sum_nloc")
            if sum_nloc is not None and sum_nloc < min_loc:
                continue
            rows.append(
                {
                    "file_path": file_path,
                    "source": sources[file_path],
                    "sum_nloc": sum_nloc,
                    "max_ccn": metrics.get("max_ccn"),
                    "avg_ccn": metrics.get("avg_ccn"),
                    "function_count": metrics.get("function_count"),
                    "longest_function_nloc": metrics.get("longest_function_nloc"),
                }
            )

        # Sort by sum_nloc desc, ``None``-last for stability.
        rows.sort(key=lambda r: (r["sum_nloc"] is None, -(r["sum_nloc"] or 0.0)))

        total = len(rows)
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return {"count": total, "files": rows}

    # ------------------------------------------------------------------
    # Code structure summary (Chunk 20 — was GET /enrichments/code-structure/summary)
    # ------------------------------------------------------------------
    def code_structure_summary(self) -> dict[str, object]:
        """Per-project counts from the CodeFrame (B2) layer.

        Reads :attr:`Graph.code_structure_projects` to discover whether
        any code-structure ingest happened, plus :attr:`Graph.code_types`
        / ``.code_methods`` / ``.code_fields`` / ``.code_refs`` to roll
        the counts up by project.

        Shape::

            {
              "loaded": <bool>,
              "source": "codeframe" | None,
              "projects": [
                {
                  "project_id":   "...",
                  "project_name": "...",
                  "kind_of_source": "codeframe",
                  "type_count":   <int>,
                  "method_count": <int>,
                  "field_count":  <int>,
                  "ref_count":    <int>,
                },
                ...
              ],
            }

        ``loaded=False`` (with ``"projects": []`` and
        ``"source": None``) when no code-structure project exists.
        ``source`` is the ``kind_of_source`` of the first project
        when exactly one is loaded; ``None`` for the multi-project
        case (caller inspects ``projects[*].kind_of_source``).
        """
        projects = self.code_structure_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        types = self.code_types
        methods = self.code_methods
        fields = self.code_fields
        refs = self.code_refs

        rows: list[dict[str, object]] = []
        for p in projects:
            p_ref = p.ref()
            rows.append(
                {
                    "project_id": p.id,
                    "project_name": p.name,
                    "kind_of_source": p.kind_of_source,
                    "type_count": len(types.by_project.get(p_ref, ())),
                    "method_count": len(methods.by_project.get(p_ref, ())),
                    "field_count": len(fields.by_project.get(p_ref, ())),
                    "ref_count": len(refs.by_project.get(p_ref, ())),
                }
            )

        # ``source`` is a convenience for the common single-project case.
        canonical = projects[0].kind_of_source if len(projects) == 1 else None
        return {"loaded": True, "source": canonical, "projects": rows}

    # ------------------------------------------------------------------
    # Duplication summary (Chunk 20 — was GET /enrichments/duplication/summary)
    # ------------------------------------------------------------------
    def duplication_summary(self) -> dict[str, object]:
        """Per-project counts from the DuDe (B3) layer.

        Reads :attr:`Graph.duplication_projects` and
        :attr:`Graph.duplications` (the
        :class:`DuplicationPairRegistry`). Pairs are bucketed by
        :class:`DuplicationKind` (``external`` / ``sibling`` /
        ``internal``).

        Shape::

            {
              "loaded": <bool>,
              "source": "dude" | None,
              "projects": [
                {
                  "project_id":     "...",
                  "project_name":   "...",
                  "external_pairs": <int>,
                  "sibling_pairs":  <int>,
                  "internal_pairs": <int>,
                  "total_pairs":    <int>,
                },
                ...
              ],
            }

        ``loaded=False`` when no duplication project exists.
        """
        from src.common.domains.duplication.models import DuplicationKind

        projects = self.duplication_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        pairs_reg = self.duplications
        rows: list[dict[str, object]] = []
        for p in projects:
            p_ref = p.ref()
            project_pairs = pairs_reg.by_project.get(p_ref, ())
            external = sum(
                1 for pp in project_pairs
                if pp.duplication_kind is DuplicationKind.EXTERNAL
            )
            sibling = sum(
                1 for pp in project_pairs
                if pp.duplication_kind is DuplicationKind.SIBLING
            )
            internal = sum(
                1 for pp in project_pairs
                if pp.duplication_kind is DuplicationKind.INTERNAL
            )
            rows.append(
                {
                    "project_id": p.id,
                    "project_name": p.name,
                    "external_pairs": external,
                    "sibling_pairs": sibling,
                    "internal_pairs": internal,
                    "total_pairs": len(project_pairs),
                }
            )

        # All duplication data today comes from DuDe; the field stays a
        # forward-compatible hatch for future tools.
        return {"loaded": True, "source": "dude", "projects": rows}

    # ------------------------------------------------------------------
    # Discoverability — list_registries (P0)
    # ------------------------------------------------------------------
    def list_registries(self) -> list[dict[str, object]]:
        """Inventory of every typed registry on the underlying Graph.

        Driven by :data:`src.common.kernel.graph._FIELD_SPECS` so the
        list stays in sync as new domains are added (no manual
        maintenance here). Each entry::

            {
              "name":           "<graph field name>",
              "entity_kind":    "<EntityKind value>",
              "registry_type":  "<Registry subclass name>",
              "count":          <int>,
              "indexes":        ["by_X", "by_Y", ...],
            }

        Use this from ``/execute`` to discover what's queryable —
        especially useful for fields not surfaced as explicit
        properties on the view (everything beyond ``commits`` /
        ``files`` / ``issues`` / ``pull_requests`` reaches the
        underlying Graph via ``__getattr__``).
        """
        from src.common.kernel.graph import _FIELD_SPECS

        out: list[dict[str, object]] = []
        for name, _cls, kind, _disk in _FIELD_SPECS:
            reg = getattr(self._graph, name)
            specs = getattr(type(reg), "indexes", []) or []
            index_names = [s.name for s in specs]
            out.append(
                {
                    "name": name,
                    "entity_kind": kind.value,
                    "registry_type": type(reg).__name__,
                    "count": len(reg),
                    "indexes": index_names,
                }
            )
        return out

    # ------------------------------------------------------------------
    # Per-entity helpers (P1)
    # ------------------------------------------------------------------
    def traits_for(
        self, ref: EntityRef, name: Optional[str] = None
    ) -> list[Trait]:
        """Every :class:`Trait` whose ``target`` is ``ref``.

        Optionally filter by trait name (matches ``trait.name == name``).
        Wraps :meth:`TraitRegistry.for_target` so callers don't have to
        reach through ``graph_data.traits.for_target(ref)``.
        """
        traits = self.traits.for_target(ref)
        if name is None:
            return list(traits)
        return [t for t in traits if getattr(t, "name", None) == name]

    def relations_for(
        self,
        ref: EntityRef,
        kind: Optional[str] = None,
        window: Optional[WindowKind | str] = None,
        direction: Literal["out", "in", "both"] = "both",
    ) -> list[Relation]:
        """Relations involving ``ref``, filtered by kind/window/direction.

        * ``direction="out"`` — relations whose ``source == ref``
        * ``direction="in"``  — relations whose ``target == ref``
        * ``direction="both"`` (default) — union of the two, deduped

        ``kind`` filters by ``relation_kind`` exact-match. ``window``
        accepts either :class:`WindowKind` or its bare string value.
        """
        rels: list[Relation] = []
        if direction in ("out", "both"):
            rels.extend(self.relations.for_source(ref))
        if direction in ("in", "both"):
            rels.extend(self.relations.for_target(ref))
        if direction == "both" and rels:
            # Dedup by id when a relation is both source-side and
            # target-side (self-relations, mostly).
            seen: set[str] = set()
            deduped: list[Relation] = []
            for r in rels:
                if r.id in seen:
                    continue
                seen.add(r.id)
                deduped.append(r)
            rels = deduped
        if kind is not None:
            rels = [r for r in rels if r.relation_kind == kind]
        if window is not None:
            win = (
                WindowKind(window)
                if isinstance(window, str) and not isinstance(window, WindowKind)
                else window
            )
            rels = [r for r in rels if r.window == win]
        return rels

    def metrics_for_file(self, file_id: str) -> dict[str, float]:
        """All Lizard-style metrics for one file, pivoted by metric name.

        Returns ``{metric_name: value}`` (e.g. ``{"sum_nloc": 220.0,
        "max_ccn": 11.0, ...}``). Empty dict when the file has no
        recorded metrics. Same shape as one row of
        :meth:`list_file_metrics` but for a single file.

        Perf: uses the :class:`FileMetricRegistry` ``by_file`` index
        instead of scanning every ``(file, metric)`` row, so a per-file
        agent loop is O(F·k) (k = metrics per file, typically ~5)
        instead of O(F·M) — same trap
        :func:`src.sandbox.helpers.commit_issues` hit with the
        commit↔issue regex join.
        """
        file_ref = EntityRef(kind=EntityKind.FILE, id=file_id)
        if self.files.get(file_id) is None:
            return {}
        return {
            fm.metric_name: fm.value
            for fm in self.file_metrics.by_file[file_ref]
        }

    # ------------------------------------------------------------------
    # Per-domain summary helpers (P2)
    #
    # Same shape pattern as the existing ``code_structure_summary`` /
    # ``duplication_summary``: ``{loaded, source, projects: [...]}``
    # with per-project counts. ``loaded=False`` for empty graphs.
    # ------------------------------------------------------------------
    def quality_summary(self) -> dict[str, object]:
        """Per-project counts from the quality (Insider/Sonar) layer."""
        projects = self.quality_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        issues_reg = self.quality_issues
        rows: list[dict[str, object]] = []
        for p in projects:
            p_ref = p.ref()
            issues = issues_reg.by_project.get(p_ref, ())
            by_severity = Counter(
                i.severity for i in issues if i.severity is not None
            )
            by_category = Counter(i.category for i in issues)
            by_rule = Counter(i.rule_id for i in issues)
            rows.append(
                {
                    "project_id": p.id,
                    "project_name": p.name,
                    "source_tool": p.source_tool,
                    "issue_count": len(issues),
                    "by_severity": dict(by_severity),
                    "by_category": dict(by_category),
                    "top_rules": by_rule.most_common(10),
                }
            )

        canonical = projects[0].source_tool if len(projects) == 1 else None
        return {"loaded": True, "source": canonical, "projects": rows}

    def github_summary(self) -> dict[str, object]:
        """Per-project counts of GitHub PRs, reviews, comments, commits."""
        projects = self.github_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        prs_reg = self.pull_requests
        reviews_reg = self.reviews
        rcs_reg = self.review_comments
        commits_reg = self.github_commits
        users_reg = self.github_users

        rows: list[dict[str, object]] = []
        for p in projects:
            p_ref = p.ref()
            prs = prs_reg.by_project.get(p_ref, ())
            users = users_reg.by_project.get(p_ref, ())
            pr_states = Counter(pr.state for pr in prs)

            reviews_total = 0
            review_states: Counter[str] = Counter()
            rc_total = 0
            commits_total = 0
            for pr in prs:
                pr_ref = pr.ref()
                pr_reviews = reviews_reg.by_pull_request.get(pr_ref, ())
                reviews_total += len(pr_reviews)
                for rv in pr_reviews:
                    review_states[rv.state] += 1
                rc_total += len(rcs_reg.by_pull_request.get(pr_ref, ()))
                commits_total += len(commits_reg.by_pull_request.get(pr_ref, ()))

            rows.append(
                {
                    "project_id": p.id,
                    "project_name": p.name,
                    "users": len(users),
                    "pull_requests": len(prs),
                    "pr_by_state": dict(pr_states),
                    "reviews": reviews_total,
                    "review_by_state": dict(review_states),
                    "review_comments": rc_total,
                    "commits": commits_total,
                }
            )

        return {"loaded": True, "source": "github", "projects": rows}

    def jira_summary(self) -> dict[str, object]:
        """Per-project counts of JIRA issues, users, statuses, types."""
        projects = self.jira_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        issues_reg = self.issues
        users_reg = self.jira_users
        statuses_reg = self.issue_statuses
        types_reg = self.issue_types

        rows: list[dict[str, object]] = []
        for p in projects:
            p_ref = p.ref()
            issues = issues_reg.by_project.get(p_ref, ())
            by_status: Counter[str] = Counter()
            by_type: Counter[str] = Counter()
            for issue in issues:
                if issue.status_ref is not None:
                    status = statuses_reg.get(issue.status_ref.id)
                    by_status[status.name if status else "<unknown>"] += 1
                if issue.type_ref is not None:
                    itype = types_reg.get(issue.type_ref.id)
                    by_type[itype.name if itype else "<unknown>"] += 1

            rows.append(
                {
                    "project_id": p.id,
                    "project_name": p.name,
                    "users": len(users_reg.by_project.get(p_ref, ())),
                    "issues": len(issues),
                    "statuses_defined": len(statuses_reg.by_project.get(p_ref, ())),
                    "types_defined": len(types_reg.by_project.get(p_ref, ())),
                    "by_status": dict(by_status),
                    "by_type": dict(by_type),
                }
            )

        return {"loaded": True, "source": "jira", "projects": rows}

    def git_summary(self) -> dict[str, object]:
        """Per-project counts of git commits, files, changes, accounts."""
        projects = self.git_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        commits_reg = self.commits
        files_reg = self.files
        accounts_reg = self.git_accounts
        changes_reg = self.changes

        rows: list[dict[str, object]] = []
        for p in projects:
            p_ref = p.ref()
            commits = commits_reg.by_project.get(p_ref, ())
            files = files_reg.by_project.get(p_ref, ())
            accounts = accounts_reg.by_project.get(p_ref, ())

            # Changes have no by_project index; aggregate by walking
            # commit -> changes per commit (cheap; one dict lookup per
            # commit, dominated by commit count which is ~thousands).
            changes_total = 0
            for c in commits:
                changes_total += len(changes_reg.by_commit.get(c.ref(), ()))

            rows.append(
                {
                    "project_id": p.id,
                    "project_name": p.name,
                    "commits": len(commits),
                    "files": len(files),
                    "accounts": len(accounts),
                    "changes": changes_total,
                }
            )

        return {"loaded": True, "source": "git", "projects": rows}

    # ------------------------------------------------------------------
    # Iteration / repr — small ergonomics
    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        graph = self._graph
        return (
            f"MCPSandboxView(project_id={graph.project_id!r}, "
            f"commits={len(graph.commits)}, "
            f"files={len(graph.files)}, "
            f"issues={len(graph.issues)}, "
            f"pull_requests={len(graph.pull_requests)})"
        )

    def __iter__(self) -> Iterator[str]:
        """Iterate the underlying graph's project_id-keyed metadata.

        Mostly here so a stray ``for x in graph_data`` doesn't blow
        up — yields the four main registry names so the surface looks
        like the legacy dict-keys iteration shape.
        """
        return iter(("commits", "files", "issues", "pull_requests"))


__all__ = ["MCPSandboxView"]
