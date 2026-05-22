"""File classifiers metric — file.status / file.role / file.creationYear.

Port of legacy ``src/enrichment/tagger/file_classifiers.py``. Emits a
:class:`Classifier` per (file, dimension) tuple — three classifiers per
file. Drives the MCP sandbox's ``classifiers.with_value("role", "test")``
lookups.

Dimensions:

* ``"status"``       — ``"active"`` / ``"idle"`` (last change vs cutoff)
* ``"role"``         — ``"production"`` / ``"test"`` / ``"config"`` /
                       ``"doc"`` / ``"build"`` (regex catalogs in
                       :class:`EnrichmentConfig`)
* ``"creationYear"`` — year string of the file's first change

Reads from the host: ``files`` (registry), ``changes`` (via
``by_file`` index), ``commits`` (via ``get``). The recent cutoff comes
from ``graph.recent_cutoff`` when the host carries one, else falls back
to the latest commit date minus ``cfg.recent_window_days`` — mirrors
``AuthorClassifierMetric`` so the ``status`` dimension is no longer
degenerate when no explicit anchor is attached.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Classifier

if TYPE_CHECKING:
    from src.common.kernel import Graph


_DEFAULT_RECENT_WINDOW_DAYS = 90


@METRICS.register
class FileClassifierMetric(Metric):
    """Emits the three mandatory file-level :class:`Classifier` rows."""

    name: ClassVar[str] = "file.classifiers"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=["status", "role", "creationYear"]
    )
    config_fields: ClassVar[list[str]] = [
        "recent_window_days",
        "build_patterns",
        "test_patterns",
        "doc_patterns",
        "config_patterns",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Classifier]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        commits_reg = getattr(graph, "commits", None)
        cutoff = _resolve_recent_cutoff(graph, commits_reg, config)

        changes_by_file = _changes_by_file_index(graph)
        commits_get = _entity_by_id(commits_reg)

        for file_ in files:
            file_ref = file_.ref()
            file_id_for_classifier_id = file_.id

            # ── status ─────────────────────────────────────────────────
            dates = _change_dates(changes_by_file(file_ref), commits_get)
            last = max(dates) if dates else None
            if last is None:
                status = "idle"
            elif cutoff is None or last >= cutoff:
                status = "active"
            else:
                status = "idle"
            yield Classifier(
                id=f"status:{file_ref.kind.value}/{file_id_for_classifier_id}",
                target=file_ref,
                dimension="status",
                value=status,
            )

            # ── role ───────────────────────────────────────────────────
            role = _classify_role(file_.path, config)
            yield Classifier(
                id=f"role:{file_ref.kind.value}/{file_id_for_classifier_id}",
                target=file_ref,
                dimension="role",
                value=role,
            )

            # ── creationYear ───────────────────────────────────────────
            first = min(dates) if dates else None
            if first is not None:
                yield Classifier(
                    id=f"creationYear:{file_ref.kind.value}/{file_id_for_classifier_id}",
                    target=file_ref,
                    dimension="creationYear",
                    value=str(first.year),
                )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _entity_by_id(reg: Any):
    if reg is None:
        return lambda _id: None
    get = getattr(reg, "get", None)
    if get is None:
        return lambda _id: None
    return get


def _changes_by_file_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _file_ref: []
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]

    def scan(file_ref):
        return [ch for ch in changes if ch.file_ref == file_ref]

    return scan


def _change_dates(changes: Iterable[Any], commits_get) -> list[Any]:
    dates: list[Any] = []
    for change in changes:
        commit = commits_get(change.commit_ref.id)
        if commit is None:
            continue
        d = getattr(commit, "author_date", None)
        if d is not None:
            dates.append(d)
    return dates


def _classify_role(path: str, config: Any) -> str:
    """File-role bucket. Mirror of legacy
    ``file_classifiers._classify_role``. Build wins over config / doc /
    test (many build files have config-like extensions).
    """
    if not path:
        return "production"
    build_patterns = _config_field(config, "build_patterns", [])
    test_patterns = _config_field(config, "test_patterns", [])
    doc_patterns = _config_field(config, "doc_patterns", [])
    config_patterns = _config_field(config, "config_patterns", [])

    for p in build_patterns:
        if p.search(path):
            return "build"
    for p in test_patterns:
        if p.search(path):
            return "test"
    for p in doc_patterns:
        if p.search(path):
            return "doc"
    for p in config_patterns:
        if p.search(path):
            return "config"
    return "production"


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


def _resolve_recent_cutoff(
    graph: Any, commits_reg: Any, config: Any
) -> Optional[datetime]:
    """Reproduce the legacy ``recent_cutoff`` semantics.

    Priority order (mirrors :func:`author_classifiers._resolve_recent_cutoff`):

    1. An explicit ``graph.recent_cutoff`` attribute attached by the
       caller (used by the legacy test stubs and the production
       processor when it carries a snapshot anchor).
    2. ``latest_commit_date(commits) - recent_window_days``.
    3. ``None`` (no commits — caller treats files as active).
    """
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    if commits_reg is None:
        return None
    window_days = _config_field(
        config, "recent_window_days", _DEFAULT_RECENT_WINDOW_DAYS
    )
    latest: Optional[datetime] = None
    try:
        for c in commits_reg:
            d = ensure_aware(
                getattr(c, "author_date", None)
                or getattr(c, "committer_date", None)
            )
            if d is None:
                continue
            if latest is None or d > latest:
                latest = d
    except TypeError:
        return None
    if latest is None:
        return None
    return latest - timedelta(days=window_days)


__all__ = ["FileClassifierMetric"]
