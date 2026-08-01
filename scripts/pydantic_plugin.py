"""Strawberry codegen plugin that emits Pydantic models instead of bare classes.

Strawberry's stock ``PythonPlugin`` emits annotation-only classes, which carry no
runtime behaviour. This subclass emits ``LinearModel`` subclasses so every response
the SDK parses is validated, and so unknown fields Linear adds are ignored rather
than crashing.

It also owns the mapping for Linear's custom scalars. ``PythonPlugin`` asserts that
every non-builtin scalar has a ``python_type``, which Linear's ``JSON`` /
``JSONObject`` / ``TimelessDate`` do not, so without this the generator aborts.
"""

from __future__ import annotations

import re
import textwrap
from typing import TYPE_CHECKING, ClassVar

from strawberry.codegen import CodegenFile
from strawberry.codegen.plugins.python import PythonPlugin
from strawberry.codegen.types import GraphQLObjectType, GraphQLOptional, GraphQLScalar

if TYPE_CHECKING:
    from strawberry.codegen.types import GraphQLType


class PythonType:
    """A Python annotation plus the imports it needs.

    Strawberry's own entry assumes the annotation is a single importable name, which
    breaks for composites like ``dict[str, Any]`` — the importable name there is
    ``Any``, not ``dict``. ``imports`` states the (module, name) pairs explicitly.
    """

    def __init__(
        self,
        type_: str,
        module: str | None = None,
        imports: list[tuple[str, str]] | None = None,
    ) -> None:
        self.type = type_
        self.module = module
        if imports is not None:
            self.imports = imports
        elif module:
            self.imports = [(module, type_)]
        else:
            self.imports = []


class PydanticPlugin(PythonPlugin):
    """Emit ``LinearModel`` subclasses and resolve Linear's custom scalars."""

    SCALARS_TO_PYTHON_TYPES: ClassVar[dict[str, PythonType]] = {
        **PythonPlugin.SCALARS_TO_PYTHON_TYPES,
        # Linear's semi-structured payloads. Deliberately loose: the SDK does not
        # model their internals, and guessing a shape here would be drift.
        "JSON": PythonType("Any", "typing"),
        "JSONObject": PythonType("dict[str, Any]", imports=[("typing", "Any")]),
        "TimelessDate": PythonType("date", "datetime"),
        "Duration": PythonType("float"),
        "DateTimeOrDuration": PythonType("Any", "typing"),
        "TimelessDateOrDuration": PythonType("Any", "typing"),
        # Stubbed by codegen.OPAQUE_INPUTS: Linear's filter graph is too large and
        # too deeply nested to expand. Callers pass plain dicts; Linear validates.
        "IssueFilter": PythonType("dict[str, Any]", imports=[("typing", "Any")]),
        "TeamFilter": PythonType("dict[str, Any]", imports=[("typing", "Any")]),
    }

    def _print_scalar_type(self, type_: GraphQLScalar) -> str:
        # Mapped scalars need no NewType alias; emitting one would shadow the import.
        if type_.name in self.SCALARS_TO_PYTHON_TYPES:
            return ""
        return super()._print_scalar_type(type_)

    @staticmethod
    def _imports_for(mapped: object) -> list[tuple[str, str]]:
        # Entries inherited from PythonPlugin are strawberry's own PythonType, which
        # only carries `.module`; ours carry an explicit `.imports`.
        explicit = getattr(mapped, "imports", None)
        if explicit is not None:
            return explicit
        module = getattr(mapped, "module", None)
        return [(module, mapped.type)] if module else []

    def _get_type_name(self, type_: GraphQLType) -> str:
        if (
            isinstance(type_, GraphQLScalar)
            and type_.name in self.SCALARS_TO_PYTHON_TYPES
        ):
            mapped = self.SCALARS_TO_PYTHON_TYPES[type_.name]
            for module, name in self._imports_for(mapped):
                self.imports[module].add(name)
            return mapped.type

        # `strawberry.Maybe[str | None]` round-trips into a doubly-wrapped optional.
        # Collapse it: Optional[Optional[X]] and Optional[X] mean the same thing here.
        if isinstance(type_, GraphQLOptional) and isinstance(
            type_.of_type, GraphQLOptional
        ):
            return self._get_type_name(type_.of_type)

        return super()._get_type_name(type_)

    def generate_code(
        self,
        types: list[GraphQLType],
        operation: object,
    ) -> list[object]:
        # Print the bodies first so self.imports is fully populated, then emit only
        # the imports the bodies actually reference. Strawberry registers `List` for
        # every list field but emits lowercase `list[...]`, which would otherwise
        # leave a dangling unused import in every generated module.
        bodies = [t for t in (self._print_type(type_) for type_ in types) if t]
        body = "\n\n".join(bodies)

        used_imports: dict[str, set[str]] = {}
        for module, names in self.imports.items():
            referenced = {n for n in names if re.search(rf"\b{re.escape(n)}\b", body)}
            if referenced:
                used_imports[module] = referenced

        lines = [
            f"from {module} import {', '.join(sorted(names))}"
            for module, names in used_imports.items()
        ]
        lines.append("from gtm_linear.models import LinearModel")
        code = "\n".join(lines) + "\n\n" + body

        return [CodegenFile(self.outfile_name, code.strip())]

    def _print_object_type(self, type_: GraphQLObjectType) -> str:
        fields = "\n".join(
            self._print_field(field)
            for field in type_.fields
            if field.name != "__typename"
        )
        indent = 4 * " "
        lines = [f"class {type_.name}(LinearModel):"]
        if type_.graphql_typename:
            lines.append(
                textwrap.indent(
                    f'"""GraphQL type: {type_.graphql_typename}."""', indent
                )
            )
        lines.append(textwrap.indent(fields, indent))
        return "\n".join(lines)
