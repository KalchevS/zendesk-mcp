#!/bin/bash
# Launch the Zendesk MCP server from its project directory.
# This script ensures the working directory is correct so that
# the .env file is found by pydantic-settings.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
exec "$SCRIPT_DIR/venv/bin/python3" "$SCRIPT_DIR/mcp_server.py"
