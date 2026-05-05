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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directory to path if running as script
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, RECURSION_LIMIT
from src.logger import get_logger
from src.inspector_git.reader.iglog.readers.ig_log_reader import IGLogReader
from src.inspector_git.reader.dto.gitlog.git_log_dto import GitLogDTO
from src.inspector_git.utils.constants import DEV_NULL

logger = get_logger("processor")
from src.inspector_git.linker.transformers import GitProjectTransformer, CommitTransformer, SimpleChangeFactory
from src.common.models import GitProject
from src.jira_miner.reader_dto.loader import JiraJsonLoader
from src.jira_miner.linker.transformers import JiraProjectTransformer
from src.github_miner.reader_dto.loader import GithubJsonLoader
from src.github_miner.linker.transformers import GitHubProjectTransformer
from src.common.project_linkers import ProjectLinker
from src.lizard_miner.reader_dto.loader import LizardCsvLoader
from src.lizard_miner.linker.transformers import LizardProjectTransformer
from src.codestructure_miner.parser import CodeStructureFormat, parse as parse_codestructure
from src.dude_miner.parser import parse_dude
from src.quality_miner.parser import parse_insider


@dataclass
class DownloadedFiles:
    """Holds downloaded file paths, supporting multiple git files (one per repo)."""
    git_files: List[Tuple[str, Path]] = field(default_factory=list)  # [(repo_name, path), ...]
    jira_file: Optional[Path] = None
    github_file: Optional[Path] = None
    lizard_file: Optional[Path] = None
    jafax_file: Optional[Path] = None
    dude_external_file: Optional[Path] = None
    dude_internal_file: Optional[Path] = None
    quality_issues_file: Optional[Path] = None


