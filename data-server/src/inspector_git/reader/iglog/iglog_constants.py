from __future__ import annotations
from typing import Final


class IGLogConstants:
    commit_id_prefix: Final[str] = "ig#"
    message_prefix: Final[str] = "$"
    git_log_message_end: Final[str] = "#{Glme}"
    change_prefix: Final[str] = "#"
    hunk_prefix_line: Final[str] = "@"
    git_log_diff_line_start: Final[str] = "diff --git"

    # Prevent instantiation
    def __new__(cls, *args, **kwargs):
        raise TypeError(f"{cls.__name__} is a constants container and cannot be instantiated.")
