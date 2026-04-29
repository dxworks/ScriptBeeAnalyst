"""Commit-level mandatory classifiers.

Ports dx's notion of "every commit has one value" for message.nature,
volume.churn, volume.spread, daytime, weekday, message.smartness.
"""
from __future__ import annotations

from typing import Iterable, Optional

from src.enrichment.models import EntityTags
from src.enrichment.tagger.base import Tagger, TaggingContext


class CommitClassifiersTagger:
    """All commits → classifiers only (no traits)."""

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        out: list[EntityTags] = []

        for commit in git.git_commit_registry.all:
            classifiers: dict[str, str] = {}

            # message.nature — first matching regex wins; merges take precedence.
            nature = self._message_nature(commit, cfg)
            classifiers["message.nature"] = nature

            # volume.churn (focused/medium/bulk)
            churn = self._commit_churn(commit)
            if churn <= cfg.churn_focused_max:
                classifiers["volume.churn"] = "focused"
            elif churn <= cfg.churn_medium_max:
                classifiers["volume.churn"] = "medium"
            else:
                classifiers["volume.churn"] = "bulk"

            # volume.spread (narrow/wide) — distinct files touched
            spread = len(commit.changes) if commit.changes is not None else 0
            classifiers["volume.spread"] = "narrow" if spread <= cfg.spread_narrow_max else "wide"

            # daytime + weekday
            dt = commit.author_date
            if dt is not None:
                classifiers["daytime"] = self._daytime_bucket(dt.hour, cfg)
                classifiers["weekday"] = self._weekday_label(dt.weekday())

            # message.smartness — "smart" if linker attached any issues
            has_issues = bool(getattr(commit, "issues", []))
            classifiers["message.smartness"] = "smart" if has_issues else "dumb"

            out.append(EntityTags(
                entity_kind="commit",
                entity_id=commit.id,
                classifiers=classifiers,
            ))

        return out

    @staticmethod
    def _message_nature(commit, cfg) -> str:
        if len(getattr(commit, "parents", []) or []) > 1:
            return "merge"
        message = commit.message or ""
        for name, pattern in cfg.nature_patterns:
            if pattern.search(message):
                return name
        return "chore"

    @staticmethod
    def _commit_churn(commit) -> int:
        total = 0
        for change in getattr(commit, "changes", None) or []:
            for hunk in getattr(change, "hunks", None) or []:
                total += len(getattr(hunk, "added_lines", []) or [])
                total += len(getattr(hunk, "deleted_lines", []) or [])
        return total

    @staticmethod
    def _daytime_bucket(hour: int, cfg) -> str:
        for label, (start, end) in cfg.daytime_buckets.items():
            if start <= hour < end:
                return label
        return "unknown"

    @staticmethod
    def _weekday_label(idx: int) -> str:
        return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][idx]
