"""Regenerate (or verify) the Ariadne Codegen proof-of-concept clients.

Runs from the root project's environment and shells out to ariadne-codegen in the
isolated ``poc/ariadne`` uv project (see ``pyproject.toml`` there for why it cannot
share the root venv). The generator is always invoked with ``poc/ariadne`` as its
working directory, so the relative schema/operation paths in the config - and the
``# Source:`` comment Ariadne stamps into every module - are machine-independent.

Usage (from the repo root):
    uv run python poc/ariadne/generate.py                 # operation-based client
    uv run python poc/ariadne/generate.py --check         # fail if it drifted
    uv run python poc/ariadne/generate.py --schema-only   # custom-operation client
                                                          # into poc/ariadne/build/
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import cast

POC_DIR = Path(__file__).resolve().parent
CONFIG = POC_DIR / "pyproject.toml"
PACKAGE = "linear_ariadne"
SCHEMA_ONLY_PACKAGE = "linear_schema_only"
BUILD_DIR = POC_DIR / "build"


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return str(value)


def _write_config(path: Path, settings: dict[str, object]) -> None:
    """Write a ``[tool.ariadne-codegen]`` config (scalars as sub-tables)."""
    settings = dict(settings)
    scalars = cast("dict[str, dict[str, object]]", settings.pop("scalars", {}))
    lines = ["[tool.ariadne-codegen]"]
    lines += [f"{key} = {_toml_value(value)}" for key, value in settings.items()]
    for name, data in scalars.items():
        lines.append(f"\n[tool.ariadne-codegen.scalars.{name}]")
        lines += [f"{key} = {_toml_value(value)}" for key, value in data.items()]
    path.write_text("\n".join(lines) + "\n")


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        msg = "uv not found on PATH; it runs the isolated generator environment"
        raise SystemExit(msg)
    return uv


def _run_ariadne(settings: dict[str, object]) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        suffix=".toml",
        dir=POC_DIR,
        delete=False,
    ) as handle:
        config = Path(handle.name)
    try:
        _write_config(config, settings)
        env = os.environ.copy()
        # Plugins are imported by module path; make poc/ariadne importable.
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [str(POC_DIR), env.get("PYTHONPATH", "")]),
        )
        # Run the generator in its own locked venv, never the root one.
        env.pop("VIRTUAL_ENV", None)
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [
                _uv(),
                "run",
                "--project",
                str(POC_DIR),
                "--locked",
                "ariadne-codegen",
                "--config",
                config.name,
            ],
            cwd=POC_DIR,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    finally:
        config.unlink(missing_ok=True)
    if result.returncode != 0:
        msg = f"ariadne-codegen failed:\n{result.stdout}\n{result.stderr}"
        raise SystemExit(msg)


def _operation_settings(target_path: str) -> dict[str, object]:
    settings = dict(tomllib.loads(CONFIG.read_text())["tool"]["ariadne-codegen"])
    settings["target_package_path"] = target_path
    return settings


def generate(target_path: str = ".") -> Path:
    """Generate the operation-based client; returns the package directory."""
    package_dir = POC_DIR / target_path / PACKAGE
    # Start clean so a renamed operation cannot leave an orphan module behind.
    shutil.rmtree(package_dir, ignore_errors=True)
    _run_ariadne(_operation_settings(target_path))
    return package_dir


def generate_schema_only() -> Path:
    """Generate the schema-only (custom-operation builder) client into build/."""
    BUILD_DIR.mkdir(exist_ok=True)
    package_dir = BUILD_DIR / SCHEMA_ONLY_PACKAGE
    shutil.rmtree(package_dir, ignore_errors=True)
    base = _operation_settings("build")
    settings: dict[str, object] = {
        "schema_path": base["schema_path"],
        "target_package_name": SCHEMA_ONLY_PACKAGE,
        "target_package_path": "build",
        "client_name": "LinearSchemaOnlyClient",
        # Stock Ariadne base client (httpx), so this mode is evaluated exactly as
        # documented. The operation-based client shows the LinearClient adapter.
        "files_to_include": base["files_to_include"],
        "enable_custom_operations": True,
        "include_comments": "stable",
        "multipart_uploads": False,
        "scalars": base["scalars"],
    }
    _run_ariadne(settings)
    return package_dir


def _diff(expected: Path, actual: Path) -> list[str]:
    stale: list[str] = []
    expected_names = {p.name for p in expected.glob("*.py")}
    actual_names = {p.name for p in actual.glob("*.py")} if actual.exists() else set()
    stale.extend(sorted(expected_names ^ actual_names))
    stale.extend(
        name
        for name in sorted(expected_names & actual_names)
        if not filecmp.cmp(expected / name, actual / name, shallow=False)
    )
    return stale


def main() -> None:
    """Parse arguments and run the requested generation mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true")
    group.add_argument("--schema-only", action="store_true")
    args = parser.parse_args()

    if args.schema_only:
        print(f"generated {generate_schema_only().relative_to(POC_DIR.parent.parent)}")
        return
    if not args.check:
        print(f"generated {generate().relative_to(POC_DIR.parent.parent)}")
        return

    scratch = POC_DIR / ".check"
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir()
    try:
        fresh = generate(".check")
        stale = _diff(fresh, POC_DIR / PACKAGE)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if stale:
        msg = (
            "poc/ariadne generated client is out of date: "
            + ", ".join(stale)
            + "\nrun: uv run python poc/ariadne/generate.py"
        )
        raise SystemExit(msg)
    print("poc/ariadne generated client is up to date")


if __name__ == "__main__":
    sys.exit(main())
