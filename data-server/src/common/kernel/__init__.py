"""ScriptBee graph v2 — kernel package.

Public API for every downstream chunk. See ``architectural_changes.md`` §1.

Typical use (Chunk 2 example)::

    from typing import ClassVar
    from src.common.kernel import (
        Entity, EntityKind, EntityRef, Registry, IndexSpec, Graph,
    )

    class Account(Entity, abstract=True):
        # Intermediate abstract base: opt out of the ``kind`` requirement.
        name: str

    class GitAccount(Account):
        kind: ClassVar[EntityKind] = EntityKind.GIT_ACCOUNT
        email: str

    class GitAccountRegistry(Registry[GitAccount, str]):
        indexes = [
            IndexSpec(name="by_email", key_fn=lambda a: a.email),
        ]
        def get_id(self, entity: GitAccount) -> str:
            return entity.id


IndexSpec ``key_fn`` return types
---------------------------------

The function attached to an ``IndexSpec`` may return any of:

* ``None``                — entity not indexed for this spec (skipped).
* a single hashable value — single primary key (e.g. ``c.author_ref``).
* a ``tuple``             — single composite key (e.g.
                            ``(c.dimension, c.value)``).
* a ``BaseModel``         — single composite key (e.g. an ``EntityRef``).
* an iterable             — fan-out: one entry per yielded key (e.g.
                            ``[ch.file_ref for ch in c.changes]``).


Lazy loading
------------

``LazyRegistryProxy`` (in ``src.common.pickle_store``) is a transparent
proxy that defers ``Registry.load`` until first access. For typed
``Graph`` fields, build proxies via ``lazy_proxy_for(RegistryCls, store,
name, loader)`` — the returned instance passes ``isinstance(...,
RegistryCls)``.
"""
from __future__ import annotations

from .entity import Entity
from .index import Index, IndexSpec
from .kinds import EntityKind
from .ref import EntityRef
from .registry import Registry

# ``Graph`` is imported LAST because Chunk-8 wired it to import every typed
# registry up-front (one per domain). Those domain registries import the
# kernel surface (``Entity``, ``EntityKind``, ``Registry`` etc.) via the
# package init — so the kernel-package symbols must be bound before the
# Graph module load triggers the domain chain. Reordering here keeps the
# kernel package internally consistent without forcing every registry
# module to use the per-submodule import form.
from .graph import Graph  # noqa: E402

__all__ = [
    "Entity",
    "EntityKind",
    "EntityRef",
    "Graph",
    "Index",
    "IndexSpec",
    "Registry",
]
