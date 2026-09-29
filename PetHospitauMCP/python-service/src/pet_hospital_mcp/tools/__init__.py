"""MCP tools package.

Phase 1 registers a single tool ``list_pets``. Future phases can add more
tool modules under this package; they are wired into the MCP server via
:func:`register_all`, which the server calls at startup.
"""

from pet_hospital_mcp.tools.list_pets import register as _register_list_pets

__all__ = ["register_all"]


def register_all(mcp) -> None:
    """Register every phase-1 tool on the given MCPServer instance.

    Future phases should call their own ``register(mcp)`` here.
    """
    _register_list_pets(mcp)