def download_serialized_files_from_supabase(project_id: str) -> DownloadedFiles:
    """
    Downloads serialized files from Supabase Storage for a given project.
    Supports multiple git (iglog) files with different repo names.

    Args:
        project_id: Project UUID to download files for

    Returns:
        DownloadedFiles with git_files list, optional jira_file and github_file
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

    downloaded = DownloadedFiles()
    temp_dir = Path("/tmp/processor_downloads")
    temp_dir.mkdir(parents=True, exist_ok=True)

    file_summaries = []
    for file_record in response.data:
        file_type = file_record["file_type"]
        storage_path = file_record["storage_path"]
        file_name = file_record["name"]
        repo_name = file_record.get("repo_name")

        # Download from Supabase Storage
        try:
            file_bytes = supabase.storage.from_("serialized-files").download(storage_path)
        except Exception as e:
            raise FileNotFoundError(f"Failed to download {storage_path}: {e}")

        if file_type == "git":
            # Multiple git files allowed, use repo_name in temp filename
            safe_repo = repo_name or "git"
            temp_file_path = temp_dir / f"git_{safe_repo}_{project_id}.iglog"
            with open(temp_file_path, "wb") as f:
                f.write(file_bytes)
            downloaded.git_files.append((safe_repo, temp_file_path))
            file_summaries.append(f"git/{safe_repo} ({file_name})")
        else:
            temp_file_path = temp_dir / f"{file_type}_{project_id}{Path(file_name).suffix}"
            with open(temp_file_path, "wb") as f:
                f.write(file_bytes)
            if file_type == "jira":
                downloaded.jira_file = temp_file_path
            elif file_type == "github":
                downloaded.github_file = temp_file_path
            elif file_type == "lizard":
                downloaded.lizard_file = temp_file_path
            elif file_type == "jafax":
                downloaded.jafax_file = temp_file_path
            elif file_type == "dude_external":
                downloaded.dude_external_file = temp_file_path
            elif file_type == "dude_internal":
                downloaded.dude_internal_file = temp_file_path
            elif file_type == "quality_issues":
                downloaded.quality_issues_file = temp_file_path
            file_summaries.append(f"{file_type} ({file_name})")

    logger.info(f"Downloaded: {', '.join(file_summaries)}")
    return downloaded


def prefix_change_paths(git_log_dto: GitLogDTO, repo_name: str) -> None:
    """
    Prefix all file paths in a GitLogDTO with the repo name.
    Modifies the DTO in place. Skips DEV_NULL paths.

    Args:
        git_log_dto: Parsed git log DTO to modify
        repo_name: Repository name to use as prefix (e.g., "backend")
    """
    for commit_dto in git_log_dto.commits:
        for change_dto in commit_dto.changes:
            if change_dto.old_file_name != DEV_NULL:
                change_dto.old_file_name = f"{repo_name}/{change_dto.old_file_name}"
            if change_dto.new_file_name != DEV_NULL:
                change_dto.new_file_name = f"{repo_name}/{change_dto.new_file_name}"


def build_graph_from_local_files() -> Dict:
    """
    Builds the project graph from local test-input files and links them together.

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    base_path = Path(__file__).parent.parent / "test-input"

    logger.info(f"Loading files from: {base_path}")

    # InspectorGit — use the same multi-repo pipeline as production
    iglog_file = base_path / "inspector-git" / "zeppelin.iglog"
    repo_name = iglog_file.stem  # "zeppelin"
    logger.info(f"Loading Git repo '{repo_name}' from {iglog_file.name}")
    with open(iglog_file, "r", encoding="utf-8") as f:
        git_log_dto = IGLogReader().read(f)

    prefix_change_paths(git_log_dto, repo_name)

    git_project = GitProject(name="LocalTest")
    change_factory = SimpleChangeFactory()
    for commit_dto in git_log_dto.commits:
        CommitTransformer.add_to_project(commit_dto, git_project, False, change_factory)

    # Compute branch IDs — matches original GitProjectTransformer behavior
    first_commit = next(iter(git_project.git_commit_registry.all), None)
    if first_commit:
        _compute_branch_ids(first_commit)

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

    # Lizard (optional)
    lizard_metrics: list = []
    lizard_file = base_path / "lizard" / f"{repo_name}.csv"
    if lizard_file.exists():
        logger.info(f"  - Loading Lizard CSV from {lizard_file.name}...")
        rows = LizardCsvLoader(str(lizard_file)).load()
        lizard_metrics = LizardProjectTransformer(rows, repo_root=repo_name, repo_prefix=repo_name).transform()

    # JaFax (optional). Conventional layout file name: <repo>-layout.json.
    code_structure = None
    jafax_file = base_path / "jafax" / f"{repo_name}-layout.json"
    if jafax_file.exists():
        logger.info(f"  - Loading JaFax layout from {jafax_file.name}...")
        code_structure = parse_codestructure(
            str(jafax_file), CodeStructureFormat.JAFAX,
            path_prefix=None,
        )

    # DuDe duplication (optional). Conventional file names:
    #   <repo>-external_duplication.csv (headerless)
    #   <repo>-internal_duplication.json
    duplication = None
    dude_external = base_path / "dude" / f"{repo_name}-external_duplication.csv"
    dude_internal = base_path / "dude" / f"{repo_name}-internal_duplication.json"
    if dude_external.exists() or dude_internal.exists():
        logger.info(
            "  - Loading DuDe duplication (external=%s internal=%s)...",
            dude_external.name if dude_external.exists() else "missing",
            dude_internal.name if dude_internal.exists() else "missing",
        )
        duplication = parse_dude(
            external_csv_path=str(dude_external) if dude_external.exists() else None,
            internal_json_path=str(dude_internal) if dude_internal.exists() else None,
            path_prefix=repo_name,
        )

    # Insider code-smells (optional). Conventional file name:
    #   <repo>-code_smells.json (note: hyphen-prefix, NOT literal "codeSmells.json").
    quality_issues = None
    insider_file = base_path / "insider" / f"{repo_name}-code_smells.json"
    if insider_file.exists():
        logger.info(f"  - Loading Insider code-smells from {insider_file.name}...")
        quality_issues = parse_insider(str(insider_file), path_prefix=None)

    # Link projects together
    logger.info("Linking projects")
    ProjectLinker.link_projects(github_project, jira_project, jira_data)
    ProjectLinker.link_projects(jira_project, git_project)
    ProjectLinker.link_projects(github_project, git_project)

    logger.info("Graph built successfully")
    logger.info(f"Git commits: {len(git_project.git_commit_registry.all)}")
    logger.info(f"JIRA issues: {len(jira_project.issue_registry.all)}")
    logger.info(f"GitHub PRs: {len(github_project.pull_request_registry.all)}")
    if lizard_metrics:
        logger.info(f"Lizard FileMetrics: {len(lizard_metrics)}")
    if code_structure is not None:
        logger.info(
            f"JaFax CodeStructure: types={len(code_structure.type_registry.all)} "
            f"methods={len(code_structure.method_registry.all)} "
            f"refs={len(code_structure.reference_registry.all)}"
        )
    if duplication is not None:
        logger.info(
            f"DuDe Duplication: external_pairs={len(duplication.external_pairs)} "
            f"internal_files={len(duplication.internal_by_file)}"
        )
    if quality_issues is not None:
        logger.info(
            f"Insider QualityIssues: issues={len(quality_issues.issues)} "
            f"files={len(quality_issues.file_paths)}"
        )

    return {
        "git": git_project,
        "jira": jira_project,
        "github": github_project,
        "metrics": {"lizard": lizard_metrics},
        "code_structure": code_structure,
        "duplication": duplication,
        "quality_issues": quality_issues,
    }


