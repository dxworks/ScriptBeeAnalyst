"""Pipeline phase A / B split — UnifiedUsers redesign §H (task P4.B).

The pipeline is split into two phases keyed to the project lifecycle:

* :func:`run_pipeline_phase_a` runs at ``/projects/{id}/build``, BEFORE
  the rebind pass. It covers every builder / metric whose source and
  target avoid Account refs — the "non-people" set.
* :func:`run_pipeline_phase_b` runs at ``/projects/{id}/finalize``,
  AFTER :func:`src.smart_merge.rebind.rebind_account_refs_to_unified`
  has rebound every role-typed account ref to target a UnifiedUser.

The legacy :func:`run_pipeline` alias stays single-shot for callers
that have not yet migrated to the new lifecycle and must keep matching
``phase_a`` + ``phase_b`` chained.

These tests cover the partition (no Phase B output after phase A
alone on a PRE_MERGE graph), the finalize chain (phase A → rebind →
phase B produces the people-side outputs), and the back-compat
guarantee for the alias.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# Module-import-time env shims — matches the rest of the smart_merge
# test suite (the server module reads these at import time, and the
# rebind import triggers it transitively).
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

import pytest  # noqa: E402

from src.common.domains.git.models import (  # noqa: E402
    Change,
    ChangeType,
    Commit,
    File,
    GitAccount,
    GitProject,
    Hunk,
    LineChange,
    LineOperation,
)
from src.common.kernel import EntityKind, EntityRef, Graph, MergeState  # noqa: E402
from src.common.people import SourceKind  # noqa: E402
from src.enrichment.config import EnrichmentConfig  # noqa: E402
from src.enrichment.pipeline import (  # noqa: E402
    PipelineResult,
    phase_b_metric_names,
    phase_b_overview_names,
    phase_b_relation_kinds,
    run_pipeline,
    run_pipeline_phase_a,
    run_pipeline_phase_b,
)
from src.smart_merge.rebind import rebind_account_refs_to_unified  # noqa: E402

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Fixture builder — a git-only 3-author / 1-file graph with enough commits
# to exercise every people-side computation:
#
#   * ``coauthor`` / ``ownership`` (file-author edges)
#   * ``author.classifiers`` (activity + seniority)
#   * ``anomaly.knowledge`` (Solitaire / OrphanCausers / BusFactor /
#     ShareKnowledge / Accumulator — at minimum one fires given the
#     skew)
#   * ``anomaly.structuring`` — only fires on Jira data; we skip that
#     domain to keep the fixture tight.
#
# The graph stays a single-domain (git) so phase A's other catalog
# members (issue_file / pr_file / duplication_* / hierarchy / ...) have
# nothing to do but still execute without error.
# ---------------------------------------------------------------------------
def _build_git_fixture(project_id: str = "test-split") -> Graph:
    """Build a git-only graph: 1 project, 3 accounts, 1 file, 7 commits."""
    graph = Graph(project_id=project_id)

    project = GitProject(id="gp:demo", name="demo", source=SourceKind.GIT)
    graph.add_project(project)

    alice = GitAccount(
        id=GitAccount.make_id("Alice", "alice@example.com"),
        name="Alice",
        email="alice@example.com",
        project_ref=project.ref(),
    )
    bob = GitAccount(
        id=GitAccount.make_id("Bob", "bob@example.com"),
        name="Bob",
        email="bob@example.com",
        project_ref=project.ref(),
    )
    carol = GitAccount(
        id=GitAccount.make_id("Carol", "carol@example.com"),
        name="Carol",
        email="carol@example.com",
        project_ref=project.ref(),
    )
    for acc in (alice, bob, carol):
        graph.git_accounts.add(acc)

    f = File(
        id="src/x.py",
        path="src/x.py",
        project_ref=project.ref(),
        extension="py",
    )
    graph.files.add(f)

    now = datetime.now(UTC)

    def _commit(sha: str, author: GitAccount, when: datetime) -> Commit:
        c = Commit(
            id=sha,
            sha=sha,
            project_ref=project.ref(),
            message=f"work on {sha}",
            author_date=when,
            committer_date=when,
            author_ref=author.ref(),
            committer_ref=author.ref(),
        )
        graph.commits.add(c)
        change = Change(
            id=Change.make_id(sha, f.path, f.path),
            commit_ref=c.ref(),
            file_ref=f.ref(),
            change_type=ChangeType.MODIFY,
            old_path=f.path,
            new_path=f.path,
        )
        hunk = Hunk(
            id=Hunk.make_id(change.id, 0),
            change_ref=change.ref(),
            ordinal=0,
            line_changes=[
                LineChange(
                    operation=LineOperation.ADD,
                    line_number=i + 1,
                    commit_ref=c.ref(),
                )
                for i in range(10)
            ],
        )
        change.hunk_refs = [hunk.ref()]
        graph.changes.add(change)
        graph.hunks.add(hunk)
        return c

    # Alice — 5 recent commits → active + sole owner = Solitaire candidate.
    for i in range(5):
        _commit(f"al_{i}", alice, now - timedelta(days=5 + i))
    # Bob — 1 old commit → idle.
    _commit("bo_0", bob, now - timedelta(days=400))
    # Carol — 1 old commit → idle.
    _commit("ca_0", carol, now - timedelta(days=380))

    return graph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _names(items) -> list[str]:
    return sorted(getattr(i, "name", "?") for i in items)


def _classifiers_by_dim(graph: Graph, dimension: str) -> list:
    classifiers = getattr(graph, "classifiers", None)
    if classifiers is None or not hasattr(classifiers, "of_dimension"):
        return []
    return list(classifiers.of_dimension(dimension))


def _relations_of_kinds(graph: Graph, kinds: set[str]) -> list:
    relations = getattr(graph, "relations", None)
    if relations is None:
        return []
    out: list = []
    for kind in kinds:
        # ``of_kind`` returns an empty tuple if the registry doesn't
        # know about ``kind`` — no exception.
        if hasattr(relations, "of_kind"):
            out.extend(list(relations.of_kind(kind)))
    return out


def _traits_by_name(graph: Graph, name: str) -> list:
    traits = getattr(graph, "traits", None)
    if traits is None or not hasattr(traits, "of_name"):
        return []
    return list(traits.of_name(name))


# ---------------------------------------------------------------------------
# Partition snapshots — guard against accidental empty sets.
# ---------------------------------------------------------------------------
def test_phase_b_sets_are_documented_and_non_empty() -> None:
    """The Phase B partition must list every people-side step explicitly."""
    assert "ownership" in phase_b_relation_kinds()
    assert "coauthor" in phase_b_relation_kinds()
    assert "pr.reviewer" in phase_b_relation_kinds()
    assert "author.classifiers" in phase_b_metric_names()
    assert "anomaly.knowledge" in phase_b_metric_names()
    assert "authorship" in phase_b_overview_names()
    assert "knowledge" in phase_b_overview_names()


# ---------------------------------------------------------------------------
# Phase A — does NOT emit Phase B output on a PRE_MERGE graph.
# ---------------------------------------------------------------------------
def test_phase_a_on_pre_merge_graph_skips_phase_b_catalog() -> None:
    """Phase A runs on PRE_MERGE without touching the people-side set.

    The proof points:
    * No people-side relations land in ``graph.relations`` (no
      ``ownership`` / ``coauthor`` / ``pr.reviewer`` / ... edges).
    * No author-side classifiers exist (no ``activity`` / ``seniority``
      classifiers).
    * No anomaly-knowledge traits exist (no ``anomaly.knowledge.*``).
    * The state stays ``PRE_MERGE`` — phase A does not run rebind.
    * No people-side metric appears in ``result.metrics_run`` and no
      people-side relation builder appears in ``result.builders_run``.
    """
    graph = _build_git_fixture("phase-a-only")
    assert graph.merge_state == MergeState.PRE_MERGE

    result = run_pipeline_phase_a(graph, EnrichmentConfig())

    # State unchanged.
    assert graph.merge_state == MergeState.PRE_MERGE

    # No phase-B step appears in either ``metrics_run`` or
    # ``builders_run``.
    assert set(result.metrics_run).isdisjoint(phase_b_metric_names()), (
        f"unexpected phase B metric in phase A run: "
        f"{set(result.metrics_run) & phase_b_metric_names()}"
    )
    assert set(result.builders_run).isdisjoint(phase_b_relation_kinds()), (
        f"unexpected phase B relation builder in phase A run: "
        f"{set(result.builders_run) & phase_b_relation_kinds()}"
    )

    # No phase-B output landed in the registries.
    assert not _relations_of_kinds(
        graph, {"ownership", "coauthor", "pr_reviewer"}
    )
    assert not _classifiers_by_dim(graph, "activity")
    assert not _classifiers_by_dim(graph, "seniority")
    assert not _traits_by_name(graph, "anomaly.knowledge.Solitaire")
    assert not _traits_by_name(graph, "anomaly.knowledge.OrphanCausers")


# ---------------------------------------------------------------------------
# Phase B — on a FINALIZED graph populates the people-side outputs.
# ---------------------------------------------------------------------------
def test_phase_b_on_finalized_graph_populates_people_side_outputs() -> None:
    """Phase A → rebind → Phase B produces the people-side enrichment.

    Construction mirrors the planned finalize flow:
      1. Build a fresh graph in ``PRE_MERGE``.
      2. Run phase A (non-people half).
      3. Run rebind — every account ref → unified-user ref.
      4. Run phase B — emits author-side classifiers / relations / traits.
    """
    graph = _build_git_fixture("phase-b-flow")
    run_pipeline_phase_a(graph, EnrichmentConfig(recent_window_days=90))

    # Rebind must succeed against the phase-A graph.
    stats = rebind_account_refs_to_unified(graph)
    assert graph.merge_state == MergeState.FINALIZED
    # Three orphan git accounts → three singleton UnifiedUsers.
    assert stats.unified_users_created == 3
    # Each of 7 commits had author + committer refs to rewrite → 14.
    assert stats.refs_rewritten == 14

    # Phase B emits the people-side output.
    result_b = run_pipeline_phase_b(graph, EnrichmentConfig(recent_window_days=90))

    # ``author.classifiers`` ran and produced 2 dimensions × 3
    # unified users = 6 classifiers (one of each per UU).
    assert "author.classifiers" in result_b.metrics_run
    activity = _classifiers_by_dim(graph, "activity")
    seniority = _classifiers_by_dim(graph, "seniority")
    # Every author classifier now targets a UnifiedUser ref.
    assert activity, "expected ``activity`` classifiers after phase B"
    assert seniority, "expected ``seniority`` classifiers after phase B"
    for cls_obj in activity + seniority:
        assert cls_obj.target.kind == EntityKind.UNIFIED_USER

    # ``ownership`` / ``coauthor`` relations exist with UU-kinded source.
    ownership_rows = _relations_of_kinds(graph, {"ownership"})
    assert ownership_rows, "expected ``ownership`` relations after phase B"
    for rel in ownership_rows:
        assert rel.source.kind == EntityKind.UNIFIED_USER

    # ``anomaly.knowledge`` produced at least one Solitaire trait:
    # Alice has 5 recent commits while Bob & Carol are idle, so Alice
    # is the sole active author on the file.
    solitaire_traits = _traits_by_name(graph, "anomaly.knowledge.Solitaire")
    assert solitaire_traits, (
        "expected ``anomaly.knowledge.Solitaire`` after phase B "
        "(Alice is the lone active author on the file)"
    )


# ---------------------------------------------------------------------------
# Back-compat — single-shot ``run_pipeline`` equals phase_a + phase_b.
# ---------------------------------------------------------------------------
def test_run_pipeline_alias_emits_same_totals_as_phase_a_plus_phase_b() -> None:
    """The single-shot alias must produce the same total output set as
    ``phase_a`` + ``phase_b`` chained on a separate graph instance.

    The alias is used by callers that haven't migrated to the new
    lifecycle yet — it preserves the historical behaviour by running
    both phases against a single PRE_MERGE graph in one pass.

    The two paths land at the same ``PipelineResult`` totals because
    every builder / metric is deterministic and the catalog snapshot
    is identical.
    """
    # Path 1: single-shot alias.
    g1 = _build_git_fixture("alias-single")
    result_alias = run_pipeline(g1, EnrichmentConfig())

    # Path 2: phase_a then phase_b on a fresh graph (NO rebind in
    # between — the alias doesn't rebind either, so this is the right
    # comparison).
    g2 = _build_git_fixture("alias-chained")
    result_a = run_pipeline_phase_a(g2, EnrichmentConfig())
    result_b = run_pipeline_phase_b(g2, EnrichmentConfig())

    # The alias merges both phases into one ``PipelineResult``; the
    # chained call adds the two side-by-side.
    chained_totals = {
        "metrics_run": sorted(result_a.metrics_run + result_b.metrics_run),
        "builders_run": sorted(result_a.builders_run + result_b.builders_run),
        "traits_emitted": result_a.traits_emitted + result_b.traits_emitted,
        "classifiers_emitted": (
            result_a.classifiers_emitted + result_b.classifiers_emitted
        ),
        "relations_emitted": (
            result_a.relations_emitted + result_b.relations_emitted
        ),
    }
    alias_totals = {
        "metrics_run": sorted(result_alias.metrics_run),
        "builders_run": sorted(result_alias.builders_run),
        "traits_emitted": result_alias.traits_emitted,
        "classifiers_emitted": result_alias.classifiers_emitted,
        "relations_emitted": result_alias.relations_emitted,
    }

    assert alias_totals == chained_totals

    # Both graphs end up with the same registry sizes too.
    assert len(list(g1.classifiers)) == len(list(g2.classifiers))
    assert len(list(g1.relations)) == len(list(g2.relations))
    assert len(list(g1.traits)) == len(list(g2.traits))
