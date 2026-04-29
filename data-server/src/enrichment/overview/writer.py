"""CSV writer for OverviewTable — flat `entity_id + {col}_lifetime|recent|trend`."""
from __future__ import annotations

import csv
import io

from src.enrichment.models import OverviewTable


def _fmt(value):
    return "" if value is None else value


def to_csv_bytes(table: OverviewTable) -> bytes:
    """Serialise an OverviewTable to UTF-8 bytes.

    Each logical column expands to three CSV columns: `{col}_lifetime`,
    `{col}_recent`, `{col}_trend_percent`. Matches dx's export shape so the
    file opens cleanly in any spreadsheet.
    """
    buf = io.StringIO()
    writer = csv.writer(buf)

    header = ["entity_id"]
    for col in table.columns:
        header.extend([f"{col}_lifetime", f"{col}_recent", f"{col}_trend_percent"])
    writer.writerow(header)

    for row in table.rows:
        record = [row.entity_id]
        for col in table.columns:
            cell = row.cells.get(col)
            if cell is None:
                record.extend(["", "", ""])
            else:
                record.extend([
                    _fmt(cell.lifetime_value),
                    _fmt(cell.recent_value),
                    _fmt(cell.trend_percent),
                ])
        writer.writerow(record)

    return buf.getvalue().encode("utf-8")
