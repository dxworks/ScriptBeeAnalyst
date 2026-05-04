"""JaFax (FAMIX-style Java) layout JSON DTOs.

Implements §4 of communication/B2_codeframe/index_step_general.md.

The JaFax layout file is a flat JSON list of polymorphic dicts discriminated
by the inline `type` field. Five entity kinds are observed: File, Class,
Method, Attribute, ImportStatement. Cross-references are integer ids back
into the same flat list. We model each kind with a permissive Pydantic DTO
(extras ignored) and let the transformer link by id.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class _JaFaxBase(BaseModel):
    model_config = ConfigDict(extra="ignore")
    type: str
    id: int
    name: str = ""


class FileJaFax(_JaFaxBase):
    """type=='File' — `name` is a relative source path; `imports` are ImportStatement ids."""
    imports: List[int] = Field(default_factory=list)


class ImportStatementJaFax(_JaFaxBase):
    imported_class: str = Field(default="", alias="importedClass")
    on_demand: bool = Field(default=False, alias="onDemand")
    modifiers: List[str] = Field(default_factory=list)


class ClassJaFax(_JaFaxBase):
    """type=='Class' — `fileName` is a relative source path (absent for synthetic classes)."""
    pack: str = ""
    file_name: Optional[str] = Field(default=None, alias="fileName")
    modifiers: List[str] = Field(default_factory=list)
    is_external: bool = Field(default=False, alias="isExternal")
    is_interface: bool = Field(default=False, alias="isInterface")
    is_type_parameter: bool = Field(default=False, alias="isTypeParameter")
    super_class: Optional[int] = Field(default=None, alias="superClass")
    interfaces: List[int] = Field(default_factory=list)
    type_parameters: List[int] = Field(default_factory=list, alias="typeParameters")
    contained_classes: List[int] = Field(default_factory=list, alias="containedClasses")
    contained_methods: List[int] = Field(default_factory=list, alias="containedMethods")
    contained_fields: List[int] = Field(default_factory=list, alias="containedFields")
    called_methods: List[int] = Field(default_factory=list, alias="calledMethods")
    accessed_fields: List[int] = Field(default_factory=list, alias="accessedFields")
    container: Optional[int] = None


class MethodJaFax(_JaFaxBase):
    signature: str = ""
    modifiers: List[str] = Field(default_factory=list)
    cyclomatic_complexity: int = Field(default=0, alias="cyclomaticComplexity")
    is_constructor: bool = Field(default=False, alias="isConstructor")
    is_default_constructor: bool = Field(
        default=False, alias="isDefaultConstructor"
    )
    return_type: Optional[int] = Field(default=None, alias="returnType")
    container: Optional[int] = None
    parameters: List[int] = Field(default_factory=list)
    local_variables: List[int] = Field(default_factory=list, alias="localVariables")
    contained_methods: List[int] = Field(default_factory=list, alias="containedMethods")
    contained_classes: List[int] = Field(default_factory=list, alias="containedClasses")
    type_parameters: List[int] = Field(default_factory=list, alias="typeParameters")
    called_methods: List[int] = Field(default_factory=list, alias="calledMethods")
    accessed_fields: List[int] = Field(default_factory=list, alias="accessedFields")


class AttributeJaFax(_JaFaxBase):
    """type=='Attribute' — kind ∈ {Field, Parameter, LocalVariable}."""
    modifiers: List[str] = Field(default_factory=list)
    kind: str = ""
    class_id: Optional[int] = Field(default=None, alias="class")
    container: Optional[int] = None
