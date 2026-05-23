"""Supabase-backed CRUD for the ``project_filter_rules`` table.

Reads use the service-role client (the in-memory cache loads all rules for
a project in one shot). Writes use a user-scoped client when an
``Authorization: Bearer`` JWT was forwarded so RLS picks up ``auth.uid()``
for the ``user_id`` column; without a JWT we fall through to the
service-role client and persist ``user_id = NULL`` (dev/standalone mode
— see ``extension_1.md`` §"single-tenant dev mode"). The migration's
``user_id`` column is nullable so a dev-mode insert succeeds.
"""
from __future__ import annotations

from typing import List, Optional

from src.filter_rules.models import FilterRule, RuleDSL
from src.logger import get_logger
from src.supabase_client import get_service_client, get_user_client

LOG = get_logger(__name__)

_TABLE = "project_filter_rules"


def _row_to_rule(row: dict) -> FilterRule:
    return FilterRule(
        id=row["id"],
        project_id=row["project_id"],
        user_id=row.get("user_id"),
        entity_kind=row["entity_kind"],
        name=row["name"],
        nl_description=row["nl_description"],
        dsl=RuleDSL.model_validate(row["dsl"]),
        created_at=row.get("created_at"),
    )


class FilterRuleRepository:
    """Thin wrapper over the Supabase ``project_filter_rules`` table."""

    def list_for_project(self, project_id: str) -> List[FilterRule]:
        client = get_service_client()
        response = (
            client.table(_TABLE)
            .select("*")
            .eq("project_id", project_id)
            .order("created_at")
            .execute()
        )
        return [_row_to_rule(row) for row in (response.data or [])]

    def create(
        self,
        project_id: str,
        jwt: Optional[str],
        dsl: RuleDSL,
        name: str,
        nl_description: str,
    ) -> FilterRule:
        payload: dict = {
            "project_id": project_id,
            "entity_kind": dsl.entity_kind.value,
            "name": name,
            "nl_description": nl_description,
            "dsl": dsl.model_dump(mode="json"),
        }

        if jwt:
            client = get_user_client(jwt)
            # RLS infers user_id via auth.uid(); we still set it explicitly
            # so the WITH CHECK policy passes deterministically.
            user_id = _resolve_user_id_from_jwt(jwt)
            if user_id is not None:
                payload["user_id"] = user_id
        else:
            # Dev-mode: no JWT, so we cannot attribute the row to a real
            # user. Write user_id = NULL rather than impersonating the
            # project owner — mis-attribution silently breaks RLS in prod.
            client = get_service_client()

        response = client.table(_TABLE).insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError("insert returned no row")
        return _row_to_rule(rows[0])

    def delete(self, project_id: str, jwt: Optional[str], rule_id: str) -> bool:
        client = get_user_client(jwt) if jwt else get_service_client()
        response = (
            client.table(_TABLE)
            .delete()
            .eq("id", rule_id)
            .eq("project_id", project_id)
            .execute()
        )
        return bool(response.data)


def _resolve_user_id_from_jwt(jwt: str) -> Optional[str]:
    """Decode the ``sub`` claim from a Supabase access JWT.

    No signature verification — this is best-effort to populate the
    ``user_id`` column. RLS is the actual guard.
    """
    try:
        from jose import jwt as jose_jwt
        claims = jose_jwt.get_unverified_claims(jwt)
        sub = claims.get("sub")
        return sub if isinstance(sub, str) else None
    except Exception as exc:  # noqa: BLE001
        LOG.warning(f"could not decode JWT sub claim: {exc}")
        return None


__all__ = ["FilterRuleRepository"]
