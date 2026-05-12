"""
Additional tool implementations: Users, Organizations, Views, Triggers, Performance.

Separated from tools.py for readability. All functions are async.
"""

from typing import Dict, Any, Optional
from collections import Counter
from datetime import datetime

from zendesk_client import client, ZendeskError
from config import settings
from tools import _get_user_name, _zendesk_error_response, search_tickets, get_user
import structlog

logger = structlog.get_logger()


# --- User & Organization tools ---

async def search_users(query: str, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
    """Search for Zendesk users by name or email."""
    per_page = min(per_page, settings.tools_max_per_page)
    try:
        result = await client.get("/api/v2/users/search.json", params={"query": query, "page": page, "per_page": per_page})
        users = [
            {"id": u.get("id"), "name": u.get("name"), "email": u.get("email"), "role": u.get("role"), "organization_id": u.get("organization_id"), "active": u.get("active")}
            for u in result.get("users", [])
        ]
        return {"page": page, "per_page": per_page, "total": result.get("count", len(users)), "returned": len(users), "users": users}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def get_organization(org_id: int) -> Dict[str, Any]:
    """Get full details of a Zendesk organization by ID."""
    try:
        result = await client.get(f"/api/v2/organizations/{org_id}.json")
        org = result.get("organization", {})
        return {"organization": {"id": org.get("id"), "name": org.get("name"), "details": org.get("details"), "notes": org.get("notes"), "tags": org.get("tags", []), "created_at": org.get("created_at"), "updated_at": org.get("updated_at"), "url": org.get("url")}}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def search_organizations(query: str, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
    """Search for Zendesk organizations by name."""
    per_page = min(per_page, settings.tools_max_per_page)
    try:
        result = await client.get("/api/v2/organizations/search.json", params={"query": query, "page": page, "per_page": per_page})
        orgs = [{"id": o.get("id"), "name": o.get("name"), "details": o.get("details"), "tags": o.get("tags", [])} for o in result.get("organizations", [])]
        return {"page": page, "per_page": per_page, "total": result.get("count", len(orgs)), "returned": len(orgs), "organizations": orgs}
    except ZendeskError as e:
        return _zendesk_error_response(e)


# --- View tools ---

async def get_view(view_id: int) -> Dict[str, Any]:
    """Get configuration details of a Zendesk view by ID."""
    try:
        result = await client.get(f"/api/v2/views/{view_id}.json")
        view = result.get("view", {})
        return {"view": {"id": view.get("id"), "title": view.get("title"), "active": view.get("active"), "description": view.get("description"), "conditions": view.get("conditions"), "execution": view.get("execution"), "url": view.get("url")}}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def count_view(view_id: int) -> Dict[str, Any]:
    """Get the current ticket count for a specific Zendesk view."""
    try:
        result = await client.get(f"/api/v2/views/{view_id}/count.json")
        return {"view_id": view_id, "count": result.get("view_count", {}).get("value", 0)}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def list_view_tickets(view_id: int, page: int = 1, per_page: int = 25) -> Dict[str, Any]:
    """List tickets in a specific Zendesk view with pagination."""
    per_page = min(per_page, settings.tools_max_per_page)
    try:
        result = await client.get(f"/api/v2/views/{view_id}/tickets.json", params={"page": page, "per_page": per_page})
        tickets = [
            {"id": t.get("id"), "subject": t.get("subject"), "status": t.get("status"), "priority": t.get("priority"), "created_at": t.get("created_at"), "updated_at": t.get("updated_at")}
            for t in result.get("tickets", [])
        ]
        return {"view_id": view_id, "page": page, "per_page": per_page, "returned": len(tickets), "tickets": tickets}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def list_ticket_fields() -> Dict[str, Any]:
    """List all ticket fields including custom fields."""
    try:
        result = await client.get("/api/v2/ticket_fields.json")
        fields = [
            {"id": f.get("id"), "type": f.get("type"), "title": f.get("title"), "description": f.get("description"), "active": f.get("active"), "required": f.get("required"), "custom_field_options": f.get("custom_field_options", [])}
            for f in result.get("ticket_fields", [])
        ]
        return {"ticket_fields": fields, "count": len(fields)}
    except ZendeskError as e:
        return _zendesk_error_response(e)


# --- Trigger tools ---

async def list_triggers(active_only: bool = False, page: int = 1, per_page: int = 100) -> Dict[str, Any]:
    """List all Zendesk triggers (business automation rules)."""
    path = "/api/v2/triggers/active.json" if active_only else "/api/v2/triggers.json"
    try:
        result = await client.get(path, params={"page": page, "per_page": per_page})
        triggers = [
            {"id": t.get("id"), "title": t.get("title"), "active": t.get("active"), "description": t.get("description"), "position": t.get("position"), "conditions": t.get("conditions"), "actions": t.get("actions"), "category_id": t.get("category_id"), "created_at": t.get("created_at"), "updated_at": t.get("updated_at")}
            for t in result.get("triggers", [])
        ]
        return {"page": page, "per_page": per_page, "count": result.get("count", len(triggers)), "triggers": triggers, "next_page": result.get("next_page")}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def get_trigger(trigger_id: int) -> Dict[str, Any]:
    """Get full details of a single Zendesk trigger by ID."""
    try:
        result = await client.get(f"/api/v2/triggers/{trigger_id}.json")
        t = result.get("trigger", {})
        return {"trigger": {"id": t.get("id"), "title": t.get("title"), "active": t.get("active"), "description": t.get("description"), "position": t.get("position"), "conditions": t.get("conditions"), "actions": t.get("actions"), "category_id": t.get("category_id"), "created_at": t.get("created_at"), "updated_at": t.get("updated_at")}}
    except ZendeskError as e:
        return _zendesk_error_response(e)


async def search_triggers(query: str, active: Optional[bool] = None) -> Dict[str, Any]:
    """Search Zendesk triggers by title."""
    params: Dict[str, Any] = {"query": query}
    if active is not None:
        params["active"] = active
    try:
        result = await client.get("/api/v2/triggers/search.json", params=params)
        triggers = [
            {"id": t.get("id"), "title": t.get("title"), "active": t.get("active"), "description": t.get("description"), "position": t.get("position"), "conditions": t.get("conditions"), "actions": t.get("actions")}
            for t in result.get("triggers", [])
        ]
        return {"count": result.get("count", len(triggers)), "triggers": triggers}
    except ZendeskError as e:
        return _zendesk_error_response(e)


# --- Performance tools ---

async def get_agent_performance_today(date: Optional[str] = None) -> Dict[str, Any]:
    """Analyze agent performance by counting tickets solved on a given date."""
    if not date:
        date = datetime.now().strftime('%Y-%m-%d')

    query = f'type:ticket status:solved updated>={date}'
    try:
        # Fetch up to 250 tickets across 5 pages
        all_tickets = []
        page = 1
        while page <= 5:
            result = await search_tickets(query, page=page, per_page=50)
            if "items" not in result or not result["items"]:
                break
            all_tickets.extend(result["items"])
            if not result.get("truncated", False):
                break
            page += 1

        # Count by assignee
        assignee_counts: Counter = Counter()
        assignee_details: Dict[int, list] = {}
        for ticket in all_tickets:
            assignee_id = ticket.get("assignee_id")
            if assignee_id:
                assignee_counts[assignee_id] += 1
                if assignee_id not in assignee_details:
                    assignee_details[assignee_id] = []
                subject = (ticket.get("subject") or "")[:50]
                assignee_details[assignee_id].append({"id": ticket["id"], "subject": subject, "tags": ticket.get("tags", [])})

        # Resolve agent names and build ranked response
        agents = []
        for assignee_id, count in assignee_counts.most_common():
            user_result = await get_user(assignee_id)
            user_data = user_result.get("user", {}) if "error" not in user_result else {}
            agents.append({
                "agent_id": assignee_id,
                "tickets_solved": count,
                "name": user_data.get("name", "Unknown"),
                "email": user_data.get("email", "Unknown"),
                "role": user_data.get("role", "Unknown"),
                "sample_tickets": assignee_details[assignee_id][:3],
            })

        unassigned = sum(1 for t in all_tickets if not t.get("assignee_id"))
        return {
            "date": date,
            "total_tickets_analyzed": len(all_tickets),
            "total_agents": len(assignee_counts),
            "unassigned_tickets": unassigned,
            "agents": agents,
            "data_completeness": "partial" if len(all_tickets) >= 250 else "complete",
        }
    except Exception as e:
        logger.error("agent_performance_error", error=str(e))
        return {"error": {"type": "performance_analysis_error", "message": str(e), "hint": "Check date format (YYYY-MM-DD)"}}


# --- Group tools ---

async def list_groups(page: int = 1, per_page: int = 100) -> Dict[str, Any]:
    """
    List all agent groups in the Zendesk instance.

    Groups organize agents into teams (e.g. "Billing", "Technical Support").
    Useful for understanding routing and assignment options.

    Args:
        page:     Page number for pagination (default: 1).
        per_page: Results per page (default: 100).

    Returns:
        Dict with: page, per_page, count, groups[].
        Each group includes: id, name, description, created_at, updated_at.
    """
    try:
        result = await client.get("/api/v2/groups.json", params={"page": page, "per_page": per_page})
        groups = [
            {"id": g.get("id"), "name": g.get("name"), "description": g.get("description"), "default": g.get("default"), "created_at": g.get("created_at"), "updated_at": g.get("updated_at")}
            for g in result.get("groups", [])
        ]
        return {"page": page, "per_page": per_page, "count": result.get("count", len(groups)), "groups": groups}
    except ZendeskError as e:
        return _zendesk_error_response(e)


# --- Automation tools ---

async def list_automations(active_only: bool = False, page: int = 1, per_page: int = 100) -> Dict[str, Any]:
    """
    List all Zendesk automations (time-based business rules).

    Automations are similar to triggers but fire based on time conditions
    (e.g. "4 hours after ticket created" or "ticket pending for 48 hours").
    They run hourly and perform actions like notifications, escalations, or status changes.

    Args:
        active_only: If True, only return active automations.
        page:        Page number for pagination (default: 1).
        per_page:    Results per page (default: 100).

    Returns:
        Dict with: page, per_page, count, automations[].
        Each automation includes: id, title, active, conditions, actions, position, created_at, updated_at.
    """
    path = "/api/v2/automations.json"
    params: Dict[str, Any] = {"page": page, "per_page": per_page}
    if active_only:
        params["active"] = True
    try:
        result = await client.get(path, params=params)
        automations = [
            {
                "id": a.get("id"),
                "title": a.get("title"),
                "active": a.get("active"),
                "position": a.get("position"),
                "conditions": a.get("conditions"),
                "actions": a.get("actions"),
                "created_at": a.get("created_at"),
                "updated_at": a.get("updated_at"),
            }
            for a in result.get("automations", [])
        ]
        return {"page": page, "per_page": per_page, "count": result.get("count", len(automations)), "automations": automations, "next_page": result.get("next_page")}
    except ZendeskError as e:
        return _zendesk_error_response(e)


# --- Macro tools ---

async def list_macros(active_only: bool = False, page: int = 1, per_page: int = 100) -> Dict[str, Any]:
    """
    List all Zendesk macros (prepared responses and actions).

    Macros are pre-defined sets of actions that agents can apply to tickets
    with one click — e.g. a canned response + status change + tag addition.
    Useful for understanding available shortcuts and suggesting them to agents.

    Args:
        active_only: If True, only return active macros.
        page:        Page number for pagination (default: 1).
        per_page:    Results per page (default: 100).

    Returns:
        Dict with: page, per_page, count, macros[].
        Each macro includes: id, title, active, description, actions, restriction, created_at, updated_at.
    """
    path = "/api/v2/macros/active.json" if active_only else "/api/v2/macros.json"
    try:
        result = await client.get(path, params={"page": page, "per_page": per_page})
        macros = [
            {
                "id": m.get("id"),
                "title": m.get("title"),
                "active": m.get("active"),
                "description": m.get("description"),
                "actions": m.get("actions"),
                "restriction": m.get("restriction"),
                "created_at": m.get("created_at"),
                "updated_at": m.get("updated_at"),
            }
            for m in result.get("macros", [])
        ]
        return {"page": page, "per_page": per_page, "count": result.get("count", len(macros)), "macros": macros, "next_page": result.get("next_page")}
    except ZendeskError as e:
        return _zendesk_error_response(e)
