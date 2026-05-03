"""File-level mandatory classifiers: status, role, creationYear.

Uses File.changes[].commit.author_date — same fields dx's
`SourceFile.activityStatus().isIdle()` consumes.
"""
from __future__ import annotations

from typing import Iterable, Optional

from src.enrichment.models import EntityTags
from src.enrichment.recent_window import ensure_aware
from src.enrichment.tagger.base import Tagger, TaggingContext


class FileClassifiersTagger:
    """Per-file mandatory classifiers: status, role, creationYear.

    `status` derives from `last_change_date` vs `ctx.recent_cutoff` (governed by
    `cfg.recent_window_days`). `role` priority order: build → test → doc → config
    → production fallback (regex catalogs in `cfg.{build,test,doc,config}_patterns`).
    `creationYear` is the year of the file's first observed change.
    """

    CLASSIFIERS = [
        {"slot": "status",       "entity": "file",
         "values": ["active", "idle"]},
        {"slot": "role",         "entity": "file",
         "values": ["production", "test", "config", "doc", "build"]},
        # creationYear is dynamic (year string of first change) — no fixed vocabulary.
        {"slot": "creationYear", "entity": "file", "values": []},
    ]

    def tag(self, ctx: TaggingContext) -> Iterable[EntityTags]:
        git = ctx.graph_data.get("git")
        if git is None:
            return []

        cfg = ctx.config
        out: list[EntityTags] = []
        idle_cutoff = ctx.recent_cutoff  # `active` means: touched within window

        for file_ in git.file_registry.all:
            file_id = _file_id(file_)
            if file_id is None:
                continue

            classifiers: dict[str, str] = {}

            # file.status — active / idle
            last = _last_change_date(file_)
            if last is None:
                classifiers["status"] = "idle"
            elif idle_cutoff is None or last >= idle_cutoff:
                classifiers["status"] = "active"
            else:
                classifiers["status"] = "idle"

            # file.role — production / test / config / doc / build
            classifiers["role"] = _classify_role(file_id, cfg)

            # file.creationYear
            first = _first_change_date(file_)
            if first is not None:
                classifiers["creationYear"] = str(first.year)

            out.append(EntityTags(
                entity_kind="file",
                entity_id=file_id,
                classifiers=classifiers,
            ))

        return out


def _file_id(file_) -> Optional[str]:
    """Stable path-like identifier for a File. Uses last-existing name."""
    name = file_.last_existing_name() if hasattr(file_, "last_existing_name") else None
    if name:
        return name
    if file_.changes:
        return file_.changes[-1].new_file_name
    return None


def _first_change_date(file_):
    dates = [ensure_aware(getattr(ch.commit, "author_date", None))
             for ch in (file_.changes or [])
             if getattr(ch, "commit", None) is not None]
    dates = [d for d in dates if d is not None]
    return min(dates) if dates else None


def _last_change_date(file_):
    dates = [ensure_aware(getattr(ch.commit, "author_date", None))
             for ch in (file_.changes or [])
             if getattr(ch, "commit", None) is not None]
    dates = [d for d in dates if d is not None]
    return max(dates) if dates else None


def _classify_role(path: str, cfg) -> str:
    # Build comes before config, doc, test because many build files have
    # config-like extensions (package.json, pom.xml).
    for p in cfg.build_patterns:
        if p.search(path):
            return "build"
    for p in cfg.test_patterns:
        if p.search(path):
            return "test"
    for p in cfg.doc_patterns:
        if p.search(path):
            return "doc"
    for p in cfg.config_patterns:
        if p.search(path):
            return "config"
    return "production"
