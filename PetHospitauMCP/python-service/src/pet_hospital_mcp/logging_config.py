"""Structured JSON logging via structlog, with recursive PII masking.

Required log fields for every tool invocation:

* ``timestamp``    — ISO-8601 UTC.
* ``tool_name``     — name of the MCP tool (e.g. ``list_pets``).
* ``params``        — sanitised tool input parameters.
* ``status``        — ``ok`` or ``error``.
* ``duration_ms``   — wall-clock duration of the tool call.

PII fields that must be masked (recursively, in dicts and lists) under
both camelCase and snake_case spellings:

* ``ownerPhone`` / ``owner_phone``
* ``ownerAddr`` / ``owner_addr``
* ``chipNo`` / ``chip_no``

Masking keeps the first 3 and last 4 characters when possible, otherwise
returns ``"***"``. The original value is never written to logs.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from pet_hospital_mcp.config import Settings

# Fields that must be masked wherever they appear (camelCase + snake_case).
PII_FIELDS: frozenset[str] = frozenset(
    {
        "ownerPhone",
        "owner_phone",
        "ownerAddr",
        "owner_addr",
        "chipNo",
        "chip_no",
    }
)


def mask_value(value: Any) -> str:
    """Mask a single value, keeping first 3 / last 4 chars when sensible."""
    s = str(value)
    if len(s) <= 7:
        return "***"
    return f"{s[:3]}***{s[-4:]}"


def sanitize(obj: Any) -> Any:
    """Recursively deep-copy and mask PII fields in dicts / lists.

    Returns a sanitised copy; the input is never mutated.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in PII_FIELDS:
                out[k] = mask_value(v)
            else:
                out[k] = sanitize(v)
        return out
    if isinstance(obj, list):
        return [sanitize(v) for v in obj]
    return obj


def configure_logging(settings: Settings) -> None:
    """Configure structlog + stdlib logging to emit JSON to stdout.

    Safe to call once at startup; subsequent calls reconfigure.
    """
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        level=level,
        stream=sys.stdout,
        format="%(message)s",
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> "structlog.stdlib.BoundLogger":
    """Return a bound structlog logger."""
    return structlog.get_logger(name)
