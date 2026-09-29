"""Application configuration loaded from environment variables.

All knobs a deployment needs live here:

* ``MCP_HOST`` / ``MCP_PORT`` — where the MCP server listens (default 127.0.0.1:8081).
* ``MCP_PATH`` — the MCP endpoint path (default ``/mcp``).
* ``PET_HOSPITAL_BASE_URL`` — upstream Go REST API base URL.
* ``LOG_LEVEL`` — structlog level.
* ``REQUEST_TIMEOUT`` — per-request httpx timeout in seconds.
* ``MAX_RETRIES`` — how many times to retry idempotent backend calls on
  5xx / connection errors (4xx is never retried).
* ``RETRY_BACKOFF`` — base seconds for exponential backoff between retries.

The class is a thin :class:`pydantic_settings.BaseSettings` wrapper; tests
construct it directly via ``Settings()`` or override env vars.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # MCP server bind / endpoint
    mcp_host: str = Field(default="127.0.0.1", alias="MCP_HOST")
    mcp_port: int = Field(default=8081, alias="MCP_PORT")
    mcp_path: str = Field(default="/mcp", alias="MCP_PATH")

    # Upstream Go REST API
    pet_hospital_base_url: str = Field(
        default="http://127.0.0.1:8080", alias="PET_HOSPITAL_BASE_URL"
    )

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # httpx backend client
    request_timeout: float = Field(default=10.0, alias="REQUEST_TIMEOUT")
    max_retries: int = Field(default=2, alias="MAX_RETRIES")
    retry_backoff: float = Field(default=0.4, alias="RETRY_BACKOFF")

    def httpx_timeout(self) -> "float":
        """Return the request timeout as a plain float (helps typing)."""
        return float(self.request_timeout)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a process-wide cached :class:`Settings` instance.

    Tests can call ``get_settings.cache_clear()`` to pick up env changes.
    """
    return Settings()
