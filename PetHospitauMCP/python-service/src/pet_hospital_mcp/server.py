"""MCP server assembly: MCPServer + tools + ``/health`` + stateless HTTP.

The server is built around :class:`mcp.server.MCPServer` (NOT FastMCP,
which is not even shipped with the 2.x SDK).

Key protocol-level properties enforced here:

* **2026-07-28 stateless core**: the Streamable HTTP app is created with
  ``stateless_http=True`` and ``json_response=True``. There is no
  ``initialize``/``initialized`` handshake, no ``Mcp-Session-Id`` header,
  and no server-side session storage. Every request is self-contained
  and can land on any instance behind a round-robin load balancer.
* **Single POST endpoint** at ``MCP_PATH`` (default ``/mcp``).
* **``/health``** is registered via ``MCPServer.custom_route`` so it is
  served by the same Starlette ASGI app, on the same port, without
  sessions or auth (per the spec's "public endpoints" carve-out).

The module exposes both:

* :func:`create_app` — returns the Starlette ASGI app (for tests and
  for embedding under any ASGI server).
* :func:`run` — blocking entry point that calls ``mcp.run`` with the
  Streamable HTTP transport and the configured host/port/path.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from pet_hospital_mcp import __version__
from pet_hospital_mcp.config import Settings, get_settings
from pet_hospital_mcp.logging_config import configure_logging, get_logger
from pet_hospital_mcp.tools import register_all

_log = get_logger("pet_hospital_mcp.server")

# Process-start timestamp for uptime reporting in /health.
_START_TIME = time.time()


def default_transport_security() -> TransportSecuritySettings:
    """Return the transport-security settings used by this service.

    Per ``prompt-step1.md`` ("教学场景不实现认证、权限、CORS 或 Origin
    校验"), DNS-rebinding protection is disabled by default. Deployments
    that want to expose the service to a broader network should pass an
    explicit :class:`TransportSecuritySettings` to :func:`create_app`.
    """
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
        allowed_hosts=[],
        allowed_origins=[],
    )


def create_mcp(settings: Settings | None = None) -> MCPServer:
    """Build an :class:`MCPServer` with all phase-1 tools registered.

    The server is *not* running after this returns; callers either run it
    via :func:`run` or mount its ASGI app via :func:`create_app`.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    mcp = MCPServer(
        name="pet-hospital-mcp",
        version=__version__,
        description=(
            "MCP service exposing the Go Pet Hospital REST API "
            "(GET /api/v1/pets) to AI agents. Stateless Streamable HTTP."
        ),
        instructions=(
            "Phase 1 exposes a single tool `list_pets` that proxies "
            "GET /api/v1/pets on the upstream Go Pet Hospital REST API. "
            "All filter/sort/pagination parameters are optional and "
            "validate against the real backend enums."
        ),
    )

    # Register phase-1 tools (list_pets).
    register_all(mcp)

    # /health — public, no auth, no session, served alongside /mcp.
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        """Liveness probe. Always returns 200 with a small JSON payload."""
        uptime_seconds = int(time.time() - _START_TIME)
        body: dict[str, Any] = {
            "status": "healthy",
            "service": "pet-hospital-mcp",
            "version": __version__,
            "mcp_sdk_version": "2.0.0",
            "protocol_version": "2026-07-28",
            "stateless": True,
            "uptime_seconds": uptime_seconds,
        }
        return JSONResponse(body)

    return mcp


def create_app(
    settings: Settings | None = None,
    transport_security: Optional[TransportSecuritySettings] = None,
):
    """Return the Starlette ASGI app with ``/mcp`` (stateless) + ``/health``.

    The app is suitable for any ASGI server (uvicorn, hypercorn, daphne)
    and for in-process testing with httpx's ASGI transport.

    Args:
        settings: Optional settings override; defaults to env-loaded.
        transport_security: Optional transport security override.
            Defaults to :func:`default_transport_security`, which
            disables DNS-rebinding protection for the teaching scenario.
    """
    settings = settings or get_settings()
    if transport_security is None:
        transport_security = default_transport_security()
    mcp = create_mcp(settings)
    app = mcp.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        json_response=True,
        stateless_http=True,
        host=settings.mcp_host,
        transport_security=transport_security,
    )
    # Help observability: attach references for tests / debugging.
    setattr(app, "mcp_server", mcp)
    setattr(app, "settings", settings)
    _log.info(
        "asgi app created",
        mcp_path=settings.mcp_path,
        host=settings.mcp_host,
        port=settings.mcp_port,
        stateless=True,
    )
    return app


def run(settings: Settings | None = None) -> None:
    """Blocking entry point — run the MCP server over Streamable HTTP.

    Uses ``MCPServer.run`` which internally calls
    ``run_streamable_http_async`` with the configured host / port / path
    and the stateless + json_response options mandated by the
    2026-07-28 protocol.
    """
    settings = settings or get_settings()
    configure_logging(settings)
    mcp = create_mcp(settings)

    _log.info(
        "starting mcp server",
        host=settings.mcp_host,
        port=settings.mcp_port,
        mcp_path=settings.mcp_path,
        stateless=True,
        json_response=True,
        upstream=settings.pet_hospital_base_url,
    )
    print(
        json.dumps(
            {
                "service": "pet-hospital-mcp",
                "version": __version__,
                "mcp_endpoint": f"http://{settings.mcp_host}:{settings.mcp_port}{settings.mcp_path}",
                "health": f"http://{settings.mcp_host}:{settings.mcp_port}/health",
                "upstream": settings.pet_hospital_base_url,
                "protocol": "2026-07-28",
                "mcp_sdk": "2.0.0",
                "stateless": True,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    # mcp.run is synchronous and blocks until interrupted.
    mcp.run(
        transport="streamable-http",
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path=settings.mcp_path,
        json_response=True,
        stateless_http=True,
    )


def main() -> None:
    """Console-script entry point."""
    run()


if __name__ == "__main__":
    main()
