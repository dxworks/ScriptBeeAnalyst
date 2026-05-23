"""Predicate resolvers and excluded-id computation for one project.

Two surfaces:

* :func:`validate_dsl` — called at POST time. Rejects unsupported
  entity_kind / field / op combinations with a clear message so the agent
  can apologise and ask the user to rephrase.
* :func:`compute_excluded_ids` — called from
  :class:`~src.filter_rules.store.FilterRuleStore` on every refresh and
  is the only consumer of the resolver map at query time.

The resolver map is intentionally small — only the v1 fields listed in
``filter_files.md`` §"Critical files to modify or create". Adding a new
field is a one-line entry in ``_RESOLVERS`` plus an entry in
``_FIELD_OPS``.
"""
from __future__ import annotations

import re
from typing import Callable, Dict, Iterable, List, Set, Tuple

from src.common.kernel import EntityKind, EntityRef
from src.common.kernel.graph import Graph
from src.filter_rules.models import AllOf, FilterRule, Predicate, RuleDSL


# (entity_kind, field) -> function(graph) -> iterable[(entity_id, comparable_value)]
ResolverFn = Callable[[Graph], Iterable[Tuple[str, object]]]


def _file_loc_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    """Yield ``(file_id, sum_nloc)`` per FileMetric where ``metric_name == "sum_nloc"``.

    ``File.loc`` is not a direct attribute on :class:`File`; it lives in
    :class:`FileMetric` and we filter by ``metric_name="sum_nloc"`` per
    :class:`MCPSandboxView.list_file_metrics`. The yielded id is the
    file's registry id so the caller can union directly into the FILE
    excluded set.
    """
    for fm in graph.file_metrics:
        if fm.metric_name != "sum_nloc":
            continue
        yield fm.file_ref.id, fm.value


def _file_extension_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    for f in graph.files:
        yield f.id, f.extension


def _file_path_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    for f in graph.files:
        yield f.id, f.path


def _commit_author_email_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    accounts = graph.git_accounts
    for c in graph.commits:
        author = accounts.get(c.author_ref.id) if c.author_ref else None
        yield c.id, (author.email if author is not None else None)


def _commit_message_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    for c in graph.commits:
        yield c.id, c.message


def _issue_status_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    statuses = graph.issue_statuses
    for issue in graph.issues:
        status = statuses.get(issue.status_ref.id) if issue.status_ref else None
        yield issue.id, (status.name if status is not None else None)


def _issue_type_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    types = graph.issue_types
    for issue in graph.issues:
        itype = types.get(issue.type_ref.id) if issue.type_ref else None
        yield issue.id, (itype.name if itype is not None else None)


def _pr_state_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    for pr in graph.pull_requests:
        yield pr.id, pr.state


def _pr_author_resolver(graph: Graph) -> Iterable[Tuple[str, object]]:
    users = graph.github_users
    for pr in graph.pull_requests:
        author = users.get(pr.author_ref.id) if pr.author_ref else None
        yield pr.id, (author.login if author is not None else None)


_RESOLVERS: Dict[Tuple[EntityKind, str], ResolverFn] = {
    (EntityKind.FILE, "loc"): _file_loc_resolver,
    (EntityKind.FILE, "extension"): _file_extension_resolver,
    (EntityKind.FILE, "path"): _file_path_resolver,
    (EntityKind.COMMIT, "author_email"): _commit_author_email_resolver,
    (EntityKind.COMMIT, "message"): _commit_message_resolver,
    (EntityKind.ISSUE, "status"): _issue_status_resolver,
    (EntityKind.ISSUE, "type"): _issue_type_resolver,
    (EntityKind.PULL_REQUEST, "state"): _pr_state_resolver,
    (EntityKind.PULL_REQUEST, "author"): _pr_author_resolver,
}


_NUMERIC_OPS = {"lt", "le", "gt", "ge"}
_EQUALITY_OPS = {"eq", "ne"}
_LIST_OPS = {"in", "not_in"}
_STRING_OPS = {"contains", "regex"}
_ALL_OPS = _NUMERIC_OPS | _EQUALITY_OPS | _LIST_OPS | _STRING_OPS


class FilterRuleValidationError(ValueError):
    """Raised when a DSL references an unsupported entity_kind/field/op."""


