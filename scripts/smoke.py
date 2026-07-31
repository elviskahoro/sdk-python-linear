"""Live smoke test against the Linear API.

Usage:
    LINEAR_API_KEY=lin_api_xxx uv run python scripts/smoke.py [--team-key ENG]

Exercises: viewer query, get_team_by_key, list_issues, search_issues.
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

from gtm_linear import (  # noqa: E402
    IssueCreateInput,
    IssueFilterInput,
    LinearClient,
    LinearMutations,
    LinearQueries,
    StringComparatorInput,
    TeamFilterInput,
)


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
        msg = "LINEAR_API_KEY not set"
        raise SystemExit(msg)

    async with LinearClient(api_key=api_key) as client:
        queries = LinearQueries(client)

        viewer = await client.execute_async("query { viewer { id name email } }")
        print(f"viewer: {viewer['viewer']}")

        if args.team_key:
            team = await queries.get_team_by_key(args.team_key)
            if team is None:
                msg = f"No team found with key {args.team_key!r}"
                raise SystemExit(msg)
            team_id, team_name = team.id, team.name
        else:
            data = await client.execute_async(
                "query { teams(first: 1) { nodes { id name key } } }",
            )
            node = data["teams"]["nodes"][0]
            team_id, team_name = node["id"], node["name"]
            print(f"using first team: {node['key']} ({team_name})")

        team = await queries.get_team(team_id)
        print(f"get_team: {team}")

        issues = await queries.list_issues_page(
            IssueFilterInput(  # type: ignore[call-arg]
                team=TeamFilterInput(  # type: ignore[call-arg]
                    id=StringComparatorInput(eq=team_id),  # type: ignore[call-arg]
                ),
            ),
            first=5,
        )
        print(f"list_issues: {len(issues.nodes)} returned")
        for issue in issues.nodes[:3]:
            print(f"  - {issue.identifier}: {issue.title}")

        results = await queries.search_issues(args.search)
        print(f"search_issues({args.search!r}): {len(results)} matches")

        if args.create:
            mutations = LinearMutations(client)
            created = await mutations.create_issue(
                IssueCreateInput(  # pyright: ignore[reportCallIssue]
                    title="[smoke test] delete me",  # pyright: ignore[reportCallIssue]
                    teamId=team_id,  # pyright: ignore[reportCallIssue]
                    description="created by scripts/smoke.py",  # pyright: ignore[reportCallIssue]
                ),
            )
            print(f"created: {created.identifier} ({created.id})")
            deleted = await mutations.delete_issue(created.id)
            print(f"deleted: {deleted}")


if __name__ == "__main__":
    asyncio.run(main())
