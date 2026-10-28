"""Schema descriptions land as docstrings on generated models."""

from __future__ import annotations

from scripts import pydantic_plugin as pp
from gtm_linear._generated import fragments
from gtm_linear._generated.CreateComment import CreateCommentResultCommentCreate
from gtm_linear._generated.CreateIssue import SLADayCountType


def test_docstring_escapes_quotes_and_newlines() -> None:
    literal = pp._docstring('say "hi"\nback\\slash')
    assert literal == '"""say \\"hi\\"\\nback\\\\slash"""'
    assert eval(literal) == 'say "hi"\nback\\slash'  # noqa: S307


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
