"""Per-file code structure (types, methods, fields, references) ingested
from JaFax (Java FAMIX) — abstraction layer ready for CodeFrame later.

Implements §3 of communication/B2_codeframe/index_step_general.md.

A `CodeType` is a class/interface/enum/record. `CodeMethod` is a method or
function (with parent-type and file backref). `CodeField` is a field/property.
`CodeReference` is a directed edge of a single kind (call / fieldAccess /
inheritance / interface). All four are picklable plain Pydantic models — they
do not back-reference the GitProject and do not need __reduce__ shims.

The `CodeStructureProject` is a thin container holding the four registries
plus the source format tag, so the processor can stash the whole project at
`graph_data['code_structure']`.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.common.registries import AbstractRegistry


class CodeType(BaseModel):
    """A class, interface, enum, or record."""
    model_config = ConfigDict(frozen=False)

    id: str  # globally unique within the project (e.g. "jafax:19")
    name: str
    qualified_name: str
    file_path: str  # the source file that declares this type
    kind: str  # "class" | "interface" | "enum" | "record"
    is_external: bool = False
    is_type_parameter: bool = False
    super_class_id: Optional[str] = None
    interface_ids: List[str] = Field(default_factory=list)


class CodeMethod(BaseModel):
    """A method or function."""
    model_config = ConfigDict(frozen=False)

    id: str
    name: str
    signature: str = ""
    parent_type_id: Optional[str] = None  # backref to the declaring CodeType
    file_path: Optional[str] = None  # set when the parent type has a file
    cyclomatic_complexity: int = 0
    is_constructor: bool = False


class CodeField(BaseModel):
    """A field or property."""
    model_config = ConfigDict(frozen=False)

    id: str
    name: str
    parent_type_id: Optional[str] = None
    file_path: Optional[str] = None


class CodeReference(BaseModel):
    """A directed edge from one entity to another.

    `from_file_path` and `to_file_path` are the canonical file ids so the
    relation extractors can aggregate without re-resolving owners. `weight`
    is the count of occurrences (each call/access contributes 1; aggregation
    happens in the relation extractors).
    """
    model_config = ConfigDict(frozen=False)

    id: str
    kind: str  # "call" | "fieldAccess" | "inheritance" | "interface"
    from_entity_id: str
    from_file_path: str
    to_entity_id: str
    to_file_path: str
    weight: int = 1


class CodeTypeRegistry(AbstractRegistry[CodeType, str]):
    def get_id(self, entity: CodeType) -> str:
        return entity.id


class CodeMethodRegistry(AbstractRegistry[CodeMethod, str]):
    def get_id(self, entity: CodeMethod) -> str:
        return entity.id


class CodeFieldRegistry(AbstractRegistry[CodeField, str]):
    def get_id(self, entity: CodeField) -> str:
        return entity.id


class CodeReferenceRegistry(AbstractRegistry[CodeReference, str]):
    def get_id(self, entity: CodeReference) -> str:
        return entity.id


class CodeStructureProject(BaseModel):
    """Container for the four registries plus the source-format tag."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    source: str = "jafax"  # also the format identifier — "jafax" | "codeframe"
    type_registry: CodeTypeRegistry = Field(default_factory=CodeTypeRegistry)
    method_registry: CodeMethodRegistry = Field(default_factory=CodeMethodRegistry)
    field_registry: CodeFieldRegistry = Field(default_factory=CodeFieldRegistry)
    reference_registry: CodeReferenceRegistry = Field(
        default_factory=CodeReferenceRegistry
    )

    @property
    def file_paths(self) -> set[str]:
        """Union of every distinct file_path observed across the registries.

        Useful for processors that need to know "which files does code-structure
        cover" without iterating every entity.
        """
        out: set[str] = set()
        for t in self.type_registry.all:
            if t.file_path:
                out.add(t.file_path)
        for m in self.method_registry.all:
            if m.file_path:
                out.add(m.file_path)
        return out
