"""Unified error model for the MCP service.

The MCP tool ``list_pets`` (and future tools) return a single, consistent
error envelope instead of leaking httpx / pydantic / SDK / Python stack
traces to MCP clients::

    {
        "error": {
            "code": "ERROR_CODE",
            "message": "可读错误信息",
            "details": { ... }
        }
    }

Six error codes cover the failure modes the service can produce:

* ``VALIDATION_ERROR``         — tool input failed pydantic validation.
* ``BACKEND_TIMEOUT``          — httpx timed out calling the Go API.
* ``BACKEND_UNAVAILABLE``      — httpx could not establish a connection.
* ``BACKEND_API_ERROR``        — Go API returned non-200 or a body that
                                  fails the response envelope check.
* ``BACKEND_INVALID_RESPONSE``— response body is not valid JSON or does
                                  not match the expected data model.
* ``INTERNAL_ERROR``           — any other unexpected exception.

The :class:`ToolError` exception carries these codes; tools catch it and
convert it to :class:`ErrorOutput`, which is the structure returned to the
MCP client through ``structured_content``.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """Stable error code strings exposed to MCP clients."""

    VALIDATION_ERROR = "VALIDATION_ERROR"
    BACKEND_TIMEOUT = "BACKEND_TIMEOUT"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    BACKEND_API_ERROR = "BACKEND_API_ERROR"
    BACKEND_INVALID_RESPONSE = "BACKEND_INVALID_RESPONSE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorPayload(BaseModel):
    """Inner payload of the unified error envelope."""

    code: str = Field(description="Stable error code, e.g. VALIDATION_ERROR")
    message: str = Field(description="Human-readable error message")
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured details (field errors, status, etc.)",
    )


class ErrorOutput(BaseModel):
    """Top-level error envelope returned by tools on failure.

    Modelled so that the JSON shape on the wire is exactly::

        {"error": {"code": "...", "message": "...", "details": {...}}}
    """

    error: ErrorPayload


class ToolError(Exception):
    """Internal exception carrying a structured error.

    Tools raise :class:`ToolError` (or wrap caught exceptions in it). The
    outer tool wrapper converts it to :class:`ErrorOutput` so the wire
    shape stays consistent regardless of which layer raised the error.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_output(self) -> ErrorOutput:
        """Convert to the wire-shape :class:`ErrorOutput` model."""
        return ErrorOutput(
            error=ErrorPayload(
                code=self.code.value,
                message=self.message,
                details=self.details,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to a plain dict (used in logs and tests)."""
        return self.to_output().model_dump(mode="json")
