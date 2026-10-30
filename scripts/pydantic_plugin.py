"""Strawberry codegen plugin that emits Pydantic models instead of bare classes.

Strawberry's stock ``PythonPlugin`` emits annotation-only classes, which carry no
runtime behaviour. This subclass emits ``LinearModel`` subclasses so every response
the SDK parses is validated, and so unknown fields Linear adds are ignored rather
than crashing.

It also owns the mapping for Linear's custom scalars. ``PythonPlugin`` asserts that
every non-builtin scalar has a ``python_type``, which Linear's ``JSON`` /
``JSONObject`` / ``TimelessDate`` do not, so without this the generator aborts
(U3, strawberry-graphql/strawberry#4741). ``generate_code`` also prunes the unused
``List`` import (U4, #4738).
"""

from __future__ import annotations

import re
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from graphql import (
    GraphQLField as GraphQLField_,
    GraphQLInputObjectType,
    GraphQLInterfaceType,
    GraphQLObjectType as GqlObjectType,
    GraphQLSchema,
    build_schema,
    get_named_type,
)
from strawberry.codegen import CodegenFile
from strawberry.codegen.plugins.python import (
    PythonPlugin,
    PythonType as StrawberryPythonType,
)
from strawberry.codegen.types import (
    GraphQLList,
    GraphQLObjectType,
    GraphQLOptional,
    GraphQLScalar,
)

if TYPE_CHECKING:
    from strawberry.codegen.types import (
        GraphQLEnum,
        GraphQLField,
        GraphQLOperation,
        GraphQLType,
    )

SCHEMA_FILE = Path(__file__).resolve().parent.parent / "schema" / "linear.graphql"


@lru_cache(maxsize=1)
def _schema() -> GraphQLSchema:
    return build_schema(SCHEMA_FILE.read_text())


def _describe(member: GraphQLField_) -> str | None:
    """Description plus a ``Deprecated:`` note when Linear has deprecated the member."""
    parts = [member.description] if member.description else []
    if member.deprecation_reason:
        parts.append(f"Deprecated: {member.deprecation_reason}")
    return " ".join(parts) or None


@lru_cache(maxsize=1)
def _descriptions() -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """Return (type descriptions, (type, field) descriptions) from the pinned SDL."""
    schema = _schema()
    types: dict[str, str] = {}
    fields: dict[tuple[str, str], str] = {}
    for name, gql in schema.type_map.items():
        if name.startswith("__"):
            continue
        if gql.description:
            types[name] = gql.description
        if isinstance(
            gql,
            (GqlObjectType, GraphQLInterfaceType, GraphQLInputObjectType),
        ):
            for fname, fld in gql.fields.items():
                if doc := _describe(fld):
                    fields[name, fname] = doc
        # Enum values are skipped: Linear's SDL carries no descriptions on them.
    return types, fields


def _schema_names(field: GraphQLField) -> list[str]:
    """Candidate GraphQL field names for a (possibly snake_cased) generated field."""
    head, *rest = field.name.split("_")
    camel = head + "".join(part.title() for part in rest)
    return [k for k in (field.name, camel, field.alias) if k]


def _docstring(text: str) -> str:
    """Render ``text`` as a triple-quoted literal safe to embed in generated code."""
    # SDL block descriptions carry the source's line wrapping and indentation;
    # collapse internal whitespace so every emitted docstring is one clean line.
    flattened = re.sub(r"\s+", " ", text).strip()
    escaped = flattened.replace("\\", "\\\\").replace('"', '\\"')
    return f'"""{escaped}"""'


