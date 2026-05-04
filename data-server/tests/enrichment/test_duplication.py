"""B3 DuDe — duplication models, parser, transformer, relation extractors.

Verifies §10 of communication/B3_dude/index_step_general.md.

Synthetic fixture: a tiny external CSV (headerless, 3-col) with two pairs of
files — one cross-directory, one same-directory — plus a tiny internal JSON
listing per-file duplicated lines. The fixtures are written to tmp_path so
the test exercises the real loaders end-to-end.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.common.duplication_models import (
    Duplication,
    DuplicationKind,
    DuplicationPair,
)
from src.dude_miner.linker.transformers import DudeDuplicationTransformer
from src.dude_miner.parser import parse_dude
from src.dude_miner.reader_dto.loader import (
    DudeExternalCsvLoader,
    DudeInternalJsonLoader,
)
from src.dude_miner.reader_dto.models import (
    ExternalDuplicationRowDTO,
    InternalDuplicationEntryDTO,
)
from src.enrichment.config import EnrichmentConfig
from src.enrichment.relations.duplication_external import (
    ExternalDuplicationExtractor,
)
from src.enrichment.relations.duplication_internal_summary import (
    InternalDuplicationSummaryExtractor,
)
from src.enrichment.relations.duplication_sibling import (
    SiblingDuplicationExtractor,
)
from src.enrichment.tagger.base import TaggingContext


# ── Fixtures ────────────────────────────────────────────────────────────────


def _write_external_csv(path: Path) -> Path:
    """3-col headerless CSV: file_a, file_b, block_length.

    Pair A: cross-directory (foo vs bar) — TWO rows so aggregation kicks in.
    Pair B: same-directory siblings (both under shims/).
    """
    path.write_text(
        "src/foo/Foo.java,src/bar/Bar.java,40\n"
        "src/foo/Foo.java,src/bar/Bar.java,30\n"
        "src/shims/Shim15.java,src/shims/Shim16.java,80\n",
        encoding="utf-8",
    )
    return path


def _write_internal_json(path: Path) -> Path:
    path.write_text(json.dumps([
        {"file": "src/foo/Foo.java", "name": "Internal File Duplication",
         "category": "Duplication", "value": 49},
        {"file": "src/bar/Bar.java", "name": "Internal File Duplication",
         "category": "Duplication", "value": 30},
    ]), encoding="utf-8")
    return path


def _empty_ctx_with_duplication(dup: Duplication) -> TaggingContext:
    return TaggingContext(
        graph_data={"git": None, "jira": None, "github": None,
                    "code_structure": None, "duplication": dup,
                    "metrics": {"lizard": []}},
        config=EnrichmentConfig(),
        anchor_date=None,
        recent_cutoff=None,
    )


# ── Loader / parser tests ──────────────────────────────────────────────────


def test_external_loader_reads_headerless_three_columns(tmp_path: Path):
    csv = _write_external_csv(tmp_path / "z-external_duplication.csv")
    rows = DudeExternalCsvLoader(str(csv)).load()
    assert len(rows) == 3
    assert rows[0].file_a == "src/foo/Foo.java"
    assert rows[0].file_b == "src/bar/Bar.java"
    assert rows[0].block_length == 40


def test_external_loader_skips_malformed_rows(tmp_path: Path):
    csv = tmp_path / "broken.csv"
    csv.write_text(
        "src/a.java,src/b.java,not_an_int\n"        # bad block_length
        "src/a.java,src/b.java\n"                    # too few columns
        "src/a.java,src/b.java,10,extra\n"           # too many columns
        "src/a.java,src/b.java,10\n"                 # one good row
        ",src/b.java,5\n"                            # empty file_a
        "\n",                                          # blank line
        encoding="utf-8",
    )
    rows = DudeExternalCsvLoader(str(csv)).load()
    assert len(rows) == 1
    assert rows[0].block_length == 10


def test_internal_loader_drops_constant_keys(tmp_path: Path):
    j = _write_internal_json(tmp_path / "z-internal_duplication.json")
    entries = DudeInternalJsonLoader(str(j)).load()
    assert len(entries) == 2
    # `name` and `category` are dropped — DTO only carries file + value.
    assert all(isinstance(e, InternalDuplicationEntryDTO) for e in entries)
    assert entries[0].file == "src/foo/Foo.java"
    assert entries[0].value == 49


def test_parse_dude_end_to_end(tmp_path: Path):
    csv = _write_external_csv(tmp_path / "z-external_duplication.csv")
    j = _write_internal_json(tmp_path / "z-internal_duplication.json")
    dup = parse_dude(str(csv), str(j))
    # Two distinct unordered pairs after aggregation.
    assert len(dup.external_pairs) == 2
    assert len(dup.internal_by_file) == 2
    # Pair (foo, bar) aggregated: 40 + 30 = 70 across 2 blocks.
    pair_foo_bar = next(p for p in dup.external_pairs
                        if p.file_a_path == "src/bar/Bar.java"
                        or p.file_b_path == "src/bar/Bar.java")
    assert pair_foo_bar.total_block_length == 70
    assert pair_foo_bar.block_count == 2


def test_parse_dude_path_prefix_prepends(tmp_path: Path):
    csv = tmp_path / "x.csv"
    csv.write_text("src/a.java,src/b.java,30\n", encoding="utf-8")
    dup = parse_dude(str(csv), None, path_prefix="zeppelin")
    pair = dup.external_pairs[0]
    assert pair.file_a_path.startswith("zeppelin/")
    assert pair.file_b_path.startswith("zeppelin/")


def test_parse_dude_path_prefix_idempotent_when_already_prefixed(tmp_path: Path):
    csv = tmp_path / "x.csv"
    csv.write_text("zeppelin/src/a.java,zeppelin/src/b.java,30\n", encoding="utf-8")
    dup = parse_dude(str(csv), None, path_prefix="zeppelin")
    pair = dup.external_pairs[0]
    # Prefix not duplicated when input already carries it.
    assert not pair.file_a_path.startswith("zeppelin/zeppelin/")
    assert pair.file_a_path.startswith("zeppelin/src/")


# ── Transformer canonicalisation tests ─────────────────────────────────────


def test_transformer_canonicalises_pair_orientation():
    rows = [
        ExternalDuplicationRowDTO(file_a="src/B.java", file_b="src/A.java", block_length=40),
        ExternalDuplicationRowDTO(file_a="src/A.java", file_b="src/B.java", block_length=30),
    ]
    dup = DudeDuplicationTransformer(rows, []).transform()
    # Both rows collapse onto one canonical pair.
    assert len(dup.external_pairs) == 1
    pair = dup.external_pairs[0]
    assert pair.file_a_path == "src/A.java"
    assert pair.file_b_path == "src/B.java"
    assert pair.total_block_length == 70
    assert pair.block_count == 2


def test_transformer_skips_self_pairs():
    rows = [
        ExternalDuplicationRowDTO(file_a="src/A.java", file_b="src/A.java", block_length=10),
        ExternalDuplicationRowDTO(file_a="src/A.java", file_b="src/B.java", block_length=20),
    ]
    dup = DudeDuplicationTransformer(rows, []).transform()
    assert len(dup.external_pairs) == 1
    assert dup.external_pairs[0].total_block_length == 20


# ── Relation extractor tests ───────────────────────────────────────────────


def test_external_extractor_excludes_siblings(tmp_path: Path):
    csv = _write_external_csv(tmp_path / "ext.csv")
    j = _write_internal_json(tmp_path / "int.json")
    dup = parse_dude(str(csv), str(j))
    rels = ExternalDuplicationExtractor().extract(_empty_ctx_with_duplication(dup))
    assert len(rels) == 1
    rf = rels[0]
    assert rf.kind == "duplication.file-file.external"
    # Only the cross-directory (foo, bar) pair survives.
    assert len(rf.relations) == 1
    rel = rf.relations[0]
    assert rel.source_id == "src/bar/Bar.java"
    assert rel.target_id == "src/foo/Foo.java"
    assert rel.strength == 70.0
    assert rel.extras["block_count"] == 2


def test_sibling_extractor_keeps_only_same_dir_pairs(tmp_path: Path):
    csv = _write_external_csv(tmp_path / "ext.csv")
    j = _write_internal_json(tmp_path / "int.json")
    dup = parse_dude(str(csv), str(j))
    rels = SiblingDuplicationExtractor().extract(_empty_ctx_with_duplication(dup))
    assert len(rels) == 1
    rf = rels[0]
    assert rf.kind == "duplication.file-file.sibling"
    assert len(rf.relations) == 1
    rel = rf.relations[0]
    # Both shims live under src/shims/.
    assert "shims/" in rel.source_id and "shims/" in rel.target_id
    assert rel.strength == 80.0


def test_internal_summary_extractor_emits_self_loops(tmp_path: Path):
    csv = _write_external_csv(tmp_path / "ext.csv")
    j = _write_internal_json(tmp_path / "int.json")
    dup = parse_dude(str(csv), str(j))
    rels = InternalDuplicationSummaryExtractor().extract(_empty_ctx_with_duplication(dup))
    assert len(rels) == 1
    rf = rels[0]
    assert rf.kind == "duplication.file-file.internal-summary"
    assert len(rf.relations) == 2
    by_file = {r.source_id: r for r in rf.relations}
    assert by_file["src/foo/Foo.java"].strength == 49.0
    # Self-loop shape so the AI agent can use the same get_relation_edges call.
    assert by_file["src/foo/Foo.java"].target_id == "src/foo/Foo.java"
    assert by_file["src/bar/Bar.java"].strength == 30.0


def test_extractors_no_op_when_no_duplication():
    ctx = TaggingContext(
        graph_data={"git": None, "jira": None, "github": None,
                    "code_structure": None, "duplication": None,
                    "metrics": {"lizard": []}},
        config=EnrichmentConfig(),
        anchor_date=None,
        recent_cutoff=None,
    )
    assert ExternalDuplicationExtractor().extract(ctx) == []
    assert SiblingDuplicationExtractor().extract(ctx) == []
    assert InternalDuplicationSummaryExtractor().extract(ctx) == []


def test_external_pair_kind_default_is_external():
    pair = DuplicationPair(
        file_a_path="a", file_b_path="b",
        total_block_length=10, block_count=1,
    )
    assert pair.kind == DuplicationKind.EXTERNAL
