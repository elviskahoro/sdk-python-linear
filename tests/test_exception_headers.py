import httpx2 as httpx
import pytest

from gtm_linear.client import LinearClient
from gtm_linear.exceptions import LinearGraphQLError, LinearHTTPError


def _client() -> LinearClient:
    return LinearClient("lin_api_x")


def test_http_error_headers() -> None:
    resp = httpx.Response(429, headers={"X-RateLimit-Requests-Reset": "99"}, text="no")
    with pytest.raises(LinearHTTPError) as ei:
        _client()._handle_response(resp)
    assert ei.value.headers["x-ratelimit-requests-reset"] == "99"


def test_graphql_error_headers() -> None:
    resp = httpx.Response(
        200,
        headers={"X-RateLimit-Requests-Reset": "5"},
        json={"errors": [{"message": "m"}]},
    )
    with pytest.raises(LinearGraphQLError) as ei:
        _client()._handle_response(resp)
    assert ei.value.headers["x-ratelimit-requests-reset"] == "5"
