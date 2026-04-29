"""anomaly.testing.* — BugMagnet + TestOrphan.

BugMagnet: a file whose share of bugfix-nature commits exceeds a threshold,
with an absolute-count guard so we don't flag tiny files.

TestOrphan (Phase 3, proxy): a production-role file whose commit history
co-touches a test-role file at most `test_orphan_max_cochange_test_count`
times. This is *not* a static-analysis coverage check — flagged as proxy in
evidence per plan §B-#5.

This tagger runs after CommitClassifiersTagger, so it reads commit `message.nature`
and file `role` classifiers from `tags_by_entity` rather than re-applying logic.
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

        # Pre-resolve which file paths carry role=test for TestOrphan cochange
        # checks. Using the classifier output (rather than re-running the regex)
        # keeps the source of truth in one place.
        test_file_ids = {
            tags.entity_id for tags in self._tags.values()
            if tags.entity_kind == "file" and tags.classifiers.get("role") == "test"
        }
        # If the project has zero test-role files, every production file would
        # trivially satisfy `cochange_test_count == 0 <= threshold`. That makes
        # TestOrphan a no-signal flag in untested projects — skip it entirely.
        emit_test_orphan = bool(test_file_ids)

        for file_ in git.file_registry.all:
            fid = _file_id(file_)
            if fid is None:
                continue

            total = 0
            bugfix = 0
            commits_for_file: list = []
            for ch in file_.changes or []:
                c = getattr(ch, "commit", None)
                if c is None:
                    continue
                total += 1
                commits_for_file.append(c)
                tags = self._tags.get(f"commit:{c.id}")
                if tags and tags.classifiers.get("message.nature") == "bugfix":
                    bugfix += 1

            traits = []

            ratio = bugfix / total if total > 0 else 0.0
            if (
                bugfix >= cfg.bugmagnet_min_bugfix_commits
                and ratio >= cfg.bugmagnet_ratio_min
            ):
                traits.append(make_trait(
                    "anomaly.testing.BugMagnet",
                    family="testing",
                    severity=round(ratio, 3),
                    bugfix_commits=bugfix,
                    total_commits=total,
                    bugfix_ratio=round(ratio, 3),
                    ratio_threshold=cfg.bugmagnet_ratio_min,
                    min_count=cfg.bugmagnet_min_bugfix_commits,
                ))

            file_tags = self._tags.get(f"file:{fid}")
            role = file_tags.classifiers.get("role") if file_tags else None
            if (
                emit_test_orphan
                and role == "production"
                and total >= cfg.test_orphan_min_commits
            ):
                cochange_test_count = _commits_touching_tests(
                    commits_for_file, fid, test_file_ids,
                )
                if cochange_test_count <= cfg.test_orphan_max_cochange_test_count:
                    traits.append(make_trait(
                        "anomaly.testing.TestOrphan",
                        family="testing",
                        severity=1.0,
                        proxy=True,
                        note="no static analysis, uses commit-cochange to test files only",
                        cochange_test_count=int(cochange_test_count),
                        threshold=cfg.test_orphan_max_cochange_test_count,
                        commits=int(total),
                    ))

            if traits:
                out.append(EntityTags(
                    entity_kind="file",
                    entity_id=fid,
                    traits=traits,
                ))

        return out


def _commits_touching_tests(commits, this_fid: str, test_file_ids: set[str]) -> int:
    """Count distinct commits in `commits` that also touch any test file."""
    seen: set[str] = set()
    count = 0
    for c in commits:
        cid = getattr(c, "id", None)
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        for ch in getattr(c, "changes", None) or []:
            f = getattr(ch, "file", None)
            if f is None:
                continue
            other = _file_id(f)
            if not other or other == this_fid:
                continue
            if other in test_file_ids:
                count += 1
                break
    return count
