"""Fixtures for the Ariadne Codegen proof of concept.

Not collected by the default ``uv run pytest`` (``testpaths = ["tests"]``); run with
``uv run pytest poc/ariadne/tests``. The repo-root conftest still applies, so the
``httpx2`` alias is installed before respx is imported.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import httpx

POC_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = POC_DIR.parent.parent


@pytest.fixture(scope="session")
def schema_only_package() -> Path:
    """Regenerate the schema-only client for this session.

    Not committed (2.3 MB / ~55k lines for Linear's schema), so it is built fresh;
    a generator failure is a test error, never a skip.
    """
    subprocess.run(
        [sys.executable, str(POC_DIR / "generate.py"), "--schema-only"],
        check=True,
        cwd=REPO_ROOT,
    )
    return POC_DIR / "build" / "linear_schema_only"


def request_body(request: httpx.Request) -> dict[str, Any]:
    """Decode a captured GraphQL POST body."""
    return json.loads(request.content)
