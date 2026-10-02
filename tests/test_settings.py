"""Configuration and secret-handling tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gtm_linear.client import LinearClient
from gtm_linear.settings import LinearSettings


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_from_env")
    monkeypatch.setenv("LINEAR_TIMEOUT", "5")
    # Pyright synthesizes __init__ from the model fields and so thinks api_key
    # is required; BaseSettings fills it from the environment at runtime.
    settings = LinearSettings()  # pyright: ignore[reportCallIssue]
    assert settings.api_key.get_secret_value() == "lin_api_from_env"
    assert settings.timeout == 5.0
    assert settings.base_url == LinearClient.BASE_URL


def test_repr_redacts_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key must not appear in a repr, which is where it leaks into logs."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_supersecret")
    settings = LinearSettings()  # pyright: ignore[reportCallIssue]
    assert "supersecret" not in repr(settings)
    assert "supersecret" not in str(settings.api_key)


def test_from_env_builds_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_from_env")
    monkeypatch.setenv("LINEAR_TIMEOUT", "7")
    client = LinearClient.from_env()
    assert client.api_key.get_secret_value() == "lin_api_from_env"
    assert client.timeout == 7.0
    assert "supersecret" not in repr(client)


def test_missing_api_key_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    # _env_file=None so a developer's local .env.local does not satisfy it.
    # (Pyright cannot see BaseSettings' _env_file kwarg either — same friction.)
    with pytest.raises(ValidationError, match="api_key"):
        LinearSettings(_env_file=None)  # pyright: ignore[reportCallIssue]
