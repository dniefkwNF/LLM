"""Asynchronous HTTP client for the upstream Go Pet Hospital REST API.

The MCP service never talks to the database directly — it only issues HTTP
requests to the Go REST API exposed at :envvar:`PET_HOSPITAL_BASE_URL`.

Behaviour:

* One :class:`httpx.AsyncClient` per :class:`PetHospitalClient` instance,
  closed via :meth:`aclose`.
* Per-request timeout (:envvar:`REQUEST_TIMEOUT`).
* Limited retries (:envvar:`MAX_RETRIES`) on connection errors and 5xx
  responses with exponential backoff (:envvar:`RETRY_BACKOFF`). 4xx is
  never retried — it's a client problem.
* All failures are wrapped in :class:`ToolError` with one of
  ``BACKEND_TIMEOUT``, ``BACKEND_UNAVAILABLE``, ``BACKEND_API_ERROR`` or
  ``BACKEND_INVALID_RESPONSE`` so the tool layer can surface a uniform
  error envelope to MCP clients.

Only ``GET /api/v1/pets`` is used in phase 1.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.errors import ErrorCode, ToolError
from pet_hospital_mcp.logging_config import get_logger, sanitize

_log = get_logger("pet_hospital_mcp.rest_client")


class PetHospitalClient:
    """Thin async wrapper over ``GET /api/v1/pets`` on the Go REST API."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_url = settings.pet_hospital_base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(settings.request_timeout),
            headers={
                "Accept": "application/json",
                "User-Agent": "pet-hospital-mcp/1.0.0",
            },
        )

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        await self._client.aclose()

    async def __aenter__(self) -> "PetHospitalClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def list_pets(self, params: dict[str, Any]) -> dict[str, Any]:
        """Call ``GET /api/v1/pets`` with the given query params.

        Args:
            params: Query parameters already in camelCase (``ownerName``,
                ``pageSize`` …). Empty values are dropped before sending
                so the Go server applies its own defaults.

        Returns:
            The ``data`` object from the Go response envelope::

                {"items": [...], "total": int, "page": int,
                 "pageSize": int, "totalPages": int, "totalCost": float}

        Raises:
            ToolError: with one of ``BACKEND_TIMEOUT``,
                ``BACKEND_UNAVAILABLE``, ``BACKEND_API_ERROR`` or
                ``BACKEND_INVALID_RESPONSE``.
        """
        # Drop empty values so the backend applies its own defaults.
        clean_params = {k: v for k, v in params.items() if v is not None and v != ""}

        _log.debug(
            "calling backend",
            method="GET",
            path="/api/v1/pets",
            params=sanitize(clean_params),
        )

        last_error: ToolError | None = None
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = await self._client.get("/api/v1/pets", params=clean_params)
            except httpx.TimeoutException as exc:
                last_error = ToolError(
                    ErrorCode.BACKEND_TIMEOUT,
                    f"上游响应超时: {exc}",
                    details={"attempt": attempt + 1, "max_attempts": self._settings.max_retries + 1},
                )
                _log.warning(
                    "backend timeout",
                    attempt=attempt + 1,
                    error=str(exc),
                )
            except httpx.ConnectError as exc:
                last_error = ToolError(
                    ErrorCode.BACKEND_UNAVAILABLE,
                    f"无法连接上游服务: {exc}",
                    details={"attempt": attempt + 1, "base_url": self._base_url},
                )
                _log.warning(
                    "backend unavailable",
                    attempt=attempt + 1,
                    error=str(exc),
                )
            except httpx.HTTPError as exc:
                # Other transport-level httpx errors: treat as unavailable.
                last_error = ToolError(
                    ErrorCode.BACKEND_UNAVAILABLE,
                    f"上游请求失败: {exc}",
                    details={"attempt": attempt + 1},
                )
                _log.warning(
                    "backend http error",
                    attempt=attempt + 1,
                    error=str(exc),
                )
            else:
                # Transport succeeded — now decide based on status code.
                if 500 <= response.status_code < 600:
                    last_error = ToolError(
                        ErrorCode.BACKEND_API_ERROR,
                        f"上游返回 {response.status_code}",
                        details={
                            "status": response.status_code,
                            "body": response.text[:512],
                        },
                    )
                    _log.warning(
                        "backend 5xx",
                        status=response.status_code,
                        attempt=attempt + 1,
                    )
                else:
                    # 2xx or 4xx — do not retry; parse envelope.
                    return self._parse_list_response(response)

            # Retry backoff (skip after the last attempt).
            if attempt < self._settings.max_retries:
                await asyncio.sleep(self._settings.retry_backoff * (2 ** attempt))

        # Exhausted retries.
        assert last_error is not None
        raise last_error

    def _parse_list_response(self, response: httpx.Response) -> dict[str, Any]:
        """Validate the Go response envelope and return ``data``.

        Envelope shape from the Go API::

            {"code": 200, "message": "ok", "data": {...}, "time": "..."}

        Raises:
            ToolError: ``BACKEND_INVALID_RESPONSE`` if the body is not
                JSON or the envelope is malformed; ``BACKEND_API_ERROR``
                if the envelope carries a non-200 code.
        """
        try:
            payload = response.json()
        except Exception as exc:
            raise ToolError(
                ErrorCode.BACKEND_INVALID_RESPONSE,
                f"上游返回非法 JSON: {exc}",
                details={"status": response.status_code, "body": response.text[:512]},
            ) from exc

        if not isinstance(payload, dict):
            raise ToolError(
                ErrorCode.BACKEND_INVALID_RESPONSE,
                "上游响应不是 JSON 对象",
                details={"status": response.status_code, "body": response.text[:512]},
            )

        code = payload.get("code")
        if code != 200:
            raise ToolError(
                ErrorCode.BACKEND_API_ERROR,
                f"上游返回业务错误 code={code}: {payload.get('message', '')}",
                details={
                    "status": response.status_code,
                    "code": code,
                    "message": payload.get("message"),
                },
            )

        data = payload.get("data")
        if not isinstance(data, dict):
            raise ToolError(
                ErrorCode.BACKEND_INVALID_RESPONSE,
                "上游响应缺少 data 对象",
                details={"status": response.status_code, "envelope": payload},
            )

        return data
