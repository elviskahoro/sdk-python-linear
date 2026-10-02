from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
import respx
from pydantic import SecretStr

from gtm_linear import LinearAPIError, LinearClient
from gtm_linear.client import _coerce_error
from gtm_linear.exceptions import (
    GraphQLError,
    LinearGraphQLError,
    LinearHTTPError,
    LinearResponseError,
)
from gtm_linear.settings import LinearSettings

API_URL = LinearClient.BASE_URL


def test_execute_sync_returns_data() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}}),
        )
        with LinearClient(api_key="key") as client:
            data = client.execute("query { viewer { id } }")
    assert data == {"viewer": {"id": "u1"}}


def test_execute_passes_variables() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"ok": True}}),
        )
        with LinearClient(api_key="key") as client:
            client.execute("query($x: String!){ ok }", {"x": "y"})
    assert route.calls.last.request.read() == (
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
    assert "bad query" in str(exc.value)


def test_authorization_header_is_set() -> None:
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {}}),
        )
        with LinearClient(api_key="secret-key") as client:
            client.execute("query { __typename }")
    assert route.calls.last.request.headers["Authorization"] == "secret-key"


def test_default_timeout_is_finite() -> None:
    client = LinearClient(api_key="key")
    assert client.timeout == 30.0


async def test_execute_async_returns_data() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {"viewer": {"id": "u1"}}}),
        )
        async with LinearClient(api_key="key") as client:
            data = await client.execute_async("query { viewer { id } }")
    assert data == {"viewer": {"id": "u1"}}


async def test_async_context_manager_closes_real_httpx_client() -> None:
    with respx.mock:
        respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {}}),
        )
        client = LinearClient(api_key="key")
        async_client = client._get_async_client()
        assert not async_client.is_closed

        async with client:
            await client.execute_async("query { __typename }")

        assert async_client.is_closed


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

    assert exc.value.codes == ["AUTHENTICATION_ERROR"]
    assert exc.value.errors[0].path == ["viewer"]
    assert isinstance(exc.value, LinearAPIError)


def test_http_error_carries_status_code() -> None:
    """status_code is a real attribute, not smuggled into a fake error entry."""
    with respx.mock:
        respx.post(API_URL).mock(return_value=httpx.Response(503, text="boom"))
        with LinearClient(api_key="key") as client:
            with pytest.raises(LinearHTTPError) as exc:
                client.execute("query { viewer { id } }")

    assert exc.value.status_code == 503
    assert exc.value.body == "boom"


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

    assert exc.value.codes == ["FORBIDDEN"]


