"""Tests for the generated Strawberry schema mirror.

``gtm_linear._schema`` is generated from Linear's SDL and shipped under the
``[strawberry]`` extra, for callers re-exposing Linear data through their own
GraphQL server. It is not imported at runtime by the SDK itself, so it is skipped
when the extra is not installed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("strawberry", reason="requires the [strawberry] extra")

from gtm_linear import _schema  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# The deprecation gate: warnings attributed to the generated module become
# errors in a subprocess. Shared by the two gate tests below so the gate and
# its proof-of-life cannot drift apart.
DEPRECATION_GATE = "error::DeprecationWarning:gtm_linear._schema"


def _import_schema_module(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-W", DEPRECATION_GATE, "-c", "import gtm_linear._schema"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_schema_builds() -> None:
    assert _schema.schema is not None


def test_sdl_matches_linear_scalar_types() -> None:
    """The generated SDL must reproduce Linear's own types, not our guesses at them.

    Each assertion here is a field the hand-written types previously got wrong.
    """
    sdl = str(_schema.schema)

    # priority is Float in Linear; the old hand-written model typed it int.
    assert "priority: Float!" in sdl
    # state is non-null, and a WorkflowState object rather than a flattened string.
    assert "state: WorkflowState!" in sdl
    # ids are ID, not String.
    assert "id: ID!" in sdl


def test_generated_schema_preserves_root_field_arguments() -> None:
    sdl = str(_schema.schema)

    assert "issue(id: String!): Issue!" in sdl
    assert "issueCreate(input: IssueCreateInput!): IssuePayload!" in sdl
    assert "issues(after: String = null" in sdl


def test_custom_scalars_resolve_through_scalar_map() -> None:
    """Scalar registration moved from `strawberry.scalar(cls)` (deprecated) to
    `StrawberryConfig.scalar_map`; Linear's custom scalars must stay usable as
    field arguments rather than failing to resolve.
    """
    sdl = str(_schema.schema)

    assert "filter: IssueFilter = null" in sdl  # noqa: S101
    assert "dueDate: TimelessDate" in sdl  # noqa: S101


def test_schema_module_emits_no_deprecation_warnings() -> None:
    """The generated module must stay on strawberry's supported API.

    ``strawberry.scalar()``'s class form warned (and will be removed). Checked in
    a subprocess so a mid-import failure cannot leave this interpreter holding a
    half-reloaded module. The error filter is scoped to warnings attributed to
    ``gtm_linear._schema`` — this fails on any strawberry API *our module* uses
    that falls out of favour, while deprecations raised inside strawberry or a
    transitive dependency's own import machinery are not this test's business.
    """
    result = _import_schema_module(REPO_ROOT)
    assert result.returncode == 0, (  # noqa: S101
        f"gtm_linear/_schema.py tripped a deprecation:\n{result.stderr}"
    )


def test_deprecation_gate_fires_on_the_deprecated_scalar_form(
    tmp_path: Path,
) -> None:
    """The gate above is only useful if it is real.

    The same filter, pointed at a package whose ``_schema`` module still uses
    the deprecated ``strawberry.scalar()`` class form (cwd shadows the installed
    package), must fail. If a future strawberry changes how it attributes the
    warning, this test is what surfaces the gate going quietly blind.
    """
    package = tmp_path / "gtm_linear"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "_schema.py").write_text(
        "import strawberry\n"
        "from typing import NewType\n"
        "IssueFilter = strawberry.scalar(\n"
        '    NewType("IssueFilter", object), serialize=lambda v: v, parse_value=lambda v: v\n'
        ")\n",
    )

    result = _import_schema_module(tmp_path)
    assert result.returncode != 0, (  # noqa: S101
        "the deprecation gate no longer fires on the deprecated scalar form:\n"
        f"{result.stdout}\n{result.stderr}"
    )
    # A nonzero exit alone could be an import or syntax error in the fake
    # module; tie the failure to the deprecation itself.
    assert "DeprecationWarning" in result.stderr, (  # noqa: S101
        "the fake module failed for a non-deprecation reason:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def _input_fields(sdl: str, name: str) -> set[str]:
    """Field names declared on a GraphQL input, read from the SDL.

    Read from the SDL rather than ``field.graphql_name``: schema-codegen emits
    snake_case attributes and lets Strawberry's name converter camelCase them at
    schema-build time, so ``graphql_name`` is None on the field objects.
    """
    body = re.search(rf"input {name} \{{(.*?)\n\}}", sdl, re.S)
    assert body is not None, f"{name} missing from SDL"
    return {
        line.strip().split(":")[0]
        for line in body.group(1).strip().splitlines()
        if ":" in line
    }


def test_issue_create_input_covers_the_full_schema_surface() -> None:
    """The generated input carries every field Linear accepts, not a hand-picked few."""
    names = _input_fields(str(_schema.schema), "IssueCreateInput")

    # Fields the hand-written 8-field input omitted entirely.
    assert {"dueDate", "estimate", "parentId", "subscriberIds", "slaType"} <= names
    minimum_schema_fields = 30
    assert len(names) >= minimum_schema_fields


def test_generated_models_and_schema_agree_on_issue_fields() -> None:
    """Cross-check: the Pydantic layer and the Strawberry layer describe one API.

    Both are generated from the same SDL, so a divergence means the pipeline broke.
    """
    from gtm_linear import Issue

    sdl = str(_schema.schema)
    body = re.search(r"type Issue \{(.*?)\n\}", sdl, re.S)
    assert body is not None
    strawberry_fields = {
        line.strip().split(":")[0]
        for line in body.group(1).strip().splitlines()
        if ":" in line
    }
    pydantic_fields = {f.alias or name for name, f in Issue.model_fields.items()}
    assert pydantic_fields <= strawberry_fields


def test_generated_schema_preserves_nested_field_arguments() -> None:
    """The four ``Issue`` nested collection fields keep their full argument lists.

    Regression test for BUG-7's nested-field gap. ``attachments``/``comments``/
    ``relations``/``inverseRelations`` carry pagination/filter args in Linear's
    real schema and in the pruned SDL; the shipped Strawberry mirror must expose
    them too, or a consumer cannot express ``issue { attachments(first: 10) }``
    and queries the real Linear API validates get rejected.
    """
    sdl = str(_schema.schema)

    # attachments and comments carry the full pagination + filter argument set.
    assert (
        "attachments(after: String = null, before: String = null, "
        "filter: AttachmentFilter = null, first: Int = null, "
        "includeArchived: Boolean = null, last: Int = null, "
        "orderBy: PaginationOrderBy = null): AttachmentConnection!" in sdl
    )
    assert (
        "comments(after: String = null, before: String = null, "
        "filter: CommentFilter = null, first: Int = null, "
        "includeArchived: Boolean = null, last: Int = null, "
        "orderBy: PaginationOrderBy = null): CommentConnection!" in sdl
    )
    # relations and inverseRelations have no `filter` arg in Linear's schema,
    # so their argument list is the six pagination args only.
    assert (
        "inverseRelations(after: String = null, before: String = null, "
        "first: Int = null, includeArchived: Boolean = null, last: Int = null, "
        "orderBy: PaginationOrderBy = null): IssueRelationConnection!" in sdl
    )
    assert (
        "relations(after: String = null, before: String = null, "
        "first: Int = null, includeArchived: Boolean = null, last: Int = null, "
        "orderBy: PaginationOrderBy = null): IssueRelationConnection!" in sdl
    )


def test_shipped_schema_accepts_nested_pagination_arguments() -> None:
    """The shipped mirror must accept the queries Linear's real API accepts.

    End-to-end proof of the BUG-7 nested-field fix. The exact reproduction from
    the bug report — ``issue { attachments(first: 10) { ... } }`` — used to fail
    with ``Unknown argument 'first' on field 'Issue.attachments'``; and the
    SDK's own ``ListIssue*`` operations (composed with their shared fragments)
    must validate against the *shipped* schema, not just the pinned SDL the
    codegen pipeline validates against internally.
    """
    from graphql import build_schema, parse, validate

    from scripts import codegen

    # Validate against the schema the [strawberry] extra actually ships: build a
    # graphql-core schema from the exported SDL rather than probing the
    # Strawberry wrapper, which is what a consumer's GraphQL server would parse.
    shipped = build_schema(str(_schema.schema))

    # The bug-report reproduction: a nested pagination argument the pre-fix
    # mirror rejected. The argument-less shape still validates too, so the
    # plain read path is not regressed.
    repro = parse(
        'query { issue(id: "x") { attachments(first: 10) { nodes { id } } } }',
    )
    plain = parse('query { issue(id: "x") { attachments { nodes { id } } } }')
    assert not validate(shipped, repro)
    assert not validate(shipped, plain)

    # The SDK's own operations that traverse these nested fields, composed with
    # their shared fragments, validate against the shipped mirror.
    fragments = codegen._fragment_definitions()
    for name in (
        "ListIssueAttachments",
        "ListIssueComments",
        "ListIssueRelations",
        "ListIssueInverseRelations",
    ):
        document = parse(
            codegen._compose(codegen.OPERATIONS_DIR / f"{name}.graphql", fragments),
        )
        assert not validate(shipped, document), f"{name} rejected by shipped schema"