def build_graph_from_downloaded_files(downloaded: DownloadedFiles, project_name: str = "Project") -> Dict:
    """
    Builds the project graph from downloaded serialized files and links them together.
    Supports multiple git (iglog) files — all repos are merged into a single GitProject
    with file paths prefixed by repo name.

    Args:
        downloaded: DownloadedFiles with git_files list, optional jira_file and github_file
        project_name: Name for the merged GitProject

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    logger.info("Building graph from downloaded files")

    git_project = None
    jira_project = None
    github_project = None
    jira_data = None

    # Load Git data — multiple iglog files merged into a single GitProject
    if downloaded.git_files:
        git_project = GitProject(name=project_name)
        change_factory = SimpleChangeFactory()

        for repo_name, git_file in downloaded.git_files:
            logger.info(f"Loading Git repo '{repo_name}' from {git_file.name}")
            with open(git_file, "r", encoding="utf-8") as f:
                git_log_dto = IGLogReader().read(f)

            # Always prefix file paths with repo name
            prefix_change_paths(git_log_dto, repo_name)

            # Add all commits from this repo to the shared project
            for commit_dto in git_log_dto.commits:
                CommitTransformer.add_to_project(
                    commit_dto, git_project, False, change_factory
                )

        # Compute branch IDs — matches original GitProjectTransformer behavior
        # (only processes the first commit in the registry)
        first_commit = next(iter(git_project.git_commit_registry.all), None)
        if first_commit:
            _compute_branch_ids(first_commit)

        logger.info(
            f"Merged {len(downloaded.git_files)} git repo(s): "
            f"{len(git_project.git_commit_registry.all)} commits, "
            f"{len(git_project.account_registry.all)} authors, "
            f"{len(git_project.file_registry.all)} files"
        )

    # Load JIRA data if available
    if downloaded.jira_file:
        jira_file = downloaded.jira_file
        jira_loader = JiraJsonLoader(str(jira_file))
        jira_data = jira_loader.load()
        jira_project = JiraProjectTransformer(jira_data, name="Jira Project").transform()

    # Load GitHub data if available
    if downloaded.github_file:
        github_file = downloaded.github_file
        github_loader = GithubJsonLoader(str(github_file))
        github_data = github_loader.load()
        github_project = GitHubProjectTransformer(github_data, name="GitHub Project").transform()

    # Load Lizard CSV if available. Lizard emits absolute paths; the transformer
    # normalises against the first downloaded git repo's name so paths join with
    # the iglog-side repo prefix.
    lizard_metrics: list = []
    if downloaded.lizard_file:
        rows = LizardCsvLoader(str(downloaded.lizard_file)).load()
        prefix = downloaded.git_files[0][0] if downloaded.git_files else None
        lizard_metrics = LizardProjectTransformer(
            rows, repo_root=prefix, repo_prefix=prefix,
        ).transform()

    # JaFax CodeStructure (optional). The path_prefix mirrors the iglog repo
    # prefix so JaFax file paths join with git File.last_existing_name(); the
    # JaFax `name` strings are typically already prefixed with the project
    # segment (e.g. "zeppelin/...") so we leave them unchanged.
    code_structure = None
    if downloaded.jafax_file:
        code_structure = parse_codestructure(
            str(downloaded.jafax_file), CodeStructureFormat.JAFAX,
            path_prefix=None,
        )

    # DuDe duplication (optional). Same prefix story as JaFax: DuDe paths in
    # the observed Zeppelin run are already prefixed with the project segment,
    # so passing the iglog prefix is a no-op when paths already match — but
    # keeps a future ingest run that emits unprefixed paths in sync.
    duplication = None
    if downloaded.dude_external_file or downloaded.dude_internal_file:
        prefix = downloaded.git_files[0][0] if downloaded.git_files else None
        duplication = parse_dude(
            external_csv_path=(
                str(downloaded.dude_external_file)
                if downloaded.dude_external_file else None
            ),
            internal_json_path=(
                str(downloaded.dude_internal_file)
                if downloaded.dude_internal_file else None
            ),
            path_prefix=prefix,
        )

    # Insider code-smells (optional). Same prefix story as DuDe/JaFax: Insider
    # paths in the observed Zeppelin run are already prefixed with the project
    # segment, so passing the iglog prefix is a no-op when paths already match.
    quality_issues = None
    if downloaded.quality_issues_file:
        prefix = downloaded.git_files[0][0] if downloaded.git_files else None
        quality_issues = parse_insider(
            str(downloaded.quality_issues_file),
            path_prefix=prefix,
        )

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
    if lizard_metrics:
        stats.append(f"Lizard FileMetrics: {len(lizard_metrics)}")
    if code_structure is not None:
        stats.append(
            f"JaFax types/methods/refs: {len(code_structure.type_registry.all)}"
            f"/{len(code_structure.method_registry.all)}"
            f"/{len(code_structure.reference_registry.all)}"
        )
    if duplication is not None:
        stats.append(
            f"DuDe pairs/internal-files: {len(duplication.external_pairs)}"
            f"/{len(duplication.internal_by_file)}"
        )
    if quality_issues is not None:
        stats.append(
            f"Insider issues/files: {len(quality_issues.issues)}"
            f"/{len(quality_issues.file_paths)}"
        )

    logger.info(f"Project built successfully - {', '.join(stats)}")

    return {
        "git": git_project,
        "jira": jira_project,
        "github": github_project,
        "metrics": {"lizard": lizard_metrics},
        "code_structure": code_structure,
        "duplication": duplication,
        "quality_issues": quality_issues,
    }


def _compute_branch_ids(commit) -> None:
    """
    Compute branch ID for a single commit.
    Matches the original GitProjectTransformer._compute_branch_ids behavior.
    """
    parents = commit.parents
    if commit.is_merge_commit:
        commit.branch_id = parents[0].branch_id if parents else 0
    elif not parents or parents[0].is_split_commit:
        commit.branch_id = 1
    else:
        commit.branch_id = parents[0].branch_id


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


def process_project(project_id: str, user_id: str, project_name: str = "Project") -> bool:
    """
    Processes a single project: downloads files, builds graph, uploads pickle.

    Args:
        project_id: Project UUID to process
        user_id: User UUID (for storage path)
        project_name: Human-readable project name (used as GitProject name)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Step 1: Update status to 'processing'
        update_project_status(project_id, "processing")

        # Step 2: Download serialized files from Supabase Storage
        downloaded = download_serialized_files_from_supabase(project_id)

        # Step 3: Build graph from downloaded files
        graph_data = build_graph_from_downloaded_files(downloaded, project_name=project_name)

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
                process_project(project["id"], project["user_id"], project["name"])
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
