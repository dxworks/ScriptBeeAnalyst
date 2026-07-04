"""Postgres-backed persistence for smart-merge state.

Single-tenant local mode: no RLS, plain SQL against the shared connection pool
(:mod:`src.db`). The class name is retained as ``SupabaseSmartMergeRepository``
for call-site compatibility; there is no Supabase involved any more.
"""
from __future__ import annotations

from typing import Iterable, List

from src.db import connection, execute, query
from src.logger import get_logger
from src.smart_merge.identity import SourceIdentity
from src.smart_merge.repository import SmartMergeRepository
from src.smart_merge.types import RejectedPair, UserMapping

LOG = get_logger(__name__)


class SupabaseSmartMergeRepository(SmartMergeRepository):

    def get_rejected_similarities(self, project_id: str) -> List[RejectedPair]:
        rows = query(
            "select * from rejected_similarities where project_id = %s",
            (project_id,),
        )
        return [
            RejectedPair(
                project_id=str(row["project_id"]),
                first_source=row["first_source"],
                first_source_key=row["first_source_key"],
                second_source=row["second_source"],
                second_source_key=row["second_source_key"],
            )
            for row in rows
        ]

    def add_rejected_similarities(
        self,
        project_id: str,
        pairs: Iterable[RejectedPair],
    ) -> None:
        rows = []
        for pair in pairs:
            # Store in canonical order to match the DB unique index
            # (functional least()/greatest() index).
            key_a = f"{pair.first_source}:{pair.first_source_key}"
            key_b = f"{pair.second_source}:{pair.second_source_key}"
            if key_a > key_b:
                first_source, first_key = pair.second_source, pair.second_source_key
                second_source, second_key = pair.first_source, pair.first_source_key
            else:
                first_source, first_key = pair.first_source, pair.first_source_key
                second_source, second_key = pair.second_source, pair.second_source_key

            rows.append((project_id, first_source, first_key, second_source, second_key))

        if not rows:
            return

        # uq_rejected_similarity is a functional index over least()/greatest()
        # which ON CONFLICT can't target by column list, so filter out existing
        # pairs and insert only the new ones (rows are already canonical).
        existing = self.get_rejected_similarities(project_id)
        existing_keys = {
            (p.first_source, p.first_source_key, p.second_source, p.second_source_key)
            for p in existing
        }
        new_rows = [
            r for r in rows
            if (r[1], r[2], r[3], r[4]) not in existing_keys
        ]
        if new_rows:
            with connection() as conn:
                with conn.cursor() as cur:
                    cur.executemany(
                        "insert into rejected_similarities "
                        "(project_id, first_source, first_source_key, "
                        "second_source, second_source_key) "
                        "values (%s, %s, %s, %s, %s)",
                        new_rows,
                    )
        LOG.info(
            f"Persisted {len(new_rows)} new rejected similarity pairs "
            f"(skipped {len(rows) - len(new_rows)} duplicates) for project {project_id}"
        )

    def get_user_mappings(self, project_id: str) -> List[UserMapping]:
        users = query(
            "select * from unified_users where project_id = %s",
            (project_id,),
        )
        if not users:
            return []

        identity_rows = query(
            "select * from user_identity_mappings where project_id = %s",
            (project_id,),
        )

        mappings_by_user: dict[str, list] = {}
        for row in identity_rows:
            uid = str(row["unified_user_id"])
            mappings_by_user.setdefault(uid, []).append(row)

        result = []
        for user_row in users:
            uid = str(user_row["id"])
            rows = mappings_by_user.get(uid, [])
            identities = [
                SourceIdentity(
                    source=r["source"],
                    name=r["source_name"] or "unknown",
                    email=r["source_email"],
                    login=r["source_login"],
                    source_key=r["source_key"],
                )
                for r in rows
            ]
            result.append(UserMapping(
                unified_user_id=uid,
                display_name=user_row["display_name"],
                primary_email=user_row["primary_email"],
                identities=identities,
            ))

        return result

    def upsert_user_mapping(self, mapping: UserMapping, project_id: str) -> None:
        with connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into unified_users "
                    "(id, project_id, display_name, primary_email) "
                    "values (%s, %s, %s, %s) "
                    "on conflict (id) do update set "
                    "display_name = excluded.display_name, "
                    "primary_email = excluded.primary_email, "
                    "updated_at = now()",
                    (
                        mapping.unified_user_id,
                        project_id,
                        mapping.display_name,
                        mapping.primary_email,
                    ),
                )
                # Replace identity mappings for this user.
                cur.execute(
                    "delete from user_identity_mappings where unified_user_id = %s",
                    (mapping.unified_user_id,),
                )
                if mapping.identities:
                    cur.executemany(
                        "insert into user_identity_mappings "
                        "(unified_user_id, project_id, source, source_key, "
                        "source_name, source_email, source_login) "
                        "values (%s, %s, %s, %s, %s, %s, %s)",
                        [
                            (
                                mapping.unified_user_id,
                                project_id,
                                identity.source,
                                identity.source_key,
                                identity.name,
                                identity.email,
                                identity.login,
                            )
                            for identity in mapping.identities
                        ],
                    )

        LOG.info(
            f"Upserted unified user {mapping.unified_user_id} with "
            f"{len(mapping.identities)} identities for project {project_id}"
        )

    def delete_user_mapping(self, project_id: str, unified_user_id: str) -> None:
        # Identity mappings cascade-delete when the unified_user is deleted.
        execute(
            "delete from unified_users where id = %s",
            (unified_user_id,),
        )
        LOG.info(f"Deleted unified user {unified_user_id} from project {project_id}")

    def delete_all_user_mappings(self, project_id: str) -> int:
        count = execute(
            "delete from unified_users where project_id = %s",
            (project_id,),
        )
        LOG.info(f"Deleted {count} unified users from project {project_id}")
        return count

    def delete_all_rejected_similarities(self, project_id: str) -> int:
        count = execute(
            "delete from rejected_similarities where project_id = %s",
            (project_id,),
        )
        LOG.info(f"Deleted {count} rejected similarity pairs from project {project_id}")
        return count
