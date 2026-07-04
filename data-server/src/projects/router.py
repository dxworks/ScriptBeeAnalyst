"""FastAPI routes for projects + serialized-files CRUD.

Replaces the web-ui's former direct Supabase DB + storage + realtime access
(project.service.ts / file.service.ts). Everything is plain SQL against the
shared connection pool (:mod:`src.db`) and on-disk file ops (:mod:`src.storage`).

Single-tenant local mode: no auth, no RLS, no per-user filtering.

Error envelope: ``JSONResponse({"error": str}, status_code=...)`` — the web-ui's
HttpErrorResponse handlers read ``err.error.error`` first, so we deliberately
avoid FastAPI's default ``{"detail": ...}`` shape.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from src import storage
from src.config import MAX_UPLOAD_MB
from src.db import connection, query, query_one
from src.logger import get_logger

LOG = get_logger(__name__)

router = APIRouter(tags=["projects"])

_MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1)
    description: Optional[str] = None


class UpdateProjectRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


_VALID_STATUSES = {"draft", "processing", "ready", "idle", "resuming", "error"}


class UpdateStatusRequest(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------
def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _project_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    """Serialize a projects row for the UI.

    ``enrichment_config_frozen`` is intentionally NOT serialized.
    """
    out = {
        "id": str(row["id"]),
        "name": row["name"],
        "description": row.get("description"),
        "status": row["status"],
        "merge_state": row.get("merge_state") or "PRE_MERGE",
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }
    # Live pipeline progress (build/finalize), written onto the row by the
    # build worker at hardcoded checkpoints (see src/progress.py). Present
    # only while a pipeline is running (NULL otherwise), so the dashboard
    # card's top-edge loading bar shows up just for active projects.
    prog = row.get("progress")
    if prog is not None:
        out["progress"] = prog
        out["progress_stage"] = row.get("progress_stage")
    return out


def _file_to_dict(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "file_type": row["file_type"],
        "repo_name": row.get("repo_name"),
        "storage_path": row["storage_path"],
        "size_bytes": row["size_bytes"],
        "project_id": str(row["project_id"]),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _project_row(project_id: str) -> Optional[Dict[str, Any]]:
    return query_one("select * from projects where id = %s", (project_id,))


# ---------------------------------------------------------------------------
# Projects CRUD
# ---------------------------------------------------------------------------
@router.get("/projects")
async def list_projects():
    """List all projects, newest-updated first (single-tenant, no filter)."""
    try:
        rows = query("select * from projects order by updated_at desc")
        return [_project_to_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        LOG.exception("list_projects failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/projects", status_code=201)
async def create_project(body: CreateProjectRequest):
    """Create a project; server sets status='draft', merge_state='PRE_MERGE'."""
    try:
        row = query_one(
            "insert into projects (name, description, status, merge_state) "
            "values (%s, %s, 'draft', 'PRE_MERGE') returning *",
            (body.name, body.description),
        )
        return _project_to_dict(row)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("create_project failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, body: UpdateProjectRequest):
    """Partial update of name/description. updated_at stamped by DB trigger."""
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        # Nothing to update — return the current row.
        row = _project_row(project_id)
        if row is None:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return _project_to_dict(row)

    set_clause = ", ".join(f"{k} = %s" for k in fields)
    params = list(fields.values()) + [project_id]
    try:
        row = query_one(
            f"update projects set {set_clause} where id = %s returning *",
            params,
        )
        if row is None:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return _project_to_dict(row)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("update_project failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.patch("/projects/{project_id}/status")
async def update_project_status(project_id: str, body: UpdateStatusRequest):
    """Set the project's status column only (matches UI updateProjectStatus)."""
    if body.status not in _VALID_STATUSES:
        return JSONResponse(
            {"error": f"Invalid status: {body.status!r}"}, status_code=400
        )
    try:
        row = query_one(
            "update projects set status = %s where id = %s returning *",
            (body.status, project_id),
        )
        if row is None:
            return JSONResponse({"error": "Project not found"}, status_code=404)
        return _project_to_dict(row)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("update_project_status failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    """Delete a project with server-side cascade.

    Removes all of the project's serialized file bytes on disk (the
    ``{root}/{project_id}`` dir) then DELETEs the project row — the FK
    ON DELETE CASCADE clears serialized_files + dependent tables.
    """
    row = _project_row(project_id)
    if row is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    # Count files before deletion for the response.
    file_rows = query(
        "select storage_path from serialized_files where project_id = %s",
        (project_id,),
    )
    deleted_files = len(file_rows)

    try:
        storage.delete_project_dir(project_id)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("delete_project disk cleanup failed")
        return JSONResponse(
            {"error": f"Failed to delete files on disk: {exc}"}, status_code=500
        )

    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from projects where id = %s", (project_id,))
    except Exception as exc:  # noqa: BLE001
        LOG.exception("delete_project DB delete failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    return {"ok": True, "deleted_files": deleted_files}


# ---------------------------------------------------------------------------
# Serialized files
# ---------------------------------------------------------------------------
@router.get("/projects/{project_id}/files")
async def list_files(project_id: str):
    """List serialized_files for a project ordered by file_type ASC."""
    try:
        rows = query(
            "select * from serialized_files where project_id = %s "
            "order by file_type asc",
            (project_id,),
        )
        return [_file_to_dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001
        LOG.exception("list_files failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/projects/{project_id}/files/exists")
async def file_exists(
    project_id: str,
    file_type: str,
    repo_name: Optional[str] = None,
):
    """Check whether a file of a given file_type (+repo_name) exists.

    Mirrors the old maybeSingle: never 404/406. Omitted repo_name matches rows
    where repo_name IS NULL; present repo_name matches exactly.
    """
    try:
        if repo_name is None:
            row = query_one(
                "select * from serialized_files where project_id = %s "
                "and file_type = %s and repo_name is null limit 1",
                (project_id, file_type),
            )
        else:
            row = query_one(
                "select * from serialized_files where project_id = %s "
                "and file_type = %s and repo_name = %s limit 1",
                (project_id, file_type, repo_name),
            )
        if row is None:
            return {"exists": False, "file": None}
        return {"exists": True, "file": _file_to_dict(row)}
    except Exception as exc:  # noqa: BLE001
        LOG.exception("file_exists failed")
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.post("/projects/{project_id}/files", status_code=201)
async def upload_file(
    project_id: str,
    file: UploadFile,
    file_type: Optional[str] = Form(default=None),
    repo_name: Optional[str] = Form(default=None),
):
    """Upload one serialized file (multipart).

    Server validates filename/type/size, writes bytes to disk, derives the
    unique storage_path (server now owns the hash), INSERTs the row with
    size_bytes from actual bytes, and unlinks bytes on DB-insert failure.
    """
    if _project_row(project_id) is None:
        return JSONResponse({"error": "Project not found"}, status_code=404)

    filename = file.filename or ""
    # Derive file_type / repo_name server-side (same rules as the UI). Allow
    # explicit form overrides but fall back to filename derivation.
    derived_type = storage.get_file_type_from_name(filename)
    if file_type is None:
        file_type = derived_type
    if file_type is None:
        return JSONResponse(
            {"error": f"Invalid filename: {filename!r} — unrecognised serialized file type"},
            status_code=400,
        )
    if repo_name is None:
        repo_name = storage.get_repo_name_from_file(filename)

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"File too large. Maximum size is {MAX_UPLOAD_MB}MB"},
            status_code=400,
        )

    storage_path = storage.build_storage_path(project_id, filename)
    try:
        size_bytes = storage.write_bytes(storage_path, data)
    except Exception as exc:  # noqa: BLE001
        LOG.exception("upload_file disk write failed")
        return JSONResponse({"error": f"Failed to write file: {exc}"}, status_code=500)

    try:
        row = query_one(
            "insert into serialized_files "
            "(name, file_type, repo_name, storage_path, size_bytes, project_id) "
            "values (%s, %s, %s, %s, %s, %s) returning *",
            (filename, file_type, repo_name, storage_path, size_bytes, project_id),
        )
    except Exception as exc:  # noqa: BLE001
        # Rollback: unlink the bytes we just wrote (parity with the old UI).
        storage.delete_file(storage_path)
        msg = str(exc)
        # The unique index on (project_id, file_type, repo_name) → duplicate.
        if "unique" in msg.lower() or "duplicate" in msg.lower():
            return JSONResponse(
                {"error": f"A {file_type} file already exists for this project"},
                status_code=400,
            )
        LOG.exception("upload_file DB insert failed")
        return JSONResponse({"error": msg}, status_code=500)

    return _file_to_dict(row)


@router.get("/projects/{project_id}/files/{file_id}/download")
async def download_file(project_id: str, file_id: str):
    """Stream raw file bytes from disk with attachment disposition."""
    row = query_one(
        "select * from serialized_files where id = %s and project_id = %s",
        (file_id, project_id),
    )
    if row is None:
        return JSONResponse({"error": "File not found"}, status_code=404)

    path = storage.absolute_path(row["storage_path"])
    if not path.exists():
        return JSONResponse(
            {"error": "File bytes missing on disk"}, status_code=404
        )

    return FileResponse(
        path=str(path),
        media_type="application/octet-stream",
        filename=row["name"],
    )


@router.delete("/projects/{project_id}/files/{file_id}")
async def delete_file(project_id: str, file_id: str):
    """Delete one file: unlink bytes from disk then DELETE the row."""
    row = query_one(
        "select * from serialized_files where id = %s and project_id = %s",
        (file_id, project_id),
    )
    if row is None:
        return JSONResponse({"error": "File not found"}, status_code=404)

    try:
        storage.delete_file(row["storage_path"])
    except Exception as exc:  # noqa: BLE001
        LOG.exception("delete_file disk unlink failed")
        return JSONResponse(
            {"error": f"Failed to delete file on disk: {exc}"}, status_code=500
        )

    try:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute("delete from serialized_files where id = %s", (file_id,))
    except Exception as exc:  # noqa: BLE001
        LOG.exception("delete_file DB delete failed")
        return JSONResponse({"error": str(exc)}, status_code=500)

    return {"ok": True}


projects_router = router


__all__ = ["projects_router", "router"]
