"""A2.3 relations: shared-devs / shared-task-prefixes / time-windowed
co-changes (file/author/component) + file-name similarity."""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone

from src.common.models import (
    Issue,
    IssueStatus,
    IssueStatusCategory,
    IssueType,
    JiraProject,
)
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    EPOCH,
    UTC,
    build_synthetic_graph,
    make_account,
    make_change,
    make_commit,
    make_file,
)
from src.common.models import GitProject


def _split_components_mapping() -> str:
    """A mapping that splits the synthetic fixture into two components so the
    component-aggregation extractors can emit cross-component edges."""
    payload = {
        "buggy": {"path_prefix": "src/buggy"},
        "owner": {"path_prefix": "src/owner"},
    }
    fh = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(payload, fh)
    fh.close()
    return fh.name


# ── File-file family ─────────────────────────────────────────────────────────

def test_file_shared_devs_emits_for_pairs_with_overlapping_authors():
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("cochange.file-file.shared-devs", "lifetime")
    assert rf is not None
    # buggy.py + owner.py co-change in commits authored by both Alice and Bob,
    # so the shared-dev count must be >= 1.
    pairs = {
        tuple(sorted((r.source_id, r.target_id))): r.strength
        for r in rf.relations
    }
    target = ("src/buggy.py", "src/owner.py")
    assert target in pairs, f"missing pair, got {list(pairs)}"
    assert pairs[target] >= 1


def test_file_shared_task_prefixes_emits_when_jira_linked():
    g = build_synthetic_graph()
    git_proj = g["git"]

    # Stand up a tiny Jira project and link two issues (different prefixes
    # would be distinguishable, but one prefix is enough for the assertion).
    now = datetime.now(UTC)
    jp = JiraProject(name="jp")
    cat = IssueStatusCategory(key="indeterminate", name="In Progress")
    status = IssueStatus(id="1", name="In Progress", issue_status_categories=cat)
    bug = IssueType(id="t1", name="Bug", description="bug", isSubTask=False)
    iss = Issue(
        id=1, key="ZEP-1", summary="x", createdAt=now, updatedAt=now,
        issue_statuses=[status], issue_types=[bug],
    )
    jp.issue_registry.add_all([iss])
    jp.issue_status_registry.add_all([status])
    jp.issue_status_category_registry.add_all([cat])
    jp.issue_type_registry.add_all([bug])
    g["jira"] = jp

    # Attach the issue to two commits that co-touch both buggy and owner.
    bug_commits = [
        c for c in git_proj.git_commit_registry.all
        if any(getattr(ch, "new_file_name", "").endswith("buggy.py") for ch in c.changes)
        and any(getattr(ch, "new_file_name", "").endswith("owner.py") for ch in c.changes)
    ]
    assert len(bug_commits) >= 1
    for c in bug_commits[:2]:
        c.issues.append(iss)
        iss.git_commits.append(c)

    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("cochange.file-file.shared-task-prefixes", "lifetime")
    assert rf is not None
    pairs = {
        tuple(sorted((r.source_id, r.target_id))): r.strength
        for r in rf.relations
    }
    assert pairs.get(("src/buggy.py", "src/owner.py"), 0) >= 1


def test_file_time_windowed_emits_for_close_in_time_distinct_commits():
    """Two files touched by separate commits inside the Δt window."""
    proj = GitProject(name="tw")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([alice])

    f_a = make_file(proj)
    f_b = make_file(proj)
    proj.file_registry.add_all([f_a, f_b])

    now = datetime.now(UTC)
    c1 = make_commit(proj, "c1", "msg", alice, now - timedelta(hours=1))
    make_change(c1, f_a, "src/a.py", added=5)
    c2 = make_commit(proj, "c2", "msg", alice, now - timedelta(minutes=30))
    make_change(c2, f_b, "src/b.py", added=5)
    proj.git_commit_registry.add_all([c1, c2])

    g = {"git": proj, "jira": None, "github": None}
    cfg = EnrichmentConfig(time_windowed_cochange_hours=24)
    e = compute_enrichments(g, cfg)
    rf = e.relation_file("cochange.file-file.time-windowed", "lifetime")
    assert rf is not None
    pairs = {tuple(sorted((r.source_id, r.target_id))): r.strength for r in rf.relations}
    assert pairs.get(("src/a.py", "src/b.py"), 0) >= 1


