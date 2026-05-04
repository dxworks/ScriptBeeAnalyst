"""Supabase-backed cache for the DuDe Duplication blob.

Implements §6 of communication/B3_dude/index_step_general.md.
Mirrors `src/codestructure_miner/repository.py` — one JSONB payload per project.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.common.duplication_models import Duplication
from src.logger import get_logger
from src.supabase_client import get_service_client

LOG = get_logger(__name__)

TABLE = "duplication"


class SupabaseDuplicationRepository:

    def save(self, project_id: str, duplication: Duplication) -> None:
        client = get_service_client()
        payload = duplication.model_dump(mode="json")
        row = {
            "project_id": project_id,
            "payload": payload,
            "source": duplication.source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(TABLE).upsert(row, on_conflict="project_id").execute()
        LOG.info(
            "Persisted Duplication for project %s "
            "(external_pairs=%d internal_files=%d source=%s)",
            project_id,
            len(duplication.external_pairs),
            len(duplication.internal_by_file),
            duplication.source,
        )

    def load(self, project_id: str) -> Optional[Duplication]:
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
            return Duplication.model_validate(payload)
        except Exception as e:
            LOG.warning(
                "Failed to deserialize cached duplication for %s: %s",
                project_id, e,
            )
            return None

    def delete(self, project_id: str) -> None:
        client = get_service_client()
        client.table(TABLE).delete().eq("project_id", project_id).execute()
        LOG.info("Deleted cached duplication for project %s", project_id)
