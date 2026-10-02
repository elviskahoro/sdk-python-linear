from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import respx

from gtm_linear import LinearAPIError, LinearClient
from gtm_linear.client import _coerce_error
from gtm_linear.exceptions import (
    GraphQLError,
    LinearGraphQLError,
    LinearHTTPError,
    LinearResponseError,
)

API_URL = LinearClient.BASE_URL


def test_execute_sync_returns_data() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}}),
        )
        with LinearClient(api_key="key") as client:
            data = client.execute("query { viewer { id } }")
    assert data == {"viewer": {"id": "u1"}}  # noqa: S101


def test_execute_passes_variables() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"ok": True}}),
        )
        with LinearClient(api_key="key") as client:
            client.execute("query($x: String!){ ok }", {"x": "y"})
    assert route.calls.last.request.read() == (  # noqa: S101
        b'{"query":"query($x: String!){ ok }","variables":{"x":"y"}}'
    )


def test_execute_raises_on_http_error() -> None:
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(500, text="boom"))
        with LinearClient(api_key="key") as client, pytest.raises(LinearAPIError):
            client.execute("query { viewer { id } }")


def test_execute_raises_on_graphql_errors() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={"errors": [{"message": "bad query"}]},
            ),
        )
        with (
            LinearClient(api_key="key") as client,
            pytest.raises(LinearAPIError) as exc,
        ):
            client.execute("query { viewer { id } }")
    assert "bad query" in str(exc.value)  # noqa: S101


def test_authorization_header_is_set() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {}}),
        )
        with LinearClient(api_key="secret-key") as client:
            client.execute("query { __typename }")
    assert route.calls.last.request.headers["Authorization"] == "secret-key"  # noqa: S101


def test_default_timeout_is_finite() -> None:
    client = LinearClient(api_key="key")
    assert client.timeout == 30.0  # noqa: S101


async def test_execute_async_returns_data() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}}),
        )
        async with LinearClient(api_key="key") as client:
            data = await client.execute_async("query { viewer { id } }")
    assert data == {"viewer": {"id": "u1"}}  # noqa: S101


async def test_async_context_manager_closes_real_httpx_client() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {}}),
        )
        client = LinearClient(api_key="key")
        async_client = client._get_async_client()
        assert not async_client.is_closed  # noqa: S101

        async with client:
            await client.execute_async("query { __typename }")

        assert async_client.is_closed  # noqa: S101


async def test_aclose_closes_async_client_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LinearClient(api_key="key")
    async_client = AsyncMock(spec=httpx.AsyncClient)
    async_client.post.return_value = httpx.Response(200, json={"data": {}})
    async_client_factory = Mock(return_value=async_client)
    monkeypatch.setattr("gtm_linear.client.httpx.AsyncClient", async_client_factory)

    await client.execute_async("query { __typename }")

    await client.aclose()
    await client.aclose()

    async_client_factory.assert_called_once()
    async_client.aclose.assert_awaited_once()


async def test_close_raises_when_async_client_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LinearClient(api_key="key")
    sync_client = Mock(spec=httpx.Client)
    async_client = AsyncMock(spec=httpx.AsyncClient)
    sync_client.post.return_value = httpx.Response(200, json={"data": {}})
    async_client.post.return_value = httpx.Response(200, json={"data": {}})
    monkeypatch.setattr(
        "gtm_linear.client.httpx.Client",
        Mock(return_value=sync_client),
    )
    monkeypatch.setattr(
        "gtm_linear.client.httpx.AsyncClient",
        Mock(return_value=async_client),
    )

    client.execute("query { __typename }")
    await client.execute_async("query { __typename }")

    with pytest.raises(RuntimeError, match="call await aclose\\(\\) first"):
        client.close()

    sync_client.close.assert_called_once()
    async_client.aclose.assert_not_awaited()
    await client.aclose()
    async_client.aclose.assert_awaited_once()


async def test_async_context_manager_closes_both_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = LinearClient(api_key="key")
    sync_client = Mock(spec=httpx.Client)
    async_client = AsyncMock(spec=httpx.AsyncClient)
    sync_client.post.return_value = httpx.Response(200, json={"data": {}})
    async_client.post.return_value = httpx.Response(200, json={"data": {}})
    monkeypatch.setattr(
        "gtm_linear.client.httpx.Client",
        Mock(return_value=sync_client),
    )
    monkeypatch.setattr(
        "gtm_linear.client.httpx.AsyncClient",
        Mock(return_value=async_client),
    )

    async with client:
        client.execute("query { __typename }")
        await client.execute_async("query { __typename }")

    sync_client.close.assert_called_once()
    async_client.aclose.assert_awaited_once()


