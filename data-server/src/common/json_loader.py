"""Shared JSON file loader for v2 reader_dto modules.

Moved here from the deleted ``src.common.loader`` (Chunk 19) to give the
two reader_dto modules (``src/github_miner/reader_dto/loader.py``,
``src/jira_miner/reader_dto/loader.py``) a clean v2-aligned home. The
legacy ``loader.py`` survived Chunk 10 only because the smart-merge port
hadn't landed yet; once smart-merge stopped depending on the
``src.common.*`` legacy modules we relocated this last surviving helper
under the more descriptive ``json_loader.py`` name.

Behavioural contract is unchanged — subclasses override :meth:`load` to
deserialise the parsed JSON into their domain DTO.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path


class BaseJsonLoader(ABC):
    """Read-and-validate a JSON file into a domain-specific DTO.

    Subclass + override :meth:`load`. The base class handles the disk
    read + ``json.loads`` and provides a ``FileNotFoundError`` with a
    clear message if the path is missing.
    """

    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def _read_json(self):
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")
        with self.file_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    @abstractmethod
    def load(self):
        """Read + validate the file. Subclass-specific return type."""


__all__ = ["BaseJsonLoader"]
