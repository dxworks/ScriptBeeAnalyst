"""``MCPSandboxView`` — exercise every row in the plan §11 mapping table.

Builds a small typed :class:`Graph` (1 commit, 1 file (+ a sibling file
for cochange), 1 issue, 1 PR, plus 1 trait + 1 classifier + 1 cochange
relation), wraps it in :class:`MCPSandboxView`, and asserts every
public attribute / method returns the expected shape.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.common.domains.git.models import Commit, File, GitAccount, GitProject
from src.common.domains.github.models import (
    GitHubCommit,
    GitHubProject,
    GitHubUser,
    PullRequest,
)
from src.common.domains.jira.models import (
    Issue,
    IssueStatus,
    IssueType,
    JiraProject,
    JiraUser,
)
from src.common.kernel import EntityKind, EntityRef, Graph
from src.common.people import SourceKind
from src.enrichment.relations import Relation, WindowKind
from src.enrichment.tags.base import Classifier, Trait, TraitFamily
from src.sandbox import MCPSandboxView


# ----------------------------------------------------------------------
# Fixture: a small but representative graph
# ----------------------------------------------------------------------
@pytest.fixture
def small_graph() -> Graph:
    """1 commit + 2 files + 1 issue + 1 PR + 1 trait + 1 classifier +
    1 cochange relation between the two files.

    Returns the constructed :class:`Graph`.
    """
    g = Graph(project_id="sb-test")

    # Git side.
    git_project = GitProject(id="gp1", name="zep", source=SourceKind.GIT)
    git_ref = git_project.ref()
    alice = GitAccount(
        id="alice",
        name="Alice",
        project_ref=git_ref,
        email="alice@x",
    )
    file_a = File(
        id="src/a.py",
        project_ref=git_ref,
        path="src/a.py",
        extension="py",
    )
    file_b = File(
        id="src/b.py",
        project_ref=git_ref,
        path="src/b.py",
        extension="py",
    )
    commit = Commit(
        id="sha1234",  # acts as both git commit id AND GitHubCommit.sha
        sha="sha1234",
        project_ref=git_ref,
        message="Fix JIR-1: tidy up parser",
        author_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        committer_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author_ref=alice.ref(),
        committer_ref=alice.ref(),
    )
    g.add_project(git_project)
    g.git_accounts.add(alice)
    g.files.add(file_a)
    g.files.add(file_b)
    g.commits.add(commit)

    # Jira side.
    jira_project = JiraProject(id="jp1", name="JIR", source=SourceKind.JIRA)
    jira_ref = jira_project.ref()
    status = IssueStatus(
        id="open", project_ref=jira_ref, name="Open", category="new"
    )
    bug_type = IssueType(id="bug", project_ref=jira_ref, name="Bug")
    bob_jira = JiraUser(
        id="bob-jira",
        name="Bob",
        project_ref=jira_ref,
        key="bob",
        link="https://jira/bob",
    )
    issue = Issue(
        id="JIR-1",
        project_ref=jira_ref,
        key="JIR-1",
        summary="parser crash",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        status_ref=status.ref(),
        type_ref=bug_type.ref(),
        reporter_ref=bob_jira.ref(),
    )
    g.add_project(jira_project)
    g.issue_statuses.add(status)
    g.issue_types.add(bug_type)
    g.jira_users.add(bob_jira)
    g.issues.add(issue)

    # GitHub side.
    gh_project = GitHubProject(id="ghp1", name="zep-gh", source=SourceKind.GITHUB)
    gh_ref = gh_project.ref()
    gh_user = GitHubUser(
        id="alice-gh",
        name="Alice",
        project_ref=gh_ref,
        login="alice-gh",
    )
    pr = PullRequest(
        id="42",
        project_ref=gh_ref,
        number=42,
        title="parser fixes",
        body="Fixes JIR-1",
        state="closed",
        author_ref=gh_user.ref(),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
    gh_commit = GitHubCommit(
        id="sha1234",  # same id as the git Commit.sha — that's the join key
        pull_request_ref=pr.ref(),
        sha="sha1234",
        date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        message="Fix JIR-1",
        author_ref=gh_user.ref(),
    )
    pr.commit_refs = [gh_commit.ref()]
    g.add_project(gh_project)
    g.github_users.add(gh_user)
    g.github_commits.add(gh_commit)
    g.pull_requests.add(pr)

    # Enrichment side: 1 trait + 1 classifier + 1 cochange relation
    # between the two files.
    trait = Trait(
        id="t-1",
        target=file_a.ref(),
        family=TraitFamily.TESTING,
        name="anomaly.testing.BugMagnet",
        severity=0.8,
    )
    classifier = Classifier(
        id="c-1",
        target=file_a.ref(),
        dimension="role",
        value="production",
    )
    cochange = Relation(
        id=Relation.canonical_id(
            file_a.ref(), file_b.ref(), "cochange", WindowKind.LIFETIME
        ),
        source=file_a.ref(),
        target=file_b.ref(),
        relation_kind="cochange",
        window=WindowKind.LIFETIME,
        strength=3.0,
    )
    g.traits.add(trait)
    g.classifiers.add(classifier)
    g.relations.add(cochange)

    return g


@pytest.fixture
def view(small_graph: Graph) -> MCPSandboxView:
    return MCPSandboxView(small_graph)


# ----------------------------------------------------------------------
# Mapping table rows 1–3, files
# ----------------------------------------------------------------------
def test_commits_all_returns_all_commits(view: MCPSandboxView):
    items = view.commits.all()
    assert len(items) == 1
    assert items[0].id == "sha1234"


def test_commits_get_by_id(view: MCPSandboxView):
    c = view.commits.get("sha1234")
    assert c is not None
    assert c.message.startswith("Fix JIR-1")


def test_commits_iterable(view: MCPSandboxView):
    ids = [c.id for c in view.commits]
    assert ids == ["sha1234"]


def test_issues_all_and_get(view: MCPSandboxView):
    assert len(view.issues.all()) == 1
    issue = view.issues.get("JIR-1")
    assert issue is not None
    assert issue.key == "JIR-1"


def test_pull_requests_all_and_get(view: MCPSandboxView):
    assert len(view.pull_requests.all()) == 1
    pr = view.pull_requests.get("42")
    assert pr is not None
    assert pr.number == 42


def test_files_all_and_get(view: MCPSandboxView):
    assert {f.id for f in view.files.all()} == {"src/a.py", "src/b.py"}
    assert view.files.get("src/a.py") is not None


# ----------------------------------------------------------------------
# Mapping table row 7: tags_for(ref)
# ----------------------------------------------------------------------
def test_tags_for_returns_traits_and_classifiers(view: MCPSandboxView):
    file_a_ref = EntityRef(kind=EntityKind.FILE, id="src/a.py")
    tags = view.tags_for(file_a_ref)
    names = {getattr(t, "name", None) or t.dimension for t in tags}
    # Trait by name + classifier by dimension.
    assert "anomaly.testing.BugMagnet" in names
    assert "role" in names
    # Traits first, classifiers second.
    assert isinstance(tags[0], Trait)
    assert isinstance(tags[-1], Classifier)


def test_tags_for_missing_target_returns_empty(view: MCPSandboxView):
    # File with no traits/classifiers on it.
    other_ref = EntityRef(kind=EntityKind.FILE, id="src/b.py")
    assert view.tags_for(other_ref) == []


# ----------------------------------------------------------------------
# Mapping table row 8: find_files_with_trait
# ----------------------------------------------------------------------
def test_find_files_with_trait_returns_file_objects(view: MCPSandboxView):
    files = view.find_files_with_trait("anomaly.testing.BugMagnet")
    assert len(files) == 1
    assert files[0].id == "src/a.py"


def test_find_files_with_trait_unknown_name_returns_empty(view: MCPSandboxView):
    assert view.find_files_with_trait("anomaly.never.exists") == []


# ----------------------------------------------------------------------
# Mapping table row 9: cochange_neighbors
# ----------------------------------------------------------------------
def test_cochange_neighbors_returns_target_files(view: MCPSandboxView):
    neighbors = view.cochange_neighbors("src/a.py", WindowKind.LIFETIME)
    assert len(neighbors) == 1
    assert neighbors[0].id == "src/b.py"


def test_cochange_neighbors_accepts_string_window(view: MCPSandboxView):
    # Both enum and bare-string window forms work.
    neighbors = view.cochange_neighbors("src/a.py", "lifetime")
    assert len(neighbors) == 1


def test_cochange_neighbors_limit(view: MCPSandboxView):
    # Limit of 0 returns empty.
    assert view.cochange_neighbors("src/a.py", WindowKind.LIFETIME, limit=0) == []


def test_cochange_neighbors_unknown_source(view: MCPSandboxView):
    assert view.cochange_neighbors("nope.py") == []


# ----------------------------------------------------------------------
# Mapping table row 10: find_files_with_classifier
# ----------------------------------------------------------------------
def test_find_files_with_classifier(view: MCPSandboxView):
    files = view.find_files_with_classifier("role", "production")
    assert len(files) == 1
    assert files[0].id == "src/a.py"


def test_find_files_with_classifier_unknown_value(view: MCPSandboxView):
    assert view.find_files_with_classifier("role", "nope") == []


# ----------------------------------------------------------------------
# Mapping table rows 12, 13: list_metrics / list_overviews
# ----------------------------------------------------------------------
def test_list_metrics_nonempty(view: MCPSandboxView):
    names = view.list_metrics()
    assert isinstance(names, list)
    # Chunk-7 ships ≥14 metrics; the catalog must be non-empty.
    assert len(names) >= 1


def test_list_overviews_nonempty(view: MCPSandboxView):
    names = view.list_overviews()
    assert isinstance(names, list)
    # Chunk-7 ships 11 overview stubs.
    assert len(names) >= 1


# ----------------------------------------------------------------------
# Mapping table row 11: overview_as_dict
# ----------------------------------------------------------------------
def test_overview_as_dict_populated_overview_returns_dict(view: MCPSandboxView):
    # ``authorship`` is fully implemented as of Chunk 17 — the view
    # forwards the rendered :class:`OverviewTable` as a JSON-ready dict
    # (``rows`` is keyed by entity_id; one row per top-level folder +
    # the synthetic ``(project)`` row).
    result = view.overview_as_dict("authorship")
    assert isinstance(result, dict)
    assert result["name"] == "authorship"
    assert "(project)" in result["rows"]
    # The rendered (project) row mirrors the ``COLUMNS`` spec.
    assert "total_authors" in result["rows"]["(project)"]


def test_overview_as_dict_heavy_overview_now_returns_dict(view: MCPSandboxView):
    # Chunk-18 ships the five heavy overviews. ``components`` was the
    # canonical "deferred stub" example through Chunk 17; it now renders
    # to a populated dict like every other registered overview, with the
    # synthetic ``(project)`` row in ``result["rows"]``.
    result = view.overview_as_dict("components")
    assert isinstance(result, dict)
    assert result["name"] == "components"
    assert "(project)" in result["rows"]


def test_overview_as_dict_unknown_name_returns_none(view: MCPSandboxView):
    assert view.overview_as_dict("nonexistent_table") is None


# ----------------------------------------------------------------------
# Read-through: any graph attribute the view doesn't declare flows
# through __getattr__ to the underlying Graph (mapping table row 14
# "the rest" — `graph_data.unified_users` etc.).
# ----------------------------------------------------------------------
def test_read_through_unified_users(view: MCPSandboxView, small_graph: Graph):
    # ``unified_users`` is not a declared property on the view, but
    # __getattr__ should pass it through to the typed Graph.
    assert view.unified_users is small_graph.unified_users


def test_read_through_components(view: MCPSandboxView, small_graph: Graph):
    assert view.components is small_graph.components


def test_read_through_traits_and_classifiers(view: MCPSandboxView, small_graph: Graph):
    assert view.traits is small_graph.traits
    assert view.classifiers is small_graph.classifiers
    assert view.relations is small_graph.relations


def test_read_through_project_id(view: MCPSandboxView):
    # Scalar Graph attribute also accessible.
    assert view.project_id == "sb-test"


def test_attribute_error_on_unknown(view: MCPSandboxView):
    with pytest.raises(AttributeError):
        _ = view.this_does_not_exist


# ----------------------------------------------------------------------
# Ergonomics: __repr__, __iter__
# ----------------------------------------------------------------------
def test_repr_summarises_view(view: MCPSandboxView):
    r = repr(view)
    # The class was renamed to QuerySandboxView in P5.A; the
    # ``MCPSandboxView`` alias points at the new name, so the repr
    # prints the canonical class name.
    assert "QuerySandboxView" in r
    assert "sb-test" in r
    assert "commits=1" in r


def test_iter_yields_main_registry_names(view: MCPSandboxView):
    assert list(view) == ["commits", "files", "issues", "pull_requests"]


# ----------------------------------------------------------------------
# duplication_pairs alias (legacy compat)
# ----------------------------------------------------------------------
def test_duplication_pairs_alias_returns_same_registry(view: MCPSandboxView):
    assert view.duplication_pairs is view._graph.duplications
    assert view.duplication_pairs is view.duplications


# ----------------------------------------------------------------------
# list_registries — 33 typed registries
# ----------------------------------------------------------------------
def test_list_registries_lists_all_33_fields(view: MCPSandboxView):
    from src.common.kernel.graph import _FIELD_SPECS

    entries = view.list_registries()
    assert isinstance(entries, list)
    assert len(entries) == len(_FIELD_SPECS)
    for entry in entries:
        assert "name" in entry
        assert "entity_kind" in entry
        assert "registry_type" in entry
        assert "count" in entry
        assert "indexes" in entry


# ----------------------------------------------------------------------
# traits_for / relations_for / metrics_for_file (P1 helpers)
# ----------------------------------------------------------------------
def test_traits_for_returns_traits_for_ref(view: MCPSandboxView):
    file_a_ref = EntityRef(kind=EntityKind.FILE, id="src/a.py")
    traits = view.traits_for(file_a_ref)
    assert len(traits) == 1
    assert traits[0].name == "anomaly.testing.BugMagnet"
    # Name filter narrows.
    assert view.traits_for(file_a_ref, name="anomaly.testing.BugMagnet") == traits
    assert view.traits_for(file_a_ref, name="other") == []


def test_relations_for_returns_relations_filtered_by_kind(view: MCPSandboxView):
    file_a_ref = EntityRef(kind=EntityKind.FILE, id="src/a.py")
    rels = view.relations_for(file_a_ref, kind="cochange")
    assert len(rels) == 1
    assert rels[0].relation_kind == "cochange"
    # Direction filter.
    out_rels = view.relations_for(file_a_ref, direction="out")
    assert len(out_rels) == 1
    # Unknown kind filters all out.
    assert view.relations_for(file_a_ref, kind="nope") == []


def test_metrics_for_file_returns_dict(small_graph: Graph):
    from src.common.domains.git.models import GitProject
    from src.common.domains.metrics_lizard.models import (
        FileMetric,
        LizardMetricsProject,
    )

    lizard_project = LizardMetricsProject(
        id="lz1", name="zep-lz", source=SourceKind.GIT
    )
    lz_ref = lizard_project.ref()
    file_a_ref = EntityRef(kind=EntityKind.FILE, id="src/a.py")
    fm = FileMetric(
        id=FileMetric.make_id("src/a.py", "sum_nloc"),
        project_ref=lz_ref,
        file_ref=file_a_ref,
        metric_name="sum_nloc",
        value=220.0,
    )
    small_graph.add_project(lizard_project)
    small_graph.file_metrics.add(fm)

    view = MCPSandboxView(small_graph)
    metrics = view.metrics_for_file("src/a.py")
    assert isinstance(metrics, dict)
    assert metrics == {"sum_nloc": 220.0}
    # Unknown file is empty.
    assert view.metrics_for_file("nope.py") == {}


# ----------------------------------------------------------------------
# list_file_metrics
# ----------------------------------------------------------------------
def test_list_file_metrics_returns_names(small_graph: Graph):
    from src.common.domains.metrics_lizard.models import (
        FileMetric,
        LizardMetricsProject,
    )

    lizard_project = LizardMetricsProject(
        id="lz1", name="zep-lz", source=SourceKind.GIT
    )
    lz_ref = lizard_project.ref()
    file_a_ref = EntityRef(kind=EntityKind.FILE, id="src/a.py")
    for name, value in [("sum_nloc", 220.0), ("max_ccn", 11.0)]:
        small_graph.file_metrics.add(
            FileMetric(
                id=FileMetric.make_id("src/a.py", name),
                project_ref=lz_ref,
                file_ref=file_a_ref,
                metric_name=name,
                value=value,
            )
        )
    small_graph.add_project(lizard_project)

    view = MCPSandboxView(small_graph)
    result = view.list_file_metrics()
    assert isinstance(result, dict)
    assert result["count"] == 1
    assert result["files"][0]["file_path"] == "src/a.py"
    assert result["files"][0]["sum_nloc"] == 220.0
    assert result["files"][0]["max_ccn"] == 11.0


# ----------------------------------------------------------------------
# Per-domain summary helpers — empty-graph branch + populated shape
# ----------------------------------------------------------------------
def _summary_shape_keys() -> set[str]:
    return {"loaded", "source", "projects"}


def test_git_summary_returns_expected_shape(view: MCPSandboxView):
    result = view.git_summary()
    assert set(result.keys()) == _summary_shape_keys()
    assert result["loaded"] is True
    assert result["source"] == "git"
    assert isinstance(result["projects"], list)
    assert len(result["projects"]) == 1


def test_git_summary_empty_graph_returns_not_loaded():
    empty = Graph(project_id="empty")
    result = MCPSandboxView(empty).git_summary()
    assert result == {"loaded": False, "source": None, "projects": []}


def test_github_summary_returns_expected_shape(view: MCPSandboxView):
    result = view.github_summary()
    assert set(result.keys()) == _summary_shape_keys()
    assert result["loaded"] is True
    assert result["source"] == "github"
    assert len(result["projects"]) == 1


def test_github_summary_empty_graph_returns_not_loaded():
    empty = Graph(project_id="empty")
    result = MCPSandboxView(empty).github_summary()
    assert result == {"loaded": False, "source": None, "projects": []}


def test_jira_summary_returns_expected_shape(view: MCPSandboxView):
    result = view.jira_summary()
    assert set(result.keys()) == _summary_shape_keys()
    assert result["loaded"] is True
    assert result["source"] == "jira"
    assert len(result["projects"]) == 1


def test_jira_summary_empty_graph_returns_not_loaded():
    empty = Graph(project_id="empty")
    result = MCPSandboxView(empty).jira_summary()
    assert result == {"loaded": False, "source": None, "projects": []}


def test_quality_summary_returns_expected_shape(small_graph: Graph):
    from src.common.domains.quality.models import QualityIssue, QualityProject

    q_project = QualityProject(
        id="qp1", name="zep-q", source=SourceKind.GIT, source_tool="insider"
    )
    q_ref = q_project.ref()
    file_a_ref = EntityRef(kind=EntityKind.FILE, id="src/a.py")
    q_issue = QualityIssue(
        id="qi-1",
        project_ref=q_ref,
        file_ref=file_a_ref,
        rule_id="StubImplementer",
        category="design",
    )
    small_graph.add_project(q_project)
    small_graph.quality_issues.add(q_issue)

    result = MCPSandboxView(small_graph).quality_summary()
    assert set(result.keys()) == _summary_shape_keys()
    assert result["loaded"] is True
    assert result["source"] == "insider"
    assert len(result["projects"]) == 1
    assert result["projects"][0]["issue_count"] == 1


def test_quality_summary_empty_graph_returns_not_loaded():
    empty = Graph(project_id="empty")
    result = MCPSandboxView(empty).quality_summary()
    assert result == {"loaded": False, "source": None, "projects": []}


def test_code_structure_summary_returns_expected_shape(small_graph: Graph):
    from src.common.domains.code_structure.models import CodeStructureProject

    cs_project = CodeStructureProject(
        id="csp1",
        name="zep-cs",
        source=SourceKind.GIT,
        kind_of_source="codeframe",
    )
    small_graph.add_project(cs_project)

    result = MCPSandboxView(small_graph).code_structure_summary()
    assert set(result.keys()) == _summary_shape_keys()
    assert result["loaded"] is True
    assert result["source"] == "codeframe"
    assert len(result["projects"]) == 1


def test_code_structure_summary_empty_graph_returns_not_loaded():
    empty = Graph(project_id="empty")
    result = MCPSandboxView(empty).code_structure_summary()
    assert result == {"loaded": False, "source": None, "projects": []}


def test_duplication_summary_returns_expected_shape(small_graph: Graph):
    from src.common.domains.duplication.models import (
        DuplicationKind,
        DuplicationPair,
        DuplicationProject,
    )

    dup_project = DuplicationProject(
        id="dp1", name="zep-dup", source=SourceKind.GIT
    )
    dup_ref = dup_project.ref()
    file_a_ref = EntityRef(kind=EntityKind.FILE, id="src/a.py")
    file_b_ref = EntityRef(kind=EntityKind.FILE, id="src/b.py")
    pair = DuplicationPair(
        id=DuplicationPair.make_id("src/a.py", "src/b.py"),
        project_ref=dup_ref,
        file_a_ref=file_a_ref,
        file_b_ref=file_b_ref,
        token_count=42,
        duplication_kind=DuplicationKind.EXTERNAL,
    )
    small_graph.add_project(dup_project)
    small_graph.duplications.add(pair)

    result = MCPSandboxView(small_graph).duplication_summary()
    assert set(result.keys()) == _summary_shape_keys()
    assert result["loaded"] is True
    assert result["source"] == "dude"
    assert len(result["projects"]) == 1
    assert result["projects"][0]["external_pairs"] == 1
    assert result["projects"][0]["total_pairs"] == 1


def test_duplication_summary_empty_graph_returns_not_loaded():
    empty = Graph(project_id="empty")
    result = MCPSandboxView(empty).duplication_summary()
    assert result == {"loaded": False, "source": None, "projects": []}
