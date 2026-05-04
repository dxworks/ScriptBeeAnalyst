"""Supabase-backed cache for the QualityIssues blob.

Implements §6 of communication/B4_sonar_insider/index_step_general.md.
Mirrors `src/dude_miner/repository.py` — one JSONB payload per project.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.common.quality_models import QualityIssues
from src.logger import get_logger
from src.supabase_client import get_service_client

LOG = get_logger(__name__)

TABLE = "quality_issues"


class SupabaseQualityIssuesRepository:

    def save(self, project_id: str, quality_issues: QualityIssues) -> None:
        client = get_service_client()
        payload = quality_issues.model_dump(mode="json")
        row = {
            "project_id": project_id,
            "payload": payload,
            "source": quality_issues.source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(TABLE).upsert(row, on_conflict="project_id").execute()
        LOG.info(
            "Persisted QualityIssues for project %s (issues=%d source=%s)",
            project_id, len(quality_issues.issues), quality_issues.source,
        )

    def load(self, project_id: str) -> Optional[QualityIssues]:
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
            return QualityIssues.model_validate(payload)
        except Exception as e:
            LOG.warning(
                "Failed to deserialize cached quality_issues for %s: %s",
                project_id, e,
            )
            return None

    def delete(self, project_id: str) -> None:
        client = get_service_client()
        client.table(TABLE).delete().eq("project_id", project_id).execute()
        LOG.info("Deleted cached quality_issues for project %s", project_id)
