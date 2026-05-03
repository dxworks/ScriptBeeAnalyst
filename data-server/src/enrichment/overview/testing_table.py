"""Testing overview table.

Columns:
  - test_file_ratio: share of files classified as 'test' role
  - bugfix_commit_ratio: share of commits in scope with message.nature='bugfix'
  - bugmagnet_files: count of files carrying anomaly.testing.BugMagnet
  - test_to_prod_ratio: test files / production files

Rows: synthetic '(project)' aggregate + one per top-level folder.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from src.enrichment.models import OverviewCell, OverviewRow, OverviewTable
from src.enrichment.recent_window import ensure_aware, trend_percent
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


COLUMNS = [
    "test_file_ratio",
    "bugfix_commit_ratio",
    "bugmagnet_files",
    "test_to_prod_ratio",
]


class TestingTableBuilder:

    NAME = "testing"
    ENTITY_KIND = "component"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        git = ctx.graph_data.get("git")
        rows: list[OverviewRow] = []
        if git is None:
            return OverviewTable(name=self.NAME, entity_kind="component", columns=COLUMNS, rows=rows)

        cutoff = ctx.recent_cutoff

        files_by_folder: dict[str, list] = defaultdict(list)
        all_files = list(git.file_registry.all)
        for f in all_files:
            fid = _file_id(f)
            if not fid:
                continue
            top = fid.split("/", 1)[0] if "/" in fid else fid
            files_by_folder[top].append(f)

        commits_by_folder = _commits_by_top_folder(git)
        all_commits = list(git.git_commit_registry.all)

        rows.append(self._row_for(
            "(project)", all_files, all_commits, cutoff, tags_by_entity,
        ))
        for folder in sorted(files_by_folder.keys() | commits_by_folder.keys()):
            rows.append(self._row_for(
                folder,
                files_by_folder.get(folder, []),
                commits_by_folder.get(folder, []),
                cutoff,
                tags_by_entity,
            ))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _row_for(self, entity_id, files, commits, cutoff, tags_by_entity) -> OverviewRow:
        cells: dict[str, OverviewCell] = {}

        # File-role splits.
        test_count = prod_count = 0
        bugmagnet_count = 0
        for f in files:
            fid = _file_id(f)
            if fid is None:
                continue
            tags = tags_by_entity.get(f"file:{fid}")
            role = tags.classifiers.get("role") if tags else None
            if role == "test":
                test_count += 1
            elif role == "production":
                prod_count += 1
            if tags and any(t.name == "anomaly.testing.BugMagnet" for t in tags.traits):
                bugmagnet_count += 1

        total_files = len(files)
        cells["test_file_ratio"] = OverviewCell(
            lifetime_value=_ratio(test_count, total_files),
            recent_value=_ratio(test_count, total_files),
            trend_percent=None,
        )

        # Commit splits — bugfix ratio over lifetime vs. recent.
        recent_commits = _recent(commits, cutoff)
        cells["bugfix_commit_ratio"] = OverviewCell(
            lifetime_value=_bugfix_ratio(commits, tags_by_entity),
            recent_value=_bugfix_ratio(recent_commits, tags_by_entity),
            trend_percent=trend_percent(
                _bugfix_ratio(commits, tags_by_entity),
                _bugfix_ratio(recent_commits, tags_by_entity),
            ),
        )

        cells["bugmagnet_files"] = OverviewCell(
            lifetime_value=bugmagnet_count,
            recent_value=bugmagnet_count,
            trend_percent=None,
        )

        cells["test_to_prod_ratio"] = OverviewCell(
            lifetime_value=_ratio(test_count, prod_count),
            recent_value=_ratio(test_count, prod_count),
            trend_percent=None,
        )

        return OverviewRow(entity_id=entity_id, cells=cells)


# ── helpers ────────────────────────────────────────────────────────────────────

def _ratio(n: int, d: int) -> Optional[float]:
    if d <= 0:
        return None
    return round(n / d, 4)


def _bugfix_ratio(commits, tags_by_entity) -> Optional[float]:
    if not commits:
        return None
    bug = 0
    for c in commits:
        tags = tags_by_entity.get(f"commit:{c.id}")
        if tags and tags.classifiers.get("message.nature") == "bugfix":
            bug += 1
    return round(bug / len(commits), 4)


def _recent(commits, cutoff):
    if cutoff is None:
        return list(commits)
    out = []
    for c in commits:
        d = ensure_aware(getattr(c, "author_date", None))
        if d is not None and d >= cutoff:
            out.append(c)
    return out


def _commits_by_top_folder(git) -> dict[str, list]:
    out: dict[str, list] = defaultdict(list)
    for commit in git.git_commit_registry.all:
        folders = set()
        for ch in getattr(commit, "changes", None) or []:
            f = getattr(ch, "file", None)
            if f is None:
                continue
            fid = _file_id(f)
            if not fid:
                continue
            top = fid.split("/", 1)[0] if "/" in fid else fid
            folders.add(top)
        for folder in folders:
            out[folder].append(commit)
    return dict(out)
