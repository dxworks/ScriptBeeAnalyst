"""OFFLINE, opt-in validator for the git per-line attribution metric.

Faithful port of legacy ``main:data-server/src/run_blame.py`` onto the v2
enrichment shape (plan §6). It reconstructs each file's final line ownership
from the ``git.line_attribution`` Trait emitted by
:class:`GitLineAttributionMetric` (built with
``compute_annotated_lines=True``) and diffs it, line by line, against real
``git blame --show-email`` output taken from a checked-out clone of the same
repository.

Why opt-in / offline
--------------------
The check needs TWO real artefacts that the normal CI suite does not carry:

* a **checked-out git clone** of a repo (so ``git blame`` can run), and
* the **``.iglog``** (inspector-git log) extracted from THAT SAME clone,
  which is what the v2 build path consumes.

Neither lives in the repo, so the test SKIPS unless both are supplied via
environment variables. The normal ``pytest tests/enrichment/`` run therefore
stays green.

How to run
----------
Point it at a real clone + the matching iglog::

    BLAME_VALIDATOR_REPO=/path/to/checked-out/clone \
    BLAME_VALIDATOR_IGLOG=/path/to/repo.iglog \
    PYTHONPATH=. python -m pytest \
        tests/enrichment/test_blame_validator_offline.py -q -s

Optional knobs:

* ``BLAME_VALIDATOR_REPO_NAME`` — the repo_name the iglog was built under
  (defaults to the iglog filename stem). It only affects entity ids, not the
  bare ``File.path`` lookup, so the default is almost always fine.
* ``BLAME_VALIDATOR_MIN_MATCH`` — minimum overall line-match ratio the assert
  requires (default ``0.99`` == the original's "within tolerance" target;
  the legacy script only logged the percentage, this test enforces it).

Tolerance
---------
Like the original, a line "matches" when the reconstructed commit's SHA is a
prefix of the blame SHA (``commit.sha.startswith(blame_sha)`` handles git's
abbreviated blame SHAs) AND the reconstructed commit's author email equals the
blame email. Binary files are skipped (both by git's ``check-attr`` and by the
metric, which never emits a trait for them). The default ``0.99`` threshold
mirrors the original's stated goal of a near-perfect reconstruction while
absorbing the handful of legitimate edge rows (e.g. blame's whitespace /
``.mailmap`` rewrites) the original tolerated.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


# ----------------------------------------------------------------------
# Opt-in gate — skip the whole module unless the env vars are present.
# ----------------------------------------------------------------------
_REPO = os.environ.get("BLAME_VALIDATOR_REPO")
_IGLOG = os.environ.get("BLAME_VALIDATOR_IGLOG")

pytestmark = pytest.mark.skipif(
    not (_REPO and _IGLOG),
    reason=(
        "offline blame validator: set BLAME_VALIDATOR_REPO (a checked-out "
        "clone) and BLAME_VALIDATOR_IGLOG (the matching .iglog) to run"
    ),
)


# Precompiled regex for blame parsing — verbatim from legacy run_blame.py.
blame_line_pattern = re.compile(
    r'^\^?([0-9a-f]+)\s+\((.*?)\s*<(.*?)>.*?(\d+)\)'
)


# ----------------------------------------------------------------------
# Git helpers — ported from legacy run_blame.py.
# ----------------------------------------------------------------------
def _run_git_command(args: list[str], repo_path: str) -> str:
    """Run a git command inside the repo and return stdout as str."""
    result = subprocess.run(
        ["git", "-C", repo_path] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,  # capture raw bytes
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: {' '.join(args)}\n"
            f"{result.stderr.decode(errors='replace')}"
        )
    return result.stdout.decode("utf-8", errors="replace").strip()


def _is_binary_file(filepath: str, repo_path: str) -> bool:
    """Check if Git considers a file binary."""
    result = subprocess.run(
        ["git", "-C", repo_path, "check-attr", "binary", "--", filepath],
        stdout=subprocess.PIPE,
        text=True,
    )
    return "binary: set" in result.stdout


def _get_blame_data_for_file(filepath: str, repo_path: str) -> list[dict]:
    """Return blame data for a single file as a list of dicts.

    Faithful port of legacy ``get_blame_data_for_file``.
    """
    if _is_binary_file(filepath, repo_path):
        return []  # skip binary files

    blame_output = _run_git_command(
        ["blame", "--show-email", filepath], repo_path
    )
    results: list[dict] = []
    for line in blame_output.splitlines():
        match = blame_line_pattern.match(line)
        if match:
            sha, _name, email, line_idx = match.groups()
            sha = sha.lstrip("^")
            results.append(
                {
                    "sha": sha,
                    "email": email.strip(),
                    "line_index": int(line_idx),
                }
            )
    return results


# ----------------------------------------------------------------------
# Build the v2 graph from the iglog with the metric enabled.
# ----------------------------------------------------------------------
def _build_graph_with_attribution():
    """Build a graph from the supplied iglog with compute_annotated_lines on.

    Imported lazily so module collection (and the skip) never needs the
    heavier processor/transformer imports.
    """
    from src.common.domains.git.bridge import build_git_bundle
    from src.processor import build_graph_from_bundles

    repo_name = os.environ.get(
        "BLAME_VALIDATOR_REPO_NAME", Path(_IGLOG).stem
    )
    bundle = build_git_bundle(Path(_IGLOG), repo_name, repo_name)
    graph, _result = build_graph_from_bundles(
        repo_name,
        {SourceKind.GIT: [bundle]},
        compute_annotated_lines=True,
    )
    return graph


def _files_by_path(graph) -> dict[str, object]:
    """Map bare git path -> File entity (legacy used ``str(f)`` == path)."""
    return {f.path: f for f in graph.files.all()}


def _annotated_commit_ids(graph, file_) -> list[str] | None:
    """The per-line commit ids for ``file_`` from its git.line_attribution
    Trait, or ``None`` when no trait was emitted (binary / overflow)."""
    ref = EntityRef(kind=EntityKind.FILE, id=file_.id)
    trait = next(
        (
            t
            for t in graph.traits.for_target(ref)
            if t.name == "git.line_attribution"
        ),
        None,
    )
    if trait is None:
        return None
    return list(trait.evidence["annotated_lines"])


# ----------------------------------------------------------------------
# The validator test.
# ----------------------------------------------------------------------
def test_blame_matches_reconstructed_attribution():
    repo_path = _REPO
    assert repo_path is not None  # guarded by pytestmark

    graph = _build_graph_with_attribution()
    files_by_path = _files_by_path(graph)

    # ``int()`` any stringified classifier values we read (Classifier.value is
    # a str). We cross-check the git.loc classifier against the trait length.
    def _loc(file_) -> int | None:
        ref = EntityRef(kind=EntityKind.FILE, id=file_.id)
        clf = graph.classifiers.for_target(ref).get("git.loc")
        return int(clf.value) if clf is not None else None

    match = 0
    total = 0
    files_with_not_found_lines: set[str] = set()
    files_with_mismatches: set[str] = set()
    skipped_binary: set[str] = set()
    files_missing_from_graph: set[str] = set()

    tracked = _run_git_command(["ls-files"], repo_path).splitlines()

    for file_name in tracked:
        git_file = files_by_path.get(file_name)
        if git_file is None:
            files_missing_from_graph.add(file_name)
            continue
        if getattr(git_file, "is_binary", False):
            skipped_binary.add(file_name)
            continue

        blame_entries = _get_blame_data_for_file(file_name, repo_path)
        if not blame_entries:
            # git considers it binary (or empty) — skip like the original.
            continue

        annotated = _annotated_commit_ids(graph, git_file)
        if annotated is None:
            # No trait (metric skipped it, e.g. replay overflow). Count its
            # blame lines as not-found so the ratio reflects the gap.
            files_with_not_found_lines.add(file_name)
            total += len(blame_entries)
            continue

        # Sanity: git.loc classifier must agree with the trait length.
        loc = _loc(git_file)
        assert loc is None or loc == len(annotated), (
            f"git.loc ({loc}) != attribution length ({len(annotated)}) "
            f"for {file_name}"
        )

        # Resolve each annotated commit id -> Commit so we can read its bare
        # SHA + author email (the trait stores repo-scoped commit *ids*).
        for entry in blame_entries:
            line_index = entry["line_index"]
            blame_sha = entry["sha"]
            blame_email = entry["email"]

            total += 1
            try:
                commit_id = annotated[line_index - 1]
            except IndexError:
                files_with_not_found_lines.add(file_name)
                continue

            commit = graph.commits.get(commit_id)
            if commit is None:
                files_with_mismatches.add(file_name)
                continue

            author = commit.author(graph)
            author_email = author.email if author is not None else None
            # Tolerance: abbreviated blame SHA is a prefix of the full SHA.
            if commit.sha.startswith(blame_sha) and author_email == blame_email:
                match += 1
            else:
                files_with_mismatches.add(file_name)

    ratio = (match / total) if total else 0.0
    min_match = float(os.environ.get("BLAME_VALIDATOR_MIN_MATCH", "0.99"))

    # Surface the same summary the original logged (visible with -s).
    print(
        f"\n[blame-validator] repo={repo_path}\n"
        f"  matches              : {match} / {total} ({ratio * 100:.2f}%)\n"
        f"  files mismatched     : {len(files_with_mismatches)}\n"
        f"  files w/ missing line: {len(files_with_not_found_lines)}\n"
        f"  files skipped binary : {len(skipped_binary)}\n"
        f"  files not in graph   : {len(files_missing_from_graph)}\n"
        f"  threshold            : {min_match * 100:.2f}%"
    )

    assert total > 0, (
        "no comparable lines — check that the iglog matches the clone "
        "(same repo, same HEAD)"
    )
    assert ratio >= min_match, (
        f"blame match ratio {ratio * 100:.2f}% below threshold "
        f"{min_match * 100:.2f}% — reconstructed attribution diverged from "
        f"git blame (mismatched files: {sorted(files_with_mismatches)[:20]})"
    )
