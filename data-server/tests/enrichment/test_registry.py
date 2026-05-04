"""Tests for src.enrichment.registry.

Two layers of guarantees:
 1. The catalog includes every metric the pipeline currently registers
    (regression check — adding a new metric class without TRAITS / KIND / NAME
    fails here).
 2. Every catalog entry is well-formed: non-empty docstring, source_file
    pointing at an existing file, family declared, config_fields list present.
"""
from __future__ import annotations

from pathlib import Path

from src.enrichment.config import EnrichmentConfig
from src.enrichment.registry import build_metric_catalog


# data-server root — registry uses this same anchor for source_file paths.
DATA_SERVER_ROOT = Path(__file__).resolve().parents[2]


# ── Coverage assertions: every metric currently in pipeline.py must surface ─

EXPECTED_OVERVIEW_NAMES = {
    "pace", "authorship", "testing", "components", "intent_impact",
    "knowledge", "nature", "feature_traceability", "pr_lifecycle",
    "feature_encapsulation",
    # B4 — Insider code-quality overview
    "code_quality",
}

EXPECTED_RELATION_KINDS = {
    # Phase 2 / Phase 3 base extractors
    "cochange.file-file",
    "ownership.author-file",
    "issue.file",
    "coauthor.author-author",
    "pr.file",
    "pr.reviewer",
    "issue.issue",
    # A2.3 file-file family
    "cochange.file-file.shared-devs",
    "cochange.file-file.shared-task-prefixes",
    "cochange.file-file.time-windowed",
    "similarity.file-file.names",
    # A2.3 author-author family
    "cochange.author-author.shared-task-prefixes",
    "cochange.author-author.time-windowed",
    # A2.3 component aggregations
    "cochange.component-component",
    "cochange.component-component.shared-devs",
    "cochange.component-component.shared-task-prefixes",
    "cochange.component-component.time-windowed",
    # B2 — JaFax / CodeFrame
    "calls.file-file",
    "data-access.file-file",
    "hierarchy.file-file",
    "coupling.file-file",
    # B3 — DuDe duplication
    "duplication.file-file.external",
    "duplication.file-file.sibling",
    "duplication.file-file.internal-summary",
}

EXPECTED_TRAIT_NAMES = {
    # Cohesion (Phase 2 + A2.1)
    "anomaly.cohesion.coordination.Bazaar",
    "anomaly.cohesion.coordination.Cathedral",
    "anomaly.cohesion.coordination.Pulsar",
    "anomaly.cohesion.coordination.Flicker",
    "anomaly.cohesion.size.Supernova",
    "anomaly.cohesion.size.DynamicBlob",
    "anomaly.cohesion.size.FrequentChanger",
    "anomaly.cohesion.activity.Hibernator",
    "anomaly.cohesion.activity.Awakening",
    "anomaly.cohesion.activity.Erosion",
    # Knowledge (Phase 2 + A2.1 + A2.2)
    "anomaly.knowledge.Orphan",
    "anomaly.knowledge.BusFactor1",
    "anomaly.knowledge.SharedKnowledge",
    "anomaly.knowledge.Accumulator",
    "anomaly.knowledge.OwnerChurn",
    "anomaly.knowledge.PolarisedOwnership",
    "anomaly.knowledge.Solitaire",
    "anomaly.knowledge.TeamChurn",
    "anomaly.knowledge.WeakOwnership",
    "anomaly.knowledge.OrphanCausers",
    # Testing
    "anomaly.testing.BugMagnet",
    "anomaly.testing.RefactoringMagnet",
    "anomaly.testing.TestOrphan",
    # Structuring
    "anomaly.structuring.PivotFile",
    "anomaly.structuring.IdenticalFilenames",
    "anomaly.structuring.TasksBottleneck",
    # Review (A2.5)
    "anomaly.review.StalledReview",
    # B2 — JaFax / CodeFrame
    "anomaly.cohesion.ZoneCrossroad",
    "anomaly.cohesion.ConcurrentZoneCrossroad",
}

EXPECTED_CLASSIFIER_SLOTS = {
    ("commit", "message.nature"),
    ("commit", "volume.churn"),
    ("commit", "volume.spread"),
    ("commit", "daytime"),
    ("commit", "weekday"),
    ("commit", "message.smartness"),
    ("file", "status"),
    ("file", "role"),
    ("file", "creationYear"),
    ("author", "activity"),
    ("author", "seniority"),
    ("issue", "status"),
    ("issue", "type"),
    ("issue", "resolution"),
    ("issue", "age_bucket"),
    ("pr", "state"),
    ("pr", "size"),
    ("pr", "review_intensity"),
}


def test_catalog_includes_every_overview():
    cat = build_metric_catalog()
    names = {o["name"] for o in cat["overviews"]}
    missing = EXPECTED_OVERVIEW_NAMES - names
    assert not missing, f"Overviews missing from catalog: {missing}"


def test_catalog_includes_every_relation_kind():
    cat = build_metric_catalog()
    kinds = {r["kind"] for r in cat["relations"]}
    missing = EXPECTED_RELATION_KINDS - kinds
    assert not missing, f"Relation kinds missing from catalog: {missing}"


def test_catalog_includes_every_trait_name():
    cat = build_metric_catalog()
    names = {t["name"] for t in cat["traits"]}
    missing = EXPECTED_TRAIT_NAMES - names
    assert not missing, f"Traits missing from catalog: {missing}"


def test_catalog_includes_every_classifier_slot():
    cat = build_metric_catalog()
    slots = {(c["entity"], c["slot"]) for c in cat["classifiers"]}
    missing = EXPECTED_CLASSIFIER_SLOTS - slots
    assert not missing, f"Classifier slots missing from catalog: {missing}"