class PythonType(StrawberryPythonType):
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
        """Record the annotation and how to import every name it references."""
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

    # Annotated with strawberry's own PythonType so the override stays compatible
    # with the base class's declaration (ClassVar[dict] is invariant, so a
    # same-shaped dict of a *different* class would be rejected). Our subclass
    # instances are valid values of that type.
    SCALARS_TO_PYTHON_TYPES: ClassVar[dict[str, StrawberryPythonType]] = {
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
        "WorkflowStateFilter": PythonType(
            "dict[str, Any]",
            imports=[("typing", "Any")],
        ),
        "UserFilter": PythonType("dict[str, Any]", imports=[("typing", "Any")]),
        "ProjectFilter": PythonType("dict[str, Any]", imports=[("typing", "Any")]),
        "IssueLabelFilter": PythonType("dict[str, Any]", imports=[("typing", "Any")]),
    }

    def _print_scalar_type(self, type_: GraphQLScalar) -> str:
        # Mapped scalars need no NewType alias; emitting one would shadow the import.
        if type_.name in self.SCALARS_TO_PYTHON_TYPES:
            return ""
        return super()._print_scalar_type(type_)

    @staticmethod
    def _imports_for(mapped: StrawberryPythonType) -> list[tuple[str, str]]:
        # Entries inherited from PythonPlugin are strawberry's own PythonType, which
        # only carries `.module`; ours carry an explicit `.imports`. The inherited
        # instances have no `imports` attribute at all, hence the getattr.
        explicit = getattr(mapped, "imports", None)
        if explicit is not None:
            return explicit
        module = mapped.module
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
        # U5 (strawberry#4737). Collapse it: Optional[Optional[X]] and Optional[X] mean the same thing here.
        if isinstance(type_, GraphQLOptional) and isinstance(
            type_.of_type,
            GraphQLOptional,
        ):
            return self._get_type_name(type_.of_type)

        return super()._get_type_name(type_)

    def generate_code(
        self,
        types: list[GraphQLType],
        operation: GraphQLOperation,
    ) -> list[CodegenFile]:
        """Emit one module whose import block matches what its body references.

        The bodies are printed first so ``self.imports`` is fully populated, then
        only the imports the bodies actually reference are emitted. Strawberry
        registers ``List`` for every list field but emits lowercase ``list[...]``,
        which would otherwise leave a dangling unused import in every generated
        module.
        """
        self._nested = self._resolve_nested(types, operation)
        bodies = [t for t in (self._print_type(type_) for type_ in types) if t]
        body = "\n\n".join(bodies)

        used_imports: dict[str, set[str]] = {}
        # Docstrings are stripped from the scan: prose like "identifier in UUID
        # v4 format" must not keep an import alive that no annotation references.
        code = re.sub(r'""".*?"""', "", body, flags=re.DOTALL)
        for module, names in self.imports.items():
            referenced = {n for n in names if re.search(rf"\b{re.escape(n)}\b", code)}
            if referenced:
                used_imports[module] = referenced

        lines = [
            f"from {module} import {', '.join(sorted(names))}"
            for module, names in used_imports.items()
        ]
        lines.append("from gtm_linear.models import LinearModel")
        code = "\n".join(lines) + "\n\n" + body

        return [CodegenFile(self.outfile_name, code.strip())]

    _gql_type: str | None = None
    _is_variables: bool = False
    _nested: dict[str, str] = {}  # noqa: RUF012 - rebound per generate_code call

    @staticmethod
    def _object_of(type_: GraphQLType) -> GraphQLObjectType | None:
        while isinstance(type_, (GraphQLOptional, GraphQLList)):
            type_ = type_.of_type
        return type_ if isinstance(type_, GraphQLObjectType) else None

    def _resolve_nested(
        self,
        types: list[GraphQLType],
        operation: GraphQLOperation,
    ) -> dict[str, str]:
        """Map each inline class name to its GraphQL type, walking down from the root.

        Strawberry only records ``graphql_typename`` on fragment classes, so inline
        classes are resolved by following each parent field through the schema.
        """
        schema = _schema()
        root = (
            schema.mutation_type if operation.kind == "mutation" else schema.query_type
        )
        resolved: dict[str, str] = {}
        if root is not None:
            resolved[f"{operation.name}Result"] = root.name
        changed = True
        while changed:
            changed = False
            for type_ in types:
                if not isinstance(type_, GraphQLObjectType):
                    continue
                parent = type_.graphql_typename or resolved.get(type_.name)
                gql = schema.type_map.get(parent or "")
                if not isinstance(gql, (GqlObjectType, GraphQLInterfaceType)):
                    continue
                for field in type_.fields:
                    child = self._object_of(field.type)
                    gql_field = next(
                        (
                            gql.fields[k]
                            for k in _schema_names(field)
                            if k in gql.fields
                        ),
                        None,
                    )
                    if child is None or gql_field is None or child.graphql_typename:
                        continue
                    name = get_named_type(gql_field.type).name
                    if resolved.get(child.name) != name:
                        resolved[child.name] = name
                        changed = True
        return resolved

    def _print_enum_type(self, type_: GraphQLEnum) -> str:
        type_docs, _ = _descriptions()
        indent = 4 * " "
        lines = [f"class {type_.name}(str, Enum):"]
        if type_.name in type_docs:
            lines += [textwrap.indent(_docstring(type_docs[type_.name]), indent), ""]
        for value in type_.values:
            lines.append(textwrap.indent(self._print_enum_value(value), indent))
        return "\n".join(lines)

    def _print_field(self, field: GraphQLField) -> str:
        line = super()._print_field(field)
        if self._is_variables:
            return self._variables_field(field, line)
        if self._gql_type is None:
            return line
        _, field_docs = _descriptions()
        doc = next(
            (
                field_docs[self._gql_type, key]
                for key in _schema_names(field)
                if (self._gql_type, key) in field_docs
            ),
            None,
        )
        return f"{line}\n{_docstring(doc)}" if doc else line

    def _print_object_type(self, type_: GraphQLObjectType) -> str:
        type_docs, _ = _descriptions()
        self._gql_type = (
            type_.graphql_typename
            or self._nested.get(type_.name)
            or (type_.name if type_.name in type_docs else None)
        )
        try:
            return self._print_object_body(type_)
        finally:
            self._gql_type = None

    @staticmethod
    def _variables_field(field: GraphQLField, line: str) -> str:
        """Print a variable as a snake_case field; nullable ones default to None.

        ``LinearModel``'s alias generator maps the name back to camelCase on the
        wire, and the default lets callers omit an optional variable entirely.
        """
        name = re.sub(r"(?<!^)(?=[A-Z])", "_", field.name).lower()
        line = line.replace(f"{field.name}:", f"{name}:", 1)
        return f"{line} = None" if isinstance(field.type, GraphQLOptional) else line

    def _print_object_body(self, type_: GraphQLObjectType) -> str:
        type_docs, _ = _descriptions()
        self._is_variables = type_.name.endswith("Variables")
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
                    f'"""GraphQL type: {type_.graphql_typename}."""',
                    indent,
                ),
            )
        elif self._gql_type and self._gql_type in type_docs:
            lines.append(textwrap.indent(_docstring(type_docs[self._gql_type]), indent))
        lines.append(textwrap.indent(fields, indent))
        return "\n".join(lines)