def validate_dsl(dsl: RuleDSL) -> None:
    """Raise :class:`FilterRuleValidationError` if the DSL cannot be applied."""
    predicates = (
        dsl.predicate.all_of if isinstance(dsl.predicate, AllOf) else [dsl.predicate]
    )
    if not predicates:
        raise FilterRuleValidationError("predicate is empty")
    for p in predicates:
        key = (dsl.entity_kind, p.field)
        if key not in _RESOLVERS:
            raise FilterRuleValidationError(
                f"unsupported (entity_kind, field): "
                f"({dsl.entity_kind.value}, {p.field}). "
                f"Supported: {sorted((k.value, f) for k, f in _RESOLVERS)}"
            )
        if p.op not in _ALL_OPS:
            raise FilterRuleValidationError(
                f"unsupported op {p.op!r}. Supported: {sorted(_ALL_OPS)}"
            )
        if p.op in _LIST_OPS and not isinstance(p.value, list):
            raise FilterRuleValidationError(
                f"op {p.op!r} requires a list value; got {type(p.value).__name__}"
            )
        if p.op in _STRING_OPS and not isinstance(p.value, str):
            raise FilterRuleValidationError(
                f"op {p.op!r} requires a string value; got {type(p.value).__name__}"
            )


def _evaluate(op: str, value: object, target: object) -> bool:
    """Apply ``op`` to a pair, treating ``None`` targets as never matching."""
    if op == "eq":
        return target == value
    if op == "ne":
        return target != value
    if target is None:
        return False
    if op in _NUMERIC_OPS:
        try:
            t = float(target)  # type: ignore[arg-type]
            v = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return False
        if op == "lt":
            return t < v
        if op == "le":
            return t <= v
        if op == "gt":
            return t > v
        if op == "ge":
            return t >= v
    if op == "in":
        return target in (value or [])  # type: ignore[operator]
    if op == "not_in":
        return target not in (value or [])  # type: ignore[operator]
    if op == "contains":
        try:
            return str(value) in str(target)
        except Exception:  # noqa: BLE001
            return False
    if op == "regex":
        try:
            return re.search(str(value), str(target)) is not None
        except re.error:
            return False
    return False


def _ids_matching_predicate(
    graph: Graph, entity_kind: EntityKind, predicate: Predicate
) -> Set[str]:
    resolver = _RESOLVERS[(entity_kind, predicate.field)]
    out: Set[str] = set()
    for entity_id, target in resolver(graph):
        if _evaluate(predicate.op, predicate.value, target):
            out.add(entity_id)
    return out


def _ids_matching_dsl(graph: Graph, dsl: RuleDSL) -> Set[str]:
    if isinstance(dsl.predicate, AllOf):
        sets: List[Set[str]] = [
            _ids_matching_predicate(graph, dsl.entity_kind, p)
            for p in dsl.predicate.all_of
        ]
        if not sets:
            return set()
        out = sets[0]
        for s in sets[1:]:
            out = out & s
        return out
    return _ids_matching_predicate(graph, dsl.entity_kind, dsl.predicate)


def compute_excluded_ids(
    graph: Graph, rules: Iterable[FilterRule]
) -> Dict[EntityKind, Set[str]]:
    """Union the matched-id sets of every rule, keyed by ``entity_kind``."""
    excluded: Dict[EntityKind, Set[str]] = {}
    for rule in rules:
        try:
            validate_dsl(rule.dsl)
        except FilterRuleValidationError:
            # Skip rules whose DSL is no longer satisfiable (e.g. after a
            # schema change); the POST path already rejects on validation,
            # so this is a defensive guard for legacy rows.
            continue
        matched = _ids_matching_dsl(graph, rule.dsl)
        if not matched:
            continue
        excluded.setdefault(rule.entity_kind, set()).update(matched)
    return excluded


def supported_fields() -> Dict[str, List[str]]:
    """``{entity_kind_value: [field, …]}`` for diagnostics."""
    out: Dict[str, List[str]] = {}
    for (kind, field) in _RESOLVERS:
        out.setdefault(kind.value, []).append(field)
    return out


__all__ = [
    "FilterRuleValidationError",
    "compute_excluded_ids",
    "supported_fields",
    "validate_dsl",
]
