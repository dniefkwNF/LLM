# Pet Hospital MCP Service

An MCP (Model Context Protocol) service that exposes the Go Pet Hospital REST API
(`GET /api/v1/pets`) to AI agents via a single `list_pets` tool.

Built with the **official Python SDK `mcp==2.0.0`**, using `MCPServer` (not
FastMCP), on the **`2026-07-28` protocol** with **stateless Streamable HTTP**.
No `initialize` handshake, no `Mcp-Session-Id`, no session storage.

## Prerequisites

| Component | Version |
|-----------|---------|
| Python    | 3.11+ (tested on 3.13) |
| MCP SDK   | `mcp==2.0.0` |
| Protocol  | `2026-07-28` |
| Go backend| The existing pet-hospital REST API (unchanged) |

The **Go Pet Hospital REST API must be started first** — the MCP service calls
it over HTTP for every `list_pets` invocation.

## Installation

```bash
cd pet_hospital_mcp
pip install -e ".[dev]"
```

This installs `mcp==2.0.0`, `httpx`, `pydantic`, `pydantic-settings`,
`structlog`, `uvicorn`, `starlette`, and dev dependencies (`pytest`,
`pytest-asyncio`, `respx`, `asgi-lifespan`).

## Configuration

All settings are environment variables (or a `.env` file):

| Variable | Default | Description |
|----------|---------|-------------|
| `MCP_HOST` | `127.0.0.1` | MCP server bind address |
| `MCP_PORT` | `8081` | MCP server bind port |
| `MCP_PATH` | `/mcp` | MCP Streamable HTTP endpoint path |
| `PET_HOSPITAL_BASE_URL` | `http://127.0.0.1:8080` | Go REST API base URL |
| `LOG_LEVEL` | `INFO` | structlog severity |
| `REQUEST_TIMEOUT` | `10.0` | httpx timeout (seconds) |
| `MAX_RETRIES` | `2` | retries on 5xx / connection errors |
| `RETRY_BACKOFF` | `0.4` | exponential backoff base (seconds) |

## Starting the services

### 1. Start the Go Pet Hospital REST API

```bash
cd my-mcp-projects/pet-hospital-mcp-teaching
go run .          # listens on :8080
```

### 2. Start the MCP service

```bash
cd pet_hospital_mcp
python -m pet_hospital_mcp       # or: pet-hospital-mcp (console script)
```

The MCP service listens on `http://127.0.0.1:8081/mcp` by default.

## MCP endpoint

| Path | Method | Description |
|------|--------|-------------|
| `/mcp` | `POST` | MCP Streamable HTTP (JSON-RPC 2.0) |
| `/health` | `GET` | Health check |

### `/health` example

```bash
curl http://127.0.0.1:8081/health
```

Response:
```json
{
  "status": "healthy",
  "service": "pet-hospital-mcp",
  "version": "1.0.0",
  "mcp_sdk_version": "2.0.0",
  "protocol_version": "2026-07-28",
  "stateless": true,
  "uptime_seconds": 3.14
}
```

## `list_pets` tool

Exposes `GET /api/v1/pets` with 14 optional parameters (all `snake_case`):

| Parameter | Type | Validation |
|-----------|------|------------|
| `q` | string | full-text search |
| `name` | string | pet name (fuzzy) |
| `owner_name` | string | owner name (fuzzy) |
| `owner_phone` | string | owner phone (fuzzy, masked in logs) |
| `species` | string | enum: 犬/猫/兔/鸟/仓鼠/爬宠/其他 |
| `doctor` | string | doctor (fuzzy) |
| `disease` | string | disease (fuzzy) |
| `status` | string | enum: 待就诊/就诊中/住院中/已康复/慢性病随访 |
| `min` | number | min total cost (≥ 0, finite) |
| `max` | number | max total cost (≥ 0, finite, ≥ min) |
| `sort_by` | string | enum: id/name/ownerName/species/doctor/disease/status/totalCost/visitCount/createdAt/updatedAt |
| `order` | string | enum: asc/desc |
| `page` | integer | ≥ 1 |
| `page_size` | integer | 1–500 |

### Call example (curl + JSON-RPC)

