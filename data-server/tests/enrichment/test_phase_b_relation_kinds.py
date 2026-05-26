"""Phase B relation kind/endpoint invariants — UU aftermath §Bug 3.

After the rebind pass flips ``graph.merge_state`` from ``PRE_MERGE`` to
``FINALIZED``, every people-side relation that Phase B emits must carry
``UNIFIED_USER`` endpoints — never a per-source account kind
(``GIT_ACCOUNT`` / ``GITHUB_USER`` / ``JIRA_USER``).

This test builds a tiny synthetic graph (two ``GitAccount`` instances
pre-merged into a single ``UnifiedUser``, plus three commits across two
files), runs the rebind, then runs ``run_pipeline_phase_b`` and asserts
the kind invariants per-relation-kind.

Scope rationale
---------------

The test is intentionally narrower than ``tests/enrichment/
test_pipeline_split.py``: it doesn't probe metric outputs or classifier
state — only the relation table. The companion test
``tests/smart_merge/test_rebind.py::TestFinalizeCleanupDropsAccountRelations``
covers the defensive cleanup pass at finalize that drops leftover
Account-keyed relation rows from Phase A.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

# Module-import-time env shims — matches the rest of the smart_merge /
# server test suite (the server module reads these at import time, and
# the rebind import transitively triggers it).
os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "fake")
os.environ.setdefault("SUPABASE_ANON_KEY", "fake")
os.environ.setdefault("WORKSPACE_ROOT", "/tmp")

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
from src.common.kernel import EntityKind, Graph, MergeState  # noqa: E402
from src.common.people import SourceKind  # noqa: E402
from src.common.people.unified import UnifiedUser  # noqa: E402
from src.enrichment.config import EnrichmentConfig  # noqa: E402
from src.enrichment.pipeline import (  # noqa: E402
    phase_b_relation_kinds,
    run_pipeline_phase_a,
    run_pipeline_phase_b,
)
from src.smart_merge.rebind import rebind_account_refs_to_unified  # noqa: E402

UTC = timezone.utc


# The actual ``relation_kind`` values emitted by the seven Phase B
# builders. NOTE: these differ from the builder ``.name`` values
# returned by ``phase_b_relation_kinds()`` — names use dotted casing
# (``"pr.reviewer"``) and the emitted ``relation_kind`` field uses
# underscores (``"pr_reviewer"``). The cleanup pass and this assertion
# block intentionally key off the emitted ``relation_kind`` strings —
# those are what ``graph.relations.of_kind(...)`` looks up.
_PHASE_B_EMITTED_RELATION_KINDS: frozenset[str] = frozenset({
    "coauthor",
    "ownership",
    "pr_reviewer",
    "cochange_author_time_windowed",
    "cochange_author_shared_task_prefixes",
    "cochange_file_shared_devs",
    "cochange_component_shared_devs",
})


def _build_two_authors_one_uu_graph() -> Graph:
    """Build a tiny graph: 2 ``GitAccount`` pre-merged into 1 ``UnifiedUser``,
    3 commits across 2 files, enough Changes/Hunks for cochange + ownership.

    The pre-existing ``UnifiedUser`` means the rebind synthesizes 0 new
    UUs — every account's ``unified_user_id`` is already populated. This
    isolates the kind-rewrite step from the singleton-creation step.
    """
    graph = Graph(project_id="test-phase-b-relation-kinds")

    project = GitProject(id="gp:demo", name="demo", source=SourceKind.GIT)
    graph.add_project(project)

    # Build the UnifiedUser FIRST (so its id is known) — the two accounts
    # below carry its id directly so no singleton is synthesised.
    uu = UnifiedUser(
        id="uu-merged",
        display_name="Alice",
        primary_email="alice@example.com",
        account_refs=[],  # populated after the accounts are constructed.
    )

    alice = GitAccount(
        id=GitAccount.make_id("Alice", "alice@example.com"),
        name="Alice",
        email="alice@example.com",
        project_ref=project.ref(),
        unified_user_id=uu.id,
    )
    alice_alt = GitAccount(
        id=GitAccount.make_id("Alice", "a@aliases.example.com"),
        name="Alice",
        email="a@aliases.example.com",
        project_ref=project.ref(),
        unified_user_id=uu.id,
    )
    graph.git_accounts.add(alice)
    graph.git_accounts.add(alice_alt)

    uu.account_refs = [alice.ref(), alice_alt.ref()]
    graph.unified_users.add(uu)

    # A second principal — Bob — single account, will get a singleton UU
    # at rebind. Required so coauthor edges have two distinct authors
    # post-merge (otherwise the graph holds only Alice-UU and coauthor
    # emits zero edges).
    bob = GitAccount(
        id=GitAccount.make_id("Bob", "bob@example.com"),
        name="Bob",
        email="bob@example.com",
        project_ref=project.ref(),
    )
    graph.git_accounts.add(bob)

    file_a = File(
        id="src/a.py",
        path="src/a.py",
        project_ref=project.ref(),
        extension="py",
    )
    file_b = File(
        id="src/b.py",
        path="src/b.py",
        project_ref=project.ref(),
        extension="py",
    )
    graph.files.add(file_a)
    graph.files.add(file_b)

    now = datetime.now(UTC)

    def _commit(
        sha: str,
        author: GitAccount,
        files: list[File],
        when: datetime,
        message: str = "work",
    ) -> Commit:
        c = Commit(
            id=sha,
            sha=sha,
            project_ref=project.ref(),
            message=message,
            author_date=when,
            committer_date=when,
            author_ref=author.ref(),
            committer_ref=author.ref(),
        )
        graph.commits.add(c)
        for f in files:
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
                    for i in range(5)
                ],
            )
            change.hunk_refs = [hunk.ref()]
            graph.changes.add(change)
            graph.hunks.add(hunk)
        return c

    # Three commits: Alice on file_a (via her primary account), Alice on
    # both files (via her alias account, so the file_a author-set holds
    # the same UU once after rebind), Bob on file_b.
    _commit("c1", alice, [file_a], now - timedelta(days=2), message="PROJ-1 fix")
    _commit("c2", alice_alt, [file_a, file_b], now - timedelta(days=1, hours=1), message="PROJ-1 follow-up")
    _commit("c3", bob, [file_b], now, message="PROJ-2 unrelated")

    return graph


def test_phase_b_emits_only_unified_user_endpoints_post_rebind() -> None:
    """The kind invariant: every Phase B relation_kind row carries
    ``UNIFIED_USER`` on its people-side endpoint(s).

    Per-builder endpoint contract:

    * ``coauthor``                              — source & target are authors → UU↔UU.
    * ``ownership``                             — author→file → UU→FILE.
    * ``cochange_author_time_windowed``         — author↔author → UU↔UU.
    * ``cochange_author_shared_task_prefixes``  — author↔author → UU↔UU.
    * ``cochange_file_shared_devs``             — file↔file (people-side strength
                                                  rolled up from ownership) → FILE↔FILE
                                                  on the endpoints; the people-side
                                                  refs live only in ``extras``.
    * ``cochange_component_shared_devs``        — component↔component aggregator.
    * ``pr_reviewer``                           — pr→reviewer → PULL_REQUEST→UU
                                                  (no PR present here, so no row).
    """
    graph = _build_two_authors_one_uu_graph()

    # Phase A first — required so cochange / ownership prerequisites
    # (``cochange``, ``ownership`` relations the file-shared-devs
    # builder reads) exist when Phase B runs. Phase A doesn't touch the
    # people-side catalog.
    cfg = EnrichmentConfig()
    run_pipeline_phase_a(graph, cfg)
    assert graph.merge_state == MergeState.PRE_MERGE

    # Rebind: every role-typed account ref → UNIFIED_USER. Singleton UU
    # is synthesised for Bob (the orphan), pre-merged Alice keeps her
    # existing UU.
    rebind_account_refs_to_unified(graph)
    assert graph.merge_state == MergeState.FINALIZED

    # Phase B against the rebound graph.
    run_pipeline_phase_b(graph, cfg)

    # ------------------------------------------------------------------
    # Per-kind endpoint kind checks.
    # ------------------------------------------------------------------
    # NOTE: the seven Phase B builder ``name`` values (returned by
    # ``phase_b_relation_kinds()``) differ from the emitted
    # ``relation_kind`` strings — see the module-level
    # ``_PHASE_B_EMITTED_RELATION_KINDS`` set. ``graph.relations.of_kind``
    # is keyed on the emitted ``relation_kind``, so we walk that set.
    author_author_kinds = {
        "coauthor",
        "cochange_author_time_windowed",
        "cochange_author_shared_task_prefixes",
    }
    author_file_kinds = {"ownership"}
    pr_author_kinds = {"pr_reviewer"}
    file_file_kinds = {"cochange_file_shared_devs"}
    component_component_kinds = {"cochange_component_shared_devs"}

    relations = graph.relations
    for kind in author_author_kinds:
        for rel in relations.of_kind(kind):
            assert rel.source.kind == EntityKind.UNIFIED_USER, (kind, rel)
            assert rel.target.kind == EntityKind.UNIFIED_USER, (kind, rel)

    for kind in author_file_kinds:
        for rel in relations.of_kind(kind):
            assert rel.source.kind == EntityKind.UNIFIED_USER, (kind, rel)
            assert rel.target.kind == EntityKind.FILE, (kind, rel)

    for kind in pr_author_kinds:
        for rel in relations.of_kind(kind):
            assert rel.source.kind == EntityKind.PULL_REQUEST, (kind, rel)
            assert rel.target.kind == EntityKind.UNIFIED_USER, (kind, rel)

    for kind in file_file_kinds:
        for rel in relations.of_kind(kind):
            assert rel.source.kind == EntityKind.FILE, (kind, rel)
            assert rel.target.kind == EntityKind.FILE, (kind, rel)

    for kind in component_component_kinds:
        for rel in relations.of_kind(kind):
            assert rel.source.kind == EntityKind.COMPONENT, (kind, rel)
            assert rel.target.kind == EntityKind.COMPONENT, (kind, rel)

    # ------------------------------------------------------------------
    # Universal invariant: no relation row anywhere on the seven Phase B
    # kinds may carry an Account-kind endpoint.
    # ------------------------------------------------------------------
    account_kinds = {
        EntityKind.GIT_ACCOUNT,
        EntityKind.GITHUB_USER,
        EntityKind.JIRA_USER,
    }
    for kind in _PHASE_B_EMITTED_RELATION_KINDS:
        for rel in relations.of_kind(kind):
            assert rel.source.kind not in account_kinds, (kind, rel)
            assert rel.target.kind not in account_kinds, (kind, rel)


def test_phase_b_name_to_relation_kind_partition_matches_catalog() -> None:
    """Sanity guard: the seven builder names in ``phase_b_relation_kinds()``
    align 1:1 with the seven emitted ``relation_kind`` strings the test
    above checks. A drift between the two surfaces (e.g. a new Phase B
    builder added to the set with a name → relation_kind mismatch) is
    exactly the leak path UU aftermath §Bug 3 §Scenario B describes.
    """
    # The mapping is encoded in the seven implementation modules — pull
    # it out via the BUILDERS catalog (already loaded by importing
    # ``pipeline``) and assert it matches.
    from src.enrichment.relations import BUILDERS  # noqa: E402

    name_to_relation_kind: dict[str, str] = {}
    for cls in BUILDERS:
        n = getattr(cls, "name", None)
        rk = getattr(cls, "relation_kind", None)
        if n is None or rk is None:
            continue
        name_to_relation_kind[n] = rk

    phase_b_names = phase_b_relation_kinds()
    emitted = {
        name_to_relation_kind[n]
        for n in phase_b_names
        if n in name_to_relation_kind
    }
    assert emitted == _PHASE_B_EMITTED_RELATION_KINDS, (emitted, _PHASE_B_EMITTED_RELATION_KINDS)
