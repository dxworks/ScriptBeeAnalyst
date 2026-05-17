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
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Iterator, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.relations.models import WindowKind
from src.enrichment.tags.base import Classifier, Tag, Trait

if TYPE_CHECKING:
    from src.common.domains.git.models import Commit, File
    from src.common.domains.git.registries import CommitRegistry, FileRegistry
    from src.common.domains.github.models import PullRequest
    from src.common.domains.github.registries import PullRequestRegistry
    from src.common.domains.jira.models import Issue
    from src.common.domains.jira.registries import IssueRegistry
    from src.common.kernel import Graph


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

    __slots__ = ("_graph",)

    def __init__(self, graph: "Graph") -> None:
        self._graph = graph

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
        traits: tuple[Trait, ...] = self._graph.traits.for_target(ref)
        classifiers_by_dim: dict[str, Classifier] = (
            self._graph.classifiers.for_target(ref)
        )
        classifiers: list[Classifier] = list(classifiers_by_dim.values())
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
        for trait in self._graph.traits.of_name(name):
            tgt = trait.target
            if tgt.kind is not EntityKind.FILE:
                continue
            resolved = self._graph.resolve(tgt)
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
        for classifier in self._graph.classifiers.with_value(dim, value):
            tgt = classifier.target
            if tgt.kind is not EntityKind.FILE:
                continue
            resolved = self._graph.resolve(tgt)
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

        Reads :meth:`RelationRegistry.of_kind_in_window` for the
        ``"cochange"`` relation kind, filters by
        ``rel.source.id == file_id`` AND target kind FILE, resolves
        each target to a concrete :class:`File`, and returns at most
        ``limit`` results in source-iteration order (which is the
        emission order of the cochange builder — already a
        descending-strength heuristic for many builders, but not
        guaranteed; callers that need strict ordering should sort by
        ``strength`` themselves via ``view.relations.by_pair[...]``).

        ``window`` accepts both :class:`WindowKind` enum members and
        bare strings (``"lifetime"``, ``"recent"``).
        """
        out: list["File"] = []
        if limit is not None and limit <= 0:
            return out
        for rel in self._graph.relations.of_kind_in_window("cochange", window):
            if rel.source.id != file_id:
                continue
            tgt = rel.target
            if tgt.kind is not EntityKind.FILE:
                continue
            resolved = self._graph.resolve(tgt)
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

        return {
            "name": table.name,
            "columns": list(table.columns),
            "rows": {
                row.entity_id: {
                    col: cell.model_dump() for col, cell in row.cells.items()
                }
                for row in table.rows
            },
        }

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
        for fm in self._graph.file_metrics:
            file_path = fm.file_ref.id
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
        """Per-project counts from the JaFax / Codeframe (B2) layer.

        Reads :attr:`Graph.code_structure_projects` to discover whether
        any code-structure ingest happened, plus :attr:`Graph.code_types`
        / ``.code_methods`` / ``.code_fields`` / ``.code_refs`` to roll
        the counts up by project.

        Shape::

            {
              "loaded": <bool>,
              "source": "jafax" | "codeframe" | None,
              "projects": [
                {
                  "project_id":   "...",
                  "project_name": "...",
                  "kind_of_source": "jafax" | "codeframe",
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
        projects = self._graph.code_structure_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        types = self._graph.code_types
        methods = self._graph.code_methods
        fields = self._graph.code_fields
        refs = self._graph.code_refs

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

        projects = self._graph.duplication_projects.all()
        if not projects:
            return {"loaded": False, "source": None, "projects": []}

        pairs_reg = self._graph.duplications
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
