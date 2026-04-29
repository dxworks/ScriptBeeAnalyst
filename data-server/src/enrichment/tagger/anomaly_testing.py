"""anomaly.testing.* — BugMagnet (file).

BugMagnet: a file whose share of bugfix-nature commits exceeds a threshold,
with an absolute-count guard so we don't flag tiny files. TestOrphan is Phase 3.

This tagger runs after CommitClassifiersTagger, so it reads commit `message.nature`
classifiers from `tags_by_entity` rather than re-applying regexes.
"""
from __future__ import annotations

from typing import Iterable, Optional

from src.enrichment.models import EntityTags, Trait
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id


class TestingAnomalyTagger:
    """Reads pre-computed commit classifiers via `tags_by_entity`."""

    def __init__(self, tags_by_entity: dict):
        self._tags = tags_by_entity

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []
        cfg = ctx.config
        out: list[EntityTags] = []

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if fid is None:
                continue

            total = 0
            bugfix = 0
            for ch in file_.changes or []:
                c = getattr(ch, "commit", None)
                if c is None:
                    continue
                total += 1
                tags = self._tags.get(f"commit:{c.id}")
                if tags and tags.classifiers.get("message.nature") == "bugfix":
                    bugfix += 1

            if bugfix < cfg.bugmagnet_min_bugfix_commits:
                continue
            ratio = bugfix / total if total > 0 else 0.0
            if ratio < cfg.bugmagnet_ratio_min:
                continue

            out.append(EntityTags(
                entity_kind="file",
                entity_id=fid,
                traits=[make_trait(
                    "anomaly.testing.BugMagnet",
                    family="testing",
                    severity=round(ratio, 3),
                    bugfix_commits=bugfix,
                    total_commits=total,
                    bugfix_ratio=round(ratio, 3),
                    ratio_threshold=cfg.bugmagnet_ratio_min,
                    min_count=cfg.bugmagnet_min_bugfix_commits,
                )],
            ))

        return out
