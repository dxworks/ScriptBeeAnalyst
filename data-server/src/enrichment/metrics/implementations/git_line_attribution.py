"""Git per-line attribution metric — gated annotated-lines reconstruction.

Port of legacy ``main``'s build-time annotated-lines feature
(``Change.compute_annotated_lines`` toggle + ``Commit.repo_size`` +
``File.annotated_lines``) into the v2 enrichment layer (plan §3).

The heavy lifting — the faithful per-line replay over the typed git graph
including merge reconciliation — lives in
:func:`src.enrichment.utils.annotated_lines.compute_annotated_lines`
(Chunk 1). This metric is the thin, pipeline-facing wrapper: it gates the
(expensive) reconstruction on ``config.compute_annotated_lines`` and emits
the result as tags.

Emissions (only when the flag is on):

* per :class:`File` → ``Classifier(dimension="git.loc", value=<LOC>)`` — the
  headline "lines per file" count = ``len`` of that file's surviving-line
  array.
* per :class:`File` → ``Trait(name="git.line_attribution")`` whose
  ``evidence["annotated_lines"]`` is the faithful per-line array (line →
  commit id) so blame-equivalent queries + the ``run_blame`` validator can
  read it.
* per :class:`Commit` → ``Classifier(dimension="git.repo_size", value=<size>)``
  — the repository line count after that commit.

Pure: never mutates the graph (honours the :class:`Metric` purity
contract). In particular it does NOT write the frozen ``Commit.repo_size``
model field — repo_size is surfaced as a ``git.repo_size`` classifier
instead (plan §"pure-metric constraint").

``name`` is intentionally kept OUT of ``pipeline._PHASE_B_METRIC_NAMES`` so
this runs in Phase A (build time), where the flag is threaded through.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Union

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Classifier, Trait, TraitFamily
from src.enrichment.utils.annotated_lines import compute_annotated_lines

if TYPE_CHECKING:
    from src.common.kernel import Graph


@METRICS.register
class GitLineAttributionMetric(Metric):
    """Emits ``git.loc`` / ``git.repo_size`` classifiers + a per-file
    ``git.line_attribution`` trait — gated by ``compute_annotated_lines``."""

    name: ClassVar[str] = "git.line_attribution"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_classifiers=["git.loc", "git.repo_size"],
        emits_traits=["git.line_attribution"],
    )
    config_fields: ClassVar[list[str]] = ["compute_annotated_lines"]

    def compute(
        self, graph: "Graph", config: Any
    ) -> Iterable[Union[Classifier, Trait]]:
        # Gated off → no emissions (no-op when the flag is False / absent).
        if not getattr(config, "compute_annotated_lines", False):
            return

        attribution, repo_sizes = compute_annotated_lines(graph)

        files_reg = getattr(graph, "files", None)
        files = _safe_iter(files_reg)
        for file_ in files:
            file_id = file_.id
            if file_id not in attribution:
                # Binary file / replay overflow — omitted by the utility.
                continue
            file_ref = file_.ref()
            lines = attribution[file_id]

            yield Classifier(
                id=f"git.loc:{file_ref.kind.value}/{file_id}",
                target=file_ref,
                dimension="git.loc",
                value=str(len(lines)),
            )
            yield Trait(
                id=f"git.line_attribution:{file_ref.kind.value}/{file_id}",
                target=file_ref,
                family=TraitFamily.KNOWLEDGE,
                name="git.line_attribution",
                evidence={"annotated_lines": list(lines)},
            )

        commits_reg = getattr(graph, "commits", None)
        commits = _safe_iter(commits_reg)
        for commit in commits:
            commit_id = commit.id
            if commit_id not in repo_sizes:
                continue
            commit_ref = commit.ref()
            yield Classifier(
                id=f"git.repo_size:{commit_ref.kind.value}/{commit_id}",
                target=commit_ref,
                dimension="git.repo_size",
                value=str(repo_sizes[commit_id]),
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


__all__ = ["GitLineAttributionMetric"]
