"""CLI tests for the project commands (list/show/update/issues/search --project)."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx2 as httpx
import pytest
import respx
from typer.testing import CliRunner

from gtm_linear.cli import PROJECT_DESCRIPTION_MAX, app
from tests.conftest import API_URL, issue_payload, page_info_payload

runner = CliRunner()
AUTH = {"LINEAR_API_KEY": "lin_api_test"}
UUID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.chdir(tmp_path)
    for key in ("LINEAR_API_KEY", "LINEAR_BASE_URL", "LINEAR_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)


def _project(pid: str = UUID, name: str = "CI migration") -> dict[str, Any]:
    return {
        "id": pid,
        "name": name,
        "url": f"https://linear.app/x/project/ci-migration-0123456789ab",
        "slugId": "0123456789ab",
        "description": "move runners",
        "targetDate": "2026-12-01",
        "status": {"id": "ps-1", "name": "Started", "type": "started"},
    }


def _router(
    *,
    pages: int = 1,
) -> tuple[Any, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []

    def respond(request: httpx.Request) -> Any:
        body = json.loads(request.content)
        sent.append(body)
        query, variables = body["query"], body["variables"]
        name = re.search(r"(?:query|mutation)\s+(\w+)", query)
        op = name.group(1) if name else ""
        if op == "CliGetProject":
            data: dict[str, Any] = {"project": _project()}
        elif op == "CliListProjects":
            page = int(variables.get("after") or 0)
            data = {
                "projects": {
                    "nodes": [_project(f"p-{page}", f"P{page}")],
                    "pageInfo": page_info_payload(
                        has_next=page + 1 < pages,
                        end=str(page + 1),
                    ),
                },
            }
        elif op == "CliProjectStatuses":
            data = {
                "projectStatuses": {
                    "nodes": [
                        {"id": "ps-1", "name": "Started", "type": "started"},
                        {"id": "ps-2", "name": "Done", "type": "completed"},
                    ],
                },
            }
        elif op == "CliUpdateProject":
            data = {
                "projectUpdate": {
                    "success": True,
                    "project": {
                        **_project(),
                        "description": variables["input"].get("description", "x"),
                    },
                },
            }
        elif op in {"ListIssues", "SearchIssues"}:
            page = int(variables.get("after") or 0)
            field = "issues" if op == "ListIssues" else "searchIssues"
            data = {
                field: {
                    "nodes": [issue_payload(f"iss-{page}")],
                    "pageInfo": page_info_payload(
                        has_next=page + 1 < pages,
                        end=str(page + 1),
                    ),
                },
            }
        else:
            raise AssertionError(op)
        return httpx.Response(200, json={"data": data})

    return respond, sent


def _ops(sent: list[dict[str, Any]]) -> list[str]:
    return [re.search(r"(?:query|mutation)\s+(\w+)", b["query"]).group(1) for b in sent]  # type: ignore[union-attr]


def test_projects_list_json_all_follows_pages() -> None:
    respond, sent = _router(pages=3)
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["projects", "--all", "--json"], env=AUTH)
    assert result.exit_code == 0, result.output  # noqa: S101
    out = json.loads(result.stdout)
    assert [p["id"] for p in out["results"]] == ["p-0", "p-1", "p-2"]  # noqa: S101
    assert out["complete"] is True  # noqa: S101
    assert out["truncated"] is False  # noqa: S101
    assert len(sent) == 3  # noqa: S101


def test_projects_list_human_and_limit_truncation() -> None:
    respond, _ = _router(pages=3)
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["projects", "--limit", "1"], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "p-0  P0" in result.stdout  # noqa: S101
    assert "incomplete" in result.stdout  # noqa: S101


@pytest.mark.parametrize(
    "ref",
    [
        UUID,
        "0123456789ab",
        "ci-migration-0123456789ab",
        "https://linear.app/x/project/ci-migration-0123456789ab/overview",
    ],
)
def test_project_show_json_accepts_id_slug_and_url(ref: str) -> None:
    respond, sent = _router()
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["project", ref, "--json"], env=AUTH)
    assert result.exit_code == 0, result.output  # noqa: S101
    out = json.loads(result.stdout)
    assert out["description"] == "move runners"  # noqa: S101
    assert out["state"] == "Started"  # noqa: S101
    assert sent[0]["variables"]["id"] == (UUID if ref == UUID else "0123456789ab")  # noqa: S101


def test_project_show_human() -> None:
    respond, _ = _router()
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(app, ["project", UUID], env=AUTH)
    assert result.exit_code == 0  # noqa: S101
    assert "description:" in result.stdout  # noqa: S101
    assert "move runners" in result.stdout  # noqa: S101


def test_project_update_previews_without_mutation() -> None:
    respond, sent = _router()
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            [
                "project",
                "update",
                UUID,
                "--description",
                "new",
                "--state",
                "done",
                "--json",
            ],
            env=AUTH,
        )
    assert result.exit_code == 0, result.output  # noqa: S101
    out = json.loads(result.stdout)
    assert out["applied"] is False  # noqa: S101
    assert out["payload"] == {"description": "new", "statusId": "ps-2"}  # noqa: S101
    assert "CliUpdateProject" not in _ops(sent)  # noqa: S101


def test_project_update_apply_sends_mutation() -> None:
    respond, sent = _router()
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            [
                "project",
                "update",
                UUID,
                "--name",
                "Renamed",
                "--target-date",
                "2027-01-31",
                "--apply",
                "--json",
            ],
            env=AUTH,
        )
    assert result.exit_code == 0, result.output  # noqa: S101
    mutation = next(b for b in sent if "CliUpdateProject" in b["query"])
    assert mutation["variables"] == {  # noqa: S101
        "id": UUID,
        "input": {"name": "Renamed", "targetDate": "2027-01-31"},
    }
    assert json.loads(result.stdout)["id"] == UUID  # noqa: S101


def test_project_update_description_file(tmp_path: Any) -> None:
    path = tmp_path / "d.txt"
    path.write_text("from file", encoding="utf-8")
    respond, _ = _router()
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["project", "update", UUID, "--description-file", str(path), "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0, result.output  # noqa: S101
    assert json.loads(result.stdout)["payload"] == {"description": "from file"}  # noqa: S101


def test_project_update_rejects_long_description_before_any_request() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        result = runner.invoke(
            app,
            [
                "project",
                "update",
                UUID,
                "--description",
                "x" * (PROJECT_DESCRIPTION_MAX + 1),
                "--apply",
            ],
            env=AUTH,
        )
    assert result.exit_code == 2  # noqa: S101
    assert "255" in result.output  # noqa: S101
    assert "description" in result.output  # noqa: S101
    assert route.call_count == 0  # noqa: S101


def test_project_update_accepts_description_at_limit() -> None:
    respond, _ = _router()
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            [
                "project",
                "update",
                UUID,
                "--description",
                "x" * PROJECT_DESCRIPTION_MAX,
                "--json",
            ],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101


def test_project_update_rejects_impossible_date_before_any_request() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        result = runner.invoke(
            app,
            ["project", "update", UUID, "--target-date", "2027-02-30", "--apply"],
            env=AUTH,
        )
    assert result.exit_code == 2  # noqa: S101
    assert "2027-02-30 is not a valid calendar date" in result.output  # noqa: S101
    assert route.call_count == 0  # noqa: S101


def test_project_update_rejects_unpadded_date_before_any_request() -> None:
    with respx.mock:
        route = respx.post(API_URL)
        result = runner.invoke(
            app,
            ["project", "update", UUID, "--target-date", "2027-1-31", "--apply"],
            env=AUTH,
        )
    assert result.exit_code == 2  # noqa: S101
    assert "must be YYYY-MM-DD" in result.output  # noqa: S101
    assert route.call_count == 0  # noqa: S101


def test_project_update_accepts_leap_day() -> None:
    respond, _ = _router()
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["project", "update", UUID, "--target-date", "2028-02-29", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0, result.output  # noqa: S101


def test_project_update_requires_a_field() -> None:
    result = runner.invoke(app, ["project", "update", UUID], env=AUTH)
    assert result.exit_code == 2  # noqa: S101


def test_project_not_found_via_api_error_is_a_clean_error() -> None:
    """Linear reports unknown project ids as a GraphQL error, not null data.

    The payload mirrors the live-captured Issue wording pinned in
    ``tests/test_cli.py`` (2026-10-02); the Project phrasing is assumed to
    match until a live capture lands.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "Entity not found: Project",
                            "extensions": {
                                "type": "invalid input",
                                "code": "INPUT_ERROR",
                                "statusCode": 400,
                                "userError": True,
                                "userPresentableMessage": (
                                    "Could not find referenced Project."
                                ),
                            },
                        },
                    ],
                },
            ),
        )
        result = runner.invoke(app, ["project", UUID], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert f"no project found matching '{UUID}'" in result.output  # noqa: S101


def test_project_not_found_via_null_project_is_a_clean_error() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"project": None}}),
        )
        result = runner.invoke(app, ["project", "0123456789ab"], env=AUTH)
    assert result.exit_code == 1  # noqa: S101
    assert "no project found matching '0123456789ab'" in result.output  # noqa: S101


