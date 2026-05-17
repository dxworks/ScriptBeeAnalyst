"""
Supabase-backed persistence for smart merge state.
Uses service key client (bypasses RLS) since endpoints are called from the data-server.
"""
from __future__ import annotations

from typing import Iterable, List

from src.smart_merge.identity import SourceIdentity
from src.logger import get_logger
from src.smart_merge.repository import SmartMergeRepository
from src.smart_merge.types import RejectedPair, UserMapping
from src.supabase_client import get_service_client

LOG = get_logger(__name__)


class SupabaseSmartMergeRepository(SmartMergeRepository):

    def get_rejected_similarities(self, project_id: str) -> List[RejectedPair]:
        client = get_service_client()
        response = (
            client.table("rejected_similarities")
            .select("*")
            .eq("project_id", project_id)
            .execute()
        )
        return [
            RejectedPair(
                project_id=row["project_id"],
                first_source=row["first_source"],
                first_source_key=row["first_source_key"],
                second_source=row["second_source"],
                second_source_key=row["second_source_key"],
            )
            for row in response.data
        ]

    def add_rejected_similarities(
        self,
        project_id: str,
        pairs: Iterable[RejectedPair],
    ) -> None:
        client = get_service_client()
        rows = []
        for pair in pairs:
            # Store in canonical order to match the DB unique index
            key_a = f"{pair.first_source}:{pair.first_source_key}"
            key_b = f"{pair.second_source}:{pair.second_source_key}"
            if key_a > key_b:
                first_source, first_key = pair.second_source, pair.second_source_key
                second_source, second_key = pair.first_source, pair.first_source_key
            else:
                first_source, first_key = pair.first_source, pair.first_source_key
                second_source, second_key = pair.second_source, pair.second_source_key

            rows.append({
                "project_id": project_id,
                "first_source": first_source,
                "first_source_key": first_key,
                "second_source": second_source,
                "second_source_key": second_key,
            })

        if not rows:
            return

        # The DB's uq_rejected_similarity is a functional index over
        # least()/greatest(), which PostgREST can't target via on_conflict.
        # Rows are already in canonical order, so filter out existing pairs
        # and insert only the new ones.
        existing = self.get_rejected_similarities(project_id)
        existing_keys = {
            (p.first_source, p.first_source_key, p.second_source, p.second_source_key)
            for p in existing
        }
        new_rows = [
            r for r in rows
            if (r["first_source"], r["first_source_key"], r["second_source"], r["second_source_key"])
            not in existing_keys
        ]
        if new_rows:
            client.table("rejected_similarities").insert(new_rows).execute()
        LOG.info(
            f"Persisted {len(new_rows)} new rejected similarity pairs "
            f"(skipped {len(rows) - len(new_rows)} duplicates) for project {project_id}"
        )

    def get_user_mappings(self, project_id: str) -> List[UserMapping]:
        client = get_service_client()

        # Get all unified users for this project
        users_response = (
            client.table("unified_users")
            .select("*")
            .eq("project_id", project_id)
            .execute()
        )

        if not users_response.data:
            return []

        # Get all identity mappings for this project
        mappings_response = (
            client.table("user_identity_mappings")
            .select("*")
            .eq("project_id", project_id)
            .execute()
        )

        # Group mappings by unified_user_id
        mappings_by_user: dict[str, list] = {}
        for row in mappings_response.data:
            uid = row["unified_user_id"]
            mappings_by_user.setdefault(uid, []).append(row)

        result = []
        for user_row in users_response.data:
            uid = user_row["id"]
            identity_rows = mappings_by_user.get(uid, [])
            identities = [
                SourceIdentity(
                    source=r["source"],
                    name=r["source_name"] or "unknown",
                    email=r["source_email"],
                    login=r["source_login"],
                    source_key=r["source_key"],
                )
                for r in identity_rows
            ]
            result.append(UserMapping(
                unified_user_id=uid,
                display_name=user_row["display_name"],
                primary_email=user_row["primary_email"],
                identities=identities,
            ))

        return result

    def upsert_user_mapping(self, mapping: UserMapping, project_id: str) -> None:
        client = get_service_client()

        # Upsert the unified user
        client.table("unified_users").upsert({
            "id": mapping.unified_user_id,
            "project_id": project_id,
            "display_name": mapping.display_name,
            "primary_email": mapping.primary_email,
        }).execute()

        # Delete existing identity mappings for this user, then re-insert
        client.table("user_identity_mappings").delete().eq(
            "unified_user_id", mapping.unified_user_id
        ).execute()

        if mapping.identities:
            rows = [
                {
                    "unified_user_id": mapping.unified_user_id,
                    "project_id": project_id,
                    "source": identity.source,
                    "source_key": identity.source_key,
                    "source_name": identity.name,
                    "source_email": identity.email,
                    "source_login": identity.login,
                }
                for identity in mapping.identities
            ]
            client.table("user_identity_mappings").insert(rows).execute()

        LOG.info(
            f"Upserted unified user {mapping.unified_user_id} with "
            f"{len(mapping.identities)} identities for project {project_id}"
        )

    def delete_user_mapping(self, project_id: str, unified_user_id: str) -> None:
        client = get_service_client()
        # Identity mappings are cascade-deleted when unified_user is deleted
        client.table("unified_users").delete().eq("id", unified_user_id).execute()
        LOG.info(f"Deleted unified user {unified_user_id} from project {project_id}")

    def delete_all_user_mappings(self, project_id: str) -> int:
        client = get_service_client()
        response = (
            client.table("unified_users")
            .delete()
            .eq("project_id", project_id)
            .execute()
        )
        count = len(response.data or [])
        LOG.info(f"Deleted {count} unified users from project {project_id}")
        return count

    def delete_all_rejected_similarities(self, project_id: str) -> int:
        client = get_service_client()
        response = (
            client.table("rejected_similarities")
            .delete()
            .eq("project_id", project_id)
            .execute()
        )
        count = len(response.data or [])
        LOG.info(f"Deleted {count} rejected similarity pairs from project {project_id}")
        return count
