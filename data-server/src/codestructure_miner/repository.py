"""Supabase-backed cache for the JaFax CodeStructureProject.

Implements §6 of communication/B2_codeframe/index_step_general.md.
Mirrors `src/lizard_miner/repository.py` — one JSONB payload per project.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.common.codestructure_models import CodeStructureProject
from src.logger import get_logger
from src.supabase_client import get_service_client

LOG = get_logger(__name__)

TABLE = "code_structure"


class SupabaseCodeStructureRepository:

    def save(self, project_id: str, project: CodeStructureProject) -> None:
        client = get_service_client()
        payload = project.model_dump(mode="json")
        row = {
            "project_id": project_id,
            "payload": payload,
            "source": project.source,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(TABLE).upsert(row, on_conflict="project_id").execute()
        LOG.info(
            "Persisted CodeStructure for project %s "
            "(types=%d methods=%d fields=%d refs=%d source=%s)",
            project_id,
            len(project.type_registry.all),
            len(project.method_registry.all),
            len(project.field_registry.all),
            len(project.reference_registry.all),
            project.source,
        )

    def load(self, project_id: str) -> Optional[CodeStructureProject]:
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
            return CodeStructureProject.model_validate(payload)
        except Exception as e:
            LOG.warning(
                "Failed to deserialize cached code_structure for %s: %s",
                project_id, e,
            )
            return None

    def delete(self, project_id: str) -> None:
        client = get_service_client()
        client.table(TABLE).delete().eq("project_id", project_id).execute()
        LOG.info("Deleted cached code_structure for project %s", project_id)
