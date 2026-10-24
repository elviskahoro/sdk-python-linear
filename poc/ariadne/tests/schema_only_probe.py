"""Exercise the schema-only (custom-operation builder) client and report as JSON.

Runs under ``poc/ariadne/.venv`` (graphql-core 3.2, stock httpx), NOT the root venv:
the generated client cannot print its documents on graphql-core 3.3, which the root
environment pins (see test_schema_only.py). Driven by test_schema_only.py; prints one
JSON object to stdout. Uses ``httpx.MockTransport`` - no network.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx
from graphql import build_schema, parse, validate

POC_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = POC_DIR.parent.parent
sys.path.insert(0, str(POC_DIR / "build"))

from linear_schema_only.base_operation import GraphQLField
from linear_schema_only.client import LinearSchemaOnlyClient
from linear_schema_only.custom_fields import (
    IssueConnectionFields,
    IssueFields,
    IssueNotificationFields,
    IssuePayloadFields,
    NotificationConnectionFields,
    NotificationInterface,
    PageInfoFields,
    UserFields,
    WorkflowStateFields,
)
from linear_schema_only.custom_mutations import Mutation
from linear_schema_only.custom_queries import Query
from linear_schema_only.enums import PaginationOrderBy
from linear_schema_only.exceptions import GraphQLClientError
from linear_schema_only.input_types import IssueFilter, IssueUpdateInput

URL = "https://api.linear.app/graphql"


def _client(
    response: httpx.Response,
    seen: list[httpx.Request],
) -> LinearSchemaOnlyClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return response

    return LinearSchemaOnlyClient(
        url=URL,
        headers={"Authorization": "lin_api_test"},
        http_client=httpx.AsyncClient(
            headers={"Authorization": "lin_api_test"},
            transport=httpx.MockTransport(handler),
        ),
    )


def _issues_query() -> GraphQLField:
    return Query.issues(
        first=2,
        after=None,  # explicit null: dropped by the builder
        filter_=IssueFilter.model_validate({"team": {"id": {"eq": "t-1"}}}),
        order_by=PaginationOrderBy.updatedAt,
    ).fields(
        IssueConnectionFields.nodes()
        .alias("items")
        .fields(
            IssueFields.id,
            IssueFields.identifier,
            IssueFields.state().fields(
                WorkflowStateFields.name,
                WorkflowStateFields.type_,
            ),
            IssueFields.assignee().fields(UserFields.email),
        ),
        IssueConnectionFields.page_info().fields(
            PageInfoFields.end_cursor,
            PageInfoFields.has_next_page,
        ),
    )


async def _success() -> dict[str, Any]:
    data = {
        "issues": {
            "items": [
                {
                    "id": "i-1",
                    "identifier": "ENG-1",
                    "state": {"name": "Todo", "type": "unstarted"},
                    "assignee": None,
                },
            ],
            "pageInfo": {"endCursor": "c", "hasNextPage": False},
        },
        "notifications": {
            "nodes": [{"__typename": "IssueNotification", "id": "n-1"}],
        },
    }
    seen: list[httpx.Request] = []
    client = _client(httpx.Response(200, json={"data": data}), seen)
    notifications = Query.notifications(first=1).fields(
        NotificationConnectionFields.nodes()
        .fields(GraphQLField("__typename"), NotificationInterface.id)
        .on("IssueNotification", IssueNotificationFields.id),
    )
    result = await client.query(
        _issues_query(),
        notifications,
        operation_name="Probe",
    )
    body = json.loads(seen[0].content)
    schema = build_schema((REPO_ROOT / "schema" / "linear.graphql").read_text())
    errors = validate(schema, parse(body["query"]))
    return {
        "result_type": type(result).__name__,
        "result": result,
        "query": body["query"],
        "variables": body["variables"],
        "operation_name": body["operationName"],
        "authorization": seen[0].headers["authorization"],
        "validation_errors": [e.message for e in errors],
        "default_timeout": client.http_client.timeout.read,  # type: ignore[attr-defined]
    }


async def _mutation() -> dict[str, Any]:
    seen: list[httpx.Request] = []
    client = _client(
        httpx.Response(200, json={"data": {"issueUpdate": {"success": True}}}),
        seen,
    )
    built = Mutation.issue_update(
        id="i-1",
        input=IssueUpdateInput(title="New", assignee_id=None),
    ).fields(IssuePayloadFields.success)
    result = await client.mutation(built, operation_name="Update")
    return {"result": result, "variables": json.loads(seen[0].content)["variables"]}


async def _failure(response: httpx.Response) -> dict[str, Any]:
    client = _client(response, [])
    try:
        await client.query(
            Query.viewer().fields(UserFields.id),
            operation_name="Viewer",
        )
    except GraphQLClientError as exc:
        return {
            "raised": type(exc).__name__,
            "in_hierarchy": True,
            "status_code": getattr(exc, "status_code", None),
            "codes": [
                (e.extensions or {}).get("code") for e in getattr(exc, "errors", [])
            ],
            "partial_data": getattr(exc, "data", None),
        }
    except Exception as exc:  # noqa: BLE001 - reporting what escapes is the point
        return {"raised": type(exc).__name__, "in_hierarchy": False}
    return {"raised": None}


async def main() -> None:
    """Run every probe and print one JSON report."""
    auth = {"message": "Authentication required"}
    auth["extensions"] = {"code": "AUTHENTICATION_ERROR"}  # type: ignore[assignment]
    report = {
        "success": await _success(),
        "mutation": await _mutation(),
        "auth_400": await _failure(httpx.Response(400, json={"errors": [auth]})),
        "errors_no_data": await _failure(httpx.Response(200, json={"errors": [auth]})),
        "partial": await _failure(
            httpx.Response(
                200,
                json={"data": {"viewer": {"id": "u"}}, "errors": [{"message": "p"}]},
            ),
        ),
        "malformed_error": await _failure(
            httpx.Response(200, json={"errors": [{"code": 1}]}),
        ),
        "http_502": await _failure(httpx.Response(502, text="bad gateway")),
        "not_json": await _failure(httpx.Response(200, text="not json")),
        "no_data_key": await _failure(httpx.Response(200, json={"nodata": True})),
    }
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())