```bash
curl -s http://127.0.0.1:8081/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/call",
    "params": {
      "name": "list_pets",
      "arguments": {"species": "猫", "page": 1, "page_size": 5},
      "_meta": {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "curl", "version": "1.0"}
      }
    }
  }' | python -m json.tool
```

Success response (abbreviated):
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [{"type": "text", "text": "..."}],
    "structuredContent": {
      "items": [{"name": "咪咪", "species": "猫", ...}],
      "total": 1,
      "page": 1,
      "pageSize": 20,
      "totalPages": 1,
      "totalCost": 0.0
    },
    "isError": false
  }
}
```

Error response (invalid `species`):
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "content": [{"type": "text", "text": "{\"error\": {...}}"}],
    "structuredContent": {
      "error": {
        "code": "VALIDATION_ERROR",
        "message": "输入校验失败: ...",
        "details": {"validation_errors": [...]}
      }
    },
    "isError": true
  }
}
```

### Discover tools

```bash
curl -s http://127.0.0.1:8081/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/list"}' | python -m json.tool
```

## Verification with MCP Inspector

```bash
npx @modelcontextprotocol/inspector \
  --transport http \
  --endpoint http://127.0.0.1:8081/mcp
```

Or with the SDK 2.x Python client:

```python
import asyncio
from mcp.client.streamable_http import streamablehttp
from mcp import Client

async def main():
    async with streamablehttp("http://127.0.0.1:8081/mcp") as (read, write, _):
        async with Client(read, write) as client:
            tools = await client.list_tools()
            print([t.name for t in tools.tools])
            result = await client.call_tool("list_pets", {"species": "猫"})
            print(result.structured_content)

asyncio.run(main())
```

## Unit tests

```bash
cd pet_hospital_mcp
pytest -q
```

All tests pass (no real Go service is contacted — `respx` mocks the backend):

```
.................................................................. [100%]
66 passed
```

### Test coverage

| File | Covers |
|------|--------|
| `test_rest_client.py` | HTTP envelope parsing, 4xx/5xx, retry, timeout, connection error, invalid JSON |
| `test_list_pets.py` | Param forwarding, all validation failures, backend errors, null/array `records`+`charges` |
| `test_server.py` | Tool registration, JSON Schema (14 fields, enums, bounds, `additionalProperties: false`), description, `/health` |
| `test_stateless_flow.py` | No `initialize`, no `Mcp-Session-Id`, `tools/list` + `tools/call` over HTTP, `/health` |

## Architecture

```
pet_hospital_mcp/
├── pyproject.toml
├── README.md
├── UPGRADE_PROMPT.md
├── src/
│   └── pet_hospital_mcp/
│       ├── __init__.py          # __version__ = "1.0.0"
│       ├── __main__.py          # python -m pet_hospital_mcp
│       ├── config.py            # Settings (env-driven)
│       ├── server.py             # MCPServer + /health + streamable_http_app
│       ├── rest_client.py        # PetHospitalClient (httpx, retry, timeout)
│       ├── errors.py             # ErrorCode, ErrorOutput, ToolError
│       ├── logging_config.py     # structlog + PII masking
│       └── tools/
│           ├── __init__.py       # register_all()
│           └── list_pets.py      # list_pets tool (Pydantic models + impl)
└── tests/
    ├── conftest.py
    ├── test_rest_client.py
    ├── test_list_pets.py
    ├── test_server.py
    └── test_stateless_flow.py
```

## Error codes

| Code | When |
|------|------|
| `VALIDATION_ERROR` | Invalid tool input (enum, bound, type, unknown field) |
| `BACKEND_TIMEOUT` | Go API timed out after retries |
| `BACKEND_UNAVAILABLE` | Connection refused / network unreachable |
| `BACKEND_API_ERROR` | Go API returned non-200 code envelope |
| `BACKEND_INVALID_RESPONSE` | Response JSON doesn't match data model |
| `INTERNAL_ERROR` | Unexpected failure |

## Phase 2 — not implemented

This is phase 1 only. The single tool `list_pets` is implemented. No
additional tools (create/update/delete pet, medical records, charges, stats)
are included. See `UPGRADE_PROMPT.md` for the next-phase prompt.
