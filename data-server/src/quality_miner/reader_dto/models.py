"""DTOs matching the on-disk Insider code-smells JSON.

Implements §4 of communication/B4_sonar_insider/index_step_general.md and
§2 of index_step_data_format.md (4-field schema; nothing else exists in the
file).
"""
from __future__ import annotations

from pydantic import BaseModel


class InsiderCodeSmellRowDTO(BaseModel):
    """One entry of `<projectId>-code_smells.json`.

    The Insider file is a bare JSON array of these objects; no wrapper, no
    metadata header. All four fields are always present in the observed
    Zeppelin run; the loader still defends against missing/empty values per
    row so a malformed entry does not abort the whole ingest.
    """
    name: str       # rule name (human-readable, contains spaces)
    category: str   # rule family / bucket
    file: str       # repo-relative POSIX path (already prefixed in the Zeppelin run)
    value: int      # occurrence count of the rule inside the file