def test_missing_data_key_raises_typed_error() -> None:
    """A 200 with no `data` used to escape as a bare KeyError."""
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))
        with LinearClient(api_key="key") as client:
            with pytest.raises(LinearResponseError, match="no 'data' key"):
                client.execute("query { viewer { id } }")


def test_non_object_body_raises_typed_error() -> None:
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(200, text="not json"))
        with LinearClient(api_key="key") as client:
            with pytest.raises(LinearResponseError):
                client.execute("query { viewer { id } }")


def test_graphql_error_exposes_linear_error_code() -> None:
    """The README documents branching on extensions.code; now it is modelled."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {
                            "message": "You need to authenticate",
                            "path": ["viewer"],
                            "extensions": {"code": "AUTHENTICATION_ERROR"},
                        },
                    ],
                },
            ),
        )
        with LinearClient(api_key="key") as client:
            with pytest.raises(LinearGraphQLError) as exc:
                client.execute("query { viewer { id } }")

    assert exc.value.codes == ["AUTHENTICATION_ERROR"]  # noqa: S101
    assert exc.value.errors[0].path == ["viewer"]  # noqa: S101
    assert isinstance(exc.value, LinearAPIError)  # noqa: S101


def test_http_error_carries_status_code() -> None:
    """status_code is a real attribute, not smuggled into a fake error entry."""
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(503, text="boom"))
        with LinearClient(api_key="key") as client:
            with pytest.raises(LinearHTTPError) as exc:
                client.execute("query { viewer { id } }")

    assert exc.value.status_code == 503  # noqa: S101
    assert exc.value.body == "boom"  # noqa: S101


async def test_async_path_raises_the_same_typed_errors() -> None:
    """The async error branches were previously untested duplicated code."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "errors": [
                        {"message": "nope", "extensions": {"code": "FORBIDDEN"}},
                    ],
                },
            ),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearGraphQLError) as exc:
                await client.execute_async("query { viewer { id } }")

    assert exc.value.codes == ["FORBIDDEN"]  # noqa: S101


def test_repr_does_not_leak_the_api_key() -> None:
    client = LinearClient(api_key="lin_api_supersecret")
    assert "supersecret" not in repr(client)  # noqa: S101
    assert "supersecret" not in str(client.api_key)  # noqa: S101
    assert client.api_key.get_secret_value() == "lin_api_supersecret"  # noqa: S101


# Spec-violating ``errors[]`` entries (an empty object, a non-string ``message``,
# a non-list ``path`` / ``locations``, or an explicit ``"extensions": null``) used
# to leak ``pydantic.ValidationError`` past ``execute`` / ``execute_async`` because
# ``GraphQLError.model_validate`` was called without a guard (introduced in #13).
# ``_coerce_error`` now keeps every failure inside the ``LinearAPIError`` contract:
# entries with a usable ``message`` keep it (dropping only the offending optional
# fields), and the rest degrade to ``str(entry)`` so nothing is silently lost.
#
# (entry, expected message) pairs — salvageable entries keep their message;
# entries with an unusable message degrade to the entry's repr.
_MALFORMED_ERROR_CASES: list[tuple[dict[str, Any], str]] = [
    ({}, "{}"),
    ({"message": 123}, "{'message': 123}"),
    ({"message": "x", "path": "not-a-list"}, "x"),
    ({"message": "x", "locations": "not-a-list"}, "x"),
    ({"message": "boom", "extensions": None}, "boom"),
]
_MALFORMED_IDS = [
    "empty",
    "wrong-type-message",
    "wrong-type-path",
    "wrong-type-locations",
    "null-extensions",
]


