"""Tests for the MCP server assembly.

Covers category 7 from ``prompt-step1.md``:

* Tool registration: ``list_pets`` is registered and discoverable via the
  SDK's in-memory client (``Client(mcp)``).
* JSON Schema of the tool matches the pydantic model.
* ``/health`` returns 200.

This uses the SDK's :class:`mcp.Client` connected directly to the
:class:`MCPServer` instance (process-internal transport, no HTTP). That
is the cleanest way to assert tool registration without touching the
network. The stateless HTTP behaviour is covered separately in
``test_stateless_flow.py``.
"""

from __future__ import annotations

import json

import httpx
import pytest

from pet_hospital_mcp.server import create_app, create_mcp


# ---------------------------------------------------------------------------
# Tool registration via the SDK in-memory client.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_pets_is_registered(backend_settings):
    mcp = create_mcp(backend_settings)
    from mcp import Client

    async with Client(mcp) as client:
        result = await client.list_tools()
    tools = result.tools
    names = [t.name for t in tools]
    assert "list_pets" in names
    assert len(names) == 1, "phase 1 must register exactly one tool"


@pytest.mark.asyncio
async def test_list_pets_input_schema_matches_pydantic(backend_settings):
    mcp = create_mcp(backend_settings)
    from mcp import Client

    async with Client(mcp) as client:
        result = await client.list_tools()
    tools = result.tools
    tool = next(t for t in tools if t.name == "list_pets")

    # Pydantic schema is exposed as input_schema.
    schema = tool.input_schema
    assert schema["type"] == "object"

    props = schema["properties"]
    # All 14 fields must be present, in snake_case.
    expected = {
        "q",
        "name",
        "owner_name",
        "owner_phone",
        "species",
        "doctor",
        "disease",
        "status",
        "min",
        "max",
        "sort_by",
        "order",
        "page",
        "page_size",
    }
    assert expected.issubset(set(props.keys())), (
        f"missing fields: {expected - set(props.keys())}"
    )

    # Enum check: species carries the 7 backend values.
    assert set(props["species"]["enum"]) == {
        "犬",
        "猫",
        "兔",
        "鸟",
        "仓鼠",
        "爬宠",
        "其他",
    }
    assert set(props["status"]["enum"]) == {
        "待就诊",
        "就诊中",
        "住院中",
        "已康复",
        "慢性病随访",
    }
    assert "enum" in props["order"]
    assert set(props["order"]["enum"]) == {"asc", "desc"}

    # Numeric bounds.
    assert props["page"]["minimum"] == 1
    assert props["page_size"]["minimum"] == 1
    assert props["page_size"]["maximum"] == 500

    # Unknown fields must be rejected.
    assert schema.get("additionalProperties") is False


@pytest.mark.asyncio
async def test_list_pets_description_documented(backend_settings):
    mcp = create_mcp(backend_settings)
    from mcp import Client

    async with Client(mcp) as client:
        result = await client.list_tools()
    tools = result.tools
    tool = next(t for t in tools if t.name == "list_pets")
    # Description must mention purpose, parameters, applicable scenarios, return.
    desc = tool.description
    assert desc
    assert "GET /api/v1/pets" in desc
    assert "page" in desc and "page_size" in desc
    assert "error" in desc  # return value shape documented


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_endpoint_returns_200(asgi_app, httpx_asgi_client: httpx.AsyncClient):
    r = await httpx_asgi_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["service"] == "pet-hospital-mcp"
    assert body["mcp_sdk_version"] == "2.0.0"
    assert body["protocol_version"] == "2026-07-28"
    assert body["stateless"] is True
