"""Configuration and secret-handling tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gtm_linear.client import LinearClient
from gtm_linear.settings import LinearSettings


def test_reads_prefixed_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_from_env")
    monkeypatch.setenv("LINEAR_TIMEOUT", "5")
    settings = LinearSettings()
    assert settings.api_key.get_secret_value() == "lin_api_from_env"  # noqa: S101
    assert settings.timeout == 5.0  # noqa: S101
    assert settings.base_url == LinearClient.BASE_URL  # noqa: S101


def test_repr_redacts_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The key must not appear in a repr, which is where it leaks into logs."""
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_supersecret")
    settings = LinearSettings()
    assert "supersecret" not in repr(settings)  # noqa: S101
    assert "supersecret" not in str(settings.api_key)  # noqa: S101


def test_from_env_builds_a_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_from_env")
    monkeypatch.setenv("LINEAR_TIMEOUT", "7")
    client = LinearClient.from_env()
    assert client.api_key.get_secret_value() == "lin_api_from_env"  # noqa: S101
    assert client.timeout == 7.0  # noqa: S101
    assert "supersecret" not in repr(client)  # noqa: S101


def test_missing_api_key_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    # _env_file=None so a developer's local .env.local does not satisfy it.
    with pytest.raises(ValidationError, match="api_key"):
        LinearSettings(_env_file=None)