def test_repr_does_not_leak_the_api_key() -> None:
    client = LinearClient(api_key="lin_api_supersecret")
    assert "supersecret" not in repr(client)
    assert "supersecret" not in str(client.api_key)
    assert client.api_key.get_secret_value() == "lin_api_supersecret"


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
    assert isinstance(exc.value, LinearGraphQLError)
    assert len(exc.value.errors) == 1
    assert exc.value.errors[0].message == expected_message
    assert "GraphQL error" in str(exc.value)


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
    assert isinstance(exc.value, LinearGraphQLError)
    assert len(exc.value.errors) == 1
    assert exc.value.errors[0].message == expected_message


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
    assert len(errors) == len(error_entries)
    # Well-formed entry keeps its structured shape and surfaces a code.
    assert errors[0].message == "auth"
    assert errors[0].code == "AUTHENTICATION_ERROR"
    # Salvageable entry keeps its message and code; only the bad field drops.
    assert errors[1].message == "partial"
    assert errors[1].code == "RATELIMITED"
    assert exc.value.codes == ["AUTHENTICATION_ERROR", "RATELIMITED"]
    # Entries with an unusable message degrade to str(entry) instead of
    # leaking ValidationError.
    assert errors[2].message == str({})
    # Non-dict sibling branch (the pre-existing fallback) still works.
    assert errors[3].message == "bare string"
    assert errors[4].message == str({"message": 123})
    # The summary joins every entry's message.
    assert "auth" in str(exc.value)
    assert "bare string" in str(exc.value)


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
    assert isinstance(exc.value, LinearGraphQLError)
    assert len(exc.value.errors) == 1
    assert exc.value.errors[0].message == expected_message


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
    assert isinstance(exc.value, LinearGraphQLError)
    assert len(exc.value.errors) == 1
    assert exc.value.errors[0].message == expected_message


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
    assert well_formed.message == "nope"
    assert well_formed.code == "FORBIDDEN"
    salvaged = _coerce_error({"message": "boom", "extensions": None})
    assert salvaged.message == "boom"
    salvaged_code = _coerce_error(
        {"message": "x", "extensions": {"code": "RATELIMITED"}, "path": "oops"},
    )
    assert salvaged_code.message == "x"
    assert salvaged_code.code == "RATELIMITED"
    # List fields with invalid elements are dropped individually, so the
    # message, code, and any valid sibling field survive.
    bad_elements = {"message": "x", "path": [{"bad": "shape"}]}
    salvaged_elements = _coerce_error(bad_elements)
    assert salvaged_elements.message == "x"
    bad_elements_code = {
        "message": "x",
        "extensions": {"code": "RATELIMITED"},
        "path": [{"bad": "shape"}],
    }
    salvaged_elements_code = _coerce_error(bad_elements_code)
    assert salvaged_elements_code.message == "x"
    assert salvaged_elements_code.code == "RATELIMITED"
    # A valid ``locations`` survives an invalid ``path`` sibling.
    mixed = {
        "message": "x",
        "locations": [{"line": 1, "column": 2}],
        "path": [1.5],
    }
    salvaged_mixed = _coerce_error(mixed)
    assert salvaged_mixed.message == "x"
    assert salvaged_mixed.path is None
    locations = salvaged_mixed.locations
    assert locations is not None
    assert locations[0].line == 1
    assert locations[0].column == 2
    # Vendor-specific extra keys survive salvage, exactly as they survive
    # direct validation (GraphQLError uses extra="allow").
    extras = {"message": "x", "path": "oops", "vendorKey": "keep-me"}
    salvaged_extras = _coerce_error(extras)
    assert salvaged_extras.message == "x"
    assert salvaged_extras.model_extra == {"vendorKey": "keep-me"}
    for entry in ({}, {"message": 123}, "oops", 42):
        degraded = _coerce_error(entry)
        assert degraded.message == str(entry)
        assert isinstance(degraded, GraphQLError)


# API key hygiene. Both regressions below came out of one automation run
# against this SDK: LINEAR_API_KEY arrived with a trailing newline, and the
# workaround attempt passed a LinearClient instance where the key belongs.
# Neither failed at construction — the key argument was stored verbatim — so
# both surfaced only at the first request, deep inside httpx, as errors
# pointing at the transport instead of the caller's actual mistake.


@pytest.fixture
def isolated_linear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear sibling ``LINEAR_*`` vars so ambient state cannot skew ``from_env``."""
    monkeypatch.delenv("LINEAR_BASE_URL", raising=False)
    monkeypatch.delenv("LINEAR_TIMEOUT", raising=False)


def test_api_key_whitespace_is_stripped() -> None:
    r"""A trailing newline used to become an illegal header value at request time.

    Secret stores routinely attach ``\n`` to ``LINEAR_API_KEY``; stored
    verbatim it produced ``httpx.LocalProtocolError: Illegal header value
    b'lin_api_...\n'`` on the first request. Now it is stripped at
    construction, so the header actually reaches Linear.
    """
    with respx.mock:
        route = respx.post(API_URL).mock(
            return_value=httpx.Response(200, json={"data": {}}),
        )
        with LinearClient(api_key="lin_api_secret\n") as client:
            client.execute("query { __typename }")
    assert route.calls.last.request.headers["Authorization"] == "lin_api_secret"
    assert client.api_key.get_secret_value() == "lin_api_secret"


def test_from_env_strips_whitespace_from_linear_api_key(
    monkeypatch: pytest.MonkeyPatch,
    isolated_linear_env: None,
) -> None:
    """``from_env`` is the path the trailing newline actually arrived through."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_from_env\n")
    client = LinearClient.from_env()
    assert client.api_key.get_secret_value() == "lin_api_from_env"


def test_whitespace_only_api_key_is_rejected_at_construction() -> None:
    """A blank key is a misconfiguration, not a request waiting to fail.

    Whitespace-only is the degenerate form of the trailing-newline bug (an
    empty ``$(cat missing-file)``): without this guard it either crashes on
    the illegal header or wastes a round trip on Linear's auth error.
    """
    with pytest.raises(ValueError, match="empty after stripping"):
        LinearClient(api_key=" \n")


