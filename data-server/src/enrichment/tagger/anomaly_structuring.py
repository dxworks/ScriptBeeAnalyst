"""anomaly.structuring.* — PivotFile (file), TasksBottleneck (issue / author).

PivotFile: a file with high degree in the lifetime co-change graph (port of
dx's "many partners ⇒ pivot"). We compute degree from the lifetime cochange
RelationFile that the cochange extractor emits — taggers run before relation
extractors today, so we recompute here from `commit.changes` directly. Cheap
because we only need degrees, not the edge list.

TasksBottleneck: emitted on Issues that stay open beyond a threshold age and
on Authors with many in-flight (open) issues assigned to them.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Iterable, Optional

from src.enrichment.models import EntityTags, Trait
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import TaggingContext, make_trait
from src.enrichment.tagger.file_classifiers import _file_id


class StructuringAnomalyTagger:

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        out: list[EntityTags] = []
        out.extend(self._pivot_files(ctx))
        out.extend(self._task_bottlenecks(ctx))
        return out

    def _pivot_files(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []
        cfg = ctx.config

        degree: dict[str, set[str]] = defaultdict(set)
        max_files = cfg.cochange_max_files_per_commit

        for commit in git.git_commit_registry.all:
            if len(getattr(commit, "parents", []) or []) > 1:
                continue
            changes = getattr(commit, "changes", None) or []
            if not (2 <= len(changes) <= max_files):
                continue
            paths = []
            for ch in changes:
                f = getattr(ch, "file", None)
                if f is None:
                    continue
                fid = _file_id(f)
                if fid:
                    paths.append(fid)
            unique_paths = sorted(set(paths))
            for a, b in combinations(unique_paths, 2):
                degree[a].add(b)
                degree[b].add(a)

        out: list[EntityTags] = []
        for fid, neighbours in degree.items():
            d = len(neighbours)
            if d >= cfg.pivotfile_cochange_degree_min:
                out.append(EntityTags(
                    entity_kind="file",
                    entity_id=fid,
                    traits=[make_trait(
                        "anomaly.structuring.PivotFile",
                        family="structuring",
                        severity=float(d),
                        cochange_degree=d,
                        threshold=cfg.pivotfile_cochange_degree_min,
                    )],
                ))
        return out

    def _task_bottlenecks(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        jira = ctx.graph_data.get("jira")
        if jira is None:
            return []
        cfg = ctx.config

        anchor = ctx.anchor_date or datetime.now(timezone.utc)
        out: list[EntityTags] = []

        in_flight_by_assignee: dict[str, int] = defaultdict(int)
        assignee_keys: dict[str, str] = {}

        for issue in jira.issue_registry.all:
            if not _is_open(issue, cfg.resolved_status_categories):
                continue
            created = ensure_aware(getattr(issue, "createdAt", None))
            if created is not None:
                age_days = (anchor - created).days
                if age_days >= cfg.tasksbottleneck_open_age_days:
                    out.append(EntityTags(
                        entity_kind="issue",
                        entity_id=issue.key,
                        traits=[make_trait(
                            "anomaly.structuring.TasksBottleneck",
                            family="structuring",
                            severity=float(age_days),
                            open_age_days=age_days,
                            threshold=cfg.tasksbottleneck_open_age_days,
                            scope="issue",
                        )],
                    ))

            for assignee in getattr(issue, "jira_users_as_assignee", None) or []:
                aid = getattr(assignee, "key", None) or getattr(assignee, "name", None)
                if not aid:
                    continue
                in_flight_by_assignee[aid] += 1
                assignee_keys[aid] = aid

        for aid, count in in_flight_by_assignee.items():
            if count >= cfg.tasksbottleneck_min_in_flight:
                out.append(EntityTags(
                    entity_kind="author",
                    entity_id=aid,
                    traits=[make_trait(
                        "anomaly.structuring.TasksBottleneck",
                        family="structuring",
                        severity=float(count),
                        in_flight_issues=count,
                        threshold=cfg.tasksbottleneck_min_in_flight,
                        scope="author",
                    )],
                ))

        return out


def _is_open(issue, resolved_categories: tuple[str, ...]) -> bool:
    statuses = getattr(issue, "issue_statuses", None) or []
    if not statuses:
        return True
    latest = statuses[-1]
    cat = getattr(latest, "issue_status_categories", None)
    cat_name = (getattr(cat, "name", None) or getattr(cat, "key", None) or "").strip().lower()
    if cat_name in resolved_categories:
        return False
    name = (getattr(latest, "name", None) or "").strip().lower()
    return name not in resolved_categories
