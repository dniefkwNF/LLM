"""pet_hospital_mcp - MCP service exposing the Go Pet Hospital REST API.

A standalone Python MCP service that exposes the Go Pet Hospital REST API
(GET /api/v1/pets) to AI agents via the Model Context Protocol.

* SDK: official Python SDK ``mcp==2.0.0``
* Protocol: MCP ``2026-07-28`` (stateless core)
* Server class: :class:`mcp.server.MCPServer` (NOT FastMCP)
* Transport: stateless Streamable HTTP (single POST endpoint ``/mcp``)
* Tools (phase 1): ``list_pets`` only
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
