"""OPT-IN validator for the git per-line attribution metric.

Faithful port of legacy ``main:data-server/src/run_blame.py`` onto the v2
enrichment shape (plan §6). It reconstructs each file's final line ownership
from the ``git.line_attribution`` Trait emitted by
:class:`GitLineAttributionMetric` (built with
``compute_annotated_lines=True``) and diffs it, line by line, against real
``git blame --show-email`` data: per-line ``{sha, email}``.

Two modes
---------
1. **Vendored (default when opt-in is set).** The repo ships, under
   ``tests/fixtures/blame_validator/``:

   * ``zeppelin.iglog.gz`` — the inspector-git log of zeppelin at commit
     ``6353224cd1490f42ac1ea6056ba79ccd8a06c765`` (gzipped; ~2.7 MB).
   * ``zeppelin_blame_golden.json.gz`` — the EXACT data
     ``git blame --show-email`` produced for that same checkout, captured with
     this module's own :func:`_get_blame_data_for_file` parser so it is
     byte-equivalent in meaning to running blame live (gzipped; ~1.5 MB).

   In this mode the test needs **no external clone and runs no ``git`` at all**:
   it decompresses the iglog to a temp path, builds the v2 graph, and compares
   against the vendored golden. This is the self-contained, committed path.

2. **Live override.** If ``BLAME_VALIDATOR_REPO`` (a checked-out clone) **and**
   ``BLAME_VALIDATOR_IGLOG`` (the matching ``.iglog``) are BOTH set, the test
   uses them instead: it builds from that iglog and compares against live
   ``git blame --show-email`` run against that clone. Use this for ad-hoc runs
   against other repos / fresh checkouts.

Opt-in gate
-----------
The build is multi-minute, so the whole module SKIPS unless ``RUN_BLAME_VALIDATOR``
is truthy (``1``/``true``/``yes``). The normal ``pytest tests/enrichment/`` run
therefore stays fast and green.

How to run
----------
Self-contained (vendored fixtures, nothing external)::

    RUN_BLAME_VALIDATOR=1 python -m pytest \
        tests/enrichment/test_blame_validator_offline.py -q -s

Live override (ad-hoc clone + live git blame)::

    RUN_BLAME_VALIDATOR=1 \
    BLAME_VALIDATOR_REPO=/path/to/checked-out/clone \
    BLAME_VALIDATOR_IGLOG=/path/to/repo.iglog \
    python -m pytest tests/enrichment/test_blame_validator_offline.py -q -s

Optional knobs:

* ``BLAME_VALIDATOR_REPO_NAME`` — the repo_name the iglog was built under
  (defaults to the iglog filename stem). It only affects entity ids, not the
  bare ``File.path`` lookup, so the default is almost always fine.
* ``BLAME_VALIDATOR_MIN_MATCH`` — minimum overall line-match ratio the assert
  requires (default ``0.99``). Set ``0`` to only PRINT the score.

Supabase note
-------------
Building the v2 graph imports ``src.processor``, which imports ``supabase`` and
trips ``src.config``'s service-key guard at import time. Nothing on this build
path actually contacts Supabase, so this module auto-sets DUMMY
``SUPABASE_SERVICE_KEY`` / ``SUPABASE_URL`` (only if unset) purely to satisfy
that import guard — see :func:`_ensure_dummy_supabase_env`.

Tolerance
---------
Like the original, a line "matches" when the reconstructed commit's SHA is a
prefix of the blame SHA (``commit.sha.startswith(blame_sha)`` handles git's
abbreviated blame SHAs) AND the reconstructed commit's author email equals the
blame email. Binary / empty-blame files are skipped (they carry no golden
entries and no trait). The default ``0.99`` threshold mirrors the original's
stated goal of a near-perfect reconstruction while absorbing the handful of
legitimate edge rows (blame whitespace / ``.mailmap`` rewrites) it tolerated.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind


# ----------------------------------------------------------------------
# Opt-in gate — skip the whole module unless RUN_BLAME_VALIDATOR is truthy.
# ----------------------------------------------------------------------
def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


_RUN = _truthy(os.environ.get("RUN_BLAME_VALIDATOR"))

# Explicit live-override inputs (a real clone + its matching iglog).
_REPO = os.environ.get("BLAME_VALIDATOR_REPO")
_IGLOG = os.environ.get("BLAME_VALIDATOR_IGLOG")
_LIVE = bool(_REPO and _IGLOG)

pytestmark = pytest.mark.skipif(
    not _RUN,
    reason=(
        "offline blame validator: set RUN_BLAME_VALIDATOR=1 to run "
        "(uses vendored zeppelin fixtures by default; override with "
        "BLAME_VALIDATOR_REPO + BLAME_VALIDATOR_IGLOG for a live clone)"
    ),
)


# Vendored, committed fixtures (self-contained default path).
_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "blame_validator"
_VENDORED_IGLOG_GZ = _FIXTURE_DIR / "zeppelin.iglog.gz"
_VENDORED_GOLDEN_GZ = _FIXTURE_DIR / "zeppelin_blame_golden.json.gz"


# Precompiled regex for blame parsing — verbatim from legacy run_blame.py.
blame_line_pattern = re.compile(
    r'^\^?([0-9a-f]+)\s+\((.*?)\s*<(.*?)>.*?(\d+)\)'
)


def _ensure_dummy_supabase_env() -> None:
    """Set dummy SUPABASE_* (only if unset) to satisfy ``src.config``'s
    import-time guard. Safe because nothing on the iglog->graph build path
    contacts Supabase (see module docstring)."""
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "dummy")
    os.environ.setdefault("SUPABASE_URL", "http://localhost:8000")


# ----------------------------------------------------------------------
# Git helpers — ported from legacy run_blame.py (live-override mode only).
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

    Faithful port of legacy ``get_blame_data_for_file``. Used in live-override
    mode AND to capture the vendored golden, so the golden is byte-equivalent
    to running this live.
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
# Blame source — vendored golden vs. live clone. Both yield the SAME shape:
# (list_of_tracked_paths, callable path -> list[{sha,email,line_index}]).
# ----------------------------------------------------------------------
def _load_vendored_golden() -> dict[str, list[dict]]:
    assert _VENDORED_GOLDEN_GZ.is_file(), (
        f"missing vendored golden at {_VENDORED_GOLDEN_GZ}; regenerate by "
        f"capturing git blame --show-email from a checked-out clone"
    )
    with gzip.open(_VENDORED_GOLDEN_GZ, "rt", encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------
# Build the v2 graph from the iglog with the metric enabled.
# ----------------------------------------------------------------------
def _build_graph_with_attribution(iglog_path: Path):
    """Build a graph from ``iglog_path`` with compute_annotated_lines on.

    Imported lazily so module collection (and the skip) never needs the
    heavier processor/transformer imports.
    """
    _ensure_dummy_supabase_env()
    from src.common.domains.git.bridge import build_git_bundle
    from src.processor import build_graph_from_bundles

    repo_name = os.environ.get(
        "BLAME_VALIDATOR_REPO_NAME", iglog_path.stem
    )
    bundle = build_git_bundle(iglog_path, repo_name, repo_name)
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
def test_blame_matches_reconstructed_attribution(tmp_path):
    # --- Resolve inputs: live override vs. vendored fixtures. ---------
    if _LIVE:
        iglog_path = Path(_IGLOG)
        repo_path = _REPO

        def tracked_files() -> list[str]:
            return _run_git_command(["ls-files"], repo_path).splitlines()

        def blame_for(path: str) -> list[dict]:
            return _get_blame_data_for_file(path, repo_path)

        source_label = f"live clone {repo_path}"
    else:
        assert _VENDORED_IGLOG_GZ.is_file(), (
            f"missing vendored iglog at {_VENDORED_IGLOG_GZ}"
        )
        # Decompress the iglog to a temp path the bridge can read.
        iglog_path = tmp_path / "zeppelin.iglog"
        with gzip.open(_VENDORED_IGLOG_GZ, "rb") as src:
            iglog_path.write_bytes(src.read())

        golden = _load_vendored_golden()

        def tracked_files() -> list[str]:
            # The golden already excludes binary/empty-blame files; its keys
            # are exactly the files with comparable blame lines.
            return sorted(golden.keys())

        def blame_for(path: str) -> list[dict]:
            return golden.get(path, [])

        source_label = f"vendored fixtures {_FIXTURE_DIR}"

    graph = _build_graph_with_attribution(iglog_path)
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

    for file_name in tracked_files():
        git_file = files_by_path.get(file_name)
        if git_file is None:
            files_missing_from_graph.add(file_name)
            continue
        if getattr(git_file, "is_binary", False):
            skipped_binary.add(file_name)
            continue

        blame_entries = blame_for(file_name)
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
        f"\n[blame-validator] source={source_label}\n"
        f"  matches              : {match} / {total} ({ratio * 100:.2f}%)\n"
        f"  files mismatched     : {len(files_with_mismatches)}\n"
        f"  files w/ missing line: {len(files_with_not_found_lines)}\n"
        f"  files skipped binary : {len(skipped_binary)}\n"
        f"  files not in graph   : {len(files_missing_from_graph)}\n"
        f"  threshold            : {min_match * 100:.2f}%"
    )

    assert total > 0, (
        "no comparable lines — check that the iglog matches the blame data "
        "(same repo, same HEAD)"
    )
    assert ratio >= min_match, (
        f"blame match ratio {ratio * 100:.2f}% below threshold "
        f"{min_match * 100:.2f}% — reconstructed attribution diverged from "
        f"git blame (mismatched files: {sorted(files_with_mismatches)[:20]})"
    )
