from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import respx

from gtm_linear import LinearAPIError, LinearClient
from gtm_linear.exceptions import (
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
                    "errors": [{"message": "nope", "extensions": {"code": "FORBIDDEN"}}]
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
