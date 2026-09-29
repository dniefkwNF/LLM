# Phase 2 Upgrade Prompt

> **Role:** You are an expert MCP + Python developer. Continue the
> `pet_hospital_mcp` project by adding the remaining Go Pet Hospital REST API
> endpoints as MCP tools, following the same conventions established in phase 1.

## Context

Phase 1 delivered a working MCP service with one tool (`list_pets`) that maps
`GET /api/v1/pets`. The project uses:

- **Python 3.11+**, **`mcp==2.0.0`** SDK, **`MCPServer`** (not FastMCP)
- **MCP protocol `2026-07-28`**, stateless Streamable HTTP
- `pydantic` for input/output models, `httpx` for backend calls, `structlog` for logging
- Unified error envelope: `{"error": {"code", "message", "details"}}`
- Tool registration via `tools/__init__.py` → `register_all(mcp)`

Read these files before starting:

1. `README.md` — project overview and conventions
2. `src/pet_hospital_mcp/tools/list_pets.py` — reference implementation for a tool
3. `src/pet_hospital_mcp/rest_client.py` — HTTP client to extend
4. `src/pet_hospital_mcp/errors.py` — error codes and envelope
5. `my-mcp-projects/pet-hospital-mcp-teaching/internal/api/api.go` — all Go REST endpoints

## Task

Add MCP tools for the remaining Go Pet Hospital REST API endpoints. Based on
`api.go`, the endpoints not yet exposed are:

| Go endpoint | Suggested MCP tool name | Method |
|-------------|------------------------|--------|
| `GET /api/v1/pets/{id}` | `get_pet` | GET |
| `POST /api/v1/pets` | `create_pet` | POST |
| `PUT /api/v1/pets/{id}` | `update_pet` | PUT |
| `DELETE /api/v1/pets/{id}` | `delete_pet` | DELETE |
| `POST /api/v1/pets/{id}/records` | `add_medical_record` | POST |
| `POST /api/v1/pets/{id}/charges` | `add_charge` | POST |
| `GET /api/v1/stats` | `get_stats` | GET |

(Cross-check the exact routes and request/response bodies against `api.go`.)

## Requirements

1. **One module per tool** in `src/pet_hospital_mcp/tools/` — each following
   the `list_pets.py` pattern (Pydantic input model, `_impl` function,
   `register(mcp)` function, `CallToolResult` return with flat
   `structured_content`).

2. **Extend `rest_client.py`** with methods for each new endpoint, reusing
   the existing retry/timeout/error-parsing logic. Do not duplicate httpx
   client setup.

3. **Reuse `errors.py`** — add new error codes only if the existing six
   (`VALIDATION_ERROR`, `BACKEND_TIMEOUT`, `BACKEND_UNAVAILABLE`,
   `BACKEND_API_ERROR`, `BACKEND_INVALID_RESPONSE`, `INTERNAL_ERROR`)
   are insufficient.

4. **Reuse `logging_config.py`** — all tools must log `timestamp`,
   `tool_name`, `params`, `status`, `duration_ms` with PII masking.

5. **Input validation** — every Pydantic input model must strictly validate
   enums against the Go `model.go` constants, reject unknown fields
   (`extra="forbid"`), and enforce numeric bounds where applicable.

6. **Tests** — for each new tool, add a test file in `tests/` covering:
   - Normal call (params forwarded, response parsed)
   - Input validation failures (enum, bound, type, unknown field)
   - Backend 4xx / 5xx
   - Timeout / connection error
   - Invalid backend response
   - records/charges null vs array (for endpoints that return Pet data)

7. **Register all tools** via `tools/__init__.py` → `register_all(mcp)`.

8. **Do not modify the Go service.**

9. **Do not implement authentication, CORS, or Origin validation**
   (teaching scenario).

10. **Run `pytest -q` — all tests (phase 1 + phase 2) must pass.**

## Conventions to follow (from phase 1)

- Tool names: `snake_case`.
- Tool input fields: `snake_case`, sent to Go API in `camelCase`.
- Tool function signature: explicit `Optional` typed parameters (not
  `**kwargs`), with permissive types so the SDK doesn't short-circuit
  validation before the function body runs.
- Schema: override `tool.parameters` with a hand-built flat JSON Schema
  (see `_build_input_schema()` in `list_pets.py`).
- Return: `CallToolResult` directly with `structured_output=False`,
  `structured_content` set to the pydantic model dump, `is_error` flag.
- `_impl` function: pure, takes a dict + client, returns `Union[OutputModel, ErrorOutput]`.
- Pydantic error `ctx` field stripped before putting in `details` (not JSON-serialisable).
