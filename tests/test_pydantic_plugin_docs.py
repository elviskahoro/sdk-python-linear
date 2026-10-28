"""Schema descriptions land as docstrings on generated models."""

from __future__ import annotations

import ast
from pathlib import Path

from gtm_linear._generated import fragments
from gtm_linear._generated.CreateComment import CreateCommentResultCommentCreate
from gtm_linear._generated.CreateIssue import SLADayCountType
from scripts import pydantic_plugin as pp


def test_docstring_escapes_quotes_and_flattens_whitespace() -> None:
    literal = pp._docstring('say "hi"\n  back\\slash')
    assert literal == '"""say \\"hi\\" back\\\\slash"""'
    assert eval(literal) == 'say "hi" back\\slash'  # noqa: S307


def test_enum_class_docstring_is_emitted() -> None:
    assert SLADayCountType.__doc__ == "Which day count to use for SLA calculations."


def test_nested_inline_classes_are_documented() -> None:
    assert fragments.IssueFieldsState.__doc__
    assert (
        CreateCommentResultCommentCreate.__doc__ == "The result of a comment mutation."
    )


def test_deprecation_reason_is_appended() -> None:
    schema = pp._schema()
    deprecated = next(
        f
        for t in schema.type_map.values()
        if hasattr(t, "fields") and not t.name.startswith("__")
        for f in t.fields.values()
        if getattr(f, "deprecation_reason", None)
    )
    assert pp._describe(deprecated).endswith(
        f"Deprecated: {deprecated.deprecation_reason}",
    )


def test_docstring_prose_does_not_keep_imports_alive() -> None:
    """Generated modules never import a name that only docstring text mentions."""
    # Only the stdlib import modules are scanned: the `from .fragments import …`
    # lines carry module-level unused names that predate the docstring work.
    generated = Path(__file__).resolve().parent.parent / "gtm_linear" / "_generated"
    scanned = {"typing", "datetime", "uuid", "decimal", "enum"}
    for path in sorted(generated.glob("*.py")):
        tree = ast.parse(path.read_text())
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module in scanned
            for alias in node.names
        }
        referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert not imported - referenced, f"{path.name}: {imported - referenced}"