def test_file_name_similarity_emits_for_near_basenames():
    """Two files with near-identical basenames must produce a similarity edge."""
    proj = GitProject(name="sim")
    alice = make_account("Alice", "a@e")
    proj.account_registry.add_all([alice])

    a = make_file(proj)
    b = make_file(proj)
    c = make_file(proj)  # decoy with very different name
    proj.file_registry.add_all([a, b, c])

    now = datetime.now(UTC)
    ca = make_commit(proj, "ca", "init", alice, now - timedelta(days=1))
    make_change(ca, a, "src/UserService.java", added=5)
    cb = make_commit(proj, "cb", "init", alice, now - timedelta(days=1))
    make_change(cb, b, "src/UserServices.java", added=5)
    cc = make_commit(proj, "cc", "init", alice, now - timedelta(days=1))
    make_change(cc, c, "lib/totally_unrelated.kt", added=5)
    proj.git_commit_registry.add_all([ca, cb, cc])

    g = {"git": proj, "jira": None, "github": None}
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("similarity.file-file.names", "lifetime")
    assert rf is not None
    pairs = {tuple(sorted((r.source_id, r.target_id))): r.strength for r in rf.relations}
    assert ("src/UserService.java", "src/UserServices.java") in pairs


def test_file_name_similarity_scales_to_50_files():
    """Sanity: O(N²) extractor without ext-filter must still finish quickly
    on 50 files. We only assert it returns; tight time bounds aren't reliable
    in CI but ~50 files × ~50 candidates is well under 1s on any laptop."""
    proj = GitProject(name="scale")
    alice = make_account("Alice", "a@e")
    proj.account_registry.add_all([alice])

    files = []
    commits = []
    now = datetime.now(UTC)
    for i in range(50):
        f = make_file(proj)
        files.append(f)
        c = make_commit(proj, f"c{i}", "init", alice, now - timedelta(days=1))
        make_change(c, f, f"src/Module_{i:02d}.py", added=2)
        commits.append(c)
    proj.file_registry.add_all(files)
    proj.git_commit_registry.add_all(commits)

    g = {"git": proj, "jira": None, "github": None}
    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("similarity.file-file.names", "lifetime")
    assert rf is not None  # extractor produced output


# ── Author-author family ──────────────────────────────────────────────────────

def test_author_shared_task_prefixes_emits_when_authors_share_jira_prefixes():
    g = build_synthetic_graph()
    git_proj = g["git"]

    now = datetime.now(UTC)
    jp = JiraProject(name="jp")
    cat = IssueStatusCategory(key="indeterminate", name="In Progress")
    status = IssueStatus(id="1", name="In Progress", issue_status_categories=cat)
    bug = IssueType(id="t1", name="Bug", description="bug", isSubTask=False)
    iss = Issue(
        id=1, key="ZEP-1", summary="x", createdAt=now, updatedAt=now,
        issue_statuses=[status], issue_types=[bug],
    )
    jp.issue_registry.add_all([iss])
    jp.issue_status_registry.add_all([status])
    jp.issue_status_category_registry.add_all([cat])
    jp.issue_type_registry.add_all([bug])
    g["jira"] = jp

    # Link the issue to one commit per author so the shared-prefix set has > 0.
    by_author: dict[str, list] = {}
    for c in git_proj.git_commit_registry.all:
        if c.author is None:
            continue
        by_author.setdefault(c.author.id, []).append(c)
    for aid, cs in by_author.items():
        cs[0].issues.append(iss)
        iss.git_commits.append(cs[0])

    e = compute_enrichments(g, EnrichmentConfig())
    rf = e.relation_file("cochange.author-author.shared-task-prefixes", "lifetime")
    assert rf is not None
    assert len(rf.relations) >= 1
    assert all(r.strength >= 1 for r in rf.relations)


