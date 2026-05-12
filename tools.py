"""
Tool implementations for the Zendesk MCP Server.

All functions are async and use the shared httpx.AsyncClient for non-blocking I/O.
Each function corresponds to an MCP tool exposed via the FastMCP decorator pattern
in mcp_server.py.

Tools are grouped into categories:
  - Ticket operations (search, count, get, edit, solve)
  - User & Organization lookups
  - View management
  - Trigger inspection
  - Agent performance analysis

All functions return a Dict[str, Any] — either the successful result payload
or a standardized error dict with 'type', 'message', and 'hint' keys.
"""

from typing import Dict, Any, List, Optional
from collections import Counter
from datetime import datetime

from zendesk_client import client, ZendeskError
from config import settings
import structlog

logger = structlog.get_logger()

# --- Constants for ticket field validation ---
RECOGNISED_FIELDS = {"status", "priority", "subject", "assignee_id", "tags", "custom_fields"}
FORBIDDEN_FIELDS = {"requester_id", "submitter_id"}
VALID_STATUSES = {"open", "pending", "hold", "solved", "closed"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}


# --- Internal helpers ---

async def _get_user_name(user_id: int) -> Optional[str]:
    """Resolve a Zendesk user ID to their display name. Returns None on failure."""
    if not user_id:
        return None
    try:
        result = await client.get(f"/api/v2/users/{user_id}.json")
        return result.get("user", {}).get("name")
    except Exception:
        return None


def _validate_ticket_query(query: str) -> None:
    """Raise ValueError if query doesn't include 'type:ticket'."""
    if "type:ticket" not in query:
        raise ValueError("Query must include 'type:ticket'. Example: type:ticket status<solved")


def _validation_error(message: str, hint: str) -> Dict[str, Any]:
    """Construct a standardized validation error response."""
    return {"error": {"type": "validation_error", "message": message, "hint": hint}}


def _zendesk_error_response(e: ZendeskError) -> Dict[str, Any]:
    """Convert a ZendeskError to a standardized error dict, respecting mask_errors setting."""
    if settings.mask_errors:
        return {"error": {"type": "zendesk_error", "message": "An internal error occurred", "hint": "Contact your administrator"}}
    return {"error": {"type": "zendesk_error", "message": e.message, "hint": e.hint}}


# --- Ticket tools ---

