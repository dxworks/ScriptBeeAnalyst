#!/usr/bin/env python3
"""
Graph Processor - Background service for building project graphs

Downloads serialized files from Supabase Storage, builds graphs, and uploads pickles.

Usage:
    # Continuously poll database for projects with status='processing'
    python -m src.processor

    # Docker (runs automatically)
    docker compose up processor
"""

import pickle
import sys
import time
import argparse
from pathlib import Path
from typing import Dict, Optional

# Add parent directory to path if running as script
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, RECURSION_LIMIT
from src.logger import get_logger
from src.inspector_git.reader.iglog.readers.ig_log_reader import IGLogReader

logger = get_logger("processor")
from src.inspector_git.linker.transformers import GitProjectTransformer
from src.jira_miner.reader_dto.loader import JiraJsonLoader
from src.jira_miner.linker.transformers import JiraProjectTransformer
from src.github_miner.reader_dto.loader import GithubJsonLoader
from src.github_miner.linker.transformers import GitHubProjectTransformer
from src.common.project_linkers import ProjectLinker


def download_serialized_files_from_supabase(project_id: str) -> Dict[str, Path]:
    """
    Downloads serialized files from Supabase Storage for a given project.

    Args:
        project_id: Project UUID to download files for

    Returns:
        Dict mapping file_type ('git', 'github', 'jira') to local temp file paths
    """
    logger.info("Downloading serialized files from Supabase Storage")

    if not project_id:
        raise ValueError("project_id must be provided")

    # Initialize Supabase client with service key (bypasses RLS)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Query serialized_files table for this project
    response = supabase.table("serialized_files").select("*").eq("project_id", project_id).execute()

    if not response.data:
        raise ValueError(f"No serialized files found for project_id: {project_id}")

    downloaded_files = {}
    temp_dir = Path("/tmp/processor_downloads")
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_summaries = []
    for file_record in response.data:
        file_type = file_record["file_type"]
        storage_path = file_record["storage_path"]
        file_name = file_record["name"]

        # Download from Supabase Storage
        try:
            file_bytes = supabase.storage.from_("serialized-files").download(storage_path)
        except Exception as e:
            raise FileNotFoundError(f"Failed to download {storage_path}: {e}")

        # Save to temp directory
        temp_file_path = temp_dir / f"{file_type}_{project_id}{Path(file_name).suffix}"
        with open(temp_file_path, "wb") as f:
            f.write(file_bytes)

        downloaded_files[file_type] = temp_file_path
        file_summaries.append(f"{file_type} ({file_name})")

    logger.info(f"Downloaded: {', '.join(file_summaries)}")
    return downloaded_files


