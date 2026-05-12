"""Substantive port test for :class:`FileClassifierMetric`.

Synthetic mini-graph: 3 files (one production, one test, one build), 2
commits at different dates. Verifies:

* ``status`` classifier responds to the host's ``recent_cutoff``.
* ``role`` classifier respects the regex catalog order (build > test).
* ``creationYear`` reflects the earliest change date.
* All emitted entities are :class:`Classifier` instances with stable ids.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from src.common.domains.git import (
    Change,
    ChangeRegistry,
    ChangeType,
    Commit,
    CommitRegistry,
    File,
    FileRegistry,
    GitAccount,
    GitAccountRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics.implementations.file_classifiers import (
    FileClassifierMetric,
)
from src.enrichment.relations_v2 import RelationRegistry
from src.enrichment.tags import Classifier, ClassifierRegistry, TraitRegistry


# ----------------------------------------------------------------------
# Synthetic host + tiny config
# ----------------------------------------------------------------------
@dataclass
class _Config:
    """Subset of EnrichmentConfig fields the metric reads."""

    build_patterns: list = field(
        default_factory=lambda: [
            re.compile(r"(^|/)(pom\.xml|Makefile)$", re.IGNORECASE),
            re.compile(r"\.toml$", re.IGNORECASE),
        ]
    )
    test_patterns: list = field(
        default_factory=lambda: [re.compile(r"_test\.py$", re.IGNORECASE)]
    )
    doc_patterns: list = field(
        default_factory=lambda: [re.compile(r"\.md$", re.IGNORECASE)]
    )
    config_patterns: list = field(default_factory=list)


@dataclass
class _Host:
    files: FileRegistry
    commits: CommitRegistry
    changes: ChangeRegistry
    accounts: GitAccountRegistry
    relations: RelationRegistry
    traits: TraitRegistry
    classifiers: ClassifierRegistry
    recent_cutoff: Optional[datetime] = None


_PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id="p")


def _make_file(path: str) -> File:
    return File(
        id=path,
        path=path,
        project_ref=_PROJECT_REF,
        extension=File.derive_extension(path),
    )


def _make_account() -> GitAccount:
    return GitAccount(
        id=GitAccount.make_id("Alice", "alice@example.com"),
        name="Alice",
        email="alice@example.com",
        project_ref=_PROJECT_REF,
    )


def _make_commit(sha: str, *, author: GitAccount, when: datetime) -> Commit:
    return Commit(
        id=sha,
        project_ref=_PROJECT_REF,
        message=f"commit {sha}",
        author_date=when,
        committer_date=when,
        author_ref=author.ref(),
        committer_ref=author.ref(),
    )


def _make_change(commit: Commit, file_: File) -> Change:
    return Change(
        id=Change.make_id(commit.id, file_.path, file_.path),
        commit_ref=commit.ref(),
        file_ref=file_.ref(),
        change_type=ChangeType.MODIFY,
        old_path=file_.path,
        new_path=file_.path,
    )


@pytest.fixture
def host() -> _Host:
    return _Host(
        files=FileRegistry(),
        commits=CommitRegistry(),
        changes=ChangeRegistry(),
        accounts=GitAccountRegistry(),
        relations=RelationRegistry(),
        traits=TraitRegistry(),
        classifiers=ClassifierRegistry(),
    )


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_file_classifier_emits_three_classifiers_per_file(host: _Host) -> None:
    """A file with a change date emits status + role + creationYear."""
    f = _make_file("src/foo.py")
    host.files.add(f)
    alice = _make_account()
    host.accounts.add(alice)
    when = datetime(2024, 5, 1, tzinfo=timezone.utc)
    commit = _make_commit("c1", author=alice, when=when)
    host.commits.add(commit)
    host.changes.add(_make_change(commit, f))

    config = _Config()
    out = list(FileClassifierMetric().compute(host, config))
    assert all(isinstance(c, Classifier) for c in out)

    dims = {c.dimension for c in out}
    assert dims == {"status", "role", "creationYear"}

    by_dim = {c.dimension: c for c in out}
    # Without recent_cutoff, everything is "active".
    assert by_dim["status"].value == "active"
    # The path doesn't match build/test/doc/config → "production".
    assert by_dim["role"].value == "production"
    assert by_dim["creationYear"].value == "2024"


def test_status_responds_to_recent_cutoff(host: _Host) -> None:
    """Files older than the cutoff are tagged idle."""
    f_old = _make_file("old/foo.py")
    f_new = _make_file("new/bar.py")
    for ff in (f_old, f_new):
        host.files.add(ff)
    alice = _make_account()
    host.accounts.add(alice)
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    new = datetime(2024, 1, 1, tzinfo=timezone.utc)
    c_old = _make_commit("c_old", author=alice, when=old)
    c_new = _make_commit("c_new", author=alice, when=new)
    for c in (c_old, c_new):
        host.commits.add(c)
    host.changes.add(_make_change(c_old, f_old))
    host.changes.add(_make_change(c_new, f_new))

    host.recent_cutoff = new - timedelta(days=1)
    config = _Config()
    out = list(FileClassifierMetric().compute(host, config))
    by_file_dim = {(c.target.id, c.dimension): c.value for c in out}
    assert by_file_dim[("old/foo.py", "status")] == "idle"
    assert by_file_dim[("new/bar.py", "status")] == "active"


def test_role_classification_uses_regex_catalogs(host: _Host) -> None:
    """Build wins over test/doc/production for ambiguous paths."""
    files = [
        _make_file("pom.xml"),              # build (first match)
        _make_file("foo_test.py"),          # test
        _make_file("README.md"),            # doc
        _make_file("src/app.py"),           # production fallback
    ]
    for f in files:
        host.files.add(f)
    alice = _make_account()
    host.accounts.add(alice)
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i, f in enumerate(files):
        c = _make_commit(f"c{i}", author=alice, when=when)
        host.commits.add(c)
        host.changes.add(_make_change(c, f))

    config = _Config()
    out = list(FileClassifierMetric().compute(host, config))
    by_target_dim = {(c.target.id, c.dimension): c.value for c in out}
    assert by_target_dim[("pom.xml", "role")] == "build"
    assert by_target_dim[("foo_test.py", "role")] == "test"
    assert by_target_dim[("README.md", "role")] == "doc"
    assert by_target_dim[("src/app.py", "role")] == "production"


def test_no_change_dates_means_idle_and_no_creationYear(host: _Host) -> None:
    """A file with zero changes → ``status=idle``, no creationYear emitted."""
    f = _make_file("untouched.py")
    host.files.add(f)
    config = _Config()
    out = list(FileClassifierMetric().compute(host, config))
    by_dim = {c.dimension for c in out}
    assert "creationYear" not in by_dim
    assert "status" in by_dim
    status_c = next(c for c in out if c.dimension == "status")
    assert status_c.value == "idle"


def test_classifier_ids_are_stable_across_runs(host: _Host) -> None:
    """Re-running the metric produces the same classifier ids — registry dedup."""
    f = _make_file("src/foo.py")
    host.files.add(f)
    alice = _make_account()
    host.accounts.add(alice)
    when = datetime(2024, 1, 1, tzinfo=timezone.utc)
    c = _make_commit("c1", author=alice, when=when)
    host.commits.add(c)
    host.changes.add(_make_change(c, f))
    config = _Config()

    metric = FileClassifierMetric()
    ids_first = sorted(c.id for c in metric.compute(host, config))
    ids_second = sorted(c.id for c in metric.compute(host, config))
    assert ids_first == ids_second
