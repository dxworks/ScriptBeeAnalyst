import subprocess
import logging
from pathlib import Path
from urllib.parse import quote as url_encode

from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.utils.os_utils import OsUtils


class GitClient:
    LOG = logging.getLogger("GitClient")
    git = "git"
    context_threshold = "-U1"
    rename_detection_threshold = "-M60%"
    encoding_utf8 = "--encoding=UTF8"

    git_log_command = (
        f"log {rename_detection_threshold} -m {context_threshold} {encoding_utf8} "
        f'--format="{IGLogConstants.commit_id_prefix}%H%n%P%n%an%n%ae%n%ad%n%cn%n%ce%n%cd %n%s%n%b%n'
        f"{IGLogConstants.git_log_message_end}%n\" --reverse"
    )

    simple_log_command_win = (
        f"log {encoding_utf8} --no-merges --find-renames --numstat --raw "
        f'--format="commit:%H%nauthor:%an%nemail:%ae%ndate:%cD %nmessage:%n%s%n%b%nnumstat:"'
    )
    simple_log_command_unix = (
        f"log {encoding_utf8} --no-merges --find-renames --numstat --raw "
        f'--format="commit:%H%nauthor:%an%nemail:%ae%ndate:%cD%nmessage:%n%s%n%b%nnumstat:"'
    )
    git_affected_files_command = "log -M60% -m -1 --name-only --pretty=format:"
    git_commit_links_command = (
        f"log -m {encoding_utf8} --format=\"{IGLogConstants.commit_id_prefix}%H%n%P\" --reverse"
    )
    git_count_commits_command = "rev-list HEAD --count"
    git_diff_command = f"diff {rename_detection_threshold} {context_threshold}"
    git_diff_file_names_command = f"diff {rename_detection_threshold} --name-only"
    set_rename_limit_command = "config --global diff.renameLimit"
    git_blame_command = "blame -l"
    git_branch_command = "branch"
    git_clone_command = "clone"
    git_checkout_command = "checkout"

    def __init__(self, path: Path):
        self.path = path
        self.process_cwd = str(path)

    @property
    def branch(self) -> str | None:
        lines = self.run_git_command(self.git_branch_command)
        if lines:
            for line in lines:
                if line.startswith("* "):
                    return line[2:]
        return None

    def get_logs(self) -> list[str]:
        return self.run_git_command(self.git_log_command) or []

    def get_simple_log(self, result_log_file: Path) -> Path:
        print(f"Creating Git log for {Path(self.process_cwd).resolve()} in {result_log_file.resolve()}")
        log_command = self.simple_log_command_unix if OsUtils.is_unix() else self.simple_log_command_win
        self.run_git_command(f"{log_command} > \"{result_log_file}\"")
        print(f"DONE! Exported Git log for {Path(self.process_cwd).resolve()} to {result_log_file.resolve()}")
        return result_log_file

    def get_commit_count(self) -> int:
        lines = self.run_git_command(self.git_count_commits_command) or ["0"]
        return int(lines[0])

    def set_rename_limit(self, limit: int = 5000):
        return self.run_git_command(f"{self.set_rename_limit_command} {limit}")

    def get_commit_links(self) -> list[str]:
        return self.run_git_command(self.git_commit_links_command) or []

    def get_n_commit_logs(self, n: int, skip: int = 0) -> list[str]:
        return self.run_git_command(f"{self.git_log_command} --max-count={n} --skip={skip}") or []

    def get_n_commit_logs_input_stream(self, n: int, skip: int = 0):
        process = self.get_process_for_command(f"{self.git_log_command} --max-count={n} --skip={skip}")
        return process.stdout

    def diff(self, parent: str, revision: str, file: str) -> list[str]:
        return self.run_git_command(f"{self.git_diff_command} {parent} {revision} -- {file}") or []

    def diff_file_names(self, parent: str, revision: str) -> list[str]:
        return self.run_git_command(f"{self.git_diff_file_names_command} {revision}..{parent}") or []

    def blame(self, revision: str, file: str) -> list[str] | None:
        return self.run_git_command(f"{self.git_blame_command} {file} {revision}")

    def affected_files(self, revision: str) -> list[str]:
        return self.run_git_command(f"{self.git_affected_files_command} {revision}") or []

    def clone(self, repo_url: str, username: str, password: str) -> list[str] | None:
        return self.run_git_command(f"{self.git_clone_command} {self.build_authenticated_url(repo_url, username, password)}")

    def build_authenticated_url(self, repo_url: str, username: str, password: str) -> str:
        return repo_url.replace(
            "//[^@].*@", f"//{username}:{url_encode(password)}@"
        )

    def run_git_command(self, args: str) -> list[str] | None:
        process = self.get_process_for_command(args)
        stdout, stderr = process.communicate()
        if process.returncode == 0:
            self.LOG.debug("Command completed")
            return stdout.decode().splitlines()
        else:
            self.LOG.error(f"Command completed with errors:\n {stderr.decode()}")
            return None

    def get_process_for_command(self, args: str) -> subprocess.Popen:
        command = f"{self.git} {args}"
        self.LOG.debug(f"Running command: {command}")
        return subprocess.Popen(
            [OsUtils.command_interpreter_name(), OsUtils.interpreter_arg(), command],
            cwd=self.process_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

    def checkout(self, branch: str):
        return self.run_git_command(f"{self.git_checkout_command} {branch}")