def test_author_time_windowed_counts_commits_inside_window():
    proj = GitProject(name="tw-auth")
    alice = make_account("Alice", "a@e")
    bob = make_account("Bob", "b@e")
    proj.account_registry.add_all([alice, bob])

    f = make_file(proj)
    proj.file_registry.add_all([f])

    now = datetime.now(UTC)
    # Two pairs of co-occurring commits inside the 24h window.
    c1 = make_commit(proj, "c1", "m", alice, now - timedelta(hours=1))
    make_change(c1, f, "src/x.py", added=1)
    c2 = make_commit(proj, "c2", "m", bob, now - timedelta(minutes=30))
    make_change(c2, f, "src/x.py", added=1)
    c3 = make_commit(proj, "c3", "m", alice, now - timedelta(hours=20))
    make_change(c3, f, "src/x.py", added=1)
    c4 = make_commit(proj, "c4", "m", bob, now - timedelta(hours=21))
    make_change(c4, f, "src/x.py", added=1)
    # And one pair far apart (must NOT add to the 24h count).
    c5 = make_commit(proj, "c5", "m", alice, now - timedelta(days=200))
    make_change(c5, f, "src/x.py", added=1)
    proj.git_commit_registry.add_all([c1, c2, c3, c4, c5])

    g = {"git": proj, "jira": None, "github": None}
    cfg = EnrichmentConfig(time_windowed_cochange_hours=24)
    e = compute_enrichments(g, cfg)
    rf = e.relation_file("cochange.author-author.time-windowed", "lifetime")
    assert rf is not None
    edges = [r for r in rf.relations if {r.source_id, r.target_id} == {alice.id, bob.id}]
    assert len(edges) == 1
    # 4 close-in-time pairings: c1↔c2, c1↔c4, c3↔c2, c3↔c4 (all within 24h).
    # Strength counted from each Alice commit's POV — must be >= 4.
    assert edges[0].strength >= 4


# ── Component-component family ────────────────────────────────────────────────

def test_component_aggregations_emit_when_file_pairs_present():
    """All three component aggregators emit RelationFiles when their file-pair
    sources are non-empty."""
    g = build_synthetic_graph()
    git_proj = g["git"]

    # Add jira so the shared-task-prefixes pipelines have data.
    now = datetime.now(UTC)
    jp = JiraProject(name="jp")
    cat = IssueStatusCategory(key="indeterminate", name="In Progress")
    status = IssueStatus(id="1", name="In Progress", issue_status_categories=cat)
    bug = IssueType(id="t1", name="Bug", description="bug", isSubTask=False)
    iss = Issue(
        id=1, key="ZEP-1", summary="x", createdAt=now, updatedAt=now,
        issue_statuses=[status], issue_types=[bug],
    )
    jp.issue_registry.add_all([iss])
    jp.issue_status_registry.add_all([status])
    jp.issue_status_category_registry.add_all([cat])
    jp.issue_type_registry.add_all([bug])
    g["jira"] = jp
    for c in list(git_proj.git_commit_registry.all)[:3]:
        c.issues.append(iss)
        iss.git_commits.append(c)

    cfg = EnrichmentConfig(components_mapping_path=_split_components_mapping())
    e = compute_enrichments(g, cfg)

    for kind in (
        "cochange.component-component.shared-devs",
        "cochange.component-component.shared-task-prefixes",
        "cochange.component-component.time-windowed",
    ):
        rf = e.relation_file(kind, "lifetime")
        assert rf is not None, f"missing kind {kind}"
        # No self-loops.
        for r in rf.relations:
            assert r.source_id != r.target_id


def test_a23_kinds_present_after_pipeline_run():
    """Smoke: every A2.3 RelationFile is emitted (lifetime + recent where
    applicable). similarity.* is lifetime-only; the rest expose both windows."""
    g = build_synthetic_graph()
    e = compute_enrichments(g, EnrichmentConfig())
    kinds = {(r.kind, r.window) for r in e.relations}

    expected_both = [
        "cochange.file-file.shared-devs",
        "cochange.file-file.shared-task-prefixes",
        "cochange.file-file.time-windowed",
        "cochange.author-author.shared-task-prefixes",
        "cochange.author-author.time-windowed",
        "cochange.component-component.shared-devs",
        "cochange.component-component.shared-task-prefixes",
        "cochange.component-component.time-windowed",
    ]
    for k in expected_both:
        assert (k, "lifetime") in kinds, f"missing lifetime for {k}"
        assert (k, "recent") in kinds, f"missing recent for {k}"
    assert ("similarity.file-file.names", "lifetime") in kinds