@pytest.mark.parametrize(
    ("error_entry", "expected_message"),
    _MALFORMED_ERROR_CASES,
    ids=_MALFORMED_IDS,
)
def test_malformed_errors_raise_linearapierror(
    error_entry: dict[str, Any],
    expected_message: str,
) -> None:
    """Every failure from ``execute`` must be a ``LinearAPIError``.

    Before the guard, each of these raised ``pydantic.ValidationError`` — not a
    ``LinearAPIError`` — so the documented ``except LinearAPIError`` pattern did
    not catch it. Salvageable entries keep their usable ``message``; entries
    with an unusable one degrade to the entry's repr so it is not lost.
    """
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"errors": [error_entry]}),
        )
        with (
            LinearClient(api_key="key") as client,
            pytest.raises(LinearAPIError) as exc,
        ):
            client.execute("query { viewer { id } }")
    assert isinstance(exc.value, LinearGraphQLError)  # noqa: S101
    assert len(exc.value.errors) == 1  # noqa: S101
    assert exc.value.errors[0].message == expected_message  # noqa: S101
    assert "GraphQL error" in str(exc.value)  # noqa: S101


@pytest.mark.parametrize(
    ("error_entry", "expected_message"),
    _MALFORMED_ERROR_CASES,
    ids=_MALFORMED_IDS,
)
async def test_malformed_errors_raise_linearapierror_async(
    error_entry: dict[str, Any],
    expected_message: str,
) -> None:
    """The async path shares ``_handle_response``; the contract holds there too."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"errors": [error_entry]}),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearAPIError) as exc:
                await client.execute_async("query { viewer { id } }")
    assert isinstance(exc.value, LinearGraphQLError)  # noqa: S101
    assert len(exc.value.errors) == 1  # noqa: S101
    assert exc.value.errors[0].message == expected_message  # noqa: S101


def test_mixed_errors_preserve_structure_and_degrade_malformed() -> None:
    """A mixed ``errors`` array: well-formed and salvageable entries keep their
    structured ``message`` / ``code``, unusable and non-dict entries degrade,
    and the raised ``LinearGraphQLError`` exposes one ``GraphQLError`` per
    input entry.
    """
    error_entries: list[dict[str, Any] | str] = [
        {"message": "auth", "extensions": {"code": "AUTHENTICATION_ERROR"}},
        {
            "message": "partial",
            "extensions": {"code": "RATELIMITED"},
            "path": "not-a-list",
        },
        {},
        "bare string",
        {"message": 123},
    ]
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"errors": error_entries}),
        )
        with (
            LinearClient(api_key="key") as client,
            pytest.raises(LinearGraphQLError) as exc,
        ):
            client.execute("query { viewer { id } }")

    errors = exc.value.errors
    assert len(errors) == len(error_entries)  # noqa: S101
    # Well-formed entry keeps its structured shape and surfaces a code.
    assert errors[0].message == "auth"  # noqa: S101
    assert errors[0].code == "AUTHENTICATION_ERROR"  # noqa: S101
    # Salvageable entry keeps its message and code; only the bad field drops.
    assert errors[1].message == "partial"  # noqa: S101
    assert errors[1].code == "RATELIMITED"  # noqa: S101
    assert exc.value.codes == ["AUTHENTICATION_ERROR", "RATELIMITED"]  # noqa: S101
    # Entries with an unusable message degrade to str(entry) instead of
    # leaking ValidationError.
    assert errors[2].message == str({})  # noqa: S101
    # Non-dict sibling branch (the pre-existing fallback) still works.
    assert errors[3].message == "bare string"  # noqa: S101
    assert errors[4].message == str({"message": 123})  # noqa: S101
    # The summary joins every entry's message.
    assert "auth" in str(exc.value)  # noqa: S101
    assert "bare string" in str(exc.value)  # noqa: S101


# A truthy non-list ``errors`` value used to escape the contract too: a scalar
# raised ``TypeError``, a bare string split into one error per character, and a
# single error object was iterated over its keys. It is now normalized to one
# entry.
#
# (errors value, expected single-entry message) pairs.
_NON_LIST_ERRORS_CASES: list[tuple[Any, str]] = [
    (5, "5"),
    (True, "True"),
    ("boom", "boom"),
    ({"message": "x"}, "x"),
]
_NON_LIST_IDS = ["int", "bool", "str", "dict"]


@pytest.mark.parametrize(
    ("errors_value", "expected_message"),
    _NON_LIST_ERRORS_CASES,
    ids=_NON_LIST_IDS,
)
def test_non_list_errors_container_raises_linearapierror(
    errors_value: Any,
    expected_message: str,
) -> None:
    """A truthy non-list ``errors`` value is one entry, not a ``TypeError``."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"errors": errors_value}),
        )
        with (
            LinearClient(api_key="key") as client,
            pytest.raises(LinearAPIError) as exc,
        ):
            client.execute("query { viewer { id } }")
    assert isinstance(exc.value, LinearGraphQLError)  # noqa: S101
    assert len(exc.value.errors) == 1  # noqa: S101
    assert exc.value.errors[0].message == expected_message  # noqa: S101


