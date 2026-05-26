"""Live smoke test against the Linear API.

Usage:
    LINEAR_API_KEY=lin_api_xxx uv run python scripts/smoke.py [--team-key ENG]

Exercises: viewer query, get_team (by key lookup), list_issues, search_issues.
Read-only by default. Pass --create to also create+delete a throwaway issue.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from src import (  # noqa: E402
    IssueCreateInput,
    LinearClient,
    LinearMutations,
    LinearQueries,
)


async def resolve_team_id(client: LinearClient, team_key: str) -> tuple[str, str]:
    data = await client.execute_async(
        "query($key: String!) { teams(filter: {key: {eq: $key}}) { nodes { id key name } } }",
        {"key": team_key},
    )
    nodes = data.get("teams", {}).get("nodes", [])
    if not nodes:
        msg = f"No team found with key {team_key!r}"
        raise SystemExit(msg)
    return nodes[0]["id"], nodes[0]["name"]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--team-key",
        help="Team key (e.g. ENG). If omitted, uses the first team.",
    )
    parser.add_argument("--search", default="bug")
    parser.add_argument(
        "--create",
        action="store_true",
        help="Create + delete a test issue.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("LINEAR_API_KEY")
    if not api_key:
        raise SystemExit("LINEAR_API_KEY not set")

    async with LinearClient(api_key=api_key) as client:
        queries = LinearQueries(client)

        viewer = await client.execute_async("query { viewer { id name email } }")
        print(f"viewer: {viewer['viewer']}")

        if args.team_key:
            team_id, team_name = await resolve_team_id(client, args.team_key)
        else:
            data = await client.execute_async(
                "query { teams(first: 1) { nodes { id name key } } }",
            )
            node = data["teams"]["nodes"][0]
            team_id, team_name = node["id"], node["name"]
            print(f"using first team: {node['key']} ({team_name})")

        team = await queries.get_team(team_id)
        print(f"get_team: {team}")

        issues = await queries.list_issues(team_id, first=5)
        print(f"list_issues: {len(issues)} returned")
        for issue in issues[:3]:
            print(f"  - {issue.identifier}: {issue.title}")

        results = await queries.search_issues(args.search)
        print(f"search_issues({args.search!r}): {len(results)} matches")

        if args.create:
            mutations = LinearMutations(client)
            created = await mutations.create_issue(
                IssueCreateInput(
                    title="[smoke test] delete me",
                    teamId=team_id,
                    description="created by scripts/smoke.py",
                ),
            )
            print(f"created: {created.identifier} ({created.id})")
            deleted = await mutations.delete_issue(created.id)
            print(f"deleted: {deleted}")


if __name__ == "__main__":
    asyncio.run(main())
