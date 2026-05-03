"""A2.2 author-level git-only anomaly traits — synthetic-graph smoke tests.

OrphanCausers fires on retired (idle) authors whose former files now match
`anomaly.knowledge.Orphan`. We verify the positive case plus two negatives:
an active author with many orphan files (must NOT fire) and an idle author
with too few orphan files (must NOT fire).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.common.models import GitProject
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import compute_enrichments
from tests.enrichment.fixtures import (
    make_account,
    make_change,
    make_commit,
    make_file,
)


UTC = timezone.utc


def _trait(tags, name):
    return next((t for t in tags.traits if t.name == name), None)


def _wrap(proj):
    return {"git": proj, "jira": None, "github": None}


def _author_key(account):
    return f"author:{account.id}"


def test_orphan_causers_emitted_for_idle_author_with_many_orphans():
    now = datetime.now(UTC)
    proj = GitProject(name="oc")
    # Retired author Eve — sole touch on each of three files, all old.
    eve = make_account("Eve", "eve@example.com")
    # Active author so an enrichment anchor exists in the recent window
    # (without it, Eve's old commits are themselves "recent" relative to
    # the anchor and Orphan never fires).
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([eve, alice])

    eve_files = [make_file(proj) for _ in range(3)]
    other = make_file(proj)
    proj.file_registry.add_all([*eve_files, other])

    # Eve: 12 lifetime commits, all >1y old. Each of her three target files
    # is touched by her exclusively (single-author, last-touch outside
    # recent window → Orphan).
    cid = 0
    for i, f in enumerate(eve_files):
        for _ in range(4):  # 3 files × 4 commits = 12 commits → ≥ 10 threshold
            cid += 1
            c = make_commit(
                proj, f"eve_{cid}", "feat", eve, now - timedelta(days=400 + cid),
            )
            make_change(c, f, f"src/legacy_{i}.py", added=20)
            proj.git_commit_registry.add(c)

    # Alice anchors the recent window with a touch on a separate file.
    c_recent = make_commit(proj, "al_recent", "chore", alice, now - timedelta(days=5))
    make_change(c_recent, other, "src/recent.py", added=2)
    proj.git_commit_registry.add(c_recent)

    e = compute_enrichments(_wrap(proj), EnrichmentConfig())

    # Sanity: each legacy file got Orphan.
    for i in range(3):
        f_tags = e.tags_by_entity.get(f"file:src/legacy_{i}.py")
        assert f_tags is not None
        assert _trait(f_tags, "anomaly.knowledge.Orphan") is not None

    # Eve flagged as OrphanCauser.
    eve_tags = e.tags_by_entity.get(_author_key(eve))
    assert eve_tags is not None
    t = _trait(eve_tags, "anomaly.knowledge.OrphanCausers")
    assert t is not None
    assert t.evidence["orphan_files_count"] == 3
    assert t.evidence["lifetime_commits"] == 12
    assert sorted(t.evidence["orphan_file_ids_sample"]) == [
        "src/legacy_0.py", "src/legacy_1.py", "src/legacy_2.py",
    ]

    # Alice (active) is never flagged.
    alice_tags = e.tags_by_entity.get(_author_key(alice))
    if alice_tags is not None:
        assert _trait(alice_tags, "anomaly.knowledge.OrphanCausers") is None


def test_orphan_causers_not_emitted_for_active_author():
    """Active author who happens to be sole owner of many orphan-shaped files
    must NOT fire — OrphanCausers only targets retired departures."""
    now = datetime.now(UTC)
    proj = GitProject(name="oc-active")
    bob = make_account("Bob", "bob@example.com")
    proj.account_registry.add_all([bob])

    bob_files = [make_file(proj) for _ in range(5)]
    proj.file_registry.add_all(bob_files)

    # Bob has 12 commits — but the latest is recent, so activity=active.
    # We still need each file's last touch outside the recent window to
    # form Orphan candidates. Give files old commits, then a fresh commit
    # on a *different* (sentinel) file to keep Bob active.
    sentinel = make_file(proj)
    proj.file_registry.add_all([sentinel])

    cid = 0
    for i, f in enumerate(bob_files):
        for _ in range(2):
            cid += 1
            c = make_commit(proj, f"b_{cid}", "feat", bob, now - timedelta(days=400 + cid))
            make_change(c, f, f"src/legacy_{i}.py", added=20)
            proj.git_commit_registry.add(c)

    # Two more commits on the sentinel file, with the most recent inside
    # the recent window → Bob is "active".
    c_old = make_commit(proj, "b_sent_old", "chore", bob, now - timedelta(days=200))
    make_change(c_old, sentinel, "src/sentinel.py", added=2)
    proj.git_commit_registry.add(c_old)
    c_new = make_commit(proj, "b_sent_new", "chore", bob, now - timedelta(days=5))
    make_change(c_new, sentinel, "src/sentinel.py", added=2)
    proj.git_commit_registry.add(c_new)

    e = compute_enrichments(_wrap(proj), EnrichmentConfig())

    # Bob is active.
    bob_tags = e.tags_by_entity.get(_author_key(bob))
    assert bob_tags is not None
    assert bob_tags.classifiers.get("activity") == "active"

    # Even with ≥3 orphan-shaped files in his trail, the trait must NOT fire.
    assert _trait(bob_tags, "anomaly.knowledge.OrphanCausers") is None


def test_orphan_causers_not_emitted_when_below_orphan_threshold():
    """Idle author whose former trail intersects only ONE Orphan file must NOT fire."""
    now = datetime.now(UTC)
    proj = GitProject(name="oc-low")
    eve = make_account("Eve", "eve@example.com")
    alice = make_account("Alice", "alice@example.com")
    proj.account_registry.add_all([eve, alice])

    only_orphan = make_file(proj)
    other = make_file(proj)
    proj.file_registry.add_all([only_orphan, other])

    # Eve: 12 commits but only ONE of her files matches Orphan.
    # Distribute 11 commits on `other` (which she shares with Alice → not
    # single-author → not Orphan) and 1 commit on `only_orphan`.
    cid = 0
    for _ in range(11):
        cid += 1
        c = make_commit(proj, f"e_{cid}", "feat", eve, now - timedelta(days=400 + cid))
        make_change(c, other, "src/shared.py", added=10)
        proj.git_commit_registry.add(c)
    cid += 1
    c = make_commit(proj, f"e_{cid}", "feat", eve, now - timedelta(days=500))
    make_change(c, only_orphan, "src/legacy_one.py", added=20)
    proj.git_commit_registry.add(c)

    # Alice keeps `other` from being orphan (multi-author) and anchors the
    # recent window.
    c_a = make_commit(proj, "al_recent", "chore", alice, now - timedelta(days=5))
    make_change(c_a, other, "src/shared.py", added=2)
    proj.git_commit_registry.add(c_a)

    e = compute_enrichments(_wrap(proj), EnrichmentConfig())

    # Sanity: legacy_one.py is Orphan; shared.py is NOT.
    assert _trait(
        e.tags_by_entity["file:src/legacy_one.py"], "anomaly.knowledge.Orphan"
    ) is not None

    # Eve idle but only ONE orphan file → below threshold → no trait.
    eve_tags = e.tags_by_entity.get(_author_key(eve))
    assert eve_tags is not None
    assert eve_tags.classifiers.get("activity") == "idle"
    assert _trait(eve_tags, "anomaly.knowledge.OrphanCausers") is None
