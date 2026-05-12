#!/usr/bin/env python3
"""
Zendesk MCP Server — Main entry point.

Uses the MCP Python SDK's low-level Server class with async tool handlers.
Includes proper lifespan management for the HTTP client.

The server exposes 27 tools across these categories:
  - Ticket operations (count, search, get, audits, comments, edit, solve, bulk solve)
  - User & Organization lookups
  - View management
  - Trigger inspection (business rules)
  - Agent performance analysis

Transports:
  - stdio: Run directly as a subprocess (default)
  - HTTP/SSE: See mcp_server_http.py for network deployment
"""

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.types import Tool, TextContent
import mcp.server.stdio
import structlog

from zendesk_client import client
from tools import (
    count_tickets, search_tickets, get_ticket, get_ticket_audits,
    get_ticket_comments, edit_ticket, solve_ticket, bulk_solve_tickets_by_type,
    get_user, create_ticket, add_comment, add_ticket_tags, remove_ticket_tags,
)
from tools_extra import (
    search_users, get_organization, search_organizations,
    get_view, count_view, list_view_tickets, list_ticket_fields,
    list_triggers, get_trigger, search_triggers,
    get_agent_performance_today, list_groups,
    list_automations, list_macros,
)

logger = structlog.get_logger()

# Initialize the MCP server
app = Server("zendesk")

# --- Tool Definitions ---
# Descriptions are intentionally detailed to help AI assistants choose the right
# tool and construct valid queries without trial-and-error.