def build_graph_from_local_files() -> Dict:
    """
    Builds the project graph from local test-input files and links them together.

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    base_path = Path(__file__).parent.parent / "test-input"

    logger.info(f"Loading files from: {base_path}")

    # InspectorGit
    iglog_file = base_path / "inspector-git" / "zeppelin.iglog"
    logger.info(f"Loading Git data from {iglog_file.name}")
    with open(iglog_file, "r", encoding="utf-8") as f:
        git_log_dto = IGLogReader().read(f)

    git_project = GitProjectTransformer(
        git_log_dto,
        name=iglog_file.stem,
        compute_annotated_lines=False,  # no blame
    ).transform()

    # Jira
    jira_file = base_path / "jira-miner" / "ZEPPELIN-detailed-issues.json"
    logger.info(f"  - Loading JIRA data from {jira_file.name}...")
    jira_loader = JiraJsonLoader(str(jira_file))
    jira_data = jira_loader.load()
    jira_project = JiraProjectTransformer(jira_data, name="Jira Project").transform()

    # GitHub
    github_file = base_path / "github-miner" / "githubProject.json"
    logger.info(f"  - Loading GitHub data from {github_file.name}...")
    github_loader = GithubJsonLoader(str(github_file))
    github_data = github_loader.load()
    github_project = GitHubProjectTransformer(github_data, name="GitHub Project").transform()

    # Link projects together
    logger.info("Linking projects")
    ProjectLinker.link_projects(github_project, jira_project, jira_data)
    ProjectLinker.link_projects(jira_project, git_project)
    ProjectLinker.link_projects(github_project, git_project)

    logger.info("Graph built successfully")
    logger.info(f"Git commits: {len(git_project.git_commit_registry.all)}")
    logger.info(f"JIRA issues: {len(jira_project.issue_registry.all)}")
    logger.info(f"GitHub PRs: {len(github_project.pull_request_registry.all)}")

    return {
        "git": git_project,
        "jira": jira_project,
        "github": github_project,
    }


def build_graph_from_downloaded_files(file_paths: Dict[str, Path]) -> Dict:
    """
    Builds the project graph from downloaded serialized files and links them together.

    Args:
        file_paths: Dict mapping file_type to file path (e.g., {'git': Path(...), 'jira': Path(...)})

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    logger.info("Building graph from downloaded files")

    git_project = None
    jira_project = None
    github_project = None
    jira_data = None

    # Load Git data if available
    if "git" in file_paths:
        git_file = file_paths["git"]
        with open(git_file, "r", encoding="utf-8") as f:
            git_log_dto = IGLogReader().read(f)
        git_project = GitProjectTransformer(
            git_log_dto,
            name=git_file.stem,
            compute_annotated_lines=False,  # no blame
        ).transform()

    # Load JIRA data if available
    if "jira" in file_paths:
        jira_file = file_paths["jira"]
        jira_loader = JiraJsonLoader(str(jira_file))
        jira_data = jira_loader.load()
        jira_project = JiraProjectTransformer(jira_data, name="Jira Project").transform()

    # Load GitHub data if available
    if "github" in file_paths:
        github_file = file_paths["github"]
        github_loader = GithubJsonLoader(str(github_file))
        github_data = github_loader.load()
        github_project = GitHubProjectTransformer(github_data, name="GitHub Project").transform()

    # Link projects together (only if both projects exist)
    if github_project and jira_project and jira_data:
        ProjectLinker.link_projects(github_project, jira_project, jira_data)
    if jira_project and git_project:
        ProjectLinker.link_projects(jira_project, git_project)
    if github_project and git_project:
        ProjectLinker.link_projects(github_project, git_project)

    # Build stats summary
    stats = []
    if git_project:
        stats.append(f"Git commits: {len(git_project.git_commit_registry.all)}")
    if jira_project:
        stats.append(f"JIRA issues: {len(jira_project.issue_registry.all)}")
    if github_project:
        stats.append(f"GitHub PRs: {len(github_project.pull_request_registry.all)}")

    logger.info(f"Project built successfully - {', '.join(stats)}")

    return {
        "git": git_project,
        "jira": jira_project,
        "github": github_project,
    }


def save_pickle_to_disk(graph_data: Dict, output_path: Path) -> None:
    """
    Serializes graph data to pickle file on local filesystem.

    Args:
        graph_data: Dict containing 'git', 'jira', 'github' project objects
        output_path: Path where pickle file should be saved
    """
    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize with highest protocol for performance
    with open(output_path, "wb") as f:
        pickle.dump(graph_data, f, protocol=pickle.HIGHEST_PROTOCOL)


def update_project_status(project_id: str, status: str) -> None:
    """
    Updates the status of a project in the database.

    Args:
        project_id: Project UUID
        status: New status ('processing', 'ready', 'error', etc.)
    """
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    try:
        supabase.table("projects").update({
            "status": status,
            "updated_at": "now()"
        }).eq("id", project_id).execute()
    except Exception as e:
        logger.warning(f"Failed to update project status: {e}")


def get_next_project_to_process() -> Optional[Dict]:
    """
    Queries database for the next project with status='processing'.
    Returns oldest project first (by updated_at).

    Returns:
        Project dict or None if no projects to process
    """
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    response = supabase.table("projects").select("*").eq("status", "processing").order("updated_at").limit(1).execute()

    if response.data and len(response.data) > 0:
        return response.data[0]
    return None


