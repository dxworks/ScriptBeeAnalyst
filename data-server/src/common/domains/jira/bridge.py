"""Bridge: raw Jira JSON file → entity bundle consumed by :class:`JiraTransformer`.

The :class:`JiraTransformer` only accepts a pre-built entity-bundle Mapping
(see its module docstring). This bridge produces that Mapping from the raw
``jira.json`` file shape parsed by
:class:`src.jira_miner.reader_dto.loader.JiraJsonLoader` →
:class:`JsonFileFormatJira`.

Design choices
--------------

* **Stable ids**:
    - ``JiraProject.id`` = the supplied ``project_name`` (callers typically
      pass the slug like ``"zeppelin"``). The graph stores it once.
    - ``JiraUser.id`` = the user's ``self`` URL — the same string that the
      raw issue payload puts in ``reporterId`` / ``creatorId`` /
      ``assigneeId`` / ``comments[].userId`` / ``changes[].userId``. This
      lets us de-dup users by URL across every referencing field.
    - ``IssueStatus.id`` / ``IssueType.id`` = the raw Jira string id
      (e.g. ``"5"`` / ``"1"``). De-dup'd by that id across the issue list.
    - ``Issue.id`` = ``Issue.key`` (e.g. ``"ZEPPELIN-1841"``).
* **De-dup**:
    - Users are de-dup'd by their ``self`` URL. The reader's ``users``
      list is already deduped, but downstream issue fields may reference
      a user that the file lists once; we still keyed by URL so re-runs
      with imperfect inputs don't crash.
    - ``IssueStatus`` and ``IssueType`` are de-dup'd across the issue
      list — the reader gives us the canonical list under
      ``issueStatuses`` / ``issueTypes``, so we just construct them once
      from those top-level lists.
* **Datetime parsing**: not needed at this layer — the reader DTO already
  parses ISO strings into :class:`datetime` (see
  ``src/jira_miner/reader_dto/models.py``).
* **Refs**:
    - Each :class:`Issue` carries ``status_ref`` / ``type_ref`` built from
      the canonical entity (not a fresh per-issue copy).
    - ``reporter_ref`` is required by the entity; we always set it from
      ``reporterId`` (every issue in the corpus carries one).
    - ``assignee_ref`` → ``assignee_refs`` list (entity carries a list);
      we either add a single ref or leave the list empty.
    - ``creator_ref`` set from ``creatorId`` when present.
* **Comments / transitions**: not strictly required by the transformer
  (the bucket specs only list users/issues/statuses/types), but they're
  cheap value-object lists nested on the Issue so we populate them too —
  the value-object datetimes already come parsed from the reader.

This module deliberately stays under ``src/common/domains/jira/`` (per the
chunk constraints) so the v2 jira subpackage is self-contained.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ...people.source import SourceKind
from ....jira_miner.reader_dto.loader import JiraJsonLoader
from ....jira_miner.reader_dto.models import (
    Comment as RawComment,
    Change as RawChange,
    Issue as RawIssue,
    IssueStatus as RawIssueStatus,
    IssueType as RawIssueType,
    JsonFileFormatJira,
    User as RawUser,
)
from .models import (
    Comment,
    Issue,
    IssueStatus,
    IssueTransition,
    IssueType,
    JiraProject,
    JiraUser,
    TransitionItem,
)


def build_jira_bundle(
    file_path: Path,
    project_name: str = "Project",
) -> Mapping[str, Any]:
    """Read a raw Jira JSON file and return an entity bundle.

    Parameters
    ----------
    file_path:
        Path to a ``jira.json`` file in the
        :class:`JsonFileFormatJira` shape produced by the jira_miner.
    project_name:
        Human-facing name of the project; also used as the
        :class:`JiraProject` id so cross-refs stay stable across rebuilds.

    Returns
    -------
    Mapping
        ``{"project": JiraProject, "users": [...], "issues": [...],
        "issue_statuses": [...], "issue_types": [...]}`` — the exact shape
        :meth:`JiraTransformer.transform` consumes.
    """
    loader = JiraJsonLoader(str(file_path))
    raw: JsonFileFormatJira = loader.load()

    project = JiraProject(
        id=project_name,
        name=project_name,
        source=SourceKind.JIRA,
    )
    project_ref = project.ref()

    # --- statuses --------------------------------------------------------
    status_by_id: Dict[str, IssueStatus] = {}
    for rs in raw.issueStatuses:
        status_by_id[rs.id] = _to_status(rs, project_ref)

    # --- types -----------------------------------------------------------
    type_by_id: Dict[str, IssueType] = {}
    for rt in raw.issueTypes:
        type_by_id[rt.id] = _to_type(rt, project_ref)

    # --- users -----------------------------------------------------------
    # Keyed by ``self`` URL — the same string used in reporterId etc.
    user_by_url: Dict[str, JiraUser] = {}
    for ru in raw.users:
        user_by_url[ru.self_] = _to_user(ru, project_ref)

    # --- issues ----------------------------------------------------------
    issues: List[Issue] = []
    for ri in raw.issues:
        # Resolve / fall back to a synthesized status for any issue whose
        # status id wasn't in the top-level ``issueStatuses`` list.
        status = status_by_id.get(ri.status.id)
        if status is None:
            status = _to_status(ri.status, project_ref)
            status_by_id[ri.status.id] = status

        # Same defensive fallback for type — match by string typeId.
        type_id_str = str(ri.typeId)
        issue_type = type_by_id.get(type_id_str)
        if issue_type is None:
            issue_type = IssueType(
                id=type_id_str,
                project_ref=project_ref,
                name=ri.issueType,
                description="",
                is_sub_task=False,
            )
            type_by_id[type_id_str] = issue_type

        reporter_ref = None
        if ri.reporterId:
            reporter_ref = _ensure_user_ref(
                ri.reporterId, user_by_url, project_ref
            )

        creator_ref = None
        if ri.creatorId:
            creator_ref = _ensure_user_ref(
                ri.creatorId, user_by_url, project_ref
            )

        assignee_refs = []
        if ri.assigneeId:
            assignee_refs.append(
                _ensure_user_ref(ri.assigneeId, user_by_url, project_ref)
            )

        comments = [
            _to_comment(rc, user_by_url, project_ref) for rc in ri.comments
        ]
        transitions = [
            _to_transition(rch, user_by_url, project_ref) for rch in ri.changes
        ]

        issues.append(
            Issue(
                id=ri.key,
                key=ri.key,
                project_ref=project_ref,
                summary=ri.summary,
                description=ri.description,
                created_at=ri.created,
                updated_at=ri.updated,
                numeric_id=ri.id,
                status_ref=status.ref(),
                type_ref=issue_type.ref(),
                creator_ref=creator_ref,
                reporter_ref=reporter_ref,
                assignee_refs=assignee_refs,
                priority=ri.priority,
                time_estimate=ri.timeEstimate,
                time_spent=ri.timeSpent,
                comments=comments,
                transitions=transitions,
            )
        )

    return {
        "project": project,
        "users": list(user_by_url.values()),
        "issues": issues,
        "issue_statuses": list(status_by_id.values()),
        "issue_types": list(type_by_id.values()),
    }


# ---------------------------------------------------------------------------
# Helpers (module-private)
# ---------------------------------------------------------------------------


def _to_status(rs: RawIssueStatus, project_ref) -> IssueStatus:
    return IssueStatus(
        id=rs.id,
        project_ref=project_ref,
        name=rs.name,
        category=rs.statusCategory.key,
    )


def _to_type(rt: RawIssueType, project_ref) -> IssueType:
    return IssueType(
        id=rt.id,
        project_ref=project_ref,
        name=rt.name,
        description=rt.description or "",
        is_sub_task=bool(rt.isSubTask),
    )


def _to_user(ru: RawUser, project_ref) -> JiraUser:
    # ``id`` = the ``self`` URL — see module docstring.
    return JiraUser(
        id=ru.self_,
        name=ru.name,
        project_ref=project_ref,
        key=ru.key,
        link=ru.self_,
    )


def _ensure_user_ref(
    user_url: str,
    user_by_url: Dict[str, JiraUser],
    project_ref,
):
    """Return a typed ref to ``user_url``, synthesizing a stub user if missing."""
    user = user_by_url.get(user_url)
    if user is None:
        # Synthesize a minimal placeholder — the raw users list didn't
        # include this URL, but issue fields point at it. We key the stub
        # by URL so subsequent references hit the same entity.
        # ``name`` falls back to the URL itself; ``key`` is best-effort
        # extracted from the trailing ``?username=`` query param.
        fallback_key = _extract_username(user_url) or user_url
        user = JiraUser(
            id=user_url,
            name=fallback_key,
            project_ref=project_ref,
            key=fallback_key,
            link=user_url,
        )
        user_by_url[user_url] = user
    return user.ref()


def _extract_username(url: str) -> Optional[str]:
    """Best-effort grab of the ``username`` query param from a Jira user URL."""
    marker = "username="
    idx = url.rfind(marker)
    if idx == -1:
        return None
    return url[idx + len(marker):] or None


def _to_comment(rc: RawComment, user_by_url, project_ref) -> Comment:
    author_ref = (
        _ensure_user_ref(rc.userId, user_by_url, project_ref)
        if rc.userId
        else None
    )
    updated_by_ref = (
        _ensure_user_ref(rc.updateUserId, user_by_url, project_ref)
        if rc.updateUserId
        else None
    )
    return Comment(
        body=rc.body,
        created=rc.created,
        updated=rc.updated,
        author_ref=author_ref,
        updated_by_ref=updated_by_ref,
    )


def _to_transition(
    rch: RawChange, user_by_url, project_ref
) -> IssueTransition:
    user_ref = (
        _ensure_user_ref(rch.userId, user_by_url, project_ref)
        if rch.userId
        else None
    )
    items = [
        TransitionItem(
            field=ci.field,
            from_value=ci.from_,
            from_string=ci.fromString,
            to_value=ci.to,
            to_string=ci.toString,
        )
        for ci in rch.items
    ]
    return IssueTransition(
        id=int(rch.id),
        created=rch.created,
        changed_fields=list(rch.changedFields),
        items=items,
        user_ref=user_ref,
    )


__all__ = ["build_jira_bundle"]
