"""Postgres-backed CRUD for the ``project_filter_rules`` table.

Single-tenant local mode: no JWT, no user attribution, no RLS. Runs plain SQL
against the shared connection pool (:mod:`src.db`).
"""
from __future__ import annotations

import json
from typing import List

from src.db import execute, query
from src.filter_rules.models import FilterRule, RuleDSL
from src.logger import get_logger

LOG = get_logger(__name__)

_TABLE = "project_filter_rules"


def _row_to_rule(row: dict) -> FilterRule:
    dsl = row["dsl"]
    if isinstance(dsl, str):
        dsl = json.loads(dsl)
    return FilterRule(
        id=str(row["id"]),
        project_id=str(row["project_id"]),
        entity_kind=row["entity_kind"],
        name=row["name"],
        nl_description=row["nl_description"],
        dsl=RuleDSL.model_validate(dsl),
        created_at=row.get("created_at"),
    )


class FilterRuleRepository:
    """Thin wrapper over the ``project_filter_rules`` table."""

    def list_for_project(self, project_id: str) -> List[FilterRule]:
        rows = query(
            f"select * from {_TABLE} where project_id = %s "
            "order by created_at desc",
            (project_id,),
        )
        return [_row_to_rule(row) for row in rows]

    def create(
        self,
        project_id: str,
        dsl: RuleDSL,
        name: str,
        nl_description: str,
    ) -> FilterRule:
        rows = query(
            f"insert into {_TABLE} "
            "(project_id, entity_kind, name, nl_description, dsl) "
            "values (%s, %s, %s, %s, %s) returning *",
            (
                project_id,
                dsl.entity_kind.value,
                name,
                nl_description,
                json.dumps(dsl.model_dump(mode="json")),
            ),
        )
        if not rows:
            raise RuntimeError("insert returned no row")
        return _row_to_rule(rows[0])

    def delete(self, project_id: str, rule_id: str) -> bool:
        affected = execute(
            f"delete from {_TABLE} where id = %s and project_id = %s",
            (rule_id, project_id),
        )
        return affected > 0


__all__ = ["FilterRuleRepository"]
