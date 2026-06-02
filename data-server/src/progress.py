"""Cross-process progress reporting for long-running project pipelines.

The build (``processor.build_graph``) and finalize (``server.finalize_project``)
pipelines run in a *different process* from the API that serves
``GET /projects`` — the build worker is its own ``processor`` container, the
API is the ``app`` container. So progress can't live in process memory; it is
written onto the ``projects`` row (``progress`` / ``progress_stage`` columns,
migration ``20260602000001``) at hardcoded checkpoints and read back by
``projects/router.py::_project_to_dict``. The dashboard's 4 s poll then drives
a determinate loading bar on the processing card.

Writes are best-effort: a failed/absent progress column must never block or
crash a build, so every call swallows DB errors and logs at debug.
"""

from __future__ import annotations

from src.db import execute
from src.logger import get_logger

logger = get_logger(__name__)


def report(project_id: str, percent: int, stage: str) -> None:
    """Record a checkpoint on the project row (percent clamped to 0..100)."""
    if not project_id:
        return
    clamped = max(0, min(100, int(percent)))
    try:
        execute(
            "update projects set progress = %s, progress_stage = %s where id = %s",
            (clamped, stage, project_id),
        )
    except Exception as exc:  # noqa: BLE001 — progress is best-effort
        logger.debug("progress.report failed for %s: %s", project_id, exc)


def clear(project_id: str) -> None:
    """Null out progress so the dashboard bar disappears. Idempotent."""
    if not project_id:
        return
    try:
        execute(
            "update projects set progress = NULL, progress_stage = NULL "
            "where id = %s",
            (project_id,),
        )
    except Exception as exc:  # noqa: BLE001 — progress is best-effort
        logger.debug("progress.clear failed for %s: %s", project_id, exc)
