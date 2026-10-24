"""What the repository's type checker (pyright, trunk-pinned version) knows.

Runs pyright over typing_probe.py with the repo's pyrightconfig.json and the root
venv, then asserts both the revealed types and that exactly the ``# expect-error``
lines are flagged. Needs network on first run to fetch pyright via ``uv tool run``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from .conftest import REPO_ROOT

PYRIGHT = "pyright==1.1.414"  # matches .trunk/trunk.yaml
PROBE = Path(__file__).with_name("typing_probe.py")


@pytest.fixture(scope="module")
def diagnostics(schema_only_package: Path) -> list[dict[str, Any]]:
    assert schema_only_package.exists()
    result = subprocess.run(
        [
            "uv",
            "tool",
            "run",
            "--from",
            PYRIGHT,
            "pyright",
            "--pythonpath",
            str(REPO_ROOT / ".venv" / "bin" / "python"),
            "--outputjson",
            str(PROBE),
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )
    return json.loads(result.stdout)["generalDiagnostics"]


def _revealed(diagnostics: list[dict[str, Any]]) -> dict[str, str]:
    lines = PROBE.read_text().splitlines()
    revealed = {}
    for diagnostic in diagnostics:
        if diagnostic["severity"] != "information":
            continue
        line = lines[diagnostic["range"]["start"]["line"]]
        label = line.rsplit("# ", 1)[1]
        revealed[label] = diagnostic["message"].rsplit(' is "', 1)[1].rstrip('"')
    return revealed


def test_revealed_types(diagnostics: list[dict[str, Any]]) -> None:
    assert _revealed(diagnostics) == {
        "current_get_issue": "IssueFields | None",
        "current_state_type": "str",
        "ariadne_get_issue": "GetIssue",
        "ariadne_state_type": "str",
        "ariadne_assignee": "IssueFieldsAssignee | None",
        "ariadne_nodes": "list[IssueFields]",
        "ariadne_cursor": "str | None",
        "schema_only_result": "dict[str, Any]",
        "schema_only_typo": "Any",
    }


def test_exactly_the_expected_lines_are_flagged(
    diagnostics: list[dict[str, Any]],
) -> None:
    lines = PROBE.read_text().splitlines()
    expected = {i for i, line in enumerate(lines) if line.endswith("# expect-error")}
    flagged = {
        d["range"]["start"]["line"]
        for d in diagnostics
        if d["severity"] in {"error", "warning"}
    }
    assert flagged == expected
