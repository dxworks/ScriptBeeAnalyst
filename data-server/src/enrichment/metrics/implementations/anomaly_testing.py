"""Testing anomaly metric — v2 port.

Port of legacy ``src/enrichment/tagger/anomaly_testing.py`` (~161 LOC).
Emits three :class:`Trait` rows in the :attr:`TraitFamily.TESTING` family:

* ``anomaly.testing.BugMagnet`` — file whose share of *bugfix*-nature
  commits exceeds ``cfg.bugmagnet_ratio_min`` AND whose absolute bugfix
  count is at least ``cfg.bugmagnet_min_bugfix_commits``.
* ``anomaly.testing.RefactoringMagnet`` — file with at least
  ``cfg.refactoring_magnet_min_commits`` refactor-nature commits.
* ``anomaly.testing.TestOrphan`` — *proxy* (per plan §B-#5) — a
  ``role="production"`` file whose commits co-touch a ``role="test"``
  file at most ``cfg.test_orphan_max_cochange_test_count`` times, after
  the file has accumulated at least ``cfg.test_orphan_min_commits``
  commits. Suppressed entirely when the project has zero test-role
  files (TestOrphan would otherwise be no-signal).

Reads:

* ``graph.files``                      — the file population.
* ``graph.changes.by_file``            — commits per file.
* ``graph.commits.get``                — per-commit lookup.
* ``graph.classifiers.with_value(...)`` — pre-computed commit nature
  (``"message.nature"``) and file role (``"role"``). These classifiers
  are emitted by ``CommitClassifierMetric`` / ``FileClassifierMetric``
  earlier in the same stage; the pipeline registers metrics in
  ``__init__.py`` import order so the role/nature classifiers are
  available when this metric runs.

The dependency on the pre-computed role/nature classifiers means this
metric is robust against changes to the regex catalog — same source of
truth as ``FileClassifierMetric`` / ``CommitClassifierMetric``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Iterable, Optional

from src.common.kernel import EntityKind, EntityRef
from src.enrichment.metrics import METRICS, Metric, MetricInputs, MetricOutputs
from src.enrichment.tags import Trait, TraitFamily

if TYPE_CHECKING:
    from src.common.kernel import Graph


_TRAIT_BUGMAGNET = "anomaly.testing.BugMagnet"
_TRAIT_REFACTORING_MAGNET = "anomaly.testing.RefactoringMagnet"
_TRAIT_TEST_ORPHAN = "anomaly.testing.TestOrphan"


@METRICS.register
class AnomalyTestingMetric(Metric):
    name: ClassVar[str] = "anomaly.testing"
    inputs: ClassVar[MetricInputs] = MetricInputs(source_kind=EntityKind.FILE)
    outputs: ClassVar[MetricOutputs] = MetricOutputs(
        emits_traits=[
            _TRAIT_BUGMAGNET,
            _TRAIT_REFACTORING_MAGNET,
            _TRAIT_TEST_ORPHAN,
        ]
    )
    config_fields: ClassVar[list[str]] = [
        "bugmagnet_min_bugfix_commits",
        "bugmagnet_ratio_min",
        "refactoring_magnet_min_commits",
        "test_orphan_max_cochange_test_count",
        "test_orphan_min_commits",
    ]

    def compute(self, graph: "Graph", config: Any) -> Iterable[Trait]:
        files = _safe_iter(getattr(graph, "files", None))
        if not files:
            return

        classifiers = getattr(graph, "classifiers", None)
        commits_reg = getattr(graph, "commits", None)
        if commits_reg is None:
            return

        # Pre-resolve commit-nature & file-role lookups via the
        # classifier registry's value-keyed index.
        bugfix_commit_ids = _commit_ids_with_nature(classifiers, "bugfix")
        refactor_commit_ids = _commit_ids_with_nature(classifiers, "refactor")
        test_file_refs = _file_refs_with_role(classifiers, "test")
        production_file_refs = _file_refs_with_role(classifiers, "production")

        bugmagnet_min = int(_config_field(
            config, "bugmagnet_min_bugfix_commits", 5
        ))
        bugmagnet_ratio_min = float(_config_field(
            config, "bugmagnet_ratio_min", 0.40
        ))
        refactor_min = int(_config_field(
            config, "refactoring_magnet_min_commits", 10
        ))
        test_orphan_max = int(_config_field(
            config, "test_orphan_max_cochange_test_count", 1
        ))
        test_orphan_min_commits = int(_config_field(
            config, "test_orphan_min_commits", 3
        ))

        emit_test_orphan = bool(test_file_refs)

        changes_by_file = _changes_by_file_index(graph)
        changes_by_commit = _changes_by_commit_index(graph)

        for file_ in files:
            file_ref = file_.ref()
            file_changes = list(changes_by_file(file_ref))
            commit_ids: list[str] = []
            seen_commit_ids: set[str] = set()
            for change in file_changes:
                cref = getattr(change, "commit_ref", None)
                if cref is None:
                    continue
                cid = cref.id
                if cid in seen_commit_ids:
                    continue
                seen_commit_ids.add(cid)
                commit_ids.append(cid)

            total = len(commit_ids)
            bugfix = sum(1 for cid in commit_ids if cid in bugfix_commit_ids)
            refactor = sum(1 for cid in commit_ids if cid in refactor_commit_ids)

            # ---- BugMagnet --------------------------------------------------
            if bugfix >= bugmagnet_min and total > 0:
                ratio = bugfix / total
                if ratio >= bugmagnet_ratio_min:
                    yield Trait(
                        id=f"trait:{_TRAIT_BUGMAGNET}:{file_ref.kind.value}/{file_ref.id}",
                        target=file_ref,
                        family=TraitFamily.TESTING,
                        name=_TRAIT_BUGMAGNET,
                        severity=round(ratio, 3),
                        evidence={
                            "bugfix_commits": bugfix,
                            "total_commits": total,
                            "bugfix_ratio": round(ratio, 3),
                            "ratio_threshold": bugmagnet_ratio_min,
                            "min_count": bugmagnet_min,
                        },
                    )

            # ---- RefactoringMagnet -----------------------------------------
            if refactor >= refactor_min:
                refactor_ratio = refactor / total if total > 0 else 0.0
                yield Trait(
                    id=f"trait:{_TRAIT_REFACTORING_MAGNET}:{file_ref.kind.value}/{file_ref.id}",
                    target=file_ref,
                    family=TraitFamily.TESTING,
                    name=_TRAIT_REFACTORING_MAGNET,
                    severity=float(refactor),
                    evidence={
                        "refactor_commits": refactor,
                        "total_commits": total,
                        "refactor_ratio": round(refactor_ratio, 3),
                        "threshold": refactor_min,
                    },
                )

            # ---- TestOrphan (proxy) ----------------------------------------
            if (
                emit_test_orphan
                and file_ref in production_file_refs
                and total >= test_orphan_min_commits
            ):
                cochange_test_count = _count_commits_touching_tests(
                    commit_ids, changes_by_commit, file_ref, test_file_refs,
                )
                if cochange_test_count <= test_orphan_max:
                    yield Trait(
                        id=f"trait:{_TRAIT_TEST_ORPHAN}:{file_ref.kind.value}/{file_ref.id}",
                        target=file_ref,
                        family=TraitFamily.TESTING,
                        name=_TRAIT_TEST_ORPHAN,
                        severity=1.0,
                        is_proxy=True,
                        evidence={
                            "proxy": True,
                            "note": "no static analysis, uses commit-cochange to test files only",
                            "cochange_test_count": int(cochange_test_count),
                            "threshold": int(test_orphan_max),
                            "commits": int(total),
                        },
                    )


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_iter(reg: Any) -> list[Any]:
    if reg is None:
        return []
    try:
        return list(reg)
    except TypeError:
        return []


def _config_field(config: Any, field: str, default: Any) -> Any:
    if config is None:
        return default
    return getattr(config, field, default)


def _commit_ids_with_nature(classifiers: Any, value: str) -> set[str]:
    """Set of commit ids carrying classifier (dimension='message.nature', value=value)."""
    if classifiers is None or not hasattr(classifiers, "with_value"):
        return set()
    rows = classifiers.with_value("message.nature", value)
    return {row.target.id for row in rows if row.target.kind == EntityKind.COMMIT}


def _file_refs_with_role(classifiers: Any, value: str) -> set[EntityRef]:
    """Set of file refs carrying classifier (dimension='role', value=value)."""
    if classifiers is None or not hasattr(classifiers, "with_value"):
        return set()
    rows = classifiers.with_value("role", value)
    return {row.target for row in rows if row.target.kind == EntityKind.FILE}


def _changes_by_file_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _ref: ()
    by_file = getattr(changes, "by_file", None)
    if by_file is not None:
        return lambda file_ref: by_file[file_ref]
    return lambda file_ref: tuple(
        ch for ch in changes if getattr(ch, "file_ref", None) == file_ref
    )


def _changes_by_commit_index(graph: Any):
    changes = getattr(graph, "changes", None)
    if changes is None:
        return lambda _ref: ()
    by_commit = getattr(changes, "by_commit", None)
    if by_commit is not None:
        return lambda commit_ref: by_commit[commit_ref]
    return lambda commit_ref: tuple(
        ch for ch in changes if getattr(ch, "commit_ref", None) == commit_ref
    )


def _count_commits_touching_tests(
    commit_ids: list[str],
    changes_by_commit,
    this_file_ref: EntityRef,
    test_file_refs: set[EntityRef],
) -> int:
    """Count commits whose change-set also touches any test-role file."""
    count = 0
    for cid in commit_ids:
        commit_ref = EntityRef(kind=EntityKind.COMMIT, id=cid)
        for ch in changes_by_commit(commit_ref):
            other = getattr(ch, "file_ref", None)
            if other is None or other == this_file_ref:
                continue
            if other in test_file_refs:
                count += 1
                break
    return count


__all__ = ["AnomalyTestingMetric"]
