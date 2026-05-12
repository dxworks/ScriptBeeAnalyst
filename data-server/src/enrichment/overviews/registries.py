"""Overview-table catalog (plain registry, not a kernel ``Registry[Entity]``).

Mirrors :class:`src.enrichment.metrics.MetricRegistry`'s shape — overview
tables are computed code + presentational data, not addressable graph
entities, so they don't need :class:`EntityKind` / picklability.

Chunk 8 will expose this catalog on :class:`Graph` as a typed field so
the MCP sandbox helper ``overview_as_dict(name)`` can resolve a table by
name without scanning a list.
"""
from __future__ import annotations

from typing import Iterator

from .models import OverviewTableBuilder


class OverviewTableRegistry:
    """Catalog of :class:`OverviewTableBuilder` subclasses, keyed by ``name``.

    Decorator-friendly — :meth:`register` returns the class so it can be
    used as ``@OVERVIEWS.register``.
    """

    __slots__ = ("_by_name",)

    def __init__(self) -> None:
        self._by_name: dict[str, type[OverviewTableBuilder]] = {}

    def register(
        self, builder_cls: type[OverviewTableBuilder]
    ) -> type[OverviewTableBuilder]:
        if not isinstance(builder_cls, type):
            raise TypeError(
                f"OverviewTableRegistry.register expects a class, got "
                f"{type(builder_cls).__name__}"
            )
        if not issubclass(builder_cls, OverviewTableBuilder):
            raise TypeError(
                f"{builder_cls.__name__} must subclass OverviewTableBuilder"
            )
        name = getattr(builder_cls, "name", None)
        if not isinstance(name, str) or not name:
            raise TypeError(
                f"{builder_cls.__name__} must declare a non-empty "
                f"``name: ClassVar[str]`` to register"
            )
        existing = self._by_name.get(name)
        if existing is not None and existing is not builder_cls:
            raise ValueError(
                f"OverviewTableRegistry already has a different class registered "
                f"under name {name!r}: {existing.__name__} (new: "
                f"{builder_cls.__name__})"
            )
        self._by_name[name] = builder_cls
        return builder_cls

    def get(self, name: str) -> type[OverviewTableBuilder]:
        try:
            return self._by_name[name]
        except KeyError as e:
            raise KeyError(
                f"No overview-table builder registered under name {name!r}. "
                f"Registered: {sorted(self._by_name)}"
            ) from e

    def all(self) -> list[type[OverviewTableBuilder]]:
        return list(self._by_name.values())

    def names(self) -> list[str]:
        return list(self._by_name.keys())

    def __iter__(self) -> Iterator[type[OverviewTableBuilder]]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name

    def unregister(self, name: str) -> type[OverviewTableBuilder]:
        try:
            return self._by_name.pop(name)
        except KeyError as e:
            raise KeyError(
                f"No overview-table builder registered under name {name!r}. "
                f"Registered: {sorted(self._by_name)}"
            ) from e

    def clear(self) -> None:
        self._by_name.clear()


#: Module-level singleton.
OVERVIEWS = OverviewTableRegistry()


__all__ = ["OVERVIEWS", "OverviewTableRegistry"]
