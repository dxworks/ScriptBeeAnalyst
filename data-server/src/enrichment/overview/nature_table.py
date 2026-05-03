"""Nature distribution overview table.

Phase 1 folded the message-nature mix into Pace; dx exposes nature on its own
so trend visualisations can isolate "did bug-fixes spike?" from pace cadence.
This table mirrors the Pace per-nature shares but as a standalone, per-component
breakdown — Pace itself is left untouched.

Columns (lifetime + recent + trend% on every share):
  - bugfix_pct, feature_pct, refactor_pct, docs_pct, test_pct, chore_pct
  - merge_pct, revert_pct  (separate nature values per commit_classifiers)

Rows: synthetic '(project)' aggregate + one per top-level folder, where a
commit "belongs" to every top-folder it touched (mirrors pace_table).
"""
from __future__ import annotations

from collections import defaultdict

from src.enrichment.components.resolver import top_folder_of
from src.enrichment.models import OverviewCell, OverviewRow, OverviewTable
from src.enrichment.overview.builder import share_cell
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext
from src.enrichment.tagger.file_classifiers import _file_id


# Order mirrors the spec; revert kept last because it's rare enough that any
# non-zero cell is worth eyeballing.
_NATURES = ["bugfix", "feature", "refactor", "docs", "test", "chore", "merge", "revert"]
COLUMNS = [f"{n}_pct" for n in _NATURES]


class NatureTableBuilder:

    NAME = "nature"
    ENTITY_KIND = "component"

    def build(self, ctx: TaggingContext, tags_by_entity: dict) -> OverviewTable:
        git = ctx.graph_data.get("git")
        rows: list[OverviewRow] = []
        if git is None:
            return OverviewTable(name=self.NAME, entity_kind="component", columns=COLUMNS, rows=rows)

        all_commits = list(git.git_commit_registry.all)
        commits_by_folder = _bucket_commits_by_top_folder(all_commits)
        cutoff = ctx.recent_cutoff

        rows.append(self._row_for("(project)", all_commits, cutoff, tags_by_entity))
        for folder, commits in sorted(commits_by_folder.items()):
            rows.append(self._row_for(folder, commits, cutoff, tags_by_entity))

        return OverviewTable(
            name=self.NAME,
            entity_kind="component",
            columns=COLUMNS,
            rows=rows,
        )

    def _row_for(self, entity_id, lifetime_commits, cutoff, tags_by_entity) -> OverviewRow:
        recent_commits = (
            [c for c in lifetime_commits
             if ensure_aware(c.author_date or c.committer_date)
             and ensure_aware(c.author_date or c.committer_date) >= cutoff]
            if cutoff is not None else list(lifetime_commits)
        )

        cells: dict[str, OverviewCell] = {}
        for nature in _NATURES:
            cells[f"{nature}_pct"] = share_cell(
                lifetime_commits,
                recent_commits,
                predicate=_nature_is(nature, tags_by_entity),
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
            top = top_folder_of(_file_id(f))
            if top is None:
                continue
            folders.add(top)
        for folder in folders:
            out[folder].append(commit)
    return dict(out)


def _nature_is(value: str, tags_by_entity: dict):
    def predicate(commit):
        t = tags_by_entity.get(f"commit:{commit.id}")
        return bool(t and t.classifiers.get("message.nature") == value)
    return predicate
