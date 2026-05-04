"""JaFax layout JSON loader.

Implements §4 of communication/B2_codeframe/index_step_general.md.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from src.logger import get_logger

LOG = get_logger(__name__)


class JaFaxLayoutLoader:
    """Read a JaFax `*-layout.json` file as a flat list of entity dicts.

    The layout JSON is small enough (~13 MiB / ~51 k entries on Zeppelin) that
    a single `json.load` is acceptable; if larger projects are anticipated the
    same shape would still be straightforward to stream with `ijson`.
    """

    def __init__(self, json_path: str):
        self.json_path = Path(json_path)

    def load(self) -> List[Dict[str, Any]]:
        if not self.json_path.exists():
            raise FileNotFoundError(f"JaFax layout JSON not found: {self.json_path}")
        with self.json_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            raise ValueError(
                f"JaFax layout JSON must be a top-level list at {self.json_path}; "
                f"got {type(data).__name__}"
            )
        LOG.info("Loaded %d JaFax entities from %s", len(data), self.json_path)
        return data
