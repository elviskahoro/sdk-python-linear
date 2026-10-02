"""The BUG-8 rewrite: deprecated scalar class form to ``StrawberryConfig``.

``strawberry schema-codegen`` declares custom scalars as
``X = strawberry.scalar(NewType("X", object), ...)`` — the class form strawberry
deprecates in favour of ``StrawberryConfig.scalar_map`` and will remove. codegen.py
rewrites that emission before it is committed to ``gtm_linear/_schema.py``. These
tests pin the rewrite and the patch stage that wires it into the generated
``Schema`` call, so a future strawberry release that changes the emission fails
loudly here instead of silently regenerating the deprecated form.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts import codegen  # noqa: E402

# Byte-for-byte what `strawberry schema-codegen` emits for `scalar IssueFilter`
# before ruff formats it: one line, identity serialize/parse_value lambdas.
RAW_EMISSION = """\
from __future__ import annotations
import strawberry
from typing import NewType

IssueFilter = strawberry.scalar(NewType("IssueFilter", object), serialize=lambda v: v, parse_value=lambda v: v)

schema = strawberry.Schema(query=Query)
"""


def test_deprecated_class_form_is_unwrapped_to_newtype() -> None:
    source, scalars = codegen._migrate_scalar_definitions(RAW_EMISSION)  # noqa: SLF001

    assert 'IssueFilter = NewType("IssueFilter", object)' in source  # noqa: S101
    assert "strawberry.scalar(NewType" not in source  # noqa: S101
    assert scalars, "the unwrapped scalar must be reported for the scalar_map"  # noqa: S101
    ast.parse(source)  # the rewrite must leave valid Python


def test_kwargs_are_carried_verbatim_for_the_scalar_map() -> None:
    """serialize/parse_value (and anything strawberry adds later) survive as-is
    into the `strawberry.scalar(name=..., ...)` entry built from them.
    """
    _, scalars = codegen._migrate_scalar_definitions(RAW_EMISSION)  # noqa: SLF001

    assert scalars == [  # noqa: S101
        ("IssueFilter", "serialize=lambda v: v, parse_value=lambda v: v"),
    ]


def test_source_without_scalars_passes_through_untouched() -> None:
    source, scalars = codegen._migrate_scalar_definitions("x = 1\n")  # noqa: SLF001

    assert (source, scalars) == ("x = 1\n", [])  # noqa: S101


def test_unrecognised_emission_fails_loudly() -> None:
    """A supertype other than `object` means the rewrite no longer matches what
    schema-codegen emits; refuse to ship the deprecated form silently.
    """
    drifted = RAW_EMISSION.replace(
        'NewType("IssueFilter", object)',
        'NewType("IssueFilter", bytes)',
    )

    with pytest.raises(SystemExit, match="scalar migration"):
        codegen._migrate_scalar_definitions(drifted)  # noqa: SLF001


def test_wrapped_emission_is_caught_by_the_guard() -> None:
    """Line-wrapping the call defeats the one-line rewrite; the guard must
    still recognise the class form so the run fails loudly instead of
    silently shipping the deprecated emission.
    """
    wrapped = RAW_EMISSION.replace(
        "strawberry.scalar(NewType(",
        "strawberry.scalar(\n    NewType(",
    )

    with pytest.raises(SystemExit, match="scalar migration"):
        codegen._migrate_scalar_definitions(wrapped)  # noqa: SLF001


def test_divergent_assignment_and_newtype_names_are_rejected() -> None:
    """If the assignment target ever diverges from the NewType's name, the
    rewrite must decline — otherwise the scalar is silently renamed in the
    generated schema. The leftover class form then trips the drift guard.
    """
    divergent = RAW_EMISSION.replace(
        'IssueFilter = strawberry.scalar(NewType("IssueFilter", object)',
        'Alias = strawberry.scalar(NewType("IssueFilter", object)',
    )

    with pytest.raises(SystemExit, match="scalar migration"):
        codegen._migrate_scalar_definitions(divergent)  # noqa: SLF001


def test_name_kwarg_in_the_emission_is_rejected() -> None:
    """A `name=` kwarg would duplicate the one the scalar_map entry adds,
    leaving unparseable output; refuse it at generation time.
    """
    renamed = RAW_EMISSION.replace(
        "serialize=lambda v: v",
        'name="Renamed", serialize=lambda v: v',
    )

    with pytest.raises(SystemExit, match="name=` kwarg"):
        codegen._migrate_scalar_definitions(renamed)  # noqa: SLF001


def test_registration_tracks_unwrapped_scalars_not_any_newtype(
    tmp_path: Path,
) -> None:
    """A NewType the migration did not produce must stay out of types=[...]:
    registered without a scalar_map entry, it would die with an opaque
    strawberry error instead of one of the guards' messages.
    """
    (tmp_path / "pruned.graphql").write_text(PRUNED_SDL)
    module = tmp_path / "_linear_schema.py"
    module.write_text(
        RAW_PATCH_INPUT.replace(
            'TimelessDate = strawberry.scalar(NewType("TimelessDate", object))',
            'TimelessDate = strawberry.scalar(NewType("TimelessDate", object))\n\n'
            'Duration = NewType("Duration", object)',
        ),
    )
    codegen._patch_schema_module(module)  # noqa: SLF001
    patched = module.read_text()

    # The exact registration set: the two unwrapped scalars and the input,
    # with the unmanaged Duration alias left out.
    assert "types=[IssueFilter, IssueSortInput, TimelessDate]" in patched  # noqa: S101


def test_patch_stage_fails_loudly_when_the_schema_call_already_has_config(
    tmp_path: Path,
) -> None:
    """Appending our config= next to an upstream one would duplicate the
    keyword argument and leave unparseable output; stop at generation time.
    """
    (tmp_path / "pruned.graphql").write_text("type Query { ok: Int }")
    module = tmp_path / "_linear_schema.py"
    module.write_text(
        RAW_PATCH_INPUT.replace(
            "schema = strawberry.Schema(query=Query)",
            "schema = strawberry.Schema(query=Query, config=None)",
        ),
    )

    with pytest.raises(SystemExit, match="own config= kwarg"):
        codegen._patch_schema_module(module)  # noqa: SLF001


# A minimal schema-codegen emission exercising the whole patch stage: one
# scalar with kwargs, one without (the `extra == ""` branch), an input the
# BUG-6 registration must pick up, and a one-line Schema call to rewrite.
RAW_PATCH_INPUT = """\
from __future__ import annotations
import strawberry
from typing import NewType

