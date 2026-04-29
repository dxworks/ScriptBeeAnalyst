"""CSV writer for RelationFile — shape matches dx's source,target,strength."""
from __future__ import annotations

import csv
import io

from src.enrichment.models import RelationFile


def to_csv_bytes(rel_file: RelationFile) -> bytes:
    """Serialise a RelationFile to bytes.

    Output shape is the 3-column `source,target,strength` dx uses across its
    Edge-Bundle / Spring-Force / Layered renderers — drop-in compatible.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["source", "target", "strength"])
    for r in rel_file.relations:
        writer.writerow([r.source_id, r.target_id, r.strength])
    return buf.getvalue().encode("utf-8")
