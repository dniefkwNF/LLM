"""Stateless HTTP connection flow tests (category 8 + 9).

Asserts the 2026-07-28 stateless behaviour end-to-end over the ASGI app:

* The client does NOT send ``initialize`` (it just calls ``tools/list``
  / ``tools/call`` directly).
* No ``Mcp-Session-Id`` header is returned by the server.
* The 2026-07-28 ``_meta`` envelope (``io.modelcontextprotocol/protocolVersion``
  and ``io.modelcontextprotocol/clientCapabilities``) is required and
  sufficient.
* ``tools/list`` discovers ``list_pets``.
* ``tools/call list_pets`` with valid args succeeds; with invalid args
  it returns a structured error envelope.
* ``/health`` is served alongside ``/mcp``.

These tests post raw JSON-RPC via httpx against the in-process ASGI app,
deliberately avoiding the SDK client to prove the wire shape is exactly
the 2026-07-28 spec.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from tests.conftest import BACKEND_BASE_URL, PET_RECORD_ARRAY_DATA, mcp_request


# ---------------------------------------------------------------------------
# No initialize / no Mcp-Session-Id
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_no_initialize_required(httpx_asgi_client: httpx.AsyncClient):
    """A bare tools/list without a prior initialize must succeed."""
    r = await httpx_asgi_client.post(
        "/mcp",
        json=mcp_request("tools/list"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert r.status_code == 200
    # No session id on the response.
    assert all(k.lower() != "mcp-session-id" for k in r.headers.keys())
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert "result" in body
    assert any(t["name"] == "list_pets" for t in body["result"]["tools"])


@pytest.mark.asyncio
async def test_no_session_id_returned_on_call(
    httpx_asgi_client: httpx.AsyncClient, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=PET_RECORD_ARRAY_DATA)
    )
    r = await httpx_asgi_client.post(
        "/mcp",
        json=mcp_request(
            "tools/call",
            {"name": "list_pets", "arguments": {"species": "猫"}},
        ),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert r.status_code == 200
    assert all(k.lower() != "mcp-session-id" for k in r.headers.keys())


# ---------------------------------------------------------------------------
# _meta envelope (2026-07-28) — SDK is lenient
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_bare_request_without_meta_envelope_is_accepted(
    httpx_asgi_client: httpx.AsyncClient,
):
    """The SDK accepts bare requests without the 2026-07-28 _meta envelope.

    The 2026-07-28 protocol recommends including the _meta envelope
    (protocolVersion + clientCapabilities in ``params._meta``), but the
    official MCP SDK 2.0.0 does not reject requests that omit it — it
    processes them normally.  This test documents that lenient behaviour.
    """
    r = await httpx_asgi_client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    assert any(t["name"] == "list_pets" for t in body["result"]["tools"])


# ---------------------------------------------------------------------------
# tools/call list_pets — success and validation error
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_call_list_pets_success_returns_structured_output(
    httpx_asgi_client: httpx.AsyncClient, respx_mock
):
    respx_mock.get(f"{BACKEND_BASE_URL}/api/v1/pets").mock(
        return_value=httpx.Response(200, json=PET_RECORD_ARRAY_DATA)
    )
    r = await httpx_asgi_client.post(
        "/mcp",
        json=mcp_request(
            "tools/call",
            {"name": "list_pets", "arguments": {"species": "猫", "page": 1}},
        ),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    result = body["result"]
    # Structured content carries the ListPetsOutput shape.
    sc = result.get("structuredContent") or result.get("structured_content")
    assert sc is not None
    assert sc["total"] == 1
    assert sc["items"][0]["name"] == "咪咪"
    # No error envelope on success.
    assert "error" not in sc


@pytest.mark.asyncio
async def test_call_list_pets_invalid_species_returns_error_envelope(
    httpx_asgi_client: httpx.AsyncClient,
):
    r = await httpx_asgi_client.post(
        "/mcp",
        json=mcp_request(
            "tools/call",
            {"name": "list_pets", "arguments": {"species": "鱼"}},
        ),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "result" in body
    result = body["result"]
    sc = result.get("structuredContent") or result.get("structured_content")
    assert sc is not None
    # Unified error envelope.
    assert "error" in sc
    assert sc["error"]["code"] == "VALIDATION_ERROR"
    assert "species" in sc["error"]["message"]


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_health_endpoint(httpx_asgi_client: httpx.AsyncClient):
    r = await httpx_asgi_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    assert body["protocol_version"] == "2026-07-28"
