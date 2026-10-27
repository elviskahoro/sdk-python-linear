"""CLI against the live API. Needs ``LINEAR_API_KEY`` (via the ``team`` fixture)."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from gtm_linear.cli import app

if TYPE_CHECKING:
    from gtm_linear._generated.fragments import TeamFields

pytestmark = pytest.mark.network

runner = CliRunner()


def _ok(args: list[str]):
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def ready_team(team: TeamFields) -> TeamFields:
    """The fresh team's seeds can lag the CLI's reads; wait until they show up."""
    for _ in range(15):
        issues = json.loads(_ok(["issues", "--team", team.key, "--json"]).stdout)
        teams = json.loads(_ok(["teams", "--json"]).stdout)
        if issues and team.key in {t["key"] for t in teams}:
            return team
        time.sleep(2)
    pytest.fail("scaffolded team never became visible to the CLI")


@pytest.mark.usefixtures("api_key")
def test_viewer_json() -> None:
    data = json.loads(_ok(["viewer", "--json"]).stdout)
    assert data["id"]
    assert "email" in data


def test_teams_json_lists_scaffolded_team(ready_team: TeamFields) -> None:
    data = json.loads(_ok(["teams", "--json"]).stdout)
    assert ready_team.key in {t["key"] for t in data}


def test_issues_json_and_issue_lookup(ready_team: TeamFields) -> None:
    listed = json.loads(_ok(["issues", "--team", ready_team.key, "--json"]).stdout)
    assert listed
    identifier = listed[0]["identifier"]
    one = json.loads(_ok(["issue", identifier, "--json"]).stdout)
    assert one["identifier"] == identifier


def test_search_json_returns_list(ready_team: TeamFields, title_prefix: str) -> None:
    # Search indexing is eventually consistent, so assert the shape, not hits.
    assert isinstance(json.loads(_ok(["search", title_prefix, "--json"]).stdout), list)


def test_writes_are_dry_run_without_apply_and_land_with_it(
    ready_team: TeamFields,
    title_prefix: str,
) -> None:
    title = f"{title_prefix} cli"
    preview = _ok(["issue", "create", "--team", ready_team.key, "--title", title])
    assert "DRY RUN" in preview.output
    before = json.loads(_ok(["issues", "--team", ready_team.key, "--json"]).stdout)
    assert title not in {i["title"] for i in before}

    created = json.loads(
        _ok(
            [
                "issue",
                "create",
                "--team",
                ready_team.key,
                "--title",
                title,
                "--apply",
                "--json",
            ],
        ).stdout,
    )
    # The apply landed (exit 0 + parseable JSON), but the listing can lag the
    # write on a young team — the lag `ready_team` polls away — so poll for
    # the title rather than assert on a single read.
    for _ in range(15):
        listed = json.loads(
            _ok(["issues", "--team", ready_team.key, "--json"]).stdout,
        )
        match = [i for i in listed if i["title"] == title]
        if match:
            break
        time.sleep(2)
    else:
        pytest.fail(f"created issue never listed: {json.dumps(created)}")
    comment = _ok(
        ["issue", "comment", match[0]["identifier"], "--body", "sdk-it", "--apply"],
    )
    assert "Comment added" in comment.output
