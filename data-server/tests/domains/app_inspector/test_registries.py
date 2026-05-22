"""App-Inspector-domain registry tests."""
from __future__ import annotations

from pathlib import Path

from src.common.domains.app_inspector import (
    AppInspectorProject,
    AppInspectorProjectRegistry,
    AppTag,
    AppTagRegistry,
)
from src.common.kernel import EntityKind, EntityRef
from src.common.people import SourceKind
from src.common.pickle_store import PickleStore


PROJECT_ID = "ai-1"
PROJECT_REF = EntityRef(kind=EntityKind.PROJECT, id=PROJECT_ID)
FILE_A_PATH = "src/A.java"
FILE_B_PATH = "src/B.java"
FILE_A = EntityRef(kind=EntityKind.FILE, id=f"{PROJECT_ID}::{FILE_A_PATH}")
FILE_B = EntityRef(kind=EntityKind.FILE, id=f"{PROJECT_ID}::{FILE_B_PATH}")


def _tag(
    file_ref: EntityRef,
    file_path: str,
    tag: str,
    *,
    strength: int = 1,
    project_ref: EntityRef = PROJECT_REF,
    project_id: str = PROJECT_ID,
) -> AppTag:
    return AppTag(
        id=AppTag.make_id(project_id, file_path, tag),
        project_ref=project_ref,
        file_ref=file_ref,
        file_path=file_path,
        tag=tag,
        strength=strength,
    )


# ---------------------------------------------------------------------------
# AppInspectorProjectRegistry
# ---------------------------------------------------------------------------


def test_app_inspector_project_registry_indexes():
    reg = AppInspectorProjectRegistry()
    p_a = AppInspectorProject(
        id="p1",
        name="X",
        source=SourceKind.APP_INSPECTOR,
        source_tool="appinspector",
    )
    p_b = AppInspectorProject(
        id="p2",
        name="X",
        source=SourceKind.APP_INSPECTOR,
        source_tool="appinspector",
    )
    reg.add(p_a)
    reg.add(p_b)
    assert {p.id for p in reg.by_name["X"]} == {"p1", "p2"}
    assert {p.id for p in reg.by_source_tool["appinspector"]} == {"p1", "p2"}


# ---------------------------------------------------------------------------
# AppTagRegistry
# ---------------------------------------------------------------------------


def test_app_tag_registry_by_file_and_project():
    reg = AppTagRegistry()
    t1 = _tag(FILE_A, FILE_A_PATH, "appinspector.OS.Network.Connection.Socket")
    t2 = _tag(FILE_A, FILE_A_PATH, "appinspector.Cryptography.CryptoCurrency")
    t3 = _tag(FILE_B, FILE_B_PATH, "appinspector.OS.Network.Connection.Socket")
    reg.add(t1)
    reg.add(t2)
    reg.add(t3)

    assert {t.id for t in reg.by_file[FILE_A]} == {t1.id, t2.id}
    assert {t.id for t in reg.by_file[FILE_B]} == {t3.id}
    assert {t.id for t in reg.by_project[PROJECT_REF]} == {t1.id, t2.id, t3.id}


def test_app_tag_registry_by_tag_full_taxonomy():
    reg = AppTagRegistry()
    tag_socket = "appinspector.OS.Network.Connection.Socket"
    tag_crypto = "appinspector.Cryptography.CryptoCurrency"
    t1 = _tag(FILE_A, FILE_A_PATH, tag_socket)
    t2 = _tag(FILE_B, FILE_B_PATH, tag_socket)
    t3 = _tag(FILE_A, FILE_A_PATH, tag_crypto)
    reg.add(t1)
    reg.add(t2)
    reg.add(t3)

    assert {t.id for t in reg.by_tag[tag_socket]} == {t1.id, t2.id}
    assert {t.id for t in reg.by_tag[tag_crypto]} == {t3.id}


def test_app_tag_registry_by_tag_root_segments_after_prefix():
    """``by_tag_root`` keys are the first segment after ``appinspector.``."""
    reg = AppTagRegistry()
    t_os_socket = _tag(
        FILE_A,
        FILE_A_PATH,
        "appinspector.OS.Network.Connection.Socket",
    )
    t_os_dns = _tag(
        FILE_B,
        FILE_B_PATH,
        "appinspector.OS.Network.DNS",
    )
    t_crypto = _tag(
        FILE_A,
        FILE_A_PATH,
        "appinspector.Cryptography.CryptoCurrency",
    )
    reg.add(t_os_socket)
    reg.add(t_os_dns)
    reg.add(t_crypto)

    assert {t.id for t in reg.by_tag_root["OS"]} == {
        t_os_socket.id,
        t_os_dns.id,
    }
    assert {t.id for t in reg.by_tag_root["Cryptography"]} == {t_crypto.id}
    # No bleed-through between buckets.
    assert t_crypto not in reg.by_tag_root["OS"]
    assert t_os_socket not in reg.by_tag_root["Cryptography"]


def test_app_tag_registry_by_tag_root_handles_non_prefixed_tag():
    """Defensive: tags without the ``appinspector.`` prefix fall back to
    their leading dotted segment."""
    reg = AppTagRegistry()
    weird = _tag(FILE_A, FILE_A_PATH, "Custom.Top.Segment")
    reg.add(weird)
    assert {t.id for t in reg.by_tag_root["Custom"]} == {weird.id}


def test_app_tag_registry_remove_updates_indexes():
    reg = AppTagRegistry()
    t = _tag(FILE_A, FILE_A_PATH, "appinspector.OS.Network.Connection.Socket")
    reg.add(t)
    assert reg.by_file[FILE_A] == (t,)
    assert reg.by_tag_root["OS"] == (t,)
    reg.remove(t.id)
    assert reg.by_file[FILE_A] == ()
    assert reg.by_tag_root["OS"] == ()
    assert reg.by_tag["appinspector.OS.Network.Connection.Socket"] == ()


def test_app_tag_registry_pickle_round_trip(tmp_path: Path):
    reg = AppTagRegistry()
    t1 = _tag(FILE_A, FILE_A_PATH, "appinspector.OS.Network.Connection.Socket")
    t2 = _tag(FILE_B, FILE_B_PATH, "appinspector.Cryptography.CryptoCurrency")
    reg.add(t1)
    reg.add(t2)
    store = PickleStore(tmp_path)
    store.write_registry(EntityKind.APP_TAG.value, reg)
    restored = store.read_registry(EntityKind.APP_TAG.value, AppTagRegistry)
    assert len(restored) == 2
    assert {t.id for t in restored.by_tag_root["OS"]} == {t1.id}
    assert {t.id for t in restored.by_tag_root["Cryptography"]} == {t2.id}
