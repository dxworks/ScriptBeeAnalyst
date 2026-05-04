"""anomaly.structuring.PivotFile (coupling-basis) — high coupling-graph degree.

Implements §7 (PivotFileCoupling) of communication/B2_codeframe/index_step_general.md.

Per orchestrator decision: this is a SEPARATE tagger from the existing
co-change PivotFile in `anomaly_structuring.py` — same trait name, different
basis. Cleaner test isolation than retrofitting the existing tagger with a
`basis` field. Both fire independently; the agent can filter by
`evidence.basis` ∈ {"cochange", "coupling"} when both are emitted.

dx port (PivotFile.java:21–56): degree on the coupling graph; severity =
peer count − threshold + 1 with peers_threshold = max(dependencyHub, 20).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.enrichment.models import EntityTags, Trait
from src.enrichment.tagger.base import TaggingContext, make_trait


# dx's MANY_PEERS constant — coupling pivots only fire above this floor.
_MANY_PEERS = 20


class PivotFileCouplingTagger:
    """PivotFile (basis=coupling) — fires on files whose structural-coupling
    degree (distinct file peers across all CodeReference kinds) is above
    `max(cfg.pivotfile_cochange_degree_min, MANY_PEERS=20)`.

    Distinct peers = unique target files across ALL CodeReference kinds
    (calls + fieldAccess + inheritance + interface). Severity = peer count.
    """

    TRAITS = [
        {"name": "anomaly.structuring.PivotFile", "entity": "file", "family": "structuring"},
    ]

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        cs = ctx.graph_data.get("code_structure")
        if cs is None:
            return []

        cfg = ctx.config
        threshold = max(cfg.pivotfile_cochange_degree_min, _MANY_PEERS)

        peers: dict[str, set[str]] = defaultdict(set)
        for ref in cs.reference_registry.all:
            if ref.from_file_path == ref.to_file_path:
                continue
            peers[ref.from_file_path].add(ref.to_file_path)
            peers[ref.to_file_path].add(ref.from_file_path)

        out: list[EntityTags] = []
        for fid, neighbours in peers.items():
            degree = len(neighbours)
            if degree < threshold:
                continue
            traits: list[Trait] = [make_trait(
                "anomaly.structuring.PivotFile",
                family="structuring",
                severity=float(degree),
                basis="coupling",
                coupling_degree=degree,
                threshold=threshold,
            )]
            out.append(EntityTags(entity_kind="file", entity_id=fid, traits=traits))
        return out
