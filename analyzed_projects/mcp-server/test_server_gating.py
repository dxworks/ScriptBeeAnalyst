"""Smoke tests for the MCP server's state-gating layer (task P5.B).

The MCP server has no dedicated pytest infrastructure (its production
mode is stdio-driven by the FastMCP runtime). This file provides a
minimal harness sufficient for the three P5.B regression cases:

* A FINALIZED-only tool refuses cleanly when the project is PRE_MERGE.
* A PRE_MERGE-only tool refuses cleanly when the project is FINALIZED.
* ``finalize_project`` calls through to the data-server endpoint
  (mocked) when the project is PRE_MERGE.

Run with::

    cd analyzed_projects/mcp-server
    python test_server_gating.py

The script exits with a non-zero status on the first failure and
prints a short pass/fail summary; suitable for CI piggyback without
adding a pytest-asyncio dependency.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Callable

import httpx

import server
from mcp.shared.exceptions import McpError


# ---------------------------------------------------------------------------
# httpx.AsyncClient mock — captures every outbound request so the test
# body can assert routing. Each test installs a fresh router function
# that maps (method, path) -> (status, body).
# ---------------------------------------------------------------------------

_RECORDED_REQUESTS: list[tuple[str, str]] = []
_ROUTER: Callable[[str, str, dict | None], tuple[int, dict]] | None = None


def _handler(request: httpx.Request) -> httpx.Response:
    method = request.method.upper()
    path = request.url.path
    _RECORDED_REQUESTS.append((method, path))
    json_body = None
    if request.content:
        try:
            json_body = json.loads(request.content)
        except Exception:
            json_body = None
    if _ROUTER is None:
        return httpx.Response(500, json={"error": "no router installed"})
    status, body = _ROUTER(method, path, json_body)
    return httpx.Response(status, json=body)


class _MockAsyncClient(httpx.AsyncClient):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.pop("timeout", None)
        super().__init__(transport=httpx.MockTransport(_handler))


def install_router(router: Callable[[str, str, dict | None], tuple[int, dict]]) -> None:
    global _ROUTER
    _ROUTER = router
    _RECORDED_REQUESTS.clear()
    # Swap the constructor used by every tool body.
    server.httpx.AsyncClient = _MockAsyncClient  # type: ignore[assignment]


def restore_httpx() -> None:
    server.httpx.AsyncClient = httpx.AsyncClient  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Routers used by individual tests.
# ---------------------------------------------------------------------------

def router_pre_merge(method: str, path: str, body: dict | None) -> tuple[int, dict]:
    if path == "/projects/current":
        return 200, {
            "loaded": True,
            "project_id": "proj-1",
            "project_name": "demo",
            "merge_state": "PRE_MERGE",
            "stats": {"git_commits": 0, "jira_issues": 0, "github_prs": 0},
        }
    if method == "POST" and path == "/projects/proj-1/finalize":
        return 200, {
            "merge_state": "FINALIZED",
            "unified_users_created": 3,
            "refs_rewritten": 42,
            "phase_b_relations_built": 5,
            "duration_ms": 12,
        }
    return 404, {"error": f"unexpected route: {method} {path}"}


def router_finalized(method: str, path: str, body: dict | None) -> tuple[int, dict]:
    if path == "/projects/current":
        return 200, {
            "loaded": True,
            "project_id": "proj-1",
            "project_name": "demo",
            "merge_state": "FINALIZED",
            "stats": {"git_commits": 1, "jira_issues": 0, "github_prs": 0},
        }
    return 404, {"error": f"unexpected route: {method} {path}"}


# ---------------------------------------------------------------------------
# Test cases.
# ---------------------------------------------------------------------------

async def test_finalized_only_tool_refused_in_pre_merge() -> None:
    install_router(router_pre_merge)
    try:
        try:
            await server.execute_code("print('x')")
        except McpError as e:
            assert "FINALIZED" in str(e), f"unexpected error message: {e}"
            assert "PRE_MERGE" in str(e), f"unexpected error message: {e}"
            return
        raise AssertionError("execute_code did not raise McpError in PRE_MERGE")
    finally:
        restore_httpx()


async def test_pre_merge_only_tool_refused_in_finalized() -> None:
    install_router(router_finalized)
    try:
        try:
            await server.finalize_project()
        except McpError as e:
            assert "PRE_MERGE" in str(e), f"unexpected error message: {e}"
            assert "FINALIZED" in str(e), f"unexpected error message: {e}"
            return
        raise AssertionError("finalize_project did not raise McpError in FINALIZED")
    finally:
        restore_httpx()


async def test_finalize_project_calls_through_to_data_server() -> None:
    install_router(router_pre_merge)
    try:
        result = await server.finalize_project()
        assert isinstance(result, dict), f"expected dict, got {type(result)}"
        assert result.get("merge_state") == "FINALIZED", result
        assert result.get("unified_users_created") == 3, result
        # Verify the POST landed on the right path (two GETs to /current
        # happen too — one for gating, one for project-id resolution).
        finalize_calls = [
            (m, p) for (m, p) in _RECORDED_REQUESTS
            if p == "/projects/proj-1/finalize"
        ]
        assert finalize_calls == [("POST", "/projects/proj-1/finalize")], _RECORDED_REQUESTS
    finally:
        restore_httpx()


async def _run_all() -> int:
    cases = [
        ("FINALIZED-only refused in PRE_MERGE",
         test_finalized_only_tool_refused_in_pre_merge),
        ("PRE_MERGE-only refused in FINALIZED",
         test_pre_merge_only_tool_refused_in_finalized),
        ("finalize_project calls through to data-server",
         test_finalize_project_calls_through_to_data_server),
    ]
    failures = 0
    for name, fn in cases:
        try:
            await fn()
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL  {name}: {exc!r}")
            failures += 1
        else:
            print(f"ok    {name}")
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_run_all()))