@pytest.mark.parametrize(
    "bad_key",
    [
        "lin_api_a b",
        "lin_api_a\nb",
        "lin_api_a\x00b",
        "lin_api_a\x7fb",
        "lin_api_aéb",
        "Bearer lin_api_x",
    ],
    ids=["space", "newline", "nul", "del", "non-ascii", "bearer-prefix"],
)
def test_embedded_illegal_header_characters_are_rejected(bad_key: str) -> None:
    """Interior junk survives ``strip()`` and used to reach httpx untouched.

    ``strip()`` only trims the ends, so a bad ``$(cat ...)`` concatenating
    two secrets with a newline, a stray space, a control character, or a
    mojibake byte all passed the strip-only guard and still died at request
    time — as ``Illegal header value`` or an encoding error — instead of at
    construction.
    """
    with pytest.raises(ValueError, match="embedded whitespace, control, or"):
        LinearClient(api_key=bad_key)


def test_unicode_whitespace_padding_is_stripped() -> None:
    r"""``str.strip()`` also trims Unicode whitespace such as NBSP — intended.

    Padding is not interior junk: ``\u00a0lin_api_x\u00a0`` is stripped and
    the key is accepted.
    """
    client = LinearClient(api_key="\u00a0lin_api_x\u00a0")
    assert client.api_key.get_secret_value() == "lin_api_x"


@pytest.mark.parametrize(
    ("offender_kind", "expected_type"),
    [
        ("client", "LinearClient"),
        ("settings", "LinearSettings"),
        ("bytes", "bytes"),
    ],
)
def test_non_string_api_key_is_rejected_at_construction(
    offender_kind: str,
    expected_type: str,
    isolated_linear_env: None,
) -> None:
    """A client, settings, or bytes key used to be stored verbatim as the key.

    ``LinearWorkflow(client)`` forwarded the instance into
    ``LinearClient(api_key=...)``, where direct ``SecretStr(x)`` construction
    happily wrapped it; the first request then died as ``TypeError: Header
    value must be str or bytes, not LinearClient`` deep inside httpx. The
    constructor now names the mistake on the spot and points at the fix.
    Offenders are built in the body rather than the parametrize decorator so
    a hostile ambient environment cannot break module collection.
    """
    offender: object
    if offender_kind == "client":
        offender = LinearClient(api_key="key")
    elif offender_kind == "settings":
        offender = LinearSettings(api_key="lin_api_x")
    else:
        offender = b"lin_api_x"
    with pytest.raises(TypeError, match=f"not {expected_type}") as exc:
        LinearClient(api_key=cast("Any", offender))
    assert "LinearClient.from_env" in str(exc.value)


def test_secretstr_holding_a_non_string_is_rejected() -> None:
    """``SecretStr(x)`` does not validate x, so one check covers both cases."""
    smuggled: Any = 123  # what direct SecretStr(x) construction accepts at runtime
    with pytest.raises(TypeError, match="holding a str, not int"):
        LinearClient(api_key=SecretStr(smuggled))


def test_secretstr_holding_a_padded_value_is_stripped() -> None:
    """The SecretStr path is unwrapped, stripped, and re-wrapped, not trusted."""
    client = LinearClient(api_key=SecretStr("lin_api_secret\n"))
    assert client.api_key.get_secret_value() == "lin_api_secret"


def test_from_settings_strips_a_padded_secretstr(
    isolated_linear_env: None,
) -> None:
    """``from_settings`` funnels through the same constructor guard.

    A later refactor that builds headers straight from the settings object
    would bypass the strip; this pins the forwarding.
    """
    settings = LinearSettings(api_key=SecretStr("lin_api_from_settings\n"))
    client = LinearClient.from_settings(settings)
    assert client.api_key.get_secret_value() == "lin_api_from_settings"


def test_from_env_rejects_a_blank_linear_api_key(
    monkeypatch: pytest.MonkeyPatch,
    isolated_linear_env: None,
) -> None:
    """A blank ``LINEAR_API_KEY`` (empty ``$(cat missing-file)``) fails fast.

    Env vars beat ``.env``/``.env.local`` files and ``SecretStr`` places no
    constraint on the value, so the client's ``ValueError`` — not a settings
    validation error — is what surfaces.
    """
    monkeypatch.setenv("LINEAR_API_KEY", " \n")
    with pytest.raises(ValueError, match="empty after stripping"):
        LinearClient.from_env()