async def count_tickets(query: str) -> Dict[str, Any]:
    """Count tickets matching a Zendesk search query. Returns exact total count."""
    _validate_ticket_query(query)
    try:
        result = await client.get("/api/v2/search/count.json", params={"query": query})
        return {"count": result.get("count", 0), "query": query}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def search_tickets(query: str, page: int = 1, per_page: int = 25, resolve_names: bool = True) -> Dict[str, Any]:
    """Search and list Zendesk tickets with resolved assignee/requester names."""
    _validate_ticket_query(query)
    per_page = min(per_page, settings.tools_max_per_page)
    if page > settings.tools_max_pages:
        return {"error": {"type": "pagination_limit", "message": f"Page {page} exceeds maximum of {settings.tools_max_pages}", "hint": "Refine your query"}}

    try:
        result = await client.get("/api/v2/search.json", params={"query": query, "page": page, "per_page": per_page})
        tickets = []

        # Collect unique user IDs for batch name resolution
        user_ids = set()
        for item in result.get("results", []):
            if item.get("requester_id"):
                user_ids.add(item["requester_id"])
            if item.get("assignee_id"):
                user_ids.add(item["assignee_id"])

        # Resolve user names
        user_names = {}
        if resolve_names and user_ids:
            for uid in user_ids:
                name = await _get_user_name(uid)
                if name:
                    user_names[uid] = name

        for item in result.get("results", []):
            requester_id = item.get("requester_id")
            assignee_id = item.get("assignee_id")
            ticket = {
                "id": item.get("id"),
                "subject": item.get("subject"),
                "status": item.get("status"),
                "priority": item.get("priority"),
                "requester_id": requester_id,
                "assignee_id": assignee_id,
                "organization_id": item.get("organization_id"),
                "tags": item.get("tags", []),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "url": item.get("url"),
            }
            if resolve_names:
                ticket["requester_name"] = user_names.get(requester_id)
                ticket["assignee_name"] = user_names.get(assignee_id)
            tickets.append(ticket)

        total = result.get("count", len(tickets))
        response = {"page": page, "per_page": per_page, "total": total, "returned": len(tickets), "items": tickets}
        if total > (page * per_page):
            response["truncated"] = True
            response["hint"] = "Refine query or use pagination to see more results"
        return response
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def get_ticket(ticket_id: int) -> Dict[str, Any]:
    """Get full details of a single ticket by ID including all comments."""
    try:
        result = await client.get(f"/api/v2/tickets/{ticket_id}.json")
        ticket = result.get("ticket", {})

        requester_id = ticket.get("requester_id")
        assignee_id = ticket.get("assignee_id")

        ticket_data = {
            "id": ticket.get("id"),
            "subject": ticket.get("subject"),
            "description": ticket.get("description"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "type": ticket.get("type"),
            "requester_id": requester_id,
            "requester_name": await _get_user_name(requester_id),
            "assignee_id": assignee_id,
            "assignee_name": await _get_user_name(assignee_id),
            "organization_id": ticket.get("organization_id"),
            "tags": ticket.get("tags", []),
            "custom_fields": ticket.get("custom_fields", []),
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "url": ticket.get("url"),
        }

        # Include comments
        comments_result = await get_ticket_comments(ticket_id, limit=50)
        if "error" not in comments_result:
            ticket_data["comments"] = comments_result.get("comments", [])

        return {"ticket": ticket_data}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def get_ticket_audits(ticket_id: int, limit_events: int = 25) -> Dict[str, Any]:
    """Get audit trail (change history) for a ticket."""
    try:
        result = await client.get(f"/api/v2/tickets/{ticket_id}/audits.json")
        audits = result.get("audits", [])
        events = []
        for audit in audits[:limit_events]:
            for event in audit.get("events", []):
                event_type = event.get("type")
                if event_type == "Change":
                    field = event.get("field_name")
                    events.append({"type": f"{field}_change", "from": event.get("previous_value"), "to": event.get("value"), "at": audit.get("created_at")})
                elif event_type == "Comment":
                    events.append({"type": "comment", "public": event.get("public", False), "body": event.get("body", "")[:200], "at": audit.get("created_at")})
        return {"id": ticket_id, "events": events[:limit_events]}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def get_ticket_comments(ticket_id: int, limit: int = 25) -> Dict[str, Any]:
    """Get all comments and internal notes for a ticket."""
    try:
        result = await client.get(f"/api/v2/tickets/{ticket_id}/comments.json")
        comments_data = result.get("comments", [])
        comments = []
        for comment in comments_data[:limit]:
            comment_obj = {
                "id": comment.get("id"),
                "type": "comment",
                "body": comment.get("body"),
                "public": comment.get("public", False),
                "author_id": comment.get("author_id"),
                "created_at": comment.get("created_at"),
            }
            attachments = comment.get("attachments", [])
            if attachments:
                comment_obj["attachments"] = [
                    {"id": att.get("id"), "file_name": att.get("file_name"), "content_type": att.get("content_type"), "size": att.get("size"), "url": att.get("content_url")}
                    for att in attachments
                ]
            comments.append(comment_obj)
        return {"ticket_id": ticket_id, "comments": comments, "returned": len(comments)}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def edit_ticket(ticket_id: int, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Edit one or more fields on a Zendesk ticket with full input validation."""
    # Validate input
    if not fields or not (set(fields.keys()) & RECOGNISED_FIELDS):
        return _validation_error("fields must contain at least one recognised key", f"Recognised keys: {', '.join(sorted(RECOGNISED_FIELDS))}")
    if set(fields.keys()) & FORBIDDEN_FIELDS:
        return _validation_error("requester_id and submitter_id cannot be changed", "Remove these fields")
    if "status" in fields and fields["status"] not in VALID_STATUSES:
        return _validation_error(f"Invalid status '{fields['status']}'", f"Must be one of: {', '.join(sorted(VALID_STATUSES))}")
    if "priority" in fields and fields["priority"] not in VALID_PRIORITIES:
        return _validation_error(f"Invalid priority '{fields['priority']}'", f"Must be one of: {', '.join(sorted(VALID_PRIORITIES))}")
    if "tags" in fields:
        if not isinstance(fields["tags"], list) or not all(isinstance(t, str) for t in fields["tags"]):
            return _validation_error("tags must be a list of strings", 'Example: ["tag1", "tag2"]')
    if "custom_fields" in fields:
        if not isinstance(fields["custom_fields"], list) or not all(isinstance(cf, dict) and "id" in cf and "value" in cf for cf in fields["custom_fields"]):
            return _validation_error("custom_fields must be [{\"id\": int, \"value\": any}]", 'Example: [{"id": 123, "value": "foo"}]')

    try:
        result = await client.put(f"/api/v2/tickets/{ticket_id}.json", {"ticket": fields})
        ticket = result.get("ticket", {})
        logger.info("edit_ticket", ticket_id=ticket_id, fields_changed=list(fields.keys()), outcome="success")
        return {"ticket": {"id": ticket.get("id"), "subject": ticket.get("subject"), "status": ticket.get("status"), "priority": ticket.get("priority"), "tags": ticket.get("tags", []), "custom_fields": ticket.get("custom_fields", []), "updated_at": ticket.get("updated_at")}}
    except ZendeskError as e:
        logger.info("edit_ticket", ticket_id=ticket_id, outcome="error", error_status=e.status)
        if e.status == 404:
            return {"error": {"type": "not_found", "message": e.message, "hint": "Verify the ticket ID"}}
        if e.status == 403:
            return {"error": {"type": "permission_denied", "message": e.message, "hint": "Authenticated user lacks permission"}}
        return _zendesk_error_response(e)


async def solve_ticket(ticket_id: int) -> Dict[str, Any]:
    """Mark a single Zendesk ticket as solved."""
    try:
        result = await client.put(f"/api/v2/tickets/{ticket_id}.json", {"ticket": {"status": "solved"}})
        ticket = result.get("ticket", {})
        logger.info("solve_ticket", ticket_id=ticket_id, outcome="success")
        return {"ticket": {"id": ticket.get("id"), "subject": ticket.get("subject"), "status": "solved", "updated_at": ticket.get("updated_at")}}
    except ZendeskError as e:
        logger.info("solve_ticket", ticket_id=ticket_id, outcome="error", error_status=e.status)
        if e.status == 404:
            return {"error": {"type": "not_found", "message": e.message, "hint": "Verify the ticket ID"}}
        if e.status == 403:
            return {"error": {"type": "permission_denied", "message": e.message, "hint": "Authenticated user lacks permission"}}
        return _zendesk_error_response(e)


async def get_user(user_id: int) -> Dict[str, Any]:
    """Get full details of a Zendesk user by their numeric ID."""
    try:
        result = await client.get(f"/api/v2/users/{user_id}.json")
        user = result.get("user", {})
        return {"user": {"id": user.get("id"), "name": user.get("name"), "email": user.get("email"), "role": user.get("role"), "organization_id": user.get("organization_id"), "active": user.get("active"), "created_at": user.get("created_at"), "updated_at": user.get("updated_at"), "tags": user.get("tags", []), "url": user.get("url")}}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def bulk_solve_tickets_by_type(ticket_type: str) -> Dict[str, Any]:
    """Bulk solve all open/pending tickets of a specific type assigned to the authenticated user."""
    if not settings.zd_email:
        return {"error": {"type": "configuration_error", "message": "ZD_EMAIL is required for bulk_solve_tickets_by_type", "hint": "Set ZD_EMAIL in .env to identify the authenticated agent"}}
    query = f"type:ticket tags:{ticket_type} assignee:{settings.zd_email} status<solved"
    try:
        result = await client.get("/api/v2/search.json", params={"query": query, "per_page": 100})
    except ZendeskError as e:
        return _zendesk_error_response(e)

    tickets = result.get("results", [])[:100]
    if not tickets:
        return {"ticket_type": ticket_type, "found": 0, "solved": 0, "failed": 0, "results": []}

    solved = 0
    failed = 0
    results = []
    for ticket in tickets:
        tid = ticket["id"]
        try:
            await client.put(f"/api/v2/tickets/{tid}.json", {"ticket": {"status": "solved"}})
            solved += 1
            logger.info("bulk_solve", ticket_id=tid, outcome="solved")
            results.append({"ticket_id": tid, "outcome": "solved", "error": None})
        except ZendeskError as e:
            failed += 1
            logger.info("bulk_solve", ticket_id=tid, outcome="failed", error=e.message)
            results.append({"ticket_id": tid, "outcome": "failed", "error": e.message})

    return {"ticket_type": ticket_type, "found": len(tickets), "solved": solved, "failed": failed, "results": results}


# --- Ticket creation and comment tools ---

async def create_ticket(subject: str, description: str, requester_email: Optional[str] = None, priority: Optional[str] = None, tags: Optional[List[str]] = None, assignee_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Create a new Zendesk ticket.

    Args:
        subject:         Ticket subject line.
        description:     Ticket body/description (first comment).
        requester_email: Email of the requester. If omitted, the authenticated agent is the requester.
        priority:        Optional priority (low/normal/high/urgent).
        tags:            Optional list of tags to apply.
        assignee_id:     Optional agent ID to assign the ticket to.

    Returns:
        {"ticket": {...}} with the created ticket's ID, subject, status, and URL.
    """
    if priority and priority not in VALID_PRIORITIES:
        return _validation_error(f"Invalid priority '{priority}'", f"Must be one of: {', '.join(sorted(VALID_PRIORITIES))}")

    ticket_data: Dict[str, Any] = {
        "subject": subject,
        "comment": {"body": description},
    }
    if requester_email:
        ticket_data["requester"] = {"email": requester_email}
    if priority:
        ticket_data["priority"] = priority
    if tags:
        ticket_data["tags"] = tags
    if assignee_id:
        ticket_data["assignee_id"] = assignee_id

    try:
        result = await client.post("/api/v2/tickets.json", {"ticket": ticket_data})
        ticket = result.get("ticket", {})
        logger.info("create_ticket", ticket_id=ticket.get("id"), outcome="success")
        return {"ticket": {
            "id": ticket.get("id"),
            "subject": ticket.get("subject"),
            "status": ticket.get("status"),
            "priority": ticket.get("priority"),
            "requester_id": ticket.get("requester_id"),
            "assignee_id": ticket.get("assignee_id"),
            "tags": ticket.get("tags", []),
            "created_at": ticket.get("created_at"),
            "url": ticket.get("url"),
        }}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def add_comment(ticket_id: int, body: str, public: bool = True) -> Dict[str, Any]:
    """
    Add a comment (public reply or internal note) to an existing ticket.

    Args:
        ticket_id: The numeric Zendesk ticket ID.
        body:      The comment text.
        public:    If True (default), the comment is a public reply visible to the requester.
                   If False, it's an internal note visible only to agents.

    Returns:
        {"ticket": {...}} with updated ticket info confirming the comment was added.
    """
    if not body or not body.strip():
        return _validation_error("Comment body cannot be empty", "Provide a non-empty body string")

    try:
        result = await client.put(f"/api/v2/tickets/{ticket_id}.json", {
            "ticket": {"comment": {"body": body, "public": public}}
        })
        ticket = result.get("ticket", {})
        logger.info("add_comment", ticket_id=ticket_id, public=public, outcome="success")
        return {"ticket": {
            "id": ticket.get("id"),
            "subject": ticket.get("subject"),
            "status": ticket.get("status"),
            "updated_at": ticket.get("updated_at"),
        }}
    except ZendeskError as e:
        if e.status == 404:
            return {"error": {"type": "not_found", "message": e.message, "hint": "Verify the ticket ID"}}
        return _zendesk_error_response(e)


# --- Tag management tools ---

async def add_ticket_tags(ticket_id: int, tags: List[str]) -> Dict[str, Any]:
    """
    Add tags to a ticket without removing existing ones.

    Uses the PUT /api/v2/tickets/{id}/tags.json endpoint which appends tags.

    Args:
        ticket_id: The numeric Zendesk ticket ID.
        tags:      List of tag strings to add.

    Returns:
        {"tags": [...]} with the complete list of tags now on the ticket.
    """
    if not tags or not all(isinstance(t, str) for t in tags):
        return _validation_error("tags must be a non-empty list of strings", 'Example: ["urgent", "billing"]')

    try:
        result = await client.put(f"/api/v2/tickets/{ticket_id}/tags.json", {"tags": tags})
        logger.info("add_ticket_tags", ticket_id=ticket_id, tags_added=tags, outcome="success")
        return {"ticket_id": ticket_id, "tags": result.get("tags", [])}
    except ZendeskError as e:
        if e.status == 404:
            return {"error": {"type": "not_found", "message": e.message, "hint": "Verify the ticket ID"}}
        return _zendesk_error_response(e)


async def remove_ticket_tags(ticket_id: int, tags: List[str]) -> Dict[str, Any]:
    """
    Remove specific tags from a ticket without affecting other tags.

    Uses DELETE /api/v2/tickets/{id}/tags.json with the tags to remove.

    Args:
        ticket_id: The numeric Zendesk ticket ID.
        tags:      List of tag strings to remove.

    Returns:
        {"tags": [...]} with the remaining tags on the ticket.
    """
    if not tags or not all(isinstance(t, str) for t in tags):
        return _validation_error("tags must be a non-empty list of strings", 'Example: ["spam", "duplicate"]')

    try:
        # Zendesk uses DELETE with a body for tag removal
        self = client
        self._validate_path(f"/api/v2/tickets/{ticket_id}/tags.json")
        response = await self._client.request(
            "DELETE",
            f"/api/v2/tickets/{ticket_id}/tags.json",
            json={"tags": tags},
            headers={"Content-Type": "application/json"},
        )
        if response.status_code == 429:
            raise ZendeskError(429, "Rate limit exceeded", f"Retry after {response.headers.get('Retry-After', '60')} seconds")
        response.raise_for_status()
        result = response.json()
        logger.info("remove_ticket_tags", ticket_id=ticket_id, tags_removed=tags, outcome="success")
        return {"ticket_id": ticket_id, "tags": result.get("tags", [])}
    except ZendeskError as e:
        if e.status == 404:
            return {"error": {"type": "not_found", "message": e.message, "hint": "Verify the ticket ID"}}
        return _zendesk_error_response(e)
    except Exception as e:
        return {"error": {"type": "execution_error", "message": str(e), "hint": "Check ticket ID and tags"}}
