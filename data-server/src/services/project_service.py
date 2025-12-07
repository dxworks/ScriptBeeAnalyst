"""
Project service for fetching and managing project data from Supabase.
Handles file downloads, project status updates, and data validation.
"""

import tempfile
from pathlib import Path
from typing import Dict, Optional, Tuple
from supabase import Client

from src.logger import get_logger

LOG = get_logger(__name__)


class ProjectFile:
    """Represents a serialized file from database."""

    def __init__(self, file_type: str, storage_path: str, name: str):
        self.file_type = file_type  # 'git', 'github', or 'jira'
        self.storage_path = storage_path
        self.name = name


async def fetch_project_files(
    client: Client, project_id: str
) -> Dict[str, ProjectFile]:
    """
    Fetch serialized files for a project from Supabase.

    Args:
        client: Supabase client (should be user-scoped for RLS)
        project_id: UUID of the project

    Returns:
        Dict mapping file_type to ProjectFile

    Raises:
        Exception: If query fails or project not found
    """
    response = (
        client.table("serialized_files")
        .select("*")
        .eq("project_id", project_id)
        .execute()
    )

    if not response.data:
        LOG.warning(f"No files found for project {project_id}")
        return {}

    files_dict = {}
    for file_record in response.data:
        file_type = file_record["file_type"]
        files_dict[file_type] = ProjectFile(
            file_type=file_type,
            storage_path=file_record["storage_path"],
            name=file_record["name"],
        )

    LOG.info(f"Fetched {len(files_dict)} files for project {project_id}")
    return files_dict


async def download_file(client: Client, storage_path: str) -> bytes:
    """
    Download a file from Supabase Storage.

    Args:
        client: Supabase client
        storage_path: Path in storage bucket (e.g., 'user_id/project_id/file.json')

    Returns:
        File contents as bytes

    Raises:
        Exception: If download fails
    """
    bucket_name = "serialized-files"
    response = client.storage.from_(bucket_name).download(storage_path)
    return response


async def download_project_files_to_temp(
    client: Client, files_dict: Dict[str, ProjectFile]
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """
    Download project files to temporary directory.

    Args:
        client: Supabase client
        files_dict: Dict mapping file_type to ProjectFile

    Returns:
        Tuple of (git_file_path, jira_file_path, github_file_path)
        Any path can be None if file doesn't exist

    Raises:
        Exception: If download fails
    """
    temp_dir = Path(tempfile.mkdtemp(prefix="project_"))
    LOG.info(f"Created temp directory: {temp_dir}")

    git_path = None
    jira_path = None
    github_path = None

    # Download Git file
    if "git" in files_dict:
        git_file = files_dict["git"]
        git_content = await download_file(client, git_file.storage_path)
        git_path = temp_dir / git_file.name
        git_path.write_bytes(git_content)
        LOG.info(f"Downloaded git file: {git_path}")

    # Download JIRA file
    if "jira" in files_dict:
        jira_file = files_dict["jira"]
        jira_content = await download_file(client, jira_file.storage_path)
        jira_path = temp_dir / jira_file.name
        jira_path.write_bytes(jira_content)
        LOG.info(f"Downloaded jira file: {jira_path}")

    # Download GitHub file
    if "github" in files_dict:
        github_file = files_dict["github"]
        github_content = await download_file(client, github_file.storage_path)
        github_path = temp_dir / github_file.name
        github_path.write_bytes(github_content)
        LOG.info(f"Downloaded github file: {github_path}")

    return git_path, jira_path, github_path


async def update_project_status(
    client: Client, project_id: str, status: str
) -> None:
    """
    Update project status in database.

    Args:
        client: Supabase client (service role for bypassing RLS)
        project_id: UUID of the project
        status: New status ('draft', 'processing', 'ready', 'idle', 'resuming', 'error')
    """
    response = (
        client.table("projects")
        .update({"status": status})
        .eq("id", project_id)
        .execute()
    )

    if response.data:
        LOG.info(f"Updated project {project_id} status to: {status}")
    else:
        LOG.warning(f"Failed to update status for project {project_id}")


def validate_all_files_present(
    files_dict: Dict[str, ProjectFile]
) -> Tuple[bool, list[str]]:
    """
    Validate that all required file types are present.

    Args:
        files_dict: Dict mapping file_type to ProjectFile

    Returns:
        Tuple of (all_present, missing_types)
    """
    required_types = {"git", "jira", "github"}
    present_types = set(files_dict.keys())
    missing_types = required_types - present_types

    return len(missing_types) == 0, list(missing_types)
