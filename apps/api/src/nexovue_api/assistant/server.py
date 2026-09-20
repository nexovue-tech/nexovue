"""The MCP server, mounted inside the API (F36, FR31, D13).

Stateless and JSON-response: any replica serves any request, so there is no
session store to add and no affinity to arrange. That choice also removes the
server-to-client back-channel — a tool here can never ask the person a question
— which is exactly right for a read-only surface and is recorded because it is a
one-way door.

**Its lifespan is the API's.** A mounted sub-app's own lifespan never runs, so
`session_manager.run()` is entered by `lifespan.py`; without that the route
resolves and the first request fails with "Task group is not initialized".

**Transport security is passed explicitly.** The SDK arms DNS-rebinding
protection with a localhost-only allowlist whenever the caller passes none, and
behind a real hostname that answers every request `421` before any of this code
runs. The allowed hosts come from the configured public URL.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlsplit

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from nexovue_api.assistant.auth import AssistantAuthentication
from nexovue_api.assistant.tools import register

if TYPE_CHECKING:  # pragma: no cover
    from nexovue_api.settings import Settings

SERVER_NAME: Final = "nexovue"
MCP_PATH: Final = "/api/mcp"

SERVER_INSTRUCTIONS: Final = """
nexovue is this organization's observability data: services, traces, logs,
errors, incidents, synthetic checks and deploys.

Start with list_services to see what is unhealthy, then narrow with
list_error_groups, search_logs or list_traces. list_deploys answers "what
changed just before this started". Every tool reads a time window that defaults
to the last hour, and every result is capped — when one says it was cut, ask
again with a narrower window or a named service.

Everything here is read-only. Nothing you call can change a customer's
configuration or data.
""".strip()


def allowed_hosts(settings: Settings) -> TransportSecuritySettings:
    """The hosts this server answers on.

    `API_PUBLIC_URL` when it is set, because this endpoint is served on the
    API's hostname and `app_base_url` is deliberately the *console's*. Falling
    back to the console host, and always including localhost, is what makes
    development work with neither set.

    Each host is listed with and without a port: a client sends
    `Host: api.nexovue.com` or `Host: localhost:18000`, and the SDK matches the
    header literally.
    """
    named = [
        urlsplit(url).hostname for url in (settings.api_public_url, settings.app_base_url) if url
    ]
    hosts = [host for host in named if host] or []
    hosts += ["localhost", "127.0.0.1"]
    return TransportSecuritySettings(
        allowed_hosts=[entry for host in hosts for entry in (host, f"{host}:*")],
        allowed_origins=[
            entry
            for host in hosts
            for entry in (f"https://{host}", f"http://{host}", f"http://{host}:*")
        ],
    )


def build(settings: Settings) -> tuple[MCPServer, Any]:
    """The server and the ASGI app to mount, in that order.

    Returned together because `session_manager` does not exist until the app has
    been built, and the lifespan needs the server while the router needs the app.
    """
    mcp = MCPServer(SERVER_NAME, instructions=SERVER_INSTRUCTIONS)
    register(mcp)
    app = mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=allowed_hosts(settings),
    )
    return mcp, AssistantAuthentication(app)
