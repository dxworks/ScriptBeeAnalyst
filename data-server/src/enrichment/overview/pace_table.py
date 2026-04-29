"""Pace & Nature overview table.

Ports dx's `PaceAndNature` (Java) Phase-1 rows only:
  - commits/week (lifetime, recent, trend%)
  - nature mix: % bugfix, % feature, % refactor, % docs, % chore
  - daytime mix: % off-hours (night+evening vs. working hours)
  - weekday mix: % weekend

Rows are one per top-level folder plus a synthetic "(project)" aggregate.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from src.enrichment.components.resolver import top_folder_of
from src.enrichment.models import OverviewCell, OverviewRow, OverviewTable
from src.enrichment.overview.builder import (
    rate_cell,
    share_cell,
    constant_cell,
)
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


COLUMNS = [
    "commits_per_week",
    "pct_bugfix",
    "pct_feature",
    "pct_refactor",
    "pct_docs",
    "pct_chore",
    "pct_off_hours",
    "pct_weekend",
    "distinct_authors",
]

WORKING_HOURS = range(8, 18)  # 08:00–17:59 local
OFF_HOURS_DAYTIMES = {"night", "evening"}  # fallback if hour is missing


class PaceTableBuilder:

    NAME = "pace"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        git = ctx.graph_data.get("git")
        rows: list[OverviewRow] = []

        if git is None:
            return OverviewTable(name=self.NAME, entity_kind="component", columns=COLUMNS, rows=rows)

        all_commits = list(git.git_commit_registry.all)
        commit_folder = _bucket_commits_by_top_folder(all_commits)

        cutoff = ctx.recent_cutoff
        anchor = ctx.anchor_date

        # Project-level row (all commits)
        rows.append(self._row_for(
            entity_id="(project)",
            lifetime_commits=all_commits,
            cutoff=cutoff,
            anchor=anchor,
            config_days=ctx.config.recent_window_days,
            tags_by_entity=tags_by_entity,
        ))

        # Per-top-folder rows
        for folder, commits in sorted(commit_folder.items()):
            rows.append(self._row_for(
                entity_id=folder,
                lifetime_commits=commits,
                cutoff=cutoff,
                anchor=anchor,
                config_days=ctx.config.recent_window_days,
                tags_by_entity=tags_by_entity,
            ))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _row_for(
        self,
        entity_id: str,
        lifetime_commits: list,
        cutoff,
        anchor,
        config_days: int,
        tags_by_entity: dict,
    ) -> OverviewRow:
        recent_commits = (
            [c for c in lifetime_commits
             if ensure_aware(c.author_date or c.committer_date)
             and ensure_aware(c.author_date or c.committer_date) >= cutoff]
            if cutoff is not None else list(lifetime_commits)
        )

        # Lifetime "span" = days between first and last commit (fallback to
        # config recent_window_days * N so rate isn't wildly different shape).
        lifetime_days = _commit_span_days(lifetime_commits)
        recent_days = config_days

        cells: dict[str, OverviewCell] = {}

        cells["commits_per_week"] = rate_cell(
            lifetime_commits, recent_commits, lifetime_days, recent_days,
        )

        for label in ("bugfix", "feature", "refactor", "docs", "chore"):
            cells[f"pct_{label}"] = share_cell(
                lifetime_commits,
                recent_commits,
                predicate=_nature_is(label, tags_by_entity),
            )

        cells["pct_off_hours"] = share_cell(
            lifetime_commits, recent_commits, _is_off_hours,
        )
        cells["pct_weekend"] = share_cell(
            lifetime_commits, recent_commits, _is_weekend,
        )

        cells["distinct_authors"] = OverviewCell(
            lifetime_value=_distinct_authors(lifetime_commits),
            recent_value=_distinct_authors(recent_commits),
            trend_percent=None,
        )

        return OverviewRow(entity_id=entity_id, cells=cells)


# ── helpers ────────────────────────────────────────────────────────────────────

def _bucket_commits_by_top_folder(commits: list) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for commit in commits:
        folders = set()
        for ch in getattr(commit, "changes", None) or []:
            f = getattr(ch, "file", None)
            if f is None:
                continue
            fid = _file_id(f)
            top = top_folder_of(fid)
            if top is None:
                continue
            folders.add(top)
        for folder in folders:
            out[folder].append(commit)
    return dict(out)


def _commit_span_days(commits: list):
    dates = [ensure_aware(c.author_date or c.committer_date) for c in commits]
    dates = [d for d in dates if d is not None]
    if len(dates) < 2:
        return None
    return max(1, (max(dates) - min(dates)).days)


def _nature_is(value: str, tags_by_entity: dict):
    def predicate(commit):
        t = tags_by_entity.get(f"commit:{commit.id}")
        return bool(t and t.classifiers.get("message.nature") == value)
    return predicate


def _is_off_hours(commit) -> bool:
    dt = commit.author_date
    if dt is None:
        return False
    return dt.hour not in WORKING_HOURS


def _is_weekend(commit) -> bool:
    dt = commit.author_date
    if dt is None:
        return False
    # Python: Monday=0, Sunday=6
    return dt.weekday() >= 5


def _distinct_authors(commits: list) -> int:
    ids = set()
    for c in commits:
        a = getattr(c, "author", None)
        if a is not None and hasattr(a, "id"):
            ids.add(a.id)
    return len(ids)