@pytest.mark.parametrize(
    ("errors_value", "expected_message"),
    _NON_LIST_ERRORS_CASES,
    ids=_NON_LIST_IDS,
)
async def test_non_list_errors_container_raises_linearapierror_async(
    errors_value: Any,
    expected_message: str,
) -> None:
    """The async path shares ``_handle_response``; the contract holds there too."""
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"errors": errors_value}),
        )
        async with LinearClient(api_key="key") as client:
            with pytest.raises(LinearAPIError) as exc:
                await client.execute_async("query { viewer { id } }")
    assert isinstance(exc.value, LinearGraphQLError)  # noqa: S101
    assert len(exc.value.errors) == 1  # noqa: S101
    assert exc.value.errors[0].message == expected_message  # noqa: S101


def test_coerce_error_preserves_salvages_or_degrades() -> None:
    """``_coerce_error`` is the single guard backing the contract.

    Well-formed dicts keep their structured ``code``; spec-violating dicts
    salvage their usable fields (a string ``message``, a dict ``extensions``,
    vendor-specific extra keys) and drop only the optional fields that fail
    validation — each independently, so a valid sibling survives; entries
    with an unusable ``message`` and non-dict values degrade to
    ``GraphQLError(message=str(entry))`` — the same fallback the non-dict
    branch has always used.
    """
    well_formed = _coerce_error(
        {"message": "nope", "extensions": {"code": "FORBIDDEN"}},
    )
    assert well_formed.message == "nope"  # noqa: S101
    assert well_formed.code == "FORBIDDEN"  # noqa: S101
    salvaged = _coerce_error({"message": "boom", "extensions": None})
    assert salvaged.message == "boom"  # noqa: S101
    salvaged_code = _coerce_error(
        {"message": "x", "extensions": {"code": "RATELIMITED"}, "path": "oops"},
    )
    assert salvaged_code.message == "x"  # noqa: S101
    assert salvaged_code.code == "RATELIMITED"  # noqa: S101
    # List fields with invalid elements are dropped individually, so the
    # message, code, and any valid sibling field survive.
    bad_elements = {"message": "x", "path": [{"bad": "shape"}]}
    salvaged_elements = _coerce_error(bad_elements)
    assert salvaged_elements.message == "x"  # noqa: S101
    bad_elements_code = {
        "message": "x",
        "extensions": {"code": "RATELIMITED"},
        "path": [{"bad": "shape"}],
    }
    salvaged_elements_code = _coerce_error(bad_elements_code)
    assert salvaged_elements_code.message == "x"  # noqa: S101
    assert salvaged_elements_code.code == "RATELIMITED"  # noqa: S101
    # A valid ``locations`` survives an invalid ``path`` sibling.
    mixed = {
        "message": "x",
        "locations": [{"line": 1, "column": 2}],
        "path": [1.5],
    }
    salvaged_mixed = _coerce_error(mixed)
    assert salvaged_mixed.message == "x"  # noqa: S101
    assert salvaged_mixed.path is None  # noqa: S101
    locations = salvaged_mixed.locations
    assert locations is not None  # noqa: S101
    assert locations[0].line == 1  # noqa: S101
    assert locations[0].column == 2  # noqa: S101
    # Vendor-specific extra keys survive salvage, exactly as they survive
    # direct validation (GraphQLError uses extra="allow").
    extras = {"message": "x", "path": "oops", "vendorKey": "keep-me"}
    salvaged_extras = _coerce_error(extras)
    assert salvaged_extras.message == "x"  # noqa: S101
    assert salvaged_extras.model_extra == {"vendorKey": "keep-me"}  # noqa: S101
    for entry in ({}, {"message": 123}, "oops", 42):
        degraded = _coerce_error(entry)
        assert degraded.message == str(entry)  # noqa: S101
        assert isinstance(degraded, GraphQLError)  # noqa: S101
