"""Operation-based Ariadne client vs the current SDK, compared on behaviour.

Every case sends the *same* operation through the current public method
(``LinearQueries`` / ``LinearMutations``) and through the Ariadne-generated method,
both over one mocked Linear endpoint, then compares:

* the GraphQL document (parsed and normalised, so formatting is irrelevant),
* the variables actually sent on the wire,
* the parsed result (``model_dump()`` of the current return value vs the
  equivalent node of the Ariadne result).

Known, intentional differences are asserted explicitly rather than normalised away.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
import pytest
import respx
from graphql import DocumentNode, parse, print_ast

import gtm_linear
from gtm_linear import (
    AttachmentCreateInput,
    IssueCreateInput,
    IssueRelationCreateInput,
    IssueRelationType,
    IssueUpdateInput,
    LinearClient,
    LinearMutations,
    LinearQueries,
    PaginationOrderBy,
)
from gtm_linear.exceptions import (
    LinearGraphQLError,
    LinearHTTPError,
    LinearResponseError,
)
from poc.ariadne.linear_ariadne import (
    LinearAriadneClient,
    enums as ariadne_enums,
    input_types as ariadne_inputs,
)
from poc.ariadne.linear_ariadne.linear_base_client import serialize_variables
from tests.conftest import API_URL, issue_payload, page_info_payload, user_payload

from .conftest import REPO_ROOT, request_body

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

TEAM = {"id": "t-1", "name": "Eng", "key": "ENG"}
STATE = {
    "id": "st-1",
    "name": "Todo",
    "type": "unstarted",
    "color": "#fff",
    "position": 1.0,
}
COMMENT = {
    "id": "c-1",
    "body": "hi",
    "url": "https://linear.app/c/1",
    "createdAt": "2026-01-02T03:04:05.000Z",
}
CONTEXT_COMMENT = {
    **COMMENT,
    "user": user_payload(),
    "externalUser": None,
}
ATTACHMENT = {
    "id": "a-1",
    "createdAt": "2026-01-02T03:04:05.000Z",
    "updatedAt": "2026-01-02T03:04:05.000Z",
    "subtitle": None,
    "title": "PR",
    "url": "https://github.com/x/y/pull/1",
}
CONTEXT_ATTACHMENT = {
    "id": "a-1",
    "title": "PR",
    "url": "https://github.com/x/y/pull/1",
    "source": {"type": "github"},
    "sourceType": "github",
    "metadata": {"status": "open", "nested": {"n": [1, 2]}},
    "createdAt": "2026-01-02T03:04:05.000Z",
}
RELATION = {
    "id": "r-1",
    "type": "blocks",
    "createdAt": "2026-01-02T03:04:05.000Z",
    "updatedAt": "2026-01-02T03:04:05.000Z",
    "issue": {"id": "i-1", "identifier": "ENG-1"},
    "relatedIssue": {"id": "i-2", "identifier": "ENG-2"},
}
CONTEXT_RELATION = {
    "id": "r-1",
    "type": "blocks",
    "createdAt": "2026-01-02T03:04:05.000Z",
    "issue": {"id": "i-1", "identifier": "ENG-1", "title": "A", "url": "u1"},
    "relatedIssue": {"id": "i-2", "identifier": "ENG-2", "title": "B", "url": "u2"},
}
FILTER = {"team": {"id": {"eq": "t-1"}}, "labels": {"name": {"in": ["bug"]}}}


def _page(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"nodes": nodes, "pageInfo": page_info_payload(has_next=True)}


def _issue_scoped(field: str, nodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"issue": {field: _page(nodes)}}


@dataclass(frozen=True)
class Case:
    """One operation exercised through both clients."""

    operation: str
    data: dict[str, Any]
    current: Callable[[LinearQueries, LinearMutations], Awaitable[Any]]
    ariadne: Callable[[LinearAriadneClient], Awaitable[Any]]
    # Pulls the node comparable to the current method's return value.
    project: Callable[[Any], Any]
    # Variables the current SDK sends as explicit null that Ariadne omits (UNSET).
    nulls_omitted: tuple[str, ...] = ()


A = ariadne_inputs

CASES = [
    Case(
        "GetIssue",
        {"issue": issue_payload()},
        lambda q, _m: q.get_issue("iss-1"),
        lambda c: c.get_issue(id="iss-1"),
        lambda r: r.issue,
    ),
    Case(
        "GetTeam",
        {"team": TEAM},
        lambda q, _m: q.get_team("t-1"),
        lambda c: c.get_team(id="t-1"),
        lambda r: r.team,
    ),
    Case(
        "GetTeamByKey",
        {"teams": {"nodes": [TEAM]}},
        lambda q, _m: q.get_team_by_key("ENG"),
        lambda c: c.get_team_by_key(key="ENG"),
        lambda r: r.teams.nodes[0],
    ),
    Case(
        "GetUser",
        {"user": user_payload()},
        lambda q, _m: q.get_user("u-1"),
        lambda c: c.get_user(id="u-1"),
        lambda r: r.user,
    ),
    Case(
        "GetViewer",
        {"viewer": user_payload()},
        lambda q, _m: q.get_viewer(),
        lambda c: c.get_viewer(),
        lambda r: r.viewer,
    ),
    Case(
        "ListIssues",
        {"issues": _page([issue_payload(), issue_payload("iss-2", assignee=False)])},
        lambda q, _m: q.list_issues_page(
            FILTER,
            first=10,
            after="cur",
            order_by=PaginationOrderBy.updatedAt,
        ),
        # The opaque filter dict passes straight through the base client; see
        # test_opaque_filter_dict_vs_typed_issue_filter for the typed alternative.
        lambda c: c.list_issues(
            first=10,
            include_archived=False,
            filter_=FILTER,  # type: ignore[arg-type]
            after="cur",
            order_by=ariadne_enums.PaginationOrderBy.updatedAt,
        ),
        lambda r: r.issues,
    ),
    Case(
        "ListWorkflowStates",
        {"workflowStates": _page([STATE])},
        lambda q, _m: q.list_workflow_states_page("t-1", first=5),
        lambda c: c.list_workflow_states(
            first=5,
            include_archived=False,
            filter_=A.WorkflowStateFilter.model_validate(
                {"team": {"id": {"eq": "t-1"}}},
            ),
        ),
        lambda r: r.workflow_states,
        nulls_omitted=("after",),
    ),
    Case(
        "SearchIssues",
        {"searchIssues": _page([issue_payload()])},
        lambda q, _m: q.search_issues("crash", first=3),
        lambda c: c.search_issues(term="crash", first=3),
        lambda r: r.search_issues,
        nulls_omitted=("after",),
    ),
    Case(
        "ListIssueComments",
        _issue_scoped("comments", [CONTEXT_COMMENT]),
        lambda q, _m: q.list_issue_comments_page("ENG-1", first=2),
        lambda c: c.list_issue_comments(id="ENG-1", first=2, include_archived=False),
        lambda r: r.issue.comments,
        nulls_omitted=("after",),
    ),
    Case(
        "ListIssueAttachments",
        _issue_scoped("attachments", [CONTEXT_ATTACHMENT]),
        lambda q, _m: q.list_issue_attachments_page("ENG-1", first=2),
        lambda c: c.list_issue_attachments(
            id="ENG-1",
            first=2,
            include_archived=False,
        ),
        lambda r: r.issue.attachments,
        nulls_omitted=("after",),
    ),
    Case(
        "ListIssueRelations",
        _issue_scoped("relations", [CONTEXT_RELATION]),
        lambda q, _m: q.list_issue_relations_page("ENG-1", first=2),
        lambda c: c.list_issue_relations(id="ENG-1", first=2, include_archived=False),
        lambda r: r.issue.relations,
        nulls_omitted=("after",),
    ),
    Case(
        "ListIssueInverseRelations",
        _issue_scoped("inverseRelations", [CONTEXT_RELATION]),
        lambda q, _m: q.list_issue_inverse_relations_page("ENG-1", first=2),
        lambda c: c.list_issue_inverse_relations(
            id="ENG-1",
            first=2,
            include_archived=False,
        ),
        lambda r: r.issue.inverse_relations,
        nulls_omitted=("after",),
    ),
    Case(
        "CreateIssue",
        {"issueCreate": {"success": True, "issue": issue_payload()}},
        lambda _q, m: m.create_issue(
            IssueCreateInput(
                team_id="t-1",
                title="T",
                due_date=date(2026, 3, 1),
                priority=2,
            ),
        ),
        lambda c: c.create_issue(
            input=A.IssueCreateInput(
                team_id="t-1",
                title="T",
                due_date=date(2026, 3, 1),
                priority=2,
            ),
        ),
        lambda r: r.issue_create.issue,
    ),
    Case(
        "UpdateIssue",
        {"issueUpdate": {"success": True, "issue": issue_payload()}},
        # Explicit None must clear (null on the wire); unset fields are omitted.
        lambda _q, m: m.update_issue(
            "iss-1",
            IssueUpdateInput(
                title="New",
                assignee_id=None,
                sla_started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        lambda c: c.update_issue(
            id="iss-1",
            input=A.IssueUpdateInput(
                title="New",
                assignee_id=None,
                sla_started_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        ),
        lambda r: r.issue_update.issue,
    ),
    Case(
        "DeleteIssue",
        {"issueDelete": {"success": True}},
        lambda _q, m: m.delete_issue("iss-1"),
        lambda c: c.delete_issue(id="iss-1"),
        lambda r: r.issue_delete.success,
    ),
    Case(
        "CreateComment",
        {"commentCreate": {"success": True, "comment": COMMENT}},
        lambda _q, m: m.create_comment("iss-1", "hi"),
        lambda c: c.create_comment(
            input=A.CommentCreateInput(issue_id="iss-1", body="hi"),
        ),
        lambda r: r.comment_create.comment,
    ),
    Case(
        "CreateAttachment",
        {"attachmentCreate": {"success": True, "attachment": ATTACHMENT}},
        lambda _q, m: m.create_attachment(
            AttachmentCreateInput(
                issue_id="iss-1",
                title="PR",
                url="https://github.com/x/y/pull/1",
                metadata={"k": [1, {"v": None}]},
            ),
        ),
        lambda c: c.create_attachment(
            input=A.AttachmentCreateInput(
                issue_id="iss-1",
                title="PR",
                url="https://github.com/x/y/pull/1",
                metadata={"k": [1, {"v": None}]},
            ),
        ),
        lambda r: r.attachment_create.attachment,
    ),
    Case(
        "CreateIssueRelation",
        {"issueRelationCreate": {"success": True, "issueRelation": RELATION}},
        lambda _q, m: m.create_issue_relation(
            IssueRelationCreateInput(
                issue_id="ENG-1",
                related_issue_id="ENG-2",
                type=IssueRelationType.blocks,
            ),
        ),
        lambda c: c.create_issue_relation(
            input=A.IssueRelationCreateInput(
                issue_id="ENG-1",
                related_issue_id="ENG-2",
                type=ariadne_enums.IssueRelationType.blocks,
            ),
        ),
        lambda r: r.issue_relation_create.issue_relation,
    ),
]


def _normalise(document: str) -> str:
    """Order-insensitive, formatting-insensitive form of a GraphQL document."""
    parsed = parse(document)
    ordered = sorted(parsed.definitions, key=lambda d: (d.kind, d.name.value))  # type: ignore[union-attr]
    return print_ast(DocumentNode(definitions=tuple(ordered)))


def _dump(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value


async def _send_both(case: Case) -> tuple[dict[str, Any], dict[str, Any], Any, Any]:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": case.data}),
        )
        async with LinearClient(api_key="lin_api_test") as linear:
            current = await case.current(LinearQueries(linear), LinearMutations(linear))
            generated = await case.ariadne(LinearAriadneClient(linear))
        current_request, ariadne_request = (call.request for call in route.calls)
    assert current_request.headers["authorization"] == "lin_api_test"
    assert ariadne_request.headers["authorization"] == "lin_api_test"
    assert current_request.url == ariadne_request.url
    return (
        request_body(current_request),
        request_body(ariadne_request),
        current,
        generated,
    )


def test_every_operation_has_a_case() -> None:
    operations = {
        p.stem
        for p in (REPO_ROOT / "operations").glob("*.graphql")
        if not p.name.startswith("_")
    }
    assert operations == {case.operation for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.operation)
async def test_same_document_variables_and_result(case: Case) -> None:
    current_body, ariadne_body, current, generated = await _send_both(case)

    # 1. Same operation semantics (both inline exactly the fragments they use).
    assert _normalise(current_body["query"]) == _normalise(ariadne_body["query"])

    # 2. Same variables, except nullable ones the current SDK sends as explicit
    #    null and Ariadne omits. For these arguments GraphQL treats "absent" and
    #    "null" alike (no defaults are declared), so this is not a semantic change.
    current_vars = dict(current_body.get("variables") or {})
    for name in case.nulls_omitted:
        assert current_vars.pop(name) is None
        assert name not in ariadne_body.get("variables", {})
    assert current_vars == (ariadne_body.get("variables") or {})

    # 3. Same parsed data, attribute for attribute.
    assert _dump(current) == _dump(case.project(generated))


async def test_update_explicit_null_vs_unset_reaches_the_wire() -> None:
    case = next(c for c in CASES if c.operation == "UpdateIssue")
    _, ariadne_body, _, _ = await _send_both(case)
    assert ariadne_body["variables"]["input"] == {
        "title": "New",
        "assigneeId": None,
        "slaStartedAt": "2026-01-01T00:00:00Z",
    }


def test_optional_argument_omitted_vs_explicit_null() -> None:
    # UNSET (argument not passed) is omitted; None is sent as null. The current SDK
    # cannot express "omitted" for `after` and sends null every time.
    from poc.ariadne.linear_ariadne.base_model import UNSET  # noqa: PLC0415

    assert serialize_variables({"after": UNSET, "first": 1}) == {"first": 1}
    assert serialize_variables({"after": None, "first": 1}) == {
        "after": None,
        "first": 1,
    }


def test_enums_and_scalars_serialise_like_the_current_sdk() -> None:
    assert serialize_variables(
        {
            "orderBy": ariadne_enums.PaginationOrderBy.createdAt,
            "due": date(2026, 3, 1),
            "at": datetime(2026, 1, 1, 12, tzinfo=UTC),
            "list": [A.CommentCreateInput(issue_id="i", body="b")],
        },
    ) == {
        "orderBy": "createdAt",
        "due": "2026-03-01",
        "at": "2026-01-01T12:00:00Z",
        "list": [{"issueId": "i", "body": "b"}],
    }


def test_custom_scalars_parse_to_the_same_python_types() -> None:
    from poc.ariadne.linear_ariadne.fragments import (  # noqa: PLC0415
        CommentFields,
        IssueContextAttachmentFields,
    )

    attachment = IssueContextAttachmentFields.model_validate(CONTEXT_ATTACHMENT)
    current = gtm_linear.IssueContextAttachment.model_validate(CONTEXT_ATTACHMENT)
    assert attachment.metadata == current.metadata == CONTEXT_ATTACHMENT["metadata"]
    assert type(attachment.created_at) is type(current.created_at) is datetime
    assert CommentFields.model_validate(COMMENT).created_at == current.created_at


def test_public_attribute_names_match_after_plugin() -> None:
    """The process_name plugin keeps `type` (Ariadne would emit `type_`)."""
    from poc.ariadne.linear_ariadne import fragments  # noqa: PLC0415

    pairs = {
        "IssueFields": gtm_linear.Issue,
        "IssueRelationFields": gtm_linear.IssueRelation,
        "IssueContextRelationFields": gtm_linear.IssueContextRelation,
        "IssueContextCommentFields": gtm_linear.IssueContextComment,
        "IssueContextAttachmentFields": gtm_linear.IssueContextAttachment,
        "WorkflowStateFields": gtm_linear.WorkflowState,
        "PageInfoFields": gtm_linear.PageInfo,
        "TeamFields": gtm_linear.Team,
        "UserFields": gtm_linear.User,
        "CommentFields": gtm_linear.Comment,
        "AttachmentFields": gtm_linear.Attachment,
        "IssueSearchResultFields": gtm_linear.IssueSearchResult,
    }
    for name, current in pairs.items():
        generated = getattr(fragments, name)
        assert set(generated.model_fields) == set(current.model_fields), name
        for field, info in current.model_fields.items():
            assert generated.model_fields[field].is_required() == info.is_required()

    inputs = {
        "IssueCreateInput": gtm_linear.IssueCreateInput,
        "IssueUpdateInput": gtm_linear.IssueUpdateInput,
        "CommentCreateInput": gtm_linear.CommentCreateInput,
        "AttachmentCreateInput": gtm_linear.AttachmentCreateInput,
        "IssueRelationCreateInput": gtm_linear.IssueRelationCreateInput,
    }
    for name, current in inputs.items():
        generated = getattr(A, name)
        assert set(generated.model_fields) == set(current.model_fields), name


def test_known_type_identity_differences() -> None:
    """Documented gaps a migration has to absorb (not equivalences)."""
    from poc.ariadne.linear_ariadne import fragments  # noqa: PLC0415

    # Generated models do not inherit gtm_linear.LinearModel.
    assert not issubclass(fragments.IssueFields, gtm_linear.LinearModel)
    # Enums are str-mixins; the current SDK's are plain Enum.
    assert issubclass(ariadne_enums.IssueRelationType, str)
    assert not issubclass(gtm_linear.IssueRelationType, str)
    # validate_assignment=True on Ariadne's BaseModel (off on LinearModel).
    issue = fragments.IssueFields.model_validate(issue_payload())
    with pytest.raises(ValueError, match="validation error"):
        issue.priority = "high"  # type: ignore[assignment]


def test_opaque_filter_dict_vs_typed_issue_filter() -> None:
    """Ariadne types IssueFilter; the SDK deliberately keeps filters as dicts.

    A raw dict passes through unchanged (wire keys like ``in`` preserved), so a
    wrapper can keep the current ``filter: dict[str, Any]`` contract. Validating the
    dict into the typed model instead would *silently drop* unknown keys
    (extra="ignore"), turning a typo into an unfiltered query - which is why the
    wrapper must not do that.
    """
    assert serialize_variables({"filter": FILTER}) == {"filter": FILTER}

    typed = A.IssueFilter.model_validate(FILTER)
    assert serialize_variables({"filter": typed}) == {"filter": FILTER}

    typo = {"team": {"id": {"eq": "t-1"}}, "lables": {"name": {"eq": "bug"}}}
    assert serialize_variables({"filter": A.IssueFilter.model_validate(typo)}) == {
        "filter": {"team": {"id": {"eq": "t-1"}}},
    }


# --- errors and transport: identical by construction, demonstrated anyway ---------


async def _ariadne_against(response: httpx.Response | Exception) -> Any:
    with respx.mock:
        route = respx.post(API_URL)
        if isinstance(response, Exception):
            route.mock(side_effect=response)
        else:
            route.mock(return_value=response)
        async with LinearClient(api_key="lin_api_test", timeout=1.5) as linear:
            return await LinearAriadneClient(linear).get_viewer()


async def test_graphql_errors_without_data() -> None:
    with pytest.raises(LinearGraphQLError) as exc:
        await _ariadne_against(
            httpx.Response(
                200,
                json={"errors": [{"message": "nope", "extensions": {"code": "X"}}]},
            ),
        )
    assert exc.value.codes == ["X"]


async def test_partial_data_with_errors_raises_like_current_sdk() -> None:
    with pytest.raises(LinearGraphQLError):
        await _ariadne_against(
            httpx.Response(
                200,
                json={"data": {"viewer": user_payload()}, "errors": [{"message": "p"}]},
            ),
        )


async def test_auth_failure_400_keeps_error_codes() -> None:
    # Errors are read before the status code, so a 400 auth failure still surfaces
    # AUTHENTICATION_ERROR (Ariadne's default base client would raise a bare
    # GraphQLClientHttpError here - see test_schema_only.py).
    with pytest.raises(LinearGraphQLError) as exc:
        await _ariadne_against(
            httpx.Response(
                400,
                json={
                    "errors": [
                        {
                            "message": "Authentication required",
                            "extensions": {"code": "AUTHENTICATION_ERROR"},
                        },
                    ],
                },
            ),
        )
    assert exc.value.codes == ["AUTHENTICATION_ERROR"]


async def test_malformed_error_entries_stay_in_the_error_contract() -> None:
    with pytest.raises(LinearGraphQLError) as exc:
        await _ariadne_against(httpx.Response(200, json={"errors": [{"code": 1}]}))
    assert exc.value.errors[0].message


async def test_http_error() -> None:
    with pytest.raises(LinearHTTPError) as exc:
        await _ariadne_against(httpx.Response(502, text="bad gateway"))
    assert exc.value.status_code == 502


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={"nodata": True}),
    ],
)
async def test_invalid_bodies(response: httpx.Response) -> None:
    with pytest.raises(LinearResponseError):
        await _ariadne_against(response)


async def test_unexpected_shape_raises_validation_error_like_current_sdk() -> None:
    from pydantic import ValidationError  # noqa: PLC0415

    with pytest.raises(ValidationError):
        await _ariadne_against(httpx.Response(200, json={"data": {"viewer": {}}}))


async def test_timeout_propagates_and_uses_client_timeout() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(side_effect=httpx.ReadTimeout("slow"))
        async with LinearClient(api_key="lin_api_test", timeout=1.5) as linear:
            with pytest.raises(httpx.ReadTimeout):
                await LinearAriadneClient(linear).get_viewer()
        timeout = route.calls[0].request.extensions["timeout"]
    assert timeout == {"connect": 1.5, "read": 1.5, "write": 1.5, "pool": 1.5}


async def test_per_call_transport_kwargs_are_rejected() -> None:
    async with LinearClient(api_key="lin_api_test") as linear:
        with pytest.raises(TypeError, match="per-call transport options"):
            await LinearAriadneClient(linear).get_viewer(headers={"x": "y"})


async def test_context_manager_closes_wrapped_transport() -> None:
    linear = LinearClient(api_key="lin_api_test")
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"viewer": user_payload()}}),
        )
        async with LinearAriadneClient(linear) as client:
            await client.get_viewer()
            assert linear._async_client is not None
    assert linear._async_client is None


def test_generated_client_needs_no_httpx_or_graphql_core_at_runtime() -> None:
    package = Path(__file__).resolve().parent.parent / "linear_ariadne"
    sources = "\n".join(p.read_text() for p in package.glob("*.py"))
    assert "import httpx" not in sources
    assert "from graphql" not in sources
    assert "import websockets" not in sources
    assert json.loads(json.dumps(sorted(p.name for p in package.glob("*.py"))))


async def test_existing_paginate_drives_ariadne_connections() -> None:
    """pagination.paginate's protocol is structural, so Ariadne pages satisfy it."""
    from gtm_linear.pagination import paginate  # noqa: PLC0415

    pages = [
        {
            "issues": {
                "nodes": [issue_payload("iss-1")],
                "pageInfo": page_info_payload(has_next=True, end="c1"),
            },
        },
        {
            "issues": {
                "nodes": [issue_payload("iss-2")],
                "pageInfo": page_info_payload(has_next=False, end="c2"),
            },
        },
    ]
    with respx.mock:
        route = respx.post(API_URL).mock(
            side_effect=[httpx.Response(200, json={"data": p}) for p in pages],
        )
        async with LinearClient(api_key="lin_api_test") as linear:
            client = LinearAriadneClient(linear)

            async def fetch(cursor: str | None) -> Any:
                after = cursor if cursor is not None else ariadne_unset()
                result = await client.list_issues(
                    first=1,
                    include_archived=False,
                    after=after,
                )
                return result.issues

            ids = [issue.id async for issue in paginate(fetch)]
        sent = [request_body(call.request)["variables"] for call in route.calls]
    assert ids == ["iss-1", "iss-2"]
    assert sent == [
        {"first": 1, "includeArchived": False},
        {"first": 1, "includeArchived": False, "after": "c1"},
    ]


def ariadne_unset() -> Any:
    from poc.ariadne.linear_ariadne.base_model import UNSET  # noqa: PLC0415

    return UNSET
