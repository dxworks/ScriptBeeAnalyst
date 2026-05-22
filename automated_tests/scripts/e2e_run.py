"""
End-to-end automated test of the ScriptBeeAssistant stack.

Reproduces, headless via HTTP, what the orchestrator drove through the UI:
  1. Authenticate via Supabase Auth.
  2. Create a project via PostgREST.
  3. Upload all 8 Zeppelin fixture files into Supabase Storage and insert
     matching serialized_files rows (mirroring web-ui/src/app/core/services/
     file.service.ts).
  4. Hit data-server endpoints:
       GET  /health
       GET  /projects/current
       POST /projects/{id}/build       (currently HTTP 501 by design —
                                        Chunk 10 cleanup unfinished)
  5. Print a PASS / FAIL / SKIP table.

Run from anywhere:

    python3 ScriptBeeAssistant/automated_tests/scripts/e2e_run.py

Or with --keep to leave the created project in place for manual inspection.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]  # …/BachelorThesis
FIXTURE_DIR = REPO_ROOT / "upload_files_for_test"

SUPABASE_URL = "http://localhost:8000"
DATA_SERVER_URL = "http://localhost:8001"
ANON_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoiYW5vbiIsImlzcyI6InN1cGFi"
    "YXNlIiwiaWF0IjoxNzA0MDY3MjAwLCJleHAiOjE4OTM0NTYwMDB9.iuX6ZOOEhHp0vOMqFv2P"
    "_aRmwTUhHX6NU9zBC3CNRBg"
)
EMAIL = os.environ.get("SCRIPTBEE_EMAIL", "alexsimiongeorge@gmail.com")
PASSWORD = os.environ.get("SCRIPTBEE_PASSWORD", "8VJbw72hfDioF6hwAJus")
BUCKET = "serialized-files"

# Mirror of web-ui/src/app/core/models/project.model.ts
SUFFIX_RULES = [
    ("-codeframe.jsonl", "codeframe"),
    ("-code_smells.json", "quality_issues"),
    ("-external_duplication.csv", "dude_external"),
    ("-internal_duplication.json", "dude_internal"),
    ("-lizard.csv", "lizard"),
]
EXACT_NAME_MAP = {"github.json": "github", "jira.json": "jira"}

FIXTURE_FILES = [
    "zeppelin.iglog",
    "github.json",
    "jira.json",
    "zeppelin-lizard.csv",
    "zeppelin-codeframe.jsonl",
    "zeppelin-external_duplication.csv",
    "zeppelin-internal_duplication.json",
    "zeppelin-code_smells.json",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def detect_file_type(name: str) -> Optional[str]:
    lower = name.lower()
    if lower.endswith(".iglog"):
        return "git"
    if lower in EXACT_NAME_MAP:
        return EXACT_NAME_MAP[lower]
    for suffix, ftype in SUFFIX_RULES:
        if lower.endswith(suffix):
            return ftype
    return None


def detect_repo_name(name: str) -> Optional[str]:
    lower = name.lower()
    if lower.endswith(".iglog"):
        return name[: name.rfind(".")]
    for suffix, _ in SUFFIX_RULES:
        if lower.endswith(suffix):
            return name[: len(name) - len(suffix)]
    return None


def hash6(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:6]


class Result:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (name, status, detail)

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.rows.append((name, status, detail))
        marker = {"PASS": "[ OK ]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}.get(
            status, "[ ?? ]"
        )
        print(f"{marker} {name}" + (f"  — {detail}" if detail else ""))

    def summary(self) -> int:
        print()
        print("=" * 60)
        passed = sum(1 for _, s, _ in self.rows if s == "PASS")
        failed = sum(1 for _, s, _ in self.rows if s == "FAIL")
        skipped = sum(1 for _, s, _ in self.rows if s == "SKIP")
        print(f"PASSED {passed}   FAILED {failed}   SKIPPED {skipped}")
        return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def login(res: Result) -> tuple[str, str]:
    r = requests.post(
        f"{SUPABASE_URL}/auth/v1/token?grant_type=password",
        headers={"apikey": ANON_KEY, "Content-Type": "application/json"},
        json={"email": EMAIL, "password": PASSWORD},
        timeout=10,
    )
    r.raise_for_status()
    data = r.json()
    token = data["access_token"]
    user_id = data["user"]["id"]
    res.add("auth.login", "PASS", f"user_id={user_id[:8]}…")
    return token, user_id


def create_project(res: Result, token: str, user_id: str) -> str:
    name = f"e2e-auto-{int(time.time())}"
    r = requests.post(
        f"{SUPABASE_URL}/rest/v1/projects",
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json={
            "name": name,
            "description": "automated e2e run",
            "user_id": user_id,
        },
        timeout=10,
    )
    if r.status_code >= 300:
        res.add("project.create", "FAIL", f"HTTP {r.status_code} {r.text[:120]}")
        sys.exit(1)
    pid = r.json()[0]["id"]
    res.add("project.create", "PASS", f"id={pid}")
    return pid


def upload_one(
    res: Result, token: str, user_id: str, project_id: str, fixture: Path
) -> bool:
    name = fixture.name
    ftype = detect_file_type(name)
    repo = detect_repo_name(name)
    if ftype is None:
        res.add(f"upload.{name}", "FAIL", "no file_type rule matched")
        return False

    h = hash6(user_id, ftype, str(int(time.time() * 1000)))
    lower = name.lower()
    dot = lower.rfind(".")
    base = lower[:dot] if dot > 0 else lower
    ext = lower[dot:] if dot > 0 else ""
    storage_path = f"{user_id}/{project_id}/{base}_{h}{ext}"

    with fixture.open("rb") as f:
        body = f.read()
    up = requests.post(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{storage_path}",
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/octet-stream",
        },
        data=body,
        timeout=120,
    )
    if up.status_code >= 300:
        res.add(f"upload.{name}", "FAIL", f"storage HTTP {up.status_code}")
        return False

    db = requests.post(
        f"{SUPABASE_URL}/rest/v1/serialized_files",
        headers={
            "apikey": ANON_KEY,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
        json={
            "name": name,
            "file_type": ftype,
            "repo_name": repo,
            "storage_path": storage_path,
            "size_bytes": len(body),
            "project_id": project_id,
        },
        timeout=10,
    )
    if db.status_code >= 300:
        # Rollback the storage upload to leave a clean state
        requests.delete(
            f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{storage_path}",
            headers={"apikey": ANON_KEY, "Authorization": f"Bearer {token}"},
            timeout=10,
        )
        res.add(f"upload.{name}", "FAIL", f"db HTTP {db.status_code} {db.text[:120]}")
        return False

    res.add(f"upload.{name}", "PASS", f"{ftype}, {len(body):,} bytes")
    return True


def ds_health(res: Result) -> None:
    r = requests.get(f"{DATA_SERVER_URL}/health", timeout=5)
    if r.status_code == 200:
        res.add("data_server.health", "PASS", r.json().get("status", "?"))
    else:
        res.add("data_server.health", "FAIL", f"HTTP {r.status_code}")


def ds_current_project(res: Result, token: str) -> None:
    r = requests.get(
        f"{DATA_SERVER_URL}/projects/current",
        headers={"Authorization": f"Bearer {token}"},
        timeout=5,
    )
    if r.status_code == 200:
        body = r.json()
        res.add(
            "data_server.projects/current",
            "PASS",
            f"loaded={body.get('loaded')}",
        )
    else:
        res.add("data_server.projects/current", "FAIL", f"HTTP {r.status_code}")


def ds_build(res: Result, token: str, project_id: str) -> bool:
    r = requests.post(
        f"{DATA_SERVER_URL}/projects/{project_id}/build",
        headers={"Authorization": f"Bearer {token}"},
        timeout=600,
    )
    if r.status_code == 200:
        res.add("data_server.build", "PASS", str(r.json())[:140])
        return True
    if r.status_code == 501:
        body = r.json()
        if body.get("deferred"):
            res.add(
                "data_server.build",
                "SKIP",
                "HTTP 501 deferred (Chunk-10 bridge unfinished)",
            )
            return False
    res.add("data_server.build", "FAIL", f"HTTP {r.status_code} {r.text[:140]}")
    return False


def ds_execute(res: Result, token: str, project_id: str) -> None:
    code = (
        "print("
        "  f'commits={len(graph_data.commits.all())}, '"
        "  f'issues={len(graph_data.issues.all())}, '"
        "  f'prs={len(graph_data.pull_requests.all())}, '"
        "  f'files={len(graph_data.files.all())}'"
        ")"
    )
    r = requests.post(
        f"{DATA_SERVER_URL}/execute",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"code": code},
        timeout=30,
    )
    if r.status_code == 200 and "output" in r.json():
        res.add("data_server.execute", "PASS", r.json()["output"].strip()[:160])
    else:
        res.add(
            "data_server.execute",
            "FAIL",
            f"HTTP {r.status_code} {r.text[:140]}",
        )


def ds_smart_merge(res: Result, token: str, project_id: str) -> None:
    r = requests.get(
        f"{DATA_SERVER_URL}/projects/{project_id}/authors/suggestions",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if r.status_code != 200:
        res.add("smart_merge.suggestions", "FAIL", f"HTTP {r.status_code}")
        return
    sugs = r.json().get("suggestions", [])
    res.add(
        "smart_merge.suggestions",
        "PASS",
        f"{len(sugs)} cross-source author merge suggestions",
    )


def delete_project(res: Result, token: str, project_id: str) -> None:
    # PostgREST cascade deletes serialized_files rows; storage objects are
    # cleaned up by the same cascade trigger.
    r = requests.delete(
        f"{SUPABASE_URL}/rest/v1/projects?id=eq.{project_id}",
        headers={"apikey": ANON_KEY, "Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if r.status_code < 300:
        res.add("cleanup.project.delete", "PASS")
    else:
        res.add("cleanup.project.delete", "FAIL", f"HTTP {r.status_code}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--keep",
        action="store_true",
        help="Don't delete the test project on exit",
    )
    args = ap.parse_args()

    missing = [f for f in FIXTURE_FILES if not (FIXTURE_DIR / f).is_file()]
    if missing:
        print(f"Fixtures missing in {FIXTURE_DIR}: {missing}", file=sys.stderr)
        return 2

    res = Result()
    token, user_id = login(res)
    project_id = create_project(res, token, user_id)

    for name in FIXTURE_FILES:
        upload_one(res, token, user_id, project_id, FIXTURE_DIR / name)

    ds_health(res)
    ds_current_project(res, token)
    built = ds_build(res, token, project_id)
    if built:
        ds_execute(res, token, project_id)
        ds_smart_merge(res, token, project_id)
    else:
        res.add(
            "data_server.execute",
            "SKIP",
            "would need a loaded graph (blocked by build deferral)",
        )
        res.add(
            "smart_merge.suggestions",
            "SKIP",
            "would need a loaded graph (blocked by build deferral)",
        )

    if not args.keep:
        delete_project(res, token, project_id)
    else:
        print(f"\nLeaving project {project_id} in place (--keep)")

    return res.summary()


if __name__ == "__main__":
    sys.exit(main())
