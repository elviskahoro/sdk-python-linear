"""Environment-driven configuration.

The SDK does not read the environment unless you ask it to — construct
:class:`LinearClient` directly and nothing here runs. :meth:`LinearClient.from_env`
is the opt-in path.
"""

from __future__ import annotations

# Imported at runtime on purpose: pydantic resolves field annotations when the
# class body executes (even under `from __future__ import annotations`), so a
# TYPE_CHECKING-only import would NameError at import time.
from pydantic import SecretStr  # noqa: TC002
from pydantic_settings import BaseSettings, SettingsConfigDict


class LinearSettings(BaseSettings):
    """Configuration read from ``LINEAR_*`` env vars or a local dotenv file.

    ``api_key`` is a :class:`~pydantic.SecretStr`, so it renders as
    ``SecretStr('**********')`` rather than leaking into tracebacks, logs, or a
    ``repr`` of the settings object.
    """

    model_config = SettingsConfigDict(
        env_prefix="LINEAR_",
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_key: SecretStr
    base_url: str = "https://api.linear.app/graphql"
    timeout: float = 30.0