TOOLS = [
    Tool(
        name="count_tickets",
        description="Count ANY Zendesk tickets using flexible search queries. Returns EXACT total count. Query MUST include 'type:ticket'. Examples: 'type:ticket status<solved', 'type:ticket assignee:agent@example.com status:solved updated>=2025-01-10'.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string", "description": "Zendesk search query. Must include 'type:ticket'."}}, "required": ["query"]},
    ),
    Tool(
        name="search_tickets",
        description="Search and list Zendesk tickets. Returns full details with assignee/requester names, status, priority, tags. Max 100 per page. Query MUST include 'type:ticket'.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string", "description": "Zendesk search query. Must include 'type:ticket'."}, "page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}}, "required": ["query"]},
    ),
    Tool(
        name="get_ticket",
        description="Get full details of a single ticket by ID including all comments, custom fields, and resolved names.",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer", "description": "Zendesk ticket ID"}}, "required": ["ticket_id"]},
    ),
    Tool(
        name="get_ticket_audits",
        description="Get audit history for a ticket (status changes, reassignments, comments).",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "limit_events": {"type": "integer", "default": 25}}, "required": ["ticket_id"]},
    ),
    Tool(
        name="get_ticket_comments",
        description="Get all comments and private notes for a ticket.",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "limit": {"type": "integer", "default": 25}}, "required": ["ticket_id"]},
    ),
    Tool(
        name="get_user",
        description="Get details of a Zendesk user by ID. Returns name, email, role, organization, tags.",
        inputSchema={"type": "object", "properties": {"user_id": {"type": "integer"}}, "required": ["user_id"]},
    ),
    Tool(
        name="search_users",
        description="Search for Zendesk users by name or email.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}}, "required": ["query"]},
    ),
    Tool(
        name="get_agent_performance_today",
        description="Get agent performance ranking by tickets solved on a date. Returns names, counts, and sample tickets.",
        inputSchema={"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD format. Defaults to today."}}, "required": []},
    ),
    Tool(
        name="get_organization",
        description="Get details of a Zendesk organization by ID.",
        inputSchema={"type": "object", "properties": {"org_id": {"type": "integer"}}, "required": ["org_id"]},
    ),
    Tool(
        name="search_organizations",
        description="Search for Zendesk organizations by name.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}}, "required": ["query"]},
    ),
    Tool(
        name="get_view",
        description="Get details of a Zendesk view by ID (title, conditions, execution settings).",
        inputSchema={"type": "object", "properties": {"view_id": {"type": "integer"}}, "required": ["view_id"]},
    ),
    Tool(
        name="count_view",
        description="Get ticket count for a specific Zendesk view.",
        inputSchema={"type": "object", "properties": {"view_id": {"type": "integer"}}, "required": ["view_id"]},
    ),
    Tool(
        name="list_view_tickets",
        description="List tickets in a specific Zendesk view with pagination.",
        inputSchema={"type": "object", "properties": {"view_id": {"type": "integer"}, "page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25}}, "required": ["view_id"]},
    ),
    Tool(
        name="list_ticket_fields",
        description="List all ticket fields including custom fields with their types and options.",
        inputSchema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="edit_ticket",
        description="Edit fields on a Zendesk ticket. Supports: status, priority, subject, assignee_id, tags, custom_fields.",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "fields": {"type": "object", "description": "Fields to update (status, priority, subject, assignee_id, tags, custom_fields)"}}, "required": ["ticket_id", "fields"]},
    ),
    Tool(
        name="solve_ticket",
        description="Solve a single Zendesk ticket by ID.",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}}, "required": ["ticket_id"]},
    ),
    Tool(
        name="bulk_solve_tickets_by_type",
        description="Bulk solve all open/pending tickets of a specific type (by tag) assigned to the authenticated user. Max 100 per operation.",
        inputSchema={"type": "object", "properties": {"ticket_type": {"type": "string", "description": "Tag name identifying the ticket type"}}, "required": ["ticket_type"]},
    ),
    Tool(
        name="create_ticket",
        description="Create a new Zendesk ticket with subject, description, optional requester email, priority, tags, and assignee.",
        inputSchema={"type": "object", "properties": {"subject": {"type": "string", "description": "Ticket subject line"}, "description": {"type": "string", "description": "Ticket body/first comment"}, "requester_email": {"type": "string", "description": "Requester email. Omit to use authenticated agent."}, "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]}, "tags": {"type": "array", "items": {"type": "string"}}, "assignee_id": {"type": "integer", "description": "Agent ID to assign to"}}, "required": ["subject", "description"]},
    ),
    Tool(
        name="add_comment",
        description="Add a public reply or internal note to an existing ticket.",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "body": {"type": "string", "description": "Comment text"}, "public": {"type": "boolean", "description": "True for public reply (default), false for internal note", "default": True}}, "required": ["ticket_id", "body"]},
    ),
    Tool(
        name="add_ticket_tags",
        description="Add tags to a ticket without removing existing ones. Appends to current tags.",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to add"}}, "required": ["ticket_id", "tags"]},
    ),
    Tool(
        name="remove_ticket_tags",
        description="Remove specific tags from a ticket without affecting other tags.",
        inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to remove"}}, "required": ["ticket_id", "tags"]},
    ),
    Tool(
        name="list_groups",
        description="List all agent groups in the Zendesk instance. Groups organize agents into teams for routing and assignment.",
        inputSchema={"type": "object", "properties": {"page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}}, "required": []},
    ),
    Tool(
        name="list_automations",
        description="List all Zendesk automations (time-based business rules). Automations fire based on time conditions (e.g. ticket pending for 48 hours) and run hourly.",
        inputSchema={"type": "object", "properties": {"active_only": {"type": "boolean", "default": False}, "page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}}, "required": []},
    ),
    Tool(
        name="list_macros",
        description="List all Zendesk macros (prepared responses and actions agents can apply to tickets with one click). Includes canned responses, status changes, and tag additions.",
        inputSchema={"type": "object", "properties": {"active_only": {"type": "boolean", "default": False}, "page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}}, "required": []},
    ),
    Tool(
        name="list_triggers",
        description="List all Zendesk triggers (business rules) with conditions and actions.",
        inputSchema={"type": "object", "properties": {"active_only": {"type": "boolean", "default": False}, "page": {"type": "integer", "minimum": 1, "default": 1}, "per_page": {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}}, "required": []},
    ),
    Tool(
        name="get_trigger",
        description="Get full details of a single Zendesk trigger by ID.",
        inputSchema={"type": "object", "properties": {"trigger_id": {"type": "integer"}}, "required": ["trigger_id"]},
    ),
    Tool(
        name="search_triggers",
        description="Search Zendesk triggers by title.",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}, "active": {"type": "boolean", "description": "Filter by active/inactive. Omit for all."}}, "required": ["query"]},
    ),
]

# --- Tool dispatch map (replaces if/elif chain) ---
TOOL_HANDLERS = {
    "count_tickets": lambda args: count_tickets(args["query"]),
    "search_tickets": lambda args: search_tickets(args["query"], args.get("page", 1), args.get("per_page", 25)),
    "get_ticket": lambda args: get_ticket(args["ticket_id"]),
    "get_ticket_audits": lambda args: get_ticket_audits(args["ticket_id"], args.get("limit_events", 25)),
    "get_ticket_comments": lambda args: get_ticket_comments(args["ticket_id"], args.get("limit", 25)),
    "get_user": lambda args: get_user(args["user_id"]),
    "search_users": lambda args: search_users(args["query"], args.get("page", 1), args.get("per_page", 25)),
    "get_agent_performance_today": lambda args: get_agent_performance_today(args.get("date")),
    "get_organization": lambda args: get_organization(args["org_id"]),
    "search_organizations": lambda args: search_organizations(args["query"], args.get("page", 1), args.get("per_page", 25)),
    "get_view": lambda args: get_view(args["view_id"]),
    "count_view": lambda args: count_view(args["view_id"]),
    "list_view_tickets": lambda args: list_view_tickets(args["view_id"], args.get("page", 1), args.get("per_page", 25)),
    "list_ticket_fields": lambda args: list_ticket_fields(),
    "edit_ticket": lambda args: edit_ticket(args["ticket_id"], args["fields"]),
    "solve_ticket": lambda args: solve_ticket(args["ticket_id"]),
    "bulk_solve_tickets_by_type": lambda args: bulk_solve_tickets_by_type(args["ticket_type"]),
    "create_ticket": lambda args: create_ticket(args["subject"], args["description"], args.get("requester_email"), args.get("priority"), args.get("tags"), args.get("assignee_id")),
    "add_comment": lambda args: add_comment(args["ticket_id"], args["body"], args.get("public", True)),
    "add_ticket_tags": lambda args: add_ticket_tags(args["ticket_id"], args["tags"]),
    "remove_ticket_tags": lambda args: remove_ticket_tags(args["ticket_id"], args["tags"]),
    "list_groups": lambda args: list_groups(args.get("page", 1), args.get("per_page", 100)),
    "list_automations": lambda args: list_automations(args.get("active_only", False), args.get("page", 1), args.get("per_page", 100)),
    "list_macros": lambda args: list_macros(args.get("active_only", False), args.get("page", 1), args.get("per_page", 100)),
    "list_triggers": lambda args: list_triggers(args.get("active_only", False), args.get("page", 1), args.get("per_page", 100)),
    "get_trigger": lambda args: get_trigger(args["trigger_id"]),
    "search_triggers": lambda args: search_triggers(args["query"], args.get("active")),
}


@app.list_tools()
async def handle_list_tools() -> list[Tool]:
    """MCP protocol handler: returns available tools to the client."""
    return TOOLS


@app.call_tool()
async def handle_call_tool(name: str, arguments: Any) -> list[TextContent]:
    """
    MCP protocol handler: dispatches tool calls via the TOOL_HANDLERS map.

    All tool functions are async, so we await the handler coroutine.
    Exceptions are caught and returned as structured JSON errors.
    """
    try:
        handler = TOOL_HANDLERS.get(name)
        if not handler:
            result = {"error": {"type": "unknown_tool", "message": f"Unknown tool: {name}"}}
        else:
            result = await handler(arguments)
        return [TextContent(type="text", text=json.dumps(result, indent=2))]
    except Exception as e:
        logger.error("tool_error", tool=name, error=str(e))
        error_result = {"error": {"type": "execution_error", "message": str(e), "hint": "Check tool parameters and try again"}}
        return [TextContent(type="text", text=json.dumps(error_result, indent=2))]


async def run_stdio():
    """Run the MCP server over stdio with proper lifespan management."""
    # Open the async HTTP client before serving
    await client.open()
    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        # Ensure the HTTP client is closed on shutdown
        await client.close()


def main():
    """Entry point for the MCP server (stdio transport)."""
    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
