"""Guards that the committed generated code matches what the schema produces.

This is the test the old hand-maintained ``generated_types.py`` never had. Without
it, a well-meaning hand-edit to a generated model silently becomes the new source of
truth and the schema drifts away underneath it again.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _check(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, f"scripts/{script}", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_operations_match_spec() -> None:
    """operations/*.graphql must match what the spec plus the schema produce."""
    result = _check("gen_operations.py")
    assert result.returncode == 0, (  # noqa: S101
        "Operation documents are stale or hand-edited.\n"
        "Run: uv run python scripts/gen_operations.py\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_generated_code_matches_schema() -> None:
    """Regenerating from the pinned schema must reproduce the committed output."""
    result = _check("codegen.py")
    assert result.returncode == 0, (  # noqa: S101
        "Generated code is stale or hand-edited.\n"
        "Run: uv run python scripts/codegen.py\n\n"
        f"{result.stdout}\n{result.stderr}"
    )