# ── Well-formedness: every entry has docstring + valid source_file ──────────

_CONTRACT_HINT = (
    "See src/enrichment/claude.md → 'Adding a new metric — required contract' "
    "for the class-attribute and docstring requirements."
)


def _assert_entry_is_well_formed(entry: dict, label: str, doc_required: bool = True):
    assert entry.get("source_file"), f"{label}: empty source_file. {_CONTRACT_HINT}"
    src = DATA_SERVER_ROOT / entry["source_file"]
    assert src.exists(), f"{label}: source_file does not exist: {src}. {_CONTRACT_HINT}"
    if doc_required:
        assert entry.get("docstring"), (
            f"{label}: empty docstring on the class AND the module — add at least "
            f"a one-paragraph module docstring naming the metric and its rule. "
            f"{_CONTRACT_HINT}"
        )


def test_every_overview_entry_well_formed():
    cat = build_metric_catalog()
    assert cat["overviews"], "no overviews discovered"
    for o in cat["overviews"]:
        label = f"overview {o['name']}"
        _assert_entry_is_well_formed(o, label)
        assert o.get("entity_kind"), (
            f"{label}: missing ENTITY_KIND class attribute on the builder. "
            f"{_CONTRACT_HINT}"
        )
        assert isinstance(o.get("columns"), list), f"{label}: columns must be a list"


def test_every_relation_entry_well_formed():
    cat = build_metric_catalog()
    assert cat["relations"], "no relations discovered"
    for r in cat["relations"]:
        label = f"relation {r['kind']}"
        _assert_entry_is_well_formed(r, label)
        assert r.get("source_kind"), (
            f"{label}: source_kind not parsed from KIND — KIND should follow "
            f"'<family>.<src>-<tgt>[.<mod>]' or '<src>.<tgt>'. {_CONTRACT_HINT}"
        )
        assert r.get("target_kind"), (
            f"{label}: target_kind not parsed from KIND. {_CONTRACT_HINT}"
        )


def test_every_trait_entry_well_formed():
    cat = build_metric_catalog()
    assert cat["traits"], "no traits discovered"
    for t in cat["traits"]:
        label = f"trait {t['name']} ({t['entity']})"
        _assert_entry_is_well_formed(t, label)
        assert t.get("family"), (
            f"{label}: missing 'family' key in the TRAITS class attribute. "
            f"Each TRAITS entry must be {{'name', 'entity', 'family'}}. {_CONTRACT_HINT}"
        )
        assert isinstance(t.get("config_fields"), list), \
            f"{label}: config_fields must be a list"


def test_every_classifier_entry_well_formed():
    cat = build_metric_catalog()
    assert cat["classifiers"], "no classifiers discovered"
    for c in cat["classifiers"]:
        label = f"classifier {c['entity']}.{c['slot']}"
        # Classifier docstrings live on the tagger class (one tagger emits multiple
        # slots) — they may not mention each slot individually, but the class
        # docstring should be present.
        _assert_entry_is_well_formed(c, label, doc_required=True)
        assert isinstance(c.get("values"), list), f"{label}: values must be a list"


# ── Targeted regression checks for the high-risk wiring ─────────────────────

def test_orphan_does_not_swallow_orphancauser_config_fields():
    """Substring matching used to misattribute orphancauser_* fields to Orphan."""
    cat = build_metric_catalog()
    orphan = next(t for t in cat["traits"] if t["name"] == "anomaly.knowledge.Orphan")
    for f in orphan["config_fields"]:
        assert not f.startswith("orphancauser_"), \
            f"Orphan should not match {f!r} (belongs to OrphanCausers)"
        assert not f.startswith("test_orphan_"), \
            f"Orphan should not match {f!r} (belongs to TestOrphan)"


def test_trait_config_fields_exist_on_enrichment_config():
    """Config field names returned by registry must actually exist on EnrichmentConfig."""
    cat = build_metric_catalog()
    real_fields = set(EnrichmentConfig.__dataclass_fields__)
    for t in cat["traits"]:
        for f in t.get("config_fields", []):
            assert f in real_fields, \
                f"trait {t['name']} references non-existent cfg field {f!r}"


def test_relations_with_dot_only_kind_parse_endpoints():
    """`issue.file`, `pr.reviewer` etc. don't have a hyphen — regression check."""
    cat = build_metric_catalog()
    by_kind = {r["kind"]: r for r in cat["relations"]}
    expected = {
        "issue.file":   ("issue", "file"),
        "issue.issue":  ("issue", "issue"),
        "pr.file":      ("pr", "file"),
        "pr.reviewer":  ("pr", "author"),  # 'reviewer' is aliased to 'author'
    }
    for kind, (src, tgt) in expected.items():
        assert by_kind[kind]["source_kind"] == src, f"{kind}: source"
        assert by_kind[kind]["target_kind"] == tgt, f"{kind}: target"


def test_counts_match_lists():
    cat = build_metric_catalog()
    assert cat["counts"]["classifiers"] == len(cat["classifiers"])
    assert cat["counts"]["traits"] == len(cat["traits"])
    assert cat["counts"]["relations"] == len(cat["relations"])
    assert cat["counts"]["overviews"] == len(cat["overviews"])


def test_helpers_block_lists_sandbox_helpers():
    cat = build_metric_catalog()
    helpers = {h["name"] for h in cat["helpers"]}
    assert {"find_files_with_trait", "cochange_neighbors", "overview_as_dict"} <= helpers
