"""One definition of "deprecated" for the generation pipeline.

Both ``gen_operations.py`` (spec time) and ``codegen.py`` (model time), plus the
weekly schema-drift test, call :func:`find_deprecations`, so they cannot disagree.
Members are keyed ``Type.member``; list ones that are knowingly kept in
``allow_deprecated`` at the top of ``operations/_spec.toml``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import graphql.language.ast as gql_ast
from graphql import (
    GraphQLEnumType,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNamedType,
    GraphQLNonNull,
    GraphQLSchema,
    TypeInfo,
    TypeInfoVisitor,
    Visitor,
    parse,
    visit,
)

if TYPE_CHECKING:
    from collections.abc import Collection

SPEC_FILE = Path(__file__).resolve().parent.parent / "operations" / "_spec.toml"


def _unwrap(type_: object) -> GraphQLNamedType | None:
    while isinstance(type_, (GraphQLNonNull, GraphQLList)):
        type_ = type_.of_type
    return type_ if isinstance(type_, GraphQLNamedType) else None


def deprecation_allowlist() -> set[str]:
    """Read ``allow_deprecated`` (``"Type.member"`` strings) from the operations spec."""
    spec = SPEC_FILE
    if not spec.exists():
        return set()
    return set(tomllib.loads(spec.read_text()).get("allow_deprecated", []))


def find_deprecations(
    schema: GraphQLSchema,
    operations: list[tuple[str, str]],
    opaque_inputs: Collection[str] = frozenset(),
) -> list[str]:
    """Report deprecated fields, arguments, enum values and input fields in use.

    Entries look like ``op.graphql: Type.field (reason)``; anything listed in
    ``allow_deprecated`` in ``operations/_spec.toml`` is skipped.
    """
    allowed = deprecation_allowlist()
    found: set[str] = set()

    def note(op: str, key: str, reason: str | None) -> None:
        if key not in allowed:
            found.add(f"{op}: {key} ({reason})")

    def walk_input(op: str, type_: object, seen: set[str]) -> None:
        named = _unwrap(type_)
        if named is None or named.name in seen or named.name in opaque_inputs:
            return
        seen.add(named.name)
        if isinstance(named, GraphQLInputObjectType):
            for fname, field in named.fields.items():
                if field.deprecation_reason is not None:
                    note(op, f"{named.name}.{fname}", field.deprecation_reason)
                walk_input(op, field.type, seen)

    for name, text in operations:
        document = parse(text)
        type_info = TypeInfo(schema)

        class _Deprecations(Visitor):
            def __init__(self, type_info: TypeInfo, op: str) -> None:
                """Bind the per-document TypeInfo and operation name."""
                super().__init__()
                self._type_info = type_info
                self._op = op

            def enter_field(self, node: gql_ast.FieldNode, *_: object) -> None:
                field_def = self._type_info.get_field_def()
                parent = _unwrap(self._type_info.get_parent_type())
                if (
                    field_def is not None
                    and parent is not None
                    and field_def.deprecation_reason is not None
                ):
                    note(
                        self._op,
                        f"{parent.name}.{node.name.value}",
                        field_def.deprecation_reason,
                    )

            def enter_argument(self, node: gql_ast.ArgumentNode, *_: object) -> None:
                argument = self._type_info.get_argument()
                parent = _unwrap(self._type_info.get_parent_type())
                if (
                    argument is not None
                    and argument.deprecation_reason is not None
                    and parent is not None
                ):
                    note(
                        self._op,
                        f"{parent.name}.{node.name.value}",
                        argument.deprecation_reason,
                    )

            def enter_enum_value(self, node: gql_ast.EnumValueNode, *_: object) -> None:
                enum = _unwrap(self._type_info.get_input_type())
                if isinstance(enum, GraphQLEnumType):
                    value = enum.values.get(node.value)
                    if value is not None and value.deprecation_reason is not None:
                        note(
                            self._op,
                            f"{enum.name}.{node.value}",
                            value.deprecation_reason,
                        )

        visit(document, TypeInfoVisitor(type_info, _Deprecations(type_info, name)))
        for definition in document.definitions:
            for variable in getattr(definition, "variable_definitions", None) or []:
                node = variable.type
                while hasattr(node, "type"):
                    node = node.type
                resolved = schema.get_type(node.name.value)
                if resolved is not None:
                    walk_input(name, resolved, set())
    return sorted(found)
