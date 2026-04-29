"""Supabase-backed cache for the enrichment layer.

Mirrors `src/smart_merge/supabase_repository.py`. Stores the entire
Enrichments container as a single JSONB blob keyed by `project_id`. Cheap to
read on project load, cheap to recompute when invalidated.

The schema (table, indexes, RLS policies) lives in
``supabase/migrations/20260429000001_create_enrichments.sql`` — apply via
``./db-push.sh`` (or ``./db-reset.sh`` during development) before relying on
the cache. The data-server runs with the service-role key, so RLS is bypassed
for its writes — the policies are there so the web-ui can read directly later.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from src.enrichment.models import Enrichments
from src.logger import get_logger
from src.supabase_client import get_service_client

LOG = get_logger(__name__)


TABLE = "enrichments"


class SupabaseEnrichmentRepository:

    def save(self, project_id: str, enrichments: Enrichments) -> None:
        client = get_service_client()
        payload = enrichments.model_dump(mode="json")
        row = {
            "project_id": project_id,
            "payload": payload,
            "generated_at": enrichments.generated_at.astimezone(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(TABLE).upsert(row, on_conflict="project_id").execute()
        LOG.info(
            "Persisted enrichments for project %s (tags=%d, relations=%d, overviews=%d)",
            project_id,
            len(enrichments.tags_by_entity),
            len(enrichments.relations),
            len(enrichments.overviews),
        )

    def load(self, project_id: str) -> Optional[Enrichments]:
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
            return Enrichments.model_validate(payload)
        except Exception as e:
            LOG.warning("Failed to deserialize cached enrichments for %s: %s", project_id, e)
            return None

    def delete(self, project_id: str) -> None:
        client = get_service_client()
        client.table(TABLE).delete().eq("project_id", project_id).execute()
        LOG.info("Deleted cached enrichments for project %s", project_id)