IssueFilter = strawberry.scalar(NewType("IssueFilter", object), serialize=lambda v: v, parse_value=lambda v: v)

TimelessDate = strawberry.scalar(NewType("TimelessDate", object))

@strawberry.input
class IssueSortInput:
    order: str | None

@strawberry.type
class Query:
    issues: int | None

schema = strawberry.Schema(query=Query)
"""

PRUNED_SDL = """\
scalar IssueFilter

type Query {
  issues(filter: IssueFilter): Int
}
"""


def _patched_module(tmp_path: Path) -> str:
    """Run the patch stage over RAW_PATCH_INPUT and return what it wrote."""
    (tmp_path / "pruned.graphql").write_text(PRUNED_SDL)
    module = tmp_path / "_linear_schema.py"
    module.write_text(RAW_PATCH_INPUT)
    codegen._patch_schema_module(module)  # noqa: SLF001
    return module.read_text()


def test_patch_stage_wires_scalars_into_the_schema_config(tmp_path: Path) -> None:
    patched = _patched_module(tmp_path)

    assert (  # noqa: S101
        "from strawberry.schema.config import StrawberryConfig" in patched
    )
    assert 'IssueFilter = NewType("IssueFilter", object)' in patched  # noqa: S101
    assert "strawberry.scalar(NewType" not in patched  # noqa: S101
    assert "config=StrawberryConfig(scalar_map={" in patched  # noqa: S101
    assert (  # noqa: S101
        'IssueFilter: strawberry.scalar(name="IssueFilter"'
        ", serialize=lambda v: v, parse_value=lambda v: v)" in patched
    )
    # A kwargs-less emission becomes a name-only definition.
    assert 'TimelessDate: strawberry.scalar(name="TimelessDate")' in patched  # noqa: S101
    # BUG-6: the unwrapped aliases and the input all stay registered.
    assert "types=[IssueFilter, IssueSortInput, TimelessDate]" in patched  # noqa: S101


def test_patch_stage_fails_loudly_without_an_import_strawberry_line(
    tmp_path: Path,
) -> None:
    """Nothing to hang the StrawberryConfig import on; refuse to emit a module
    that would NameError only at import time."""
    (tmp_path / "pruned.graphql").write_text("type Query { ok: Int }")
    module = tmp_path / "_linear_schema.py"
    module.write_text(RAW_PATCH_INPUT.replace("import strawberry\n", ""))

    with pytest.raises(SystemExit, match="import strawberry"):
        codegen._patch_schema_module(module)  # noqa: SLF001


def test_patch_stage_fails_loudly_without_a_one_line_schema_call(
    tmp_path: Path,
) -> None:
    """A wrapped Schema() call defeats the rewrite; the scalars would ship
    unregistered, so the run must stop at generation time."""
    (tmp_path / "pruned.graphql").write_text("type Query { ok: Int }")
    module = tmp_path / "_linear_schema.py"
    module.write_text(
        RAW_PATCH_INPUT.replace(
            "schema = strawberry.Schema(query=Query)",
            "schema = strawberry.Schema(\n    query=Query,\n)",
        ),
    )

    with pytest.raises(SystemExit, match="one-line strawberry.Schema"):
        codegen._patch_schema_module(module)  # noqa: SLF001
