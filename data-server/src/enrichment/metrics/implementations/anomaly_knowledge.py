"""Knowledge anomaly metric — v2 port (Chunk 16).

Port of legacy ``src/enrichment/tagger/anomaly_knowledge.py``. Emits
ten :attr:`TraitFamily.KNOWLEDGE` traits across two sub-domains:

**File-level** (target = :class:`File`)::

    anomaly.knowledge.Orphan
    anomaly.knowledge.BusFactor1
    anomaly.knowledge.SharedKnowledge
    anomaly.knowledge.Accumulator
    anomaly.knowledge.OwnerChurn
    anomaly.knowledge.PolarisedOwnership
    anomaly.knowledge.Solitaire
    anomaly.knowledge.TeamChurn
    anomaly.knowledge.WeakOwnership

**Author-level** (target = :class:`GitAccount`)::

    anomaly.knowledge.OrphanCausers

Mutual exclusion
----------------

* **PolarisedOwnership ↔ BusFactor1** — both fire on dominated files.
  PolarisedOwnership represents the "tight pair" case; BusFactor1 the
  "single dominant author" case. We post-filter so BusFactor1
  suppresses PolarisedOwnership on the same file (matches legacy v1).

Reads
-----

* ``graph.files``                              — file population.
* ``graph.git_accounts``                       — author population
                                                 (for OrphanCausers).
* ``graph.commits.{by_author, get}``           — commit metadata.
* ``graph.changes.by_file``                    — per-file change walk.
* ``graph.classifiers.for_target(account_ref)``
                                                — author ``activity``
                                                 classifier (locked in
                                                 Chunk-11 handoff §"Decisions"
                                                 point 6).
* ``graph.recent_cutoff`` (optional)            — recent window anchor.

Reuses ``file_trait_utils`` helpers (Chunk 11): ``author_churn``,
``author_churn_within``, ``active_author_churn``, ``time_bucketed_churn``,
``files_touched_by_account``, ``commit_dates``.

Stage ordering: this metric runs in the same stage 2 as
``author_classifiers`` (which emits the ``activity`` classifier we
read). The classifier metric is imported FIRST in
``metrics/implementations/__init__.py`` so registration order →
``run_pipeline`` iteration order → activity classifiers exist by the
time we run.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Iterator, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tags import Trait, TraitFamily
from src.enrichment.utils.file_trait_utils import (
    active_author_churn,
    author_churn,
    author_churn_within,
    commit_dates,
    files_touched_by_account,
    time_bucketed_churn,
)

if TYPE_CHECKING:
    from src.common.kernel import Graph


# ----------------------------------------------------------------------
# Trait names
# ----------------------------------------------------------------------
_TRAIT_ORPHAN = "anomaly.knowledge.Orphan"
_TRAIT_BUSFACTOR1 = "anomaly.knowledge.BusFactor1"
_TRAIT_SHARED_KNOWLEDGE = "anomaly.knowledge.SharedKnowledge"
_TRAIT_ACCUMULATOR = "anomaly.knowledge.Accumulator"
_TRAIT_OWNER_CHURN = "anomaly.knowledge.OwnerChurn"
_TRAIT_POLARISED_OWNERSHIP = "anomaly.knowledge.PolarisedOwnership"
_TRAIT_SOLITAIRE = "anomaly.knowledge.Solitaire"
_TRAIT_TEAM_CHURN = "anomaly.knowledge.TeamChurn"
_TRAIT_WEAK_OWNERSHIP = "anomaly.knowledge.WeakOwnership"
_TRAIT_ORPHAN_CAUSERS = "anomaly.knowledge.OrphanCausers"


@METRICS.register
class AnomalyKnowledgeMetric(Metric):
    name: ClassVar[str] = "anomaly.knowledge"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            _TRAIT_ORPHAN,
            _TRAIT_BUSFACTOR1,
            _TRAIT_SHARED_KNOWLEDGE,
            _TRAIT_ACCUMULATOR,
            _TRAIT_OWNER_CHURN,
            _TRAIT_POLARISED_OWNERSHIP,
            _TRAIT_SOLITAIRE,
            _TRAIT_TEAM_CHURN,
            _TRAIT_WEAK_OWNERSHIP,
            _TRAIT_ORPHAN_CAUSERS,
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "hermit_dominance_ratio",
        "busfactor1_min_distinct_authors",
        "shared_knowledge_entropy_min",
        "shared_knowledge_min_distinct_authors",
        "accumulator_bucket_weeks",
        "accumulator_min_windows",
        "owner_churn_dominance_threshold",
        "polarised_top_share",
        "polarised_min_authors",
        "solitaire_min_lifetime_commits",
        "team_churn_set_change_ratio",
        "weak_owner_max_share",
        "weak_owner_min_active_authors",
        "orphancauser_min_orphan_files",
        "orphancauser_min_lifetime_commits",
        "orphancauser_orphan_sample_cap",
        "recent_window_days",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return

        cutoff = _resolve_recent_cutoff(graph, commits_reg, config)
        cfg = _KnowledgeConfig.from_config(config)

        # Author-activity totals come from the classifier registry that
        # `author_classifiers` (same stage, earlier import) populates.
        active_author_total = _active_author_total(graph)
        activity_lookup = _build_activity_lookup(graph)

        # Track which files were flagged Orphan so OrphanCausers can
        # post-process retired authors against them.
        orphan_file_ids: set[str] = set()

        for file_ in files:
            file_ref = file_.ref()
            churn_by_author = author_churn(graph, file_ref)
            if not churn_by_author:
                continue

            total = sum(churn_by_author.values())
            last = _last_change_date(graph, file_ref)
            lifetime_commits = _lifetime_commit_count(graph, file_ref)

            busfactor1_fired = False

            # ---- Orphan ----------------------------------------------------
            if len(churn_by_author) == 1:
                outside_recent = (
                    cutoff is not None and last is not None and last < cutoff
                )
                if outside_recent:
                    only_author = next(iter(churn_by_author.keys()))
                    orphan_file_ids.add(file_ref.id)
                    yield Trait(
                        id=_trait_id(_TRAIT_ORPHAN, file_ref),
                        target=file_ref,
                        family=TraitFamily.KNOWLEDGE,
                        name=_TRAIT_ORPHAN,
                        evidence={
                            "author": only_author,
                            "last_change": last.isoformat() if last else "",
                            "churn": int(total),
                        },
                    )

            # ---- BusFactor1 / Hermit --------------------------------------
            top_author, top_churn = max(
                churn_by_author.items(), key=lambda kv: kv[1]
            )
            dominance = top_churn / total if total > 0 else 0.0
            if (
                dominance >= cfg.hermit_dominance_ratio
                and len(churn_by_author) >= cfg.busfactor1_min_distinct_authors
            ):
                busfactor1_fired = True
                yield Trait(
                    id=_trait_id(_TRAIT_BUSFACTOR1, file_ref),
                    target=file_ref,
                    family=TraitFamily.KNOWLEDGE,
                    name=_TRAIT_BUSFACTOR1,
                    severity=round(dominance, 3),
                    evidence={
                        "dominant_author": top_author,
                        "dominance_ratio": round(dominance, 3),
                        "threshold": float(cfg.hermit_dominance_ratio),
                        "distinct_authors": int(len(churn_by_author)),
                    },
                )

            # ---- SharedKnowledge ------------------------------------------
            entropy = _entropy(churn_by_author.values(), total)
            if (
                entropy >= cfg.shared_knowledge_entropy_min
                and len(churn_by_author)
                >= cfg.shared_knowledge_min_distinct_authors
            ):
                yield Trait(
                    id=_trait_id(_TRAIT_SHARED_KNOWLEDGE, file_ref),
                    target=file_ref,
                    family=TraitFamily.KNOWLEDGE,
                    name=_TRAIT_SHARED_KNOWLEDGE,
                    severity=round(entropy, 3),
                    evidence={
                        "entropy": round(entropy, 3),
                        "threshold": float(cfg.shared_knowledge_entropy_min),
                        "distinct_authors": int(len(churn_by_author)),
                    },
                )

            # ---- Accumulator -----------------------------------------------
            buckets = time_bucketed_churn(
                graph, file_ref, cfg.accumulator_bucket_weeks
            )
            positive_windows = sum(1 for _, n in buckets if n > 0)
            if positive_windows >= cfg.accumulator_min_windows:
                yield Trait(
                    id=_trait_id(_TRAIT_ACCUMULATOR, file_ref),
                    target=file_ref,
                    family=TraitFamily.KNOWLEDGE,
                    name=_TRAIT_ACCUMULATOR,
                    severity=float(positive_windows),
                    evidence={
                        "positive_windows": int(positive_windows),
                        "total_windows": int(len(buckets)),
                        "bucket_weeks": int(cfg.accumulator_bucket_weeks),
                        "threshold": int(cfg.accumulator_min_windows),
                    },
                )

            # ---- PolarisedOwnership (suppressed when BusFactor1 fired) -----
            if (
                not busfactor1_fired
                and len(churn_by_author) >= cfg.polarised_min_authors
                and total > 0
            ):
                top2 = sorted(churn_by_author.values(), reverse=True)[:2]
                top2_share = sum(top2) / total
                if top2_share >= cfg.polarised_top_share:
                    sorted_authors = sorted(
                        churn_by_author.items(), key=lambda kv: kv[1],
                        reverse=True,
                    )
                    top_author_ids = [a for a, _ in sorted_authors[:2]]
                    yield Trait(
                        id=_trait_id(_TRAIT_POLARISED_OWNERSHIP, file_ref),
                        target=file_ref,
                        family=TraitFamily.KNOWLEDGE,
                        name=_TRAIT_POLARISED_OWNERSHIP,
                        severity=round(top2_share, 3),
                        evidence={
                            "top_authors": top_author_ids,
                            "top2_share": round(top2_share, 3),
                            "threshold": float(cfg.polarised_top_share),
                            "distinct_authors": int(len(churn_by_author)),
                        },
                    )

            # ---- OwnerChurn -----------------------------------------------
            recent_churn = author_churn_within(graph, file_ref, cutoff)
            recent_total = sum(recent_churn.values())
            if recent_total > 0 and total > 0:
                top_lt_a, top_lt_c = max(
                    churn_by_author.items(), key=lambda kv: kv[1]
                )
                top_rc_a, top_rc_c = max(
                    recent_churn.items(), key=lambda kv: kv[1]
                )
                lt_share = top_lt_c / total
                rc_share = top_rc_c / recent_total
                if (
                    top_lt_a != top_rc_a
                    and lt_share >= cfg.owner_churn_dominance_threshold
                    and rc_share >= cfg.owner_churn_dominance_threshold
                ):
                    yield Trait(
                        id=_trait_id(_TRAIT_OWNER_CHURN, file_ref),
                        target=file_ref,
                        family=TraitFamily.KNOWLEDGE,
                        name=_TRAIT_OWNER_CHURN,
                        severity=round(rc_share, 3),
                        evidence={
                            "lifetime_owner": top_lt_a,
                            "recent_owner": top_rc_a,
                            "lifetime_share": round(lt_share, 3),
                            "recent_share": round(rc_share, 3),
                            "threshold": float(cfg.owner_churn_dominance_threshold),
                        },
                    )

            # ---- Solitaire -------------------------------------------------
            if lifetime_commits >= cfg.solitaire_min_lifetime_commits:
                file_authors = list(churn_by_author.keys())
                actives = [
                    a for a in file_authors if activity_lookup.get(a) == "active"
                ]
                idles = [
                    a for a in file_authors if activity_lookup.get(a) == "idle"
                ]
                if (
                    len(actives) == 1
                    and len(idles) == len(file_authors) - 1
                ):
                    yield Trait(
                        id=_trait_id(_TRAIT_SOLITAIRE, file_ref),
                        target=file_ref,
                        family=TraitFamily.KNOWLEDGE,
                        name=_TRAIT_SOLITAIRE,
                        evidence={
                            "active_author": actives[0],
                            "retired_authors": idles,
                            "lifetime_commits": int(lifetime_commits),
                            "threshold": int(cfg.solitaire_min_lifetime_commits),
                        },
                    )

            # ---- TeamChurn -------------------------------------------------
            lifetime_authors = set(churn_by_author.keys())
            recent_authors = set(recent_churn.keys())
            if len(recent_authors) >= 2 and len(lifetime_authors) >= 2:
                union = lifetime_authors | recent_authors
                inter = lifetime_authors & recent_authors
                if union:
                    jaccard_sim = len(inter) / len(union)
                    distance = 1.0 - jaccard_sim
                    if distance >= cfg.team_churn_set_change_ratio:
                        yield Trait(
                            id=_trait_id(_TRAIT_TEAM_CHURN, file_ref),
                            target=file_ref,
                            family=TraitFamily.KNOWLEDGE,
                            name=_TRAIT_TEAM_CHURN,
                            severity=round(distance, 3),
                            evidence={
                                "jaccard_distance": round(distance, 3),
                                "lifetime_authors_count": int(len(lifetime_authors)),
                                "recent_authors_count": int(len(recent_authors)),
                                "shared_authors_count": int(len(inter)),
                                "threshold": float(cfg.team_churn_set_change_ratio),
                            },
                        )

            # ---- WeakOwnership --------------------------------------------
            if (
                active_author_total >= cfg.weak_owner_min_active_authors
                and recent_total > 0
            ):
                active_recent = active_author_churn(graph, file_ref, cutoff)
                active_total = sum(active_recent.values())
                share = active_total / recent_total
                if share < cfg.weak_owner_max_share:
                    yield Trait(
                        id=_trait_id(_TRAIT_WEAK_OWNERSHIP, file_ref),
                        target=file_ref,
                        family=TraitFamily.KNOWLEDGE,
                        name=_TRAIT_WEAK_OWNERSHIP,
                        severity=round(1.0 - share, 3),
                        evidence={
                            "active_share": round(share, 3),
                            "active_authors_in_file": int(len(active_recent)),
                            "active_authors_total": int(active_author_total),
                            "recent_churn": int(recent_total),
                            "threshold": float(cfg.weak_owner_max_share),
                        },
                    )

        # ---- OrphanCausers (post-pass over authors) ------------------------
        yield from _emit_orphan_causers(
            graph, orphan_file_ids, activity_lookup, cfg,
        )


# ----------------------------------------------------------------------
# Author-level: OrphanCausers
# ----------------------------------------------------------------------
def _emit_orphan_causers(
    graph: Any,
    orphan_file_ids: set[str],
    activity_lookup: dict[str, str],
    cfg: "_KnowledgeConfig",
) -> Iterator[Trait]:
    """Retired authors whose former files are now Orphan."""
    if not orphan_file_ids:
        return
    accounts = _safe_iter(getattr(graph, "git_accounts", None))
    if not accounts:
        return
    commits_reg = getattr(graph, "commits", None)
    if commits_reg is None:
        return
    by_author = getattr(commits_reg, "by_author", None)

    for account in accounts:
        if activity_lookup.get(account.id) != "idle":
            continue
        account_ref = account.ref()
        if by_author is not None:
            lifetime_commits = len(list(by_author[account_ref]))
        else:
            lifetime_commits = sum(
                1 for c in commits_reg
                if getattr(c, "author_ref", None) == account_ref
            )
        if lifetime_commits < cfg.orphancauser_min_lifetime_commits:
            continue
        touched_files = files_touched_by_account(graph, account_ref)
        touched_ids = {fref.id for fref in touched_files}
        intersection = touched_ids & orphan_file_ids
        if len(intersection) < cfg.orphancauser_min_orphan_files:
            continue
        sample = sorted(intersection)[: cfg.orphancauser_orphan_sample_cap]
        yield Trait(
            id=_trait_id(_TRAIT_ORPHAN_CAUSERS, account_ref),
            target=account_ref,
            family=TraitFamily.KNOWLEDGE,
            name=_TRAIT_ORPHAN_CAUSERS,
            severity=float(len(intersection)),
            evidence={
                "orphan_files_count": int(len(intersection)),
                "orphan_file_ids_sample": sample,
                "lifetime_commits": int(lifetime_commits),
                "lifetime_files_touched": int(len(touched_ids)),
                "threshold": int(cfg.orphancauser_min_orphan_files),
            },
        )


# ----------------------------------------------------------------------
# Config snapshot
# ----------------------------------------------------------------------
class _KnowledgeConfig:
    """Cached snapshot of the 16 config knobs this metric reads."""

    __slots__ = (
        "hermit_dominance_ratio",
        "busfactor1_min_distinct_authors",
        "shared_knowledge_entropy_min",
        "shared_knowledge_min_distinct_authors",
        "accumulator_bucket_weeks",
        "accumulator_min_windows",
        "owner_churn_dominance_threshold",
        "polarised_top_share",
        "polarised_min_authors",
        "solitaire_min_lifetime_commits",
        "team_churn_set_change_ratio",
        "weak_owner_max_share",
        "weak_owner_min_active_authors",
        "orphancauser_min_orphan_files",
        "orphancauser_min_lifetime_commits",
        "orphancauser_orphan_sample_cap",
    )

    @classmethod
    def from_config(cls, config: Any) -> "_KnowledgeConfig":
        o = cls.__new__(cls)
        o.hermit_dominance_ratio = float(_cf(config, "hermit_dominance_ratio", 0.80))
        o.busfactor1_min_distinct_authors = int(_cf(
            config, "busfactor1_min_distinct_authors", 2,
        ))
        o.shared_knowledge_entropy_min = float(_cf(
            config, "shared_knowledge_entropy_min", 1.5,
        ))
        o.shared_knowledge_min_distinct_authors = int(_cf(
            config, "shared_knowledge_min_distinct_authors", 3,
        ))
        o.accumulator_bucket_weeks = int(_cf(config, "accumulator_bucket_weeks", 4))
        o.accumulator_min_windows = int(_cf(config, "accumulator_min_windows", 6))
        o.owner_churn_dominance_threshold = float(_cf(
            config, "owner_churn_dominance_threshold", 0.5,
        ))
        o.polarised_top_share = float(_cf(config, "polarised_top_share", 0.8))
        o.polarised_min_authors = int(_cf(config, "polarised_min_authors", 2))
        o.solitaire_min_lifetime_commits = int(_cf(
            config, "solitaire_min_lifetime_commits", 5,
        ))
        o.team_churn_set_change_ratio = float(_cf(
            config, "team_churn_set_change_ratio", 0.5,
        ))
        o.weak_owner_max_share = float(_cf(config, "weak_owner_max_share", 0.2))
        o.weak_owner_min_active_authors = int(_cf(
            config, "weak_owner_min_active_authors", 2,
        ))
        o.orphancauser_min_orphan_files = int(_cf(
            config, "orphancauser_min_orphan_files", 3,
        ))
        o.orphancauser_min_lifetime_commits = int(_cf(
            config, "orphancauser_min_lifetime_commits", 10,
        ))
        o.orphancauser_orphan_sample_cap = int(_cf(
            config, "orphancauser_orphan_sample_cap", 20,
        ))
        return o


def _cf(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


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


def _trait_id(name: str, target_ref: EntityRef) -> str:
    return f"trait:{name}:{target_ref.kind.value}/{target_ref.id}"


def _last_change_date(graph: Any, file_ref: EntityRef) -> Optional[datetime]:
    dates = commit_dates(graph, file_ref)
    return max(dates) if dates else None


def _lifetime_commit_count(graph: Any, file_ref: EntityRef) -> int:
    changes = getattr(graph, "changes", None)
    if changes is None:
        return 0
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        file_changes = list(by_file[file_ref])
    else:
        file_changes = [
            ch for ch in changes if getattr(ch, "file_ref", None) == file_ref
        ]
    return sum(
        1 for ch in file_changes if getattr(ch, "commit_ref", None) is not None
    )


def _entropy(values: Iterable[int], total: int) -> float:
    if total <= 0:
        return 0.0
    h = 0.0
    for v in values:
        if v <= 0:
            continue
        p = v / total
        h -= p * math.log(p)
    return h


def _build_activity_lookup(graph: Any) -> dict[str, str]:
    """Read author activity classifiers from ``graph.classifiers``.

    The locked Chunk-11 contract is
    ``Classifier(dimension="activity", value="active"|"idle")`` keyed on
    a ``GitAccount`` ref. Returns ``{account_id: "active"|"idle"}`` so
    the per-file Solitaire emitter does a single dict lookup per author
    instead of N classifier-registry round-trips.
    """
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None or not hasattr(classifiers, "of_dimension"):
        return {}
    out: dict[str, str] = {}
    for cls_obj in classifiers.of_dimension("activity"):
        target = getattr(cls_obj, "target", None)
        if target is None or target.kind != EntityKind.GIT_ACCOUNT:
            continue
        out[target.id] = cls_obj.value
    return out


def _active_author_total(graph: Any) -> int:
    """Total number of authors carrying ``activity="active"``."""
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None or not hasattr(classifiers, "with_value"):
        return 0
    rows = classifiers.with_value("activity", "active")
    return sum(
        1 for row in rows
        if getattr(row, "target", None) is not None
        and row.target.kind == EntityKind.GIT_ACCOUNT
    )


def _resolve_recent_cutoff(
    graph: Any, commits_reg: Any, config: Any
) -> Optional[datetime]:
    """Mirror :func:`anomaly_cohesion._resolve_recent_cutoff`."""
    explicit = getattr(graph, "recent_cutoff", None)
    if explicit is not None:
        return ensure_aware(explicit)
    window_days = int(_cf(config, "recent_window_days", 90))
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


__all__ = ["AnomalyKnowledgeMetric"]
