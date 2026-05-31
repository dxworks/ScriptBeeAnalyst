"""Gating + emission tests for :class:`GitLineAttributionMetric` (plan §§3, 7).

The metric is gated by ``EnrichmentConfig.compute_annotated_lines``:

* flag **False** (the default) ⇒ the metric is a no-op — zero ``git.loc`` /
  ``git.repo_size`` classifiers and zero ``git.line_attribution`` traits.
* flag **True** ⇒ each :class:`File` gets a ``git.loc`` classifier (LOC =
  surviving line count) + a ``git.line_attribution`` trait carrying the
  per-line attribution array; each :class:`Commit` gets a ``git.repo_size``
  classifier.

These run the FULL pipeline (like ``test_a21_file_traits.py``) and assert
against ``graph.classifiers`` / ``graph.traits`` so the routing through the
pipeline is exercised end-to-end. The deeper replay correctness is covered
by ``test_annotated_lines.py``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.common.kernel import EntityKind, EntityRef, Graph
from src.enrichment.config import EnrichmentConfig
from src.enrichment.pipeline import run_pipeline

from tests.enrichment.conftest import (
    add_change,
    build_v2_graph,
    make_account,
    make_commit,
    make_file,
)


UTC = timezone.utc
T0 = datetime(2021, 1, 1, tzinfo=UTC)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _classifier_for(graph: Graph, kind: EntityKind, id_: str, dimension: str):
    """The single :class:`Classifier` on ``(kind, id)`` for ``dimension``,
    or ``None`` (``ClassifierRegistry.for_target`` returns a dimension→
    classifier dict)."""
    ref = EntityRef(kind=kind, id=id_)
    return graph.classifiers.for_target(ref).get(dimension)


def _trait_for(graph: Graph, file_id: str, name: str):
    ref = EntityRef(kind=EntityKind.FILE, id=file_id)
    return next(
        (t for t in graph.traits.for_target(ref) if t.name == name), None
    )


def _build_two_file_graph() -> tuple[Graph, list[str], str]:
    """One commit adding 3 lines to ``a.py`` and 5 lines to ``b.py``.

    Returns ``(graph, [file_ids], commit_id)``.
    """
    graph, project = build_v2_graph("gla")
    alice = make_account("Alice", "alice@example.com", project.ref())
    graph.git_accounts.add(alice)

    fa = make_file("src/a.py", project.ref())
    fb = make_file("src/b.py", project.ref())
    graph.files.add(fa)
    graph.files.add(fb)

    c = make_commit("c0", "feat: seed", alice, T0, project.ref())
    graph.commits.add(c)
    add_change(graph, c, fa, added=3)
    add_change(graph, c, fb, added=5)

    return graph, [fa.id, fb.id], c.id


def _run(graph: Graph, config: Optional[EnrichmentConfig] = None) -> None:
    cfg = config if config is not None else EnrichmentConfig()
    graph.__dict__["config"] = cfg
    run_pipeline(graph, cfg)


# ----------------------------------------------------------------------
# Flag OFF — no-op
# ----------------------------------------------------------------------
def test_flag_false_emits_nothing_from_metric():
    graph, file_ids, commit_id = _build_two_file_graph()

    # Default config has compute_annotated_lines=False.
    _run(graph, EnrichmentConfig())

    for fid in file_ids:
        assert _classifier_for(graph, EntityKind.FILE, fid, "git.loc") is None
        assert _trait_for(graph, fid, "git.line_attribution") is None
    assert (
        _classifier_for(graph, EntityKind.COMMIT, commit_id, "git.repo_size")
        is None
    )


def test_flag_false_is_the_default():
    assert EnrichmentConfig().compute_annotated_lines is False


# ----------------------------------------------------------------------
# Flag ON — emissions present + correct
# ----------------------------------------------------------------------
def test_flag_true_emits_git_loc_per_file():
    graph, file_ids, _commit_id = _build_two_file_graph()

    _run(graph, EnrichmentConfig(compute_annotated_lines=True))

    expected = {"src/a.py": "3", "src/b.py": "5"}
    for fid in file_ids:
        loc = _classifier_for(graph, EntityKind.FILE, fid, "git.loc")
        assert loc is not None, f"expected a git.loc for {fid}"
        assert loc.value == expected[fid]


def test_flag_true_emits_attribution_trait_per_file():
    graph, file_ids, commit_id = _build_two_file_graph()

    _run(graph, EnrichmentConfig(compute_annotated_lines=True))

    expected_len = {"src/a.py": 3, "src/b.py": 5}
    for fid in file_ids:
        trait = _trait_for(graph, fid, "git.line_attribution")
        assert trait is not None, f"no attribution trait for {fid}"
        lines = trait.evidence["annotated_lines"]
        assert isinstance(lines, list)
        assert len(lines) == expected_len[fid]
        # Every surviving line is attributed to the only commit that added it.
        assert set(lines) == {commit_id}


def test_flag_true_emits_repo_size_per_commit():
    graph, _file_ids, commit_id = _build_two_file_graph()

    _run(graph, EnrichmentConfig(compute_annotated_lines=True))

    size = _classifier_for(
        graph, EntityKind.COMMIT, commit_id, "git.repo_size"
    )
    assert size is not None
    # 3 lines in a.py + 5 lines in b.py.
    assert size.value == "8"


def test_metric_registered_and_in_phase_a():
    from src.enrichment.metrics import METRICS
    from src.enrichment.pipeline import _PHASE_B_METRIC_NAMES

    names = [m.name for m in METRICS]
    assert "git.line_attribution" in names
    assert "git.line_attribution" not in _PHASE_B_METRIC_NAMES
