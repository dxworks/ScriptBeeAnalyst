"""Graph root sanity: registry_for / resolve / dump+lazy.

Updated by Chunk 8 to use the real domain registries (Chunk-2-onwards)
that the typed Graph fields now hold. The previous custom ``_Person`` +
``_PersonRegistry`` test fixtures don't fit Pydantic's typed-field
validation — the registries on Graph are concrete classes, not generic
``Registry[T, ID]``.

The intent of each test is preserved:

* meta defaults, ``registry_for`` and ``resolve`` happy paths.
* ``Graph`` accepts a ``LazyRegistryProxy`` in a typed field.
* ``Graph.dump`` materializes proxies before pickling.
* ``Graph.lazy`` enforces schema_version.

Chunk 8 ships ``tests/chunk_08/`` with the deeper round-trip tests; this
file keeps the kernel-level smoke checks targeted at the contract.
"""
from __future__ import annotations

from pathlib import Path

import pickle
import pytest

from src.common.domains.git.models import GitAccount, GitProject
from src.common.domains.git.registries import GitAccountRegistry
from src.common.kernel import EntityKind, EntityRef, Graph
from src.common.people import SourceKind
from src.common.pickle_store import LazyRegistryProxy, PickleStore, lazy_proxy_for


def _project() -> GitProject:
    return GitProject(id="p1", name="test", source=SourceKind.GIT)


def _people() -> GitAccountRegistry:
    reg = GitAccountRegistry()
    proj_ref = _project().ref()
    reg.add(
        GitAccount(
            id="alice",
            name="Alice",
            project_ref=proj_ref,
            email="alice@x",
        )
    )
    reg.add(
        GitAccount(
            id="bob",
            name="Bob",
            project_ref=proj_ref,
            email="bob@x",
        )
    )
    return reg


def test_graph_meta_defaults():
    g = Graph(project_id="p1")
    assert g.schema_version == 2
    assert g.project_id == "p1"
    assert g.built_at is not None
    # Every typed registry defaults to an empty instance of its concrete class.
    assert isinstance(g.git_accounts, GitAccountRegistry)
    assert len(g.git_accounts) == 0


def test_registry_for_and_resolve():
    g = Graph(project_id="p1", git_accounts=_people())

    reg = g.registry_for(EntityKind.GIT_ACCOUNT)
    assert reg is not None
    assert len(reg) == 2

    # Unknown kind via a default-empty registry — registry_for still returns
    # the typed field (it's just empty), not None.
    commits_reg = g.registry_for(EntityKind.COMMIT)
    assert commits_reg is not None
    assert len(commits_reg) == 0

    # resolve() routes through the right registry.
    alice = g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice"))
    assert alice is not None
    assert alice.name == "Alice"

    assert g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="ghost")) is None
    assert g.resolve(EntityRef(kind=EntityKind.COMMIT, id="x")) is None


def test_graph_rejects_non_registry_values():
    with pytest.raises(Exception):
        Graph(project_id="p", git_accounts="not a registry")


def test_graph_accepts_lazy_proxy(tmp_path: Path):
    """A LazyRegistryProxy must be valid in a typed Graph field."""
    reg = _people()
    store = PickleStore(tmp_path)
    store.write_registry("git_account", reg)

    proxy = lazy_proxy_for(
        GitAccountRegistry,
        store,
        "git_account",
        lambda: store.read_registry("git_account", GitAccountRegistry),
    )
    g = Graph(project_id="p", git_accounts=proxy)
    assert proxy.is_loaded is False

    # Resolving through the graph triggers the lazy load transparently.
    alice = g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice"))
    assert alice is not None
    assert proxy.is_loaded is True


def test_graph_dump_writes_per_registry_files(tmp_path: Path):
    g = Graph(project_id="p1", git_accounts=_people())
    store = PickleStore(tmp_path / "store")
    g.dump(store)

    # One pickle per registry + meta.json.
    assert store.path_for("git_account").exists()
    meta = store.meta_read()
    assert meta is not None
    assert meta["project_id"] == "p1"
    assert meta["schema_version"] == 2
    assert "git_account" in meta["registries"]
    # Every other typed field is also dumped — even when empty.
    assert "commit" in meta["registries"]
    assert "trait" in meta["registries"]


def test_graph_lazy_no_registries_yet_returns_empty(tmp_path: Path):
    """``Graph.lazy`` with no on-disk payload still builds proxies; reads
    on missing files return an empty registry of the right class.
    """
    store = PickleStore(tmp_path)
    store.meta_write({"schema_version": 2, "project_id": "p1"})
    g = Graph.lazy("p1", store)
    assert g.project_id == "p1"
    # All registries are lazy proxies bound to the right class.
    assert isinstance(g.git_accounts, GitAccountRegistry)
    # Materialising a missing-file proxy returns an empty registry.
    assert len(g.git_accounts) == 0


# ---- Required-fix tests --------------------------------------------------


def test_graph_dump_with_proxy_writes_real_registry_payload(tmp_path: Path):
    """``Graph.dump`` must materialize proxies before pickling so on-disk
    files contain the real registry (not a proxy with an unpicklable
    loader).
    """
    # Pre-stage a registry on disk and wrap it in a proxy.
    seed_store = PickleStore(tmp_path / "seed")
    seed_store.write_registry("git_account", _people())

    proxy = lazy_proxy_for(
        GitAccountRegistry,
        seed_store,
        "git_account",
        lambda: seed_store.read_registry("git_account", GitAccountRegistry),
    )

    g = Graph(project_id="p1", git_accounts=proxy)

    # Dump the graph to a fresh store. This MUST NOT raise even though
    # the proxy's loader is an unpicklable lambda.
    out_store = PickleStore(tmp_path / "out")
    g.dump(out_store)  # would raise PicklingError before the fix

    # The on-disk shape is the real per-registry payload (per §8.1).
    raw = out_store.read("git_account")
    assert raw is not None
    restored = pickle.loads(raw)
    assert isinstance(restored, GitAccountRegistry)
    assert not isinstance(restored, LazyRegistryProxy)
    assert restored.ids() == {"alice", "bob"}


def test_graph_lazy_rejects_mismatched_schema_version(tmp_path: Path):
    """``Graph.lazy`` enforces ``schema_version`` per §8.3."""
    store = PickleStore(tmp_path)
    store.meta_write({"schema_version": 999, "project_id": "p1"})
    with pytest.raises(ValueError, match="schema_version"):
        Graph.lazy("p1", store)


def test_graph_lazy_accepts_matching_schema_version(tmp_path: Path):
    store = PickleStore(tmp_path)
    store.meta_write({"schema_version": 2, "project_id": "p1"})
    g = Graph.lazy("p1", store)
    assert g.project_id == "p1"
    assert g.schema_version == 2


# ---- Legacy ``registries=`` kwarg backwards-compat ----------------------


def test_graph_accepts_legacy_registries_kwarg():
    """The legacy ``registries={EntityKind: reg}`` kwarg fans the dict
    out into typed fields. This keeps Chunk-1/2 callers compiling.
    """
    g = Graph(project_id="p1", registries={EntityKind.GIT_ACCOUNT: _people()})
    assert len(g.git_accounts) == 2
    assert g.resolve(EntityRef(kind=EntityKind.GIT_ACCOUNT, id="alice")) is not None


def test_graph_registries_property_returns_dict_view():
    g = Graph(project_id="p1", git_accounts=_people())
    snapshot = g.registries
    assert EntityKind.GIT_ACCOUNT in snapshot
    assert len(snapshot[EntityKind.GIT_ACCOUNT]) == 2
