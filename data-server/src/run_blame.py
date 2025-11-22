import subprocess
import re
from src.common.models import GitProject
from src.logger import get_logger

logger = get_logger(__name__)

# Precompiled regex for blame parsing
blame_line_pattern = re.compile(
    r'^\^?([0-9a-f]+)\s+\((.*?)\s*<(.*?)>.*?(\d+)\)'
)

def run_git_command(args, repo_path):
    """Run a git command inside the repo and return stdout as str."""
    result = subprocess.run(
        ["git", "-C", repo_path] + args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False  # capture raw bytes
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: {' '.join(args)}\n{result.stderr.decode(errors='replace')}"
        )
    # Decode stdout safely
    return result.stdout.decode("utf-8", errors="replace").strip()


def is_binary_file(filepath, repo_path):
    """Check if Git considers a file binary."""
    result = subprocess.run(
        ["git", "-C", repo_path, "check-attr", "binary", "--", filepath],
        stdout=subprocess.PIPE,
        text=True
    )
    return "binary: set" in result.stdout

def get_blame_data_for_file(filepath, repo_path):
    """Return blame data for a single file as a list of dicts."""
    if is_binary_file(filepath, repo_path):
        return []  # skip binary files

    blame_output = run_git_command(["blame", "--show-email", filepath], repo_path)
    results = []

    for line in blame_output.splitlines():
        match = blame_line_pattern.match(line)
        if match:
            sha, name, email, line_idx = match.groups()
            sha = sha.lstrip("^")
            results.append({
                "sha": sha,
                "email": email.strip(),
                "line_index": int(line_idx)
            })

    return results

def check_blame(project:GitProject, repo_path:str):
    match = 0
    total = 0

    files_with_not_found_lines = set()
    files_with_mismatches = set()
    skipped_because_its_binary = set()
    # Map file names from your project
    project_files = {str(f): f for f in project.file_registry.all}

    # Get repo files tracked by git
    files = run_git_command(["ls-files"], repo_path).splitlines()

    for idx, file_name in enumerate(files, start=1):
        git_file = project_files.get(file_name, None)
        if git_file is None:
            logger.warning(f"File not found in project graph: {file_name}")
            continue
        if git_file.is_binary:
            skipped_because_its_binary.add(file_name)
            continue

        progress_percent = (idx / len(files) * 100) if len(files) > 0 else 0.0
        logger.info(f"Checking file:({idx}/{len(files)}, {progress_percent:.2f}%)")

        blame_entries = get_blame_data_for_file(file_name, repo_path)
        git_info = git_file.annotated_lines()

        for entry in blame_entries:
            line_index = entry["line_index"]
            sha = entry["sha"]
            email = entry["email"]

            try:
                commit = git_info[line_index - 1]
                if commit.id.startswith(sha) and commit.author.git_id.email == email:
                    match += 1
                else:
                    if file_name not in files_with_mismatches:
                        files_with_mismatches.add(file_name)
                        logger.warning(f"Mismatch in {file_name}")
                total += 1
            except IndexError:
                if file_name not in files_with_not_found_lines:
                    files_with_not_found_lines.add(file_name)
                    logger.warning(f"Line not found from {file_name}")
                total += 1

    logger.info(f"Blame check for {repo_path} complete")
    logger.info(f"Matches: {match} / {total}")
    percentage = (match / total * 100) if total > 0 else 0
    logger.info(f"Match percentage: {percentage:.2f}%")

    logger.info(f"Files with not found lines: {len(files_with_not_found_lines)}")
    logger.info(f"Files with mismatches: {len(files_with_mismatches)}")
    logger.info(f"Skipped because its binary: {len(skipped_because_its_binary)}")
    return files_with_not_found_lines, files_with_mismatches, skipped_because_its_binary

