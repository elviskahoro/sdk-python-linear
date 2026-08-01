"""Refresh schema/linear.graphql, the pin everything else is generated from.

Two sources, because they answer different questions:

``--source github`` (default)
    Linear publishes the SDL its own TypeScript SDK is built from. Unauthenticated,
    reproducible, and identical for everyone — the right default for CI and for a
    library whose generated output is committed and diffed.

``--source introspect``
    Introspects ``https://api.linear.app/graphql`` with your API key. This is the
    schema *your workspace actually serves*, which is what you want when chasing a
    discrepancy, or if your workspace sees fields the public SDL does not.

    Caveat worth knowing: Linear answers introspection **without** authentication, so
    this path succeeds even with a missing or expired key — it just returns the
    anonymous view, which is smaller than the published SDL (~1.15 MB vs ~1.27 MB).
    A successful run is therefore not evidence that your key works. Use
    ``scripts/smoke.py`` for that. Prefer ``--source github`` for anything committed.

After refreshing, regenerate — the schema is an input to both stages:

    uv run python scripts/gen_operations.py
    uv run python scripts/codegen.py
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx
from graphql import build_client_schema, get_introspection_query, print_schema

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
SCHEMA_FILE = REPO_ROOT / "schema" / "linear.graphql"

GITHUB_URL = "https://raw.githubusercontent.com/linear/linear/master/packages/sdk/src/schema.graphql"
API_URL = "https://api.linear.app/graphql"


def from_github() -> str:
    response = httpx.get(GITHUB_URL, timeout=120.0, follow_redirects=True)
    response.raise_for_status()
    return response.text


def from_introspection(api_key: str) -> str:
    response = httpx.post(
        API_URL,
        json={"query": get_introspection_query(descriptions=True)},
        headers={"Authorization": api_key, "Content-Type": "application/json"},
        timeout=120.0,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
        msg = f"introspection failed: {messages}"
        raise SystemExit(msg)
    return print_schema(build_client_schema(body["data"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        choices=("github", "introspect"),
        default="github",
        help="where to read the schema from (default: github)",
    )
    args = parser.parse_args()

    if args.source == "github":
        sdl = from_github()
    else:
        api_key = os.environ.get("LINEAR_API_KEY")
        if not api_key:
            msg = "LINEAR_API_KEY is required for --source introspect"
            raise SystemExit(msg)
        sdl = from_introspection(api_key)

    previous = SCHEMA_FILE.read_text() if SCHEMA_FILE.exists() else ""
    SCHEMA_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_FILE.write_text(sdl)

    if previous == sdl:
        print(f"schema unchanged ({len(sdl):,} bytes)")
    else:
        delta = len(sdl) - len(previous)
        print(f"schema updated from {args.source}: {len(sdl):,} bytes ({delta:+,})")
        print(
            "now run: uv run python scripts/gen_operations.py && "
            "uv run python scripts/codegen.py"
        )


if __name__ == "__main__":
    main()
