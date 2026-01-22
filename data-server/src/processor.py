#!/usr/bin/env python3
"""
Graph Processor - Part 1 of data-server split

Builds project graphs from serialized data files and saves them as pickle files.
In Phase 1: Reads from local test-input/ directory, saves to local /tmp/pickles/

Future phases will add:
- Reading from Supabase Storage
- Uploading pickles to Supabase
- Database polling for automatic processing

Usage:
    cd data-server
    python -m src.processor
"""

import pickle
import sys
from pathlib import Path
from typing import Dict

# Add parent directory to path if running as script
if __name__ == "__main__" and __package__ is None:
    sys.path.insert(0, str(Path(__file__).parent.parent))

from supabase import create_client, Client
from src.config import SUPABASE_URL, SUPABASE_SERVICE_KEY, GRAPH_USER_ID, GRAPH_PROJECT_ID
from src.inspector_git.reader.iglog.readers.ig_log_reader import IGLogReader
from src.inspector_git.linker.transformers import GitProjectTransformer
from src.jira_miner.reader_dto.loader import JiraJsonLoader
from src.jira_miner.linker.transformers import JiraProjectTransformer
from src.github_miner.reader_dto.loader import GithubJsonLoader
from src.github_miner.linker.transformers import GitHubProjectTransformer
from src.common.project_linkers import ProjectLinker


def build_graph_from_local_files() -> Dict:
    """
    Builds the project graph from local test-input files and links them together.

    Returns:
        Dict with 'git', 'jira', 'github' keys containing project objects
    """
    base_path = Path(__file__).parent.parent / "test-input"

    print(f"📂 Loading files from: {base_path}")

    # InspectorGit
    iglog_file = base_path / "inspector-git" / "zeppelin.iglog"
    print(f"  - Loading Git data from {iglog_file.name}...")
    with open(iglog_file, "r", encoding="utf-8") as f:
        git_log_dto = IGLogReader().read(f)

    git_project = GitProjectTransformer(
        git_log_dto,
        name=iglog_file.stem,
        compute_annotated_lines=False,  # no blame
    ).transform()

    # Jira
    jira_file = base_path / "jira-miner" / "ZEPPELIN-detailed-issues.json"
    print(f"  - Loading JIRA data from {jira_file.name}...")
    jira_loader = JiraJsonLoader(str(jira_file))
    jira_data = jira_loader.load()
    jira_project = JiraProjectTransformer(jira_data, name="Jira Project").transform()

    # GitHub
    github_file = base_path / "github-miner" / "githubProject.json"
    print(f"  - Loading GitHub data from {github_file.name}...")
    github_loader = GithubJsonLoader(str(github_file))
    github_data = github_loader.load()
    github_project = GitHubProjectTransformer(github_data, name="GitHub Project").transform()

    # Link projects together
    print("🔗 Linking projects...")
    ProjectLinker.link_projects(github_project, jira_project, jira_data)
    ProjectLinker.link_projects(jira_project, git_project)
    ProjectLinker.link_projects(github_project, git_project)

    print("✅ Graph built successfully!")
    print(f"   - Git commits: {len(git_project.git_commit_registry.all)}")
    print(f"   - JIRA issues: {len(jira_project.issue_registry.all)}")
    print(f"   - GitHub PRs: {len(github_project.pull_request_registry.all)}")

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
    print(f"\n💾 Saving pickle to: {output_path}")

    # Ensure parent directory exists
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize with highest protocol for performance
    with open(output_path, "wb") as f:
        pickle.dump(graph_data, f, protocol=pickle.HIGHEST_PROTOCOL)

    # Get file size
    size_bytes = output_path.stat().st_size
    size_mb = size_bytes / (1024 * 1024)

    print(f"✅ Pickle saved successfully!")
    print(f"   - Path: {output_path}")
    print(f"   - Size: {size_mb:.2f} MB ({size_bytes:,} bytes)")


def upload_pickle_to_supabase(pickle_path: Path, user_id: str, project_id: str) -> None:
    """
    Uploads pickle file to Supabase Storage.

    Args:
        pickle_path: Path to local pickle file
        user_id: User UUID for storage path
        project_id: Project UUID for storage path
    """
    print(f"\n☁️  Uploading pickle to Supabase Storage...")

    if not user_id or not project_id:
        raise ValueError("GRAPH_USER_ID and GRAPH_PROJECT_ID must be set in .env file")

    # Initialize Supabase client with service key (bypasses RLS)
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    # Storage path: {user_id}/{project_id}/graph.pkl
    storage_path = f"{user_id}/{project_id}/graph.pkl"

    print(f"   - Target path: {storage_path}")
    print(f"   - Bucket: project-graphs")

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
            print(f"   - File exists, updating...")
            supabase.storage.from_("project-graphs").update(
                path=storage_path,
                file=pickle_bytes,
                file_options={"content-type": "application/octet-stream"}
            )
        else:
            raise

    size_mb = len(pickle_bytes) / (1024 * 1024)
    print(f"✅ Pickle uploaded successfully!")
    print(f"   - Storage path: {storage_path}")
    print(f"   - Size: {size_mb:.2f} MB")


def main():
    """Main entry point for the processor."""
    print("\n" + "="*70)
    print("🔧 Graph Processor - Phase 3: Local Files → Supabase Storage")
    print("="*70)
    print()

    try:
        # Step 1: Build graph from local test files
        graph_data = build_graph_from_local_files()

        # Step 2: Save to local filesystem
        output_path = Path("/tmp/pickles/graph.pkl")
        save_pickle_to_disk(graph_data, output_path)

        # Step 3: Upload to Supabase Storage
        upload_pickle_to_supabase(output_path, GRAPH_USER_ID, GRAPH_PROJECT_ID)

        print()
        print("="*70)
        print("🎉 Processing complete!")
        print("="*70)
        print()

        return 0

    except Exception as e:
        print()
        print("="*70)
        print("❌ ERROR: Processing failed!")
        print("="*70)
        print(f"\n{type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