def test_project_unknown_name_is_a_usage_error() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"data": {"projects": {"nodes": []}}},
            ),
        )
        result = runner.invoke(app, ["project", "Nope"], env=AUTH)
    assert result.exit_code == 2  # noqa: S101
    assert "no project named 'Nope'" in result.output  # noqa: S101


def test_project_issues_all_across_pages_with_metadata() -> None:
    respond, sent = _router(pages=2)
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["project", "issues", UUID, "--all", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0, result.output  # noqa: S101
    out = json.loads(result.stdout)
    assert len(out["results"]) == 2  # noqa: S101
    assert out["complete"] is True  # noqa: S101
    listing = [b for b in sent if "ListIssues" in b["query"]]
    assert listing[0]["variables"]["filter"] == {"project": {"id": {"eq": UUID}}}  # noqa: S101


def test_project_issues_limit_reports_truncation() -> None:
    respond, _ = _router(pages=2)
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["project", "issues", UUID, "--limit", "1"],
            env=AUTH,
        )
    assert result.exit_code == 0  # noqa: S101
    assert "incomplete" in result.stdout  # noqa: S101


def test_search_project_scopes_filter_and_follows_pages() -> None:
    respond, sent = _router(pages=2)
    with respx.mock:
        respx.post(API_URL).mock(side_effect=respond)
        result = runner.invoke(
            app,
            ["search", "runner", "--project", UUID, "--all", "--json"],
            env=AUTH,
        )
    assert result.exit_code == 0, result.output  # noqa: S101
    out = json.loads(result.stdout)
    assert len(out["results"]) == 2  # noqa: S101
    searches = [b for b in sent if "SearchIssues" in b["query"]]
    assert len(searches) == 2  # noqa: S101
    assert searches[0]["variables"]["filter"] == {"project": {"id": {"eq": UUID}}}  # noqa: S101
