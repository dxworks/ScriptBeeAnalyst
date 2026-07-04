"""Coupling anomaly metric — v2 port.

Port of legacy ``src/enrichment/tagger/anomaly_coupling.py`` (~69 LOC).
Emits ``anomaly.structuring.PivotFile`` :class:`Trait` rows on files
whose structural-coupling degree (distinct file peers across the
``coupling`` relation kind) is above
``cfg.pivotfile_cochange_degree_min`` (default 10).

dx port (PivotFile.java:21–56):

* Same trait name as the cochange-based PivotFile (``anomaly_structuring``)
  — both fire independently with ``evidence["basis"]`` discriminating
  ``"coupling"`` vs ``"cochange"``. Cleaner test isolation than
  retrofitting one tagger with a ``basis`` field.
* Degree = distinct peer files across all coupling edges (the v2
  ``coupling`` relation builder already merges per-``reference_kind``
  weights into one edge per file-pair, so degree is "distinct
  ``coupling`` edges where this file is an endpoint").
* Severity = peer count (raw degree).

The legacy walked ``CodeReference`` rows directly; v2 reads the
already-aggregated ``coupling`` relation edges instead — same signal,
single source of truth, and stays consistent with §5 of the plan's
"Reuse map" ("relations through ``BUILDERS``; no parallel scans").

Previously this emitter clamped the threshold to ``max(degree_min,
MANY_PEERS=20)`` while the structuring (cochange) emitter used the knob
directly. The hard-coded floor silently overrode the user's editor
input AND created an asymmetry where the same trait name fired under
different conditions depending on basis. Both emitters now honour the
same knob with the same interpretation — to recover dx parity (≥20
peers), dial the knob up in the editor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable

from src.common.kernel import EntityKind
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait, TraitFamily

if TYPE_CHECKING:
    from src.common.kernel import Graph


_DEFAULT_DEGREE_MIN = 10  # cfg.pivotfile_cochange_degree_min legacy default
_TRAIT_NAME = "anomaly.structuring.PivotFile"


@METRICS.register
class AnomalyCouplingMetric(Metric):
    name: ClassVar[str] = "anomaly.coupling"
    inputs: ClassVar[MetricInputs] = MetricInputs(
        source_kind=EntityKind.FILE,
        relation_kind="coupling",
    )
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[_TRAIT_NAME]
    )
    config_fields: ClassVar[list[str]] = ["pivotfile_cochange_degree_min"]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        relations = getattr(graph, "relations", None)
        if relations is None:
            return

        of_kind = getattr(relations, "of_kind", None)
        if of_kind is None:
            return
        coupling_rels = list(of_kind("coupling"))
        if not coupling_rels:
            return

        # Previously clamped to ``max(degree_min, MANY_PEERS=20)`` — the
        # hard-coded floor was removed to align with the structuring
        # (cochange) emitter; both bases now honour the same knob.
        threshold = int(_config_field(
            config, "pivotfile_cochange_degree_min", _DEFAULT_DEGREE_MIN
        ))

        # Build peer set per file. The coupling builder emits one edge per
        # (src_file, tgt_file) — drop self-loops defensively and treat
        # the relation as undirected for degree (legacy did the same).
        peers: dict[Any, set[Any]] = {}
        for rel in coupling_rels:
            src = rel.source
            tgt = rel.target
            if src == tgt:
                continue
            peers.setdefault(src, set()).add(tgt)
            peers.setdefault(tgt, set()).add(src)

        for file_ref, neighbours in peers.items():
            degree = len(neighbours)
            if degree < threshold:
                continue
            yield Trait(
                id=f"trait:{_TRAIT_NAME}:coupling:{file_ref.kind.value}/{file_ref.id}",
                target=file_ref,
                family=TraitFamily.STRUCTURING,
                name=_TRAIT_NAME,
                severity=float(degree),
                evidence={
                    "basis": "coupling",
                    "coupling_degree": int(degree),
                    "threshold": int(threshold),
                },
            )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


__all__ = ["AnomalyCouplingMetric"]
