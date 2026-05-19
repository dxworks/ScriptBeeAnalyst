"""Legacy reader -> v2 bundle bridge for the Lizard-metrics domain.

The v2 :class:`LizardMetricsTransformer` (see ``transformer.py``) consumes an
*already-built* entity bundle. This module is the single seam that walks a
raw Lizard CSV export and instantiates the v2 entities documented in
``models.py``.

The bridge is intentionally read-only on its inputs and side-effect-free:

* No registry mutation -- that is the processor's job.
* No file I/O beyond opening the CSV path passed in.
* The returned mapping matches exactly the keys
  :class:`LizardMetricsTransformer` looks up via ``_BUCKET_SPECS``.

Key translation choices
-----------------------

* The Lizard CSV emits **one row per function**. :class:`FileMetric` is
  per-file scalar, so the bridge groups rows by their ``file`` column
  and emits one :class:`FileMetric` per ``(file, metric_name)`` pair --
  exactly the shape ``models.py`` documents (``sum_nloc`` / ``max_ccn``
  / ``avg_ccn`` / ``function_count`` / ``longest_function_nloc`` /
  ``token_count``).

* The CSV's ``file`` column carries an *absolute* path that comes from
  whichever machine Lizard was run on (e.g.
  ``/home/.../voyager-target/<repo_name>/<repo_name>/<rel_path>``).
  The v2 :class:`git.File` id is the **repo-relative** path. The bridge
  normalises the CSV's absolute path against the caller-supplied
  ``repo_name`` by stripping everything up to and including the
  **last** ``/<repo_name>/`` segment. If ``repo_name`` does not appear,
  the absolute path is kept verbatim (degrades gracefully -- the
  :class:`FileMetric.file_ref` becomes a typed ref to a File id that
  the git registry will simply not resolve, which is the same fate
  the legacy CSV had).

* Per the :class:`FileMetric` docstring, the per-function value-object
  list (:class:`FunctionMetric`) is attached **only** to the
  ``function_count`` rollup row by convention; the other metric rows
  leave ``functions`` empty (the default ``[]``).

* Column layout is inferred from the CSV's header row. Lizard's default
  ``--csv`` exporter emits a header row with the columns
  ``NLOC,CCN,token,PARAM,length,location,file,function,long_name,start,end``;
  the bridge keys off those names rather than positional indexes so a
  future column reshuffle (or extra columns appended by a Lizard plugin)
  does not silently break. Header-less files raise a clear error.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from ...kernel import EntityKind, EntityRef
from ...people import SourceKind
from ..git.models import File
from .models import FileMetric, FunctionMetric, LizardMetricsProject


# Columns the bridge requires; matches Lizard's default CSV header.
_REQUIRED_COLUMNS = (
    "NLOC",
    "CCN",
    "token",
    "PARAM",
    "length",
    "file",
    "function",
    "long_name",
    "start",
    "end",
)


# ---------------------------------------------------------------------------
# Public bridge entry point
# ---------------------------------------------------------------------------


def build_lizard_bundle(
    file_path: Path,
    repo_name: str,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Parse a Lizard CSV file and return a v2 entity bundle.

    Parameters
    ----------
    file_path:
        Filesystem path to a Lizard ``--csv`` export.
    repo_name:
        Stable identifier for the repository the CSV was measured against
        (used as the :class:`LizardMetricsProject` ``id`` and as the
        **fallback** anchor for any CSV row that does not carry its own
        ``repo_name`` column).
    project_name:
        Display ``name`` on the :class:`LizardMetricsProject`. Defaults to
        ``"Project"`` if the caller has no better label.

    Returns
    -------
    Mapping with the keys :class:`LizardMetricsTransformer` expects
    (``{"project", "file_metrics"}``) plus a ``"_meta"`` entry the
    dispatcher pops before transform. ``_meta["all_rows_self_repo"]`` is
    ``True`` iff every parsed row carried its own non-empty ``repo_name``
    column (no fallback was needed).
    """
    project = LizardMetricsProject(
        id=repo_name,
        name=project_name or repo_name,
        source=SourceKind.LIZARD,
    )
    project_ref = project.ref()

    # Group rows by (repo_used, repo-relative file path) so files coming
    # from different repos but sharing the same relative path don't
    # collide. Insertion order is preserved for stable test output.
    functions_by_key: Dict[Tuple[str, str], List[FunctionMetric]] = {}

    any_row_seen = False
    all_rows_self_repo = True
    for row in _iter_function_rows(file_path):
        any_row_seen = True
        row_repo = (row.get("repo_name") or "").strip()
        if row_repo:
            repo_used = row_repo
        else:
            repo_used = repo_name
            all_rows_self_repo = False
        rel_path = _normalize_file_path(row["file"], repo_used)
        functions_by_key.setdefault((repo_used, rel_path), []).append(
            _function_metric_from_row(row)
        )

    file_metrics: List[FileMetric] = []
    for (repo_used, rel_path), functions in functions_by_key.items():
        file_ref = EntityRef(
            kind=EntityKind.FILE, id=File.make_id(repo_used, rel_path)
        )
        file_metrics.extend(
            _aggregate_file_metrics(rel_path, file_ref, project_ref, functions)
        )

    return {
        "project": project,
        "file_metrics": file_metrics,
        "_meta": {"all_rows_self_repo": bool(any_row_seen and all_rows_self_repo)},
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _iter_function_rows(file_path: Path) -> Iterable[Dict[str, str]]:
    """Yield one ``dict`` per CSV data row, keyed by header name.

    Uses :class:`csv.DictReader` so commas inside the quoted ``location``
    column don't desync the columns. Validates the header against
    :data:`_REQUIRED_COLUMNS` and raises a clear error if Lizard was run
    without ``--csv`` headers (or the header drifted).
    """
    with open(file_path, "r", encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        fieldnames = reader.fieldnames or []
        missing = [c for c in _REQUIRED_COLUMNS if c not in fieldnames]
        if missing:
            raise ValueError(
                "Lizard CSV is missing required columns "
                f"{missing!r}; got header={fieldnames!r}. Re-run Lizard "
                "with `--csv` so the default header is emitted."
            )
        for row in reader:
            yield row


def _normalize_file_path(absolute_path: str, repo_name: str) -> str:
    """Reduce a Lizard absolute path to the repo-relative form used by git.

    Lizard's ``file`` column is the path on the analyser's machine
    (e.g. ``/home/.../voyager-target/zeppelin/zeppelin/file/.../X.java``).
    The v2 :class:`git.File` id is the repo-relative path
    (``file/.../X.java`` for zeppelin) -- the same shape inspector-git
    emits. The bridge crosses the gap by anchoring on ``repo_name``.

    Two anchor patterns are tried, in order:

    1. ``/<repo_name>/<repo_name>/`` -- the doubled-segment pattern the
       voyager-target extraction uses (``.../<repo>/<repo>/<rel>``).
       Tried first because the inner repo-relative path itself may
       happen to contain a ``<repo_name>`` segment (e.g. zeppelin's
       Java package ``org/apache/zeppelin/...``), which would defeat a
       naive single-segment strip.
    2. ``/<repo_name>/`` -- the single-segment fallback for callers that
       lay out their fixtures without the voyager duplication.

    We use ``rfind`` in both cases so a duplicated segment closest to
    the end of the path wins -- avoids stripping deep into the relative
    path when ``<repo_name>`` shows up as a sub-directory.

    If neither pattern matches, the original path is returned
    untouched. Downstream :class:`git.File` resolution will simply miss
    -- the same outcome the legacy bridge produced.
    """
    doubled = f"/{repo_name}/{repo_name}/"
    idx = absolute_path.rfind(doubled)
    if idx != -1:
        return absolute_path[idx + len(doubled):]
    single = f"/{repo_name}/"
    idx = absolute_path.rfind(single)
    if idx != -1:
        return absolute_path[idx + len(single):]
    return absolute_path


def _function_metric_from_row(row: Dict[str, str]) -> FunctionMetric:
    """Convert a single Lizard CSV row into a :class:`FunctionMetric`.

    Lizard's ``function`` column is the short symbol (``Class::method``)
    and ``long_name`` is the full signature with parameters. Class name
    is parsed off the ``function`` column when it contains a ``::``
    separator -- Lizard doesn't carry an explicit ``class`` field.
    """
    short_name = row["function"]
    class_name: str | None = None
    if "::" in short_name:
        # ``A::B::method`` -> class ``A::B``, name ``method``.
        class_name, _, short_name = short_name.rpartition("::")
        class_name = class_name or None
    return FunctionMetric(
        name=short_name,
        long_name=row["long_name"],
        class_name=class_name,
        nloc=int(row["NLOC"]),
        cyclomatic_complexity=int(row["CCN"]),
        parameters=int(row["PARAM"]),
        token_count=int(row["token"]),
        length=int(row["length"]),
        start_line=int(row["start"]),
        end_line=int(row["end"]),
    )


def _aggregate_file_metrics(
    file_path: str,
    file_ref: EntityRef,
    project_ref: EntityRef,
    functions: List[FunctionMetric],
) -> List[FileMetric]:
    """Return one :class:`FileMetric` per ``(file, metric_name)`` rollup.

    Per :class:`FileMetric` docstring the canonical metric names are
    ``sum_nloc`` / ``max_ccn`` / ``avg_ccn`` / ``function_count`` /
    ``longest_function_nloc`` / ``token_count``. By convention the
    per-function list is attached to the ``function_count`` row only.
    """
    sum_nloc = sum(f.nloc for f in functions)
    max_ccn = max(f.cyclomatic_complexity for f in functions)
    avg_ccn = (
        sum(f.cyclomatic_complexity for f in functions) / len(functions)
    )
    function_count = len(functions)
    longest_function_nloc = max(f.nloc for f in functions)
    token_count = sum(f.token_count for f in functions)

    def _row(metric_name: str, value: float, *, with_funcs: bool = False) -> FileMetric:
        return FileMetric(
            id=FileMetric.make_id(file_path, metric_name),
            project_ref=project_ref,
            file_ref=file_ref,
            metric_name=metric_name,
            value=float(value),
            functions=functions if with_funcs else [],
        )

    return [
        _row("sum_nloc", sum_nloc),
        _row("max_ccn", max_ccn),
        _row("avg_ccn", avg_ccn),
        _row("function_count", function_count, with_funcs=True),
        _row("longest_function_nloc", longest_function_nloc),
        _row("token_count", token_count),
    ]


__all__ = ["build_lizard_bundle"]
