"""Regeneration after a small schema or operation change, on scratch copies."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from poc.ariadne import generate

from .conftest import REPO_ROOT

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    schema = tmp_path / "linear.graphql"
    shutil.copyfile(REPO_ROOT / "schema" / "linear.graphql", schema)
    operations = tmp_path / "operations"
    shutil.copytree(REPO_ROOT / "operations", operations)
    return schema, operations


def _generate(tmp_path: Path, schema: Path, operations: Path) -> Path:
    settings = generate._operation_settings(str(tmp_path))
    settings["schema_path"] = str(schema)
    settings["queries_path"] = str(operations)
    generate._run_ariadne(settings)
    return tmp_path / generate.PACKAGE


def test_schema_and_operation_change_regenerates(
    tmp_path: Path,
    inputs: tuple[Path, Path],
) -> None:
    schema, operations = inputs
    text = schema.read_text()
    anchor = "type Issue implements Node {\n"
    assert anchor in text
    schema.write_text(
        text.replace(anchor, anchor + '  "PoC probe."\n  pocProbe: [DateTime!]\n', 1),
    )
    fragments = operations / "_fragments.graphql"
    fragments.write_text(
        fragments.read_text().replace(
            "fragment IssueFields on Issue {\n  id\n",
            "fragment IssueFields on Issue {\n  id\n  pocProbe\n  dueDate\n",
        ),
    )

    package = _generate(tmp_path, schema, operations)
    source = (package / "fragments.py").read_text()
    # Aliases come from the base model's alias_generator (use_alias_generator).
    assert "poc_probe: Optional[list[datetime]]" in source
    assert "due_date: Optional[date]" in source
    # The committed client is now stale relative to these inputs.
    assert "fragments.py" in generate._diff(
        package,
        generate.POC_DIR / generate.PACKAGE,
    )


def test_unchanged_inputs_regenerate_byte_identically(
    tmp_path: Path,
    inputs: tuple[Path, Path],
) -> None:
    package = _generate(tmp_path, *inputs)
    # Only the "# Source:" comment depends on the input path; normalise it.
    committed = generate.POC_DIR / generate.PACKAGE
    for path in package.glob("*.py"):
        fresh = path.read_text().replace(str(inputs[1]), "../../operations")
        fresh = fresh.replace(str(inputs[0]), "../../schema/linear.graphql")
        assert fresh == (committed / path.name).read_text(), path.name


def test_invalid_operation_fails_generation(
    tmp_path: Path,
    inputs: tuple[Path, Path],
) -> None:
    schema, operations = inputs
    (operations / "Broken.graphql").write_text("query Broken { viewer { nope } }\n")
    with pytest.raises(SystemExit, match="nope"):
        _generate(tmp_path, schema, operations)


def test_generates_and_imports_inside_the_sdk_package(tmp_path: Path) -> None:
    """Intended layout for a migration: gtm_linear/_ariadne/ next to client.py."""
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    sdk = tmp_path / "gtm_linear"
    shutil.copytree(
        REPO_ROOT / "gtm_linear",
        sdk,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    settings = generate._operation_settings(str(sdk))
    settings["target_package_name"] = "_ariadne"
    settings["schema_path"] = str(REPO_ROOT / "schema" / "linear.graphql")
    settings["queries_path"] = str(REPO_ROOT / "operations")
    generate._run_ariadne(settings)
    probe = (
        "import gtm_linear, gtm_linear._ariadne as a;"
        f"assert gtm_linear.__file__.startswith({str(tmp_path)!r});"
        "c = a.LinearAriadneClient(gtm_linear.LinearClient('k'));"
        "print(type(c).__mro__[1].__module__)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    assert out.stdout.strip() == "gtm_linear._ariadne.linear_base_client"
