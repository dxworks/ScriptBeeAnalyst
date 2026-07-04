"""On-disk serialized-file store — replaces the Supabase 'serialized-files' bucket.

Files live at ``{SERIALIZED_FILES_DIR}/{storage_path}`` where ``storage_path``
keeps the exact old layout ``{project_id}/{base}_{hash}{ext}``. The hash that
used to be generated client-side (``file.service.ts generateHash``) is now
owned server-side so the data-server is the single source of truth.

Also ports the filename → file_type / repo_name derivation rules from
``web-ui/src/app/core/models/project.model.ts`` so the upload validator agrees
with the UI on every accepted filename.
"""
from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Optional, Tuple

from src.config import SERIALIZED_FILES_DIR


# --- filename → file_type / repo_name (port of project.model.ts) -----------

# (suffix, file_type, repo_from_stem). Order matters; first match wins.
_SUFFIX_RULES: list[tuple[str, str]] = [
    ("-codeframe.jsonl", "codeframe"),
    ("-code_smells.json", "quality_issues"),
    ("-chronos-tags.json", "app_inspector"),
    ("-external_duplication.csv", "dude_external"),
    ("-internal_duplication.json", "dude_internal"),
    ("-lizard.csv", "lizard"),
]

_EXACT_NAME_MAP: dict[str, str] = {
    "github.json": "github",
    "jira.json": "jira",
}


def get_file_type_from_name(filename: str) -> Optional[str]:
    """Return the file_type for ``filename`` or ``None`` if unrecognised."""
    lower = filename.lower()
    if lower.endswith(".iglog"):
        return "git"
    if lower in _EXACT_NAME_MAP:
        return _EXACT_NAME_MAP[lower]
    for suffix, file_type in _SUFFIX_RULES:
        if lower.endswith(suffix):
            return file_type
    return None


def is_valid_serialized_filename(filename: str) -> bool:
    return get_file_type_from_name(filename) is not None


def get_repo_name_from_file(filename: str) -> Optional[str]:
    """Derive the repo_name from a filename, or ``None`` for single-source files."""
    lower = filename.lower()
    if lower.endswith(".iglog"):
        dot = filename.rfind(".")
        return filename[:dot] if dot > 0 else None
    for suffix, _file_type in _SUFFIX_RULES:
        if lower.endswith(suffix):
            stem_end = len(filename) - len(suffix)
            return filename[:stem_end] if stem_end > 0 else None
    return None


# --- storage_path / on-disk layout -----------------------------------------

def root() -> Path:
    return Path(SERIALIZED_FILES_DIR)


def absolute_path(storage_path: str) -> Path:
    """Resolve a relative ``storage_path`` to its absolute on-disk path."""
    return root() / storage_path


def build_storage_path(project_id: str, filename: str) -> str:
    """Mint a unique ``{project_id}/{base}_{hash}{ext}`` storage key.

    Mirrors the old client layout. The hash is now a server-generated random
    token (was a client-side timestamp hash); it only needs to make the path
    unique on disk, the DB unique index on (project_id, file_type, repo_name)
    enforces logical uniqueness.
    """
    lower = filename.lower()
    dot = lower.rfind(".")
    if dot > 0:
        base = lower[:dot]
        ext = lower[dot:]
    else:
        base = lower
        ext = ""
    token = secrets.token_hex(8)
    return f"{project_id}/{base}_{token}{ext}"


def write_bytes(storage_path: str, data: bytes) -> int:
    """Write ``data`` to ``{root}/{storage_path}``, creating parent dirs.

    Returns the number of bytes written.
    """
    dest = absolute_path(storage_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return len(data)


def delete_file(storage_path: str) -> bool:
    """Unlink ``{root}/{storage_path}``. Returns True if a file was removed."""
    dest = absolute_path(storage_path)
    if dest.exists():
        dest.unlink()
        return True
    return False


def delete_project_dir(project_id: str) -> None:
    """Recursively remove ``{root}/{project_id}`` if present."""
    project_dir = root() / project_id
    if project_dir.is_dir():
        shutil.rmtree(project_dir, ignore_errors=True)


def read_bytes(storage_path: str) -> bytes:
    return absolute_path(storage_path).read_bytes()


__all__ = [
    "absolute_path",
    "build_storage_path",
    "delete_file",
    "delete_project_dir",
    "get_file_type_from_name",
    "get_repo_name_from_file",
    "is_valid_serialized_filename",
    "read_bytes",
    "root",
    "write_bytes",
]
