# File: /src/inspector_git/utils/constants.py
from __future__ import annotations

from pathlib import Path
import tempfile
from datetime import datetime
from typing import Final


COMMIT_DATE_FORMAT: Final[str] = "%a %b %d %H:%M:%S %Y %z"

# Helperi utili pentru parsare/formatare date în același format:
def parse_commit_date(date_str: str) -> datetime:
    return datetime.strptime(date_str, COMMIT_DATE_FORMAT)


def format_commit_date(dt: datetime) -> str:
    return dt.strftime(COMMIT_DATE_FORMAT)


DEV_NULL: Final[str] = "dev/null"
APP_FOLDER_NAME: Final[str] = ".inspectorgit"

USER_HOME_PATH: Final[Path] = Path.home()
APP_FOLDER_PATH: Final[Path] = USER_HOME_PATH / APP_FOLDER_NAME
SYSTEMS_FOLDER_PATH: Final[Path] = APP_FOLDER_PATH / "systems"
PROPERTY_FILE_PATH: Final[Path] = APP_FOLDER_PATH / "inspector.properties"

TMP_FOLDER: Final[Path] = Path(tempfile.gettempdir()) / "inspectorGit"

CHRONOS_SETTINGS_FILE_NAME: Final[str] = "chronos-settings.json"

__all__ = [
    "COMMIT_DATE_FORMAT",
    "parse_commit_date",
    "format_commit_date",
    "DEV_NULL",
    "APP_FOLDER_NAME",
    "USER_HOME_PATH",
    "APP_FOLDER_PATH",
    "SYSTEMS_FOLDER_PATH",
    "PROPERTY_FILE_PATH",
    "TMP_FOLDER",
    "CHRONOS_SETTINGS_FILE_NAME",
]
