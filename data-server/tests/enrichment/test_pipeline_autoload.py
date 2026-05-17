"""Auto-load test (review round-2 blocking #1).

Verifies that importing :mod:`src.enrichment.pipeline` ALONE is
enough to populate :data:`BUILDERS` and :data:`METRICS` — i.e. a
Chunk-8 caller that does ``from src.enrichment.pipeline import
run_pipeline`` and then runs it against the default catalogs gets a
non-empty pipeline run, not silently empty registries.

Run from a subprocess (fresh interpreter) so we're testing the cold
import path, not the side-effects of other test modules in this same
process.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap


def test_importing_pipeline_alone_populates_catalogs() -> None:
    """Cold-import ``pipeline`` and assert both catalogs are non-empty.

    Runs in a fresh subprocess so no other test module has already
    populated the registries. The script imports ``run_pipeline``
    only (NO explicit ``import …implementations``) and prints the
    catalog sizes — the parent asserts the printed numbers.
    """
    script = textwrap.dedent(
        """
        import sys
        # Ensure repo root is on path the same way pytest configures it.
        sys.path.insert(0, ".")
        from src.enrichment.pipeline import run_pipeline  # noqa: F401
        from src.enrichment.relations import BUILDERS
        from src.enrichment.metrics import METRICS
        print(f"builders={len(BUILDERS)}")
        print(f"metrics={len(METRICS)}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=".",  # pytest's cwd is the data-server root.
    )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    lines = {
        line.split("=", 1)[0]: int(line.split("=", 1)[1])
        for line in result.stdout.strip().splitlines()
    }
    # Chunks 7 ships 25 builders (after PR↔Issue round-2 addition) and
    # 14 metrics. Future chunks may add more, so we assert a floor.
    assert lines["builders"] >= 25, lines
    assert lines["metrics"] >= 14, lines


def test_run_pipeline_against_default_catalogs_emits_via_autoload() -> None:
    """The whole point of the auto-load: ``run_pipeline`` with no overrides
    must actually execute builders/metrics, not be a no-op.

    We give it an empty stub host so substantive builders short-circuit on
    missing registries (zero relations emitted) while deferred stubs
    raise ``NotImplementedError`` which the pipeline records in
    ``errors``. The expected signal is: ``len(result.errors) +
    len(result.metrics_run) + len(result.builders_run) >= 39`` — i.e.
    every shipped impl was at least attempted.
    """
    # Run in a subprocess so the test is a true cold start.
    script = textwrap.dedent(
        """
        import sys
        sys.path.insert(0, ".")
        from dataclasses import dataclass
        from src.enrichment.pipeline import run_pipeline
        from src.enrichment.relations import RelationRegistry
        from src.enrichment.tags import TraitRegistry, ClassifierRegistry

        @dataclass
        class _Stub:
            relations: RelationRegistry
            traits: TraitRegistry
            classifiers: ClassifierRegistry

        host = _Stub(
            relations=RelationRegistry(),
            traits=TraitRegistry(),
            classifiers=ClassifierRegistry(),
        )
        result = run_pipeline(host, config=None)
        attempted = (
            len(result.builders_run) + len(result.metrics_run) + len(result.errors)
        )
        print(f"attempted={attempted}")
        print(f"errors={len(result.errors)}")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=".",
    )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    out = {
        line.split("=", 1)[0]: int(line.split("=", 1)[1])
        for line in result.stdout.strip().splitlines()
    }
    # 25 builders + 15 metrics = 40 attempted steps (floor).
    assert out["attempted"] >= 39, out
    # End of Phase 2 (post-Chunk-16): every metric is substantively
    # ported and every builder either runs or short-circuits on missing
    # registries. No :class:`NotImplementedError` is expected. The
    # ``attempted`` floor above already catches silent catalog
    # truncation; here we pin the metric port to "complete".
    assert out["errors"] == 0, out
