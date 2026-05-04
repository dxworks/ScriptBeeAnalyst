"""Supabase-backed cache for Lizard FileMetric rollups.

Implements §6 of communication/B1_lizard/index_step_general.md.
Mirrors src/enrichment/repository.py — one JSONB payload per project.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from src.common.lizard_models import FileMetric
from src.logger import get_logger
from src.supabase_client import get_service_client

LOG = get_logger(__name__)

TABLE = "file_metrics"


class SupabaseFileMetricsRepository:

    def save(self, project_id: str, metrics: List[FileMetric], source: str = "lizard") -> None:
        client = get_service_client()
        payload = [m.model_dump(mode="json") for m in metrics]
        row = {
            "project_id": project_id,
            "payload": payload,
            "source": source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(TABLE).upsert(row, on_conflict="project_id").execute()
        LOG.info(
            "Persisted %d FileMetric rows for project %s (source=%s)",
            len(metrics), project_id, source,
        )

    def load(self, project_id: str) -> Optional[List[FileMetric]]:
        client = get_service_client()
        response = (
            client.table(TABLE)
            .select("payload")
            .eq("project_id", project_id)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return None
        payload = rows[0].get("payload")
        if not payload:
            return None
        try:
            return [FileMetric.model_validate(item) for item in payload]
        except Exception as e:
            LOG.warning("Failed to deserialize cached file_metrics for %s: %s", project_id, e)
            return None

    def delete(self, project_id: str) -> None:
        client = get_service_client()
        client.table(TABLE).delete().eq("project_id", project_id).execute()
        LOG.info("Deleted cached file_metrics for project %s", project_id)
