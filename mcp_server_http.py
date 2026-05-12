#!/usr/bin/env python3
"""
Zendesk MCP Server — HTTP/SSE Transport.

Exposes the MCP server over HTTP using Server-Sent Events (SSE) for remote
clients (Kiro IDE, Kiro CLI, Claude Code) connecting over a network.

Endpoints:
  GET  /sse      — SSE connection endpoint (clients connect here first)
  POST /messages — Message endpoint (clients send JSON-RPC requests here)
  GET  /health   — Health check for Docker/load balancer probes

Includes proper lifespan management: the Zendesk HTTP client is opened on
startup and closed on shutdown.
"""

import os
from contextlib import asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from mcp.server.sse import SseServerTransport

from mcp_server import app as mcp_app
from zendesk_client import client

# SSE transport — /messages is where clients POST JSON-RPC requests
sse = SseServerTransport("/messages")


@asynccontextmanager
async def lifespan(app):
    """Manage the Zendesk HTTP client lifecycle: open on startup, close on shutdown."""
    await client.open()
    yield
    await client.close()


async def handle_sse(request):
    """Handle incoming SSE connections from MCP clients."""
    async with sse.connect_sse(
        request.scope, request.receive, request._send  # ASGI send callable
    ) as (read_stream, write_stream):
        await mcp_app.run(read_stream, write_stream, mcp_app.create_initialization_options())


async def handle_messages(request):
    """Handle incoming JSON-RPC messages posted by MCP clients."""
    await sse.handle_post_message(request.scope, request.receive, request._send)


async def health(request):
    """Health check endpoint for Docker and load balancers."""
    return JSONResponse({"status": "ok", "server": "zendesk-mcp", "transport": "sse"})


# Starlette application with lifespan and routes
starlette_app = Starlette(
    debug=False,
    lifespan=lifespan,
    routes=[
        Route("/health", health, methods=["GET"]),
        Route("/sse", handle_sse, methods=["GET"]),
        Route("/messages", handle_messages, methods=["POST"]),
    ],
)


if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8080"))
    print(f"Starting Zendesk MCP Server (HTTP/SSE) on {host}:{port}")
    print(f"  SSE endpoint:      http://{host}:{port}/sse")
    print(f"  Messages endpoint: http://{host}:{port}/messages")
    print(f"  Health check:      http://{host}:{port}/health")
    uvicorn.run(starlette_app, host=host, port=port)