def upload_pickle_to_supabase(pickle_path: Path, user_id: str, project_id: str) -> None:
    """
    Uploads pickle file to Supabase Storage.

    Args:
        pickle_path: Path to local pickle file
        user_id: User UUID for storage path
        project_id: Project UUID for storage path
    """
    if not user_id or not project_id:
        raise ValueError("user_id and project_id are required")

    # Initialize Supabase client with service key (bypasses RLS)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Storage path: {user_id}/{project_id}/graph.pkl
    storage_path = f"{user_id}/{project_id}/graph.pkl"

    # Read pickle file
    with open(pickle_path, "rb") as f:
        pickle_bytes = f.read()

    # Upload to Supabase Storage (overwrites if exists)
    try:
        supabase.storage.from_("project-graphs").upload(
            path=storage_path,
            file=pickle_bytes,
            file_options={"content-type": "application/octet-stream", "upsert": "true"}
        )
    except Exception as e:
        # If file exists, try update instead
        if "already exists" in str(e).lower():
            supabase.storage.from_("project-graphs").update(
                path=storage_path,
                file=pickle_bytes,
                file_options={"content-type": "application/octet-stream"}
            )
        else:
            raise

    size_mb = len(pickle_bytes) / (1024 * 1024)
    logger.info(f"Saved to Supabase Storage - Size: {size_mb:.2f} MB")


def process_project(project_id: str, user_id: str) -> bool:
    """
    Processes a single project: downloads files, builds graph, uploads pickle.

    Args:
        project_id: Project UUID to process
        user_id: User UUID (for storage path)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Step 1: Update status to 'processing'
        update_project_status(project_id, "processing")

        # Step 2: Download serialized files from Supabase Storage
        file_paths = download_serialized_files_from_supabase(project_id)

        # Step 3: Build graph from downloaded files
        graph_data = build_graph_from_downloaded_files(file_paths)

        # Step 4: Save to local filesystem (temporary)
        output_path = Path("/tmp/pickles/graph.pkl")
        save_pickle_to_disk(graph_data, output_path)

        # Step 5: Upload to Supabase Storage
        upload_pickle_to_supabase(output_path, user_id, project_id)

        # Step 6: Update status to 'ready'
        update_project_status(project_id, "ready")

        return True

    except Exception as e:
        logger.error("ERROR: Processing failed")
        logger.error(f"{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

        # Update status to 'error'
        try:
            update_project_status(project_id, "error")
        except:
            pass

        return False


def run_loop(poll_interval: int = 60) -> int:
    """
    Continuously poll database and process projects.

    Args:
        poll_interval: Seconds to wait between polls

    Returns:
        Exit code (never returns in normal operation)
    """
    logger.info("Processor started - Polling database every 60s")

    try:
        while True:
            project = get_next_project_to_process()

            if project:
                logger.info(f"Processing: {project['name']} ({project['id']})")
                process_project(project["id"], project["user_id"])
            else:
                logger.info(f"No projects to process. Waiting {poll_interval}s")

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        logger.info("Stopped by user (Ctrl+C)")
        return 0


def main():
    """Main entry point for the processor."""
    # Increase recursion limit for large graph pickling
    sys.setrecursionlimit(RECURSION_LIMIT)

    parser = argparse.ArgumentParser(
        description="ScriptBeeAssistant Graph Processor - Builds graph from serialized files and uploads to Supabase",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Runs continuously, polling database for projects with status='processing'.
Press Ctrl+C to stop.
        """,
    )

    parser.add_argument(
        "--poll-interval",
        type=int,
        default=60,
        help="Polling interval in seconds (default: 60)",
    )

    args = parser.parse_args()

    return run_loop(args.poll_interval)


if __name__ == "__main__":
    sys.exit(main())
