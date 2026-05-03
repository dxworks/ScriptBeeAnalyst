"""anomaly.knowledge.* file traits.

Phase-2 originals: Orphan, BusFactor1 (Hermit), SharedKnowledge.
A2.1 additions: Accumulator, OwnerChurn, PolarisedOwnership, Solitaire,
TeamChurn, WeakOwnership.
A2.2 additions: OrphanCausers (author-level — retired authors whose former
files are now Orphan).

Mutual exclusion: PolarisedOwnership represents the "tight pair" case while
BusFactor1 is the "single dominant author" case. We post-filter so BusFactor1
suppresses PolarisedOwnership on the same file — they otherwise overlap when
the top author already crosses the BusFactor1 threshold alone.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

from src.enrichment.models import EntityTags, Trait
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id
from src.enrichment.tagger.file_trait_utils import (
    _active_author_churn,
    _author_churn,
    _author_churn_within,
    _files_touched_by_author,
    _time_bucketed_churn,
)


class KnowledgeAnomalyTagger:
    """Emits Orphan, BusFactor1, SharedKnowledge plus the A2.1 knowledge traits."""

    TRAITS = [
        {"name": "anomaly.knowledge.Orphan",              "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.BusFactor1",          "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.SharedKnowledge",     "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.Accumulator",         "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.OwnerChurn",          "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.PolarisedOwnership",  "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.Solitaire",           "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.TeamChurn",           "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.WeakOwnership",       "entity": "file",   "family": "knowledge"},
        {"name": "anomaly.knowledge.OrphanCausers",       "entity": "author", "family": "knowledge"},
    ]

    def __init__(self, tags_by_entity: Optional[dict] = None):
        # tags_by_entity carries classifiers (notably author.activity) populated
        # by Pass 1. Optional so existing call sites that built this without
        # arguments still work.
        self._tags = tags_by_entity or {}

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        cutoff = ctx.recent_cutoff
        out: list[EntityTags] = []

        active_author_total = sum(
            1 for t in self._tags.values()
            if t.entity_kind == "author" and t.classifiers.get("activity") == "active"
        )

        orphan_file_ids: set[str] = set()

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if fid is None:
                continue

            churn_by_author = _author_churn(file_)
            if not churn_by_author:
                continue

            traits: list[Trait] = []
            total = sum(churn_by_author.values())

            last = _last_change_date(file_)

            # Orphan: single author ever AND last touch outside recent window.
            if len(churn_by_author) == 1:
                outside_recent = (
                    cutoff is not None and last is not None and last < cutoff
                )
                if outside_recent:
                    only_author = next(iter(churn_by_author.keys()))
                    orphan_file_ids.add(fid)
                    traits.append(make_trait(
                        "anomaly.knowledge.Orphan",
                        family="knowledge",
                        author=only_author,
                        last_change=last.isoformat() if last else None,
                        churn=int(total),
                    ))

            # BusFactor1 / Hermit: dominant author > threshold.
            top_author, top_churn = max(churn_by_author.items(), key=lambda kv: kv[1])
            dominance = top_churn / total if total > 0 else 0.0
            busfactor1_fired = False
            if (
                dominance >= cfg.hermit_dominance_ratio
                and len(churn_by_author) >= cfg.busfactor1_min_distinct_authors
            ):
                busfactor1_fired = True
                traits.append(make_trait(
                    "anomaly.knowledge.BusFactor1",
                    family="knowledge",
                    severity=round(dominance, 3),
                    dominant_author=top_author,
                    dominance_ratio=round(dominance, 3),
                    threshold=cfg.hermit_dominance_ratio,
                    distinct_authors=len(churn_by_author),
                ))

            # SharedKnowledge: high entropy of churn distribution (counterpart).
            entropy = _entropy(churn_by_author.values(), total)
            if (
                entropy >= cfg.shared_knowledge_entropy_min
                and len(churn_by_author) >= cfg.shared_knowledge_min_distinct_authors
            ):
                traits.append(make_trait(
                    "anomaly.knowledge.SharedKnowledge",
                    family="knowledge",
                    severity=round(entropy, 3),
                    entropy=round(entropy, 3),
                    threshold=cfg.shared_knowledge_entropy_min,
                    distinct_authors=len(churn_by_author),
                ))

            # ── A2.1 knowledge traits ────────────────────────────────────────

            # Accumulator: many lifetime windows where net churn > 0.
            buckets = _time_bucketed_churn(file_, cfg.accumulator_bucket_weeks)
            positive_windows = sum(1 for _, n in buckets if n > 0)
            if positive_windows >= cfg.accumulator_min_windows:
                traits.append(make_trait(
                    "anomaly.knowledge.Accumulator",
                    family="knowledge",
                    severity=float(positive_windows),
                    positive_windows=positive_windows,
                    total_windows=len(buckets),
                    bucket_weeks=cfg.accumulator_bucket_weeks,
                    threshold=cfg.accumulator_min_windows,
                ))

            # PolarisedOwnership: top-2 authors hold >= polarised_top_share.
            # Suppressed if BusFactor1 fired — those overlap and the
            # single-owner reading is more accurate there.
            if (
                not busfactor1_fired
                and len(churn_by_author) >= cfg.polarised_min_authors
                and total > 0
            ):
                top2 = sorted(churn_by_author.values(), reverse=True)[:2]
                top2_share = sum(top2) / total
                if top2_share >= cfg.polarised_top_share:
                    sorted_authors = sorted(
                        churn_by_author.items(), key=lambda kv: kv[1], reverse=True
                    )
                    top_author_ids = [a for a, _ in sorted_authors[:2]]
                    traits.append(make_trait(
                        "anomaly.knowledge.PolarisedOwnership",
                        family="knowledge",
                        severity=round(top2_share, 3),
                        top_authors=top_author_ids,
                        top2_share=round(top2_share, 3),
                        threshold=cfg.polarised_top_share,
                        distinct_authors=len(churn_by_author),
                    ))

            # OwnerChurn: dominant author lifetime ≠ dominant author recent,
            # both above the dominance threshold.
            recent_churn = _author_churn_within(file_, cutoff)
            recent_total = sum(recent_churn.values())
            if recent_total > 0 and total > 0:
                top_lt_a, top_lt_c = max(churn_by_author.items(), key=lambda kv: kv[1])
                top_rc_a, top_rc_c = max(recent_churn.items(), key=lambda kv: kv[1])
                lt_share = top_lt_c / total
                rc_share = top_rc_c / recent_total
                if (
                    top_lt_a != top_rc_a
                    and lt_share >= cfg.owner_churn_dominance_threshold
                    and rc_share >= cfg.owner_churn_dominance_threshold
                ):
                    traits.append(make_trait(
                        "anomaly.knowledge.OwnerChurn",
                        family="knowledge",
                        severity=round(rc_share, 3),
                        lifetime_owner=top_lt_a,
                        recent_owner=top_rc_a,
                        lifetime_share=round(lt_share, 3),
                        recent_share=round(rc_share, 3),
                        threshold=cfg.owner_churn_dominance_threshold,
                    ))

            # Solitaire: exactly one active author for this file, every other
            # historical author classified idle, and the file is mature.
            lifetime_commits = sum(
                1 for ch in (file_.changes or []) if getattr(ch, "commit", None) is not None
            )
            if lifetime_commits >= cfg.solitaire_min_lifetime_commits:
                file_authors = list(churn_by_author.keys())
                actives = [a for a in file_authors if self._is_active(a)]
                idles = [a for a in file_authors if self._activity_of(a) == "idle"]
                if len(actives) == 1 and len(idles) == len(file_authors) - 1:
                    traits.append(make_trait(
                        "anomaly.knowledge.Solitaire",
                        family="knowledge",
                        active_author=actives[0],
                        retired_authors=idles,
                        lifetime_commits=lifetime_commits,
                        threshold=cfg.solitaire_min_lifetime_commits,
                    ))

            # TeamChurn: jaccard distance between recent and lifetime author
            # sets is materially large.
            lifetime_authors = set(churn_by_author.keys())
            recent_authors = set(recent_churn.keys())
            if len(recent_authors) >= 2 and len(lifetime_authors) >= 2:
                union = lifetime_authors | recent_authors
                inter = lifetime_authors & recent_authors
                if union:
                    jaccard_sim = len(inter) / len(union)
                    distance = 1.0 - jaccard_sim
                    if distance >= cfg.team_churn_set_change_ratio:
                        traits.append(make_trait(
                            "anomaly.knowledge.TeamChurn",
                            family="knowledge",
                            severity=round(distance, 3),
                            jaccard_distance=round(distance, 3),
                            lifetime_authors_count=len(lifetime_authors),
                            recent_authors_count=len(recent_authors),
                            shared_authors_count=len(inter),
                            threshold=cfg.team_churn_set_change_ratio,
                        ))

            # WeakOwnership: active developers hold < weak_owner_max_share of
            # the file's recent-window churn. Skipped on tiny teams (where the
            # signal is meaningless) and on files without recent activity.
            if (
                active_author_total >= cfg.weak_owner_min_active_authors
                and recent_total > 0
            ):
                active_recent = _active_author_churn(file_, self._tags, cutoff)
                active_total = sum(active_recent.values())
                share = active_total / recent_total
                if share < cfg.weak_owner_max_share:
                    traits.append(make_trait(
                        "anomaly.knowledge.WeakOwnership",
                        family="knowledge",
                        severity=round(1.0 - share, 3),
                        active_share=round(share, 3),
                        active_authors_in_file=len(active_recent),
                        active_authors_total=active_author_total,
                        recent_churn=int(recent_total),
                        threshold=cfg.weak_owner_max_share,
                    ))

            if traits:
                out.append(EntityTags(
                    entity_kind="file",
                    entity_id=fid,
                    traits=traits,
                ))

        # ── A2.2 author-level: OrphanCausers ────────────────────────────────
        # Retired (idle) author whose former files now match Orphan. We only
        # consider authors with enough lifetime commits so a single touch on a
        # later-orphaned file doesn't get them flagged.
        if orphan_file_ids:
            for account in git.account_registry.all:
                aid = account.id
                if self._activity_of(aid) != "idle":
                    continue
                lifetime_commits = len(getattr(account, "commits", None) or [])
                if lifetime_commits < cfg.orphancauser_min_lifetime_commits:
                    continue
                touched_files = _files_touched_by_author(account)
                touched_ids: set[str] = set()
                for f in touched_files:
                    f_fid = _file_id(f)
                    if f_fid:
                        touched_ids.add(f_fid)
                intersection = touched_ids & orphan_file_ids
                if len(intersection) < cfg.orphancauser_min_orphan_files:
                    continue
                sample = sorted(intersection)[: cfg.orphancauser_orphan_sample_cap]
                out.append(EntityTags(
                    entity_kind="author",
                    entity_id=aid,
                    traits=[make_trait(
                        "anomaly.knowledge.OrphanCausers",
                        family="knowledge",
                        severity=float(len(intersection)),
                        orphan_files_count=len(intersection),
                        orphan_file_ids_sample=sample,
                        lifetime_commits=lifetime_commits,
                        lifetime_files_touched=len(touched_ids),
                        threshold=cfg.orphancauser_min_orphan_files,
                    )],
                ))

        return out

    def _activity_of(self, author_id: str) -> Optional[str]:
        atags = self._tags.get(f"author:{author_id}")
        return atags.classifiers.get("activity") if atags else None

    def _is_active(self, author_id: str) -> bool:
        return self._activity_of(author_id) == "active"


def _last_change_date(file_):
    dates = [ensure_aware(getattr(ch.commit, "author_date", None))
             for ch in (file_.changes or [])
             if getattr(ch, "commit", None) is not None]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


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
