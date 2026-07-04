"""Commit task-prefix classifier metric (Phase 2 decision D3).

Parses every commit message for Jira-style task keys (``PROJ-123``) and
emits one :class:`Classifier` per distinct prefix per commit, on
dimension ``"task_prefix"``. Downstream relation builders
(``cochange.file_shared_task_prefixes``,
``cochange.author_shared_task_prefixes``,
``cochange.component_shared_task_prefixes``) consume these classifiers
through :meth:`ClassifierRegistry.with_value` instead of re-parsing
commit messages themselves — that's the D3 win.

This metric must run **before** any relation builder that consumes the
``task_prefix`` dimension. The pipeline runs metrics in registration
order; we register early via :data:`METRICS` decorator at import time
inside :mod:`src.enrichment.metrics.implementations.__init__` (importing
this module before the cochange-relation modules is enough). The driver
also separates stages — relation builders fire before metrics — but the
shared-task-prefix relation builders are themselves in the relation
stage, so the chain is: this metric is consumed by a *later* pipeline
invocation, OR the consumers re-extract from commit messages until
Chunks 13/14 wire them through ``graph.classifiers``.

Outputs
-------
Per commit, for each unique prefix appearing in ``commit.message``::

    Classifier(
        id="task_prefix:commit/<sha>#<prefix>",
        target=commit_ref,
        dimension="task_prefix",
        value=<prefix>,
    )

Empty messages and messages with no Jira-style key yield no classifiers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Classifier
from src.enrichment.utils.task_prefix import extract_task_prefixes

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class CommitTaskPrefixClassifierMetric(Metric):
    """Emit one :class:`Classifier` per distinct Jira-style key per commit."""

    name: ClassVar[str] = "commit_task_prefixes"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.COMMIT)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=["task_prefix"],
    )
    config_fields: ClassVar[list[str]] = []

    def compute(self, graph: "Graph", config: Any) -> Iterable[Classifier]:
        commits = getattr(graph, "commits", None)
        if commits is None:
            return
        for commit in commits:
            message = getattr(commit, "message", "") or ""
            prefixes = extract_task_prefixes(message)
            if not prefixes:
                continue
            commit_ref = commit.ref()
            for prefix in prefixes:
                yield Classifier(
                    id=f"task_prefix:{commit_ref.kind.value}/{commit.id}#{prefix}",
                    target=commit_ref,
                    dimension="task_prefix",
                    value=prefix,
                )


__all__ = ["CommitTaskPrefixClassifierMetric"]
