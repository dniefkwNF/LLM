"""Shared pytest fixtures for pet_hospital_mcp tests.

No test ever hits the real Go service — every backend call is mocked
via respx (httpx) or via an in-process fake client.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from pet_hospital_mcp.config import Settings
from pet_hospital_mcp.rest_client import PetHospitalClient


# ---------------------------------------------------------------------------
# Backend sample payloads — mirror what the Go API actually returns.
# ---------------------------------------------------------------------------
PET_RECORD_NULL_DATA: dict[str, Any] = {
    "code": 200,
    "message": "ok",
    "data": {
        "items": [
            {
                "id": "PET-000001",
                "name": "旺财",
                "species": "犬",
                "breed": "金毛",
                "gender": "公",
                "ageMonths": 36,
                "color": "金色",
                "chipNo": None,
                "ownerName": "张三",
                "ownerPhone": "13800001111",
                "ownerAddr": None,
                "doctor": "李医生",
                "disease": "急性肠胃炎",
                "status": "待就诊",
                "allergy": "",
                "note": "",
                # Go may serialise empty arrays as null — both must parse.
                "records": None,
                "charges": None,
                "totalCost": 0.0,
                "visitCount": 0,
                "createdAt": "2025-01-01T10:00:00+08:00",
                "updatedAt": "2025-01-01T10:00:00+08:00",
            }
        ],
        "total": 1,
        "page": 1,
        "pageSize": 20,
        "totalPages": 1,
        "totalCost": 0.0,
    },
    "time": "2025-01-01T10:00:00+08:00",
}


PET_RECORD_ARRAY_DATA: dict[str, Any] = {
    "code": 200,
    "message": "ok",
    "data": {
        "items": [
            {
                "id": "PET-000002",
                "name": "咪咪",
                "species": "猫",
                "breed": "英短",
                "gender": "母",
                "ageMonths": 24,
                "chipNo": "CHIP-002",
                "ownerName": "李四",
                "ownerPhone": "13900002222",
                "ownerAddr": "北京市朝阳区",
                "doctor": "王医生",
                "disease": "猫瘟",
                "status": "就诊中",
                "records": [
                    {
                        "id": "MR-1",
                        "visitDate": "2025-02-01",
                        "doctor": "王医生",
                        "diagnosis": "猫瘟",
                        "symptoms": "呕吐",
                        "treatment": "输液",
                        "prescription": ["干扰素"],
                        "weightKg": 4.2,
                        "temperature": 39.1,
                        "charge": 580.0,
                        "createdAt": "2025-02-01T09:00:00+08:00",
                    }
                ],
                "charges": [
                    {
                        "id": "CH-1",
                        "item": "血常规",
                        "category": "检查",
                        "amount": 180.0,
                        "doctor": "王医生",
                        "date": "2025-02-01",
                    }
                ],
                "totalCost": 580.0,
                "visitCount": 1,
                "createdAt": "2025-02-01T08:00:00+08:00",
                "updatedAt": "2025-02-01T09:00:00+08:00",
            }
        ],
        "total": 1,
        "page": 1,
        "pageSize": 20,
        "totalPages": 1,
        "totalCost": 580.0,
    },
    "time": "2025-02-01T09:00:00+08:00",
}


BACKEND_BASE_URL = "http://127.0.0.1:8080"


def _envelope(data: dict[str, Any], code: int = 200, message: str = "ok") -> dict[str, Any]:
    """Wrap a data dict in the Go response envelope."""
    return {"code": code, "message": message, "data": data, "time": "2025-01-01T00:00:00+08:00"}


# ---------------------------------------------------------------------------
# Settings / clients
# ---------------------------------------------------------------------------
@pytest.fixture
def backend_settings() -> Settings:
    """Settings pointing at the mocked backend URL."""
    return Settings(
        MCP_HOST="127.0.0.1",
        MCP_PORT=8081,
        MCP_PATH="/mcp",
        PET_HOSPITAL_BASE_URL=BACKEND_BASE_URL,
        REQUEST_TIMEOUT=5.0,
        MAX_RETRIES=1,
        RETRY_BACKOFF=0.0,
    )


@pytest.fixture
def pet_hospital_client(backend_settings: Settings) -> PetHospitalClient:
    """A PetHospitalClient bound to the mocked backend URL."""
    client = PetHospitalClient(backend_settings)
    yield client
    # Best-effort cleanup if a test left it open.
    try:
        import asyncio

        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(client.aclose())
        else:
            loop.run_until_complete(client.aclose())
    except Exception:
        pass


@pytest.fixture
def respx_mock():
    """A respx mock router started & stopped around each test that uses it."""
    with respx.mock(assert_all_called=False) as router:
        yield router


# ---------------------------------------------------------------------------
# ASGI app for stateless-flow tests
# ---------------------------------------------------------------------------
@pytest.fixture
def asgi_app(backend_settings: Settings):
    """The MCP Starlette ASGI app, configured for in-process testing."""
    # Defer import so structlog config doesn't leak between modules.
    from pet_hospital_mcp.server import create_app

    return create_app(settings=backend_settings)


@pytest.fixture
async def httpx_asgi_client(asgi_app):
    """An httpx.AsyncClient wired to the ASGI app via ASGITransport.

    Uses asgi-lifespan to drive the Starlette lifespan so the MCP session
    manager is initialised (otherwise POST /mcp raises "Task group is not
    initialized").
    """
    from asgi_lifespan import LifespanManager

    async with LifespanManager(asgi_app):
        transport = httpx.ASGITransport(app=asgi_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            yield client


def mcp_request(method: str, params: dict[str, Any] | None = None, id_: int = 1) -> dict[str, Any]:
    """Build a 2026-07-28-style JSON-RPC request with the required _meta envelope."""
    return {
        "jsonrpc": "2.0",
        "id": id_,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
                "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "1.0"},
            },
            **(params or {}),
        },
    }
