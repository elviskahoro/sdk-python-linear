"""Live smoke test against the Linear API.

Usage:
    LINEAR_API_KEY=lin_api_xxx uv run python scripts/smoke.py [--team-key ENG]

Exercises: viewer, get_team_by_key, list_issues_page, search_issues.
Read-only by default. Pass --create to also create+delete a throwaway issue.

Doubles as the check that the generated types work against the real API rather than
only against mocked payloads.
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
    IssueUpdateInput,
    LinearClient,
    LinearMutations,
    LinearQueries,
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

        viewer = await queries.get_viewer()
        print(f"viewer: {viewer.name} <{viewer.email}>")

        if not args.team_key:
            msg = "--team-key is required (e.g. --team-key ENG)"
            raise SystemExit(msg)

        team = await queries.get_team_by_key(args.team_key)
        if team is None:
            msg = f"No team found with key {args.team_key!r}"
            raise SystemExit(msg)
        print(f"get_team_by_key: {team.key} ({team.name})")

        page = await queries.list_issues_page(
            {"team": {"id": {"eq": team.id}}},
            first=5,
        )
        print(f"list_issues_page: {len(page.nodes)} returned")
        for issue in page.nodes[:3]:
            print(f"  - {issue.identifier}: {issue.title} [{issue.state.name}]")
        print(f"  page_info.has_next_page: {page.page_info.has_next_page}")

        results = await queries.search_issues(args.search)
        print(f"search_issues({args.search!r}): {len(results.nodes)} matches")

        if args.create:
            mutations = LinearMutations(client)
            created = await mutations.create_issue(
                IssueCreateInput(
                    title="[smoke test] delete me",
                    team_id=team.id,
                    description="created by scripts/smoke.py",
                ),
            )
            print(f"created: {created.identifier} ({created.id})")

            comment = await mutations.create_comment(created.id, "smoke test comment")
            print(f"commented: {comment.id} at {comment.created_at}")

            updated = await mutations.update_issue(
                created.id,
                IssueUpdateInput(title="[smoke test] renamed"),
            )
            print(f"updated title: {updated.title}")

            deleted = await mutations.delete_issue(created.id)
            print(f"deleted: {deleted}")


if __name__ == "__main__":
    asyncio.run(main())
