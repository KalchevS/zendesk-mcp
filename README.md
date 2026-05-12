# mcp-server-zendesk

An MCP server that gives AI assistants full access to your Zendesk instance — search tickets, manage tags, create tickets, inspect automations, and more.

Works with [Kiro](https://kiro.dev), [Claude Code](https://docs.anthropic.com/en/docs/claude-code), Claude Desktop, Cursor, Windsurf, and any MCP-compatible client.

## What it does

Connect your AI assistant to Zendesk and ask things like:

- "How many unsolved tickets do we have?"
- "Show me all high-priority tickets assigned to john@company.com"
- "Create a ticket for the billing team about the invoice issue"
- "Add an internal note to ticket 4521 saying we're waiting on the vendor"
- "Who solved the most tickets today?"
- "What macros do we have for password reset requests?"
- "List all automations that fire on pending tickets"

## Tools (27)

### Tickets
| Tool | Description |
|------|-------------|
| `count_tickets` | Count tickets matching any search query |
| `search_tickets` | Search tickets with full details and pagination |
| `get_ticket` | Get single ticket with all comments and custom fields |
| `get_ticket_audits` | Get change history (status changes, reassignments) |
| `get_ticket_comments` | Get all comments and internal notes |
| `create_ticket` | Create a new ticket |
| `edit_ticket` | Update ticket fields (status, priority, tags, etc.) |
| `solve_ticket` | Mark a ticket as solved |
| `bulk_solve_tickets_by_type` | Solve all tickets matching a tag |
| `add_comment` | Add a public reply or internal note |
| `add_ticket_tags` | Add tags without removing existing ones |
| `remove_ticket_tags` | Remove specific tags |

### Users & Organizations
| Tool | Description |
|------|-------------|
| `get_user` | Get user details by ID |
| `search_users` | Search users by name or email |
| `get_organization` | Get organization details by ID |
| `search_organizations` | Search organizations by name |

### Views
| Tool | Description |
|------|-------------|
| `get_view` | Get view configuration and conditions |
| `count_view` | Get ticket count for a view |
| `list_view_tickets` | List tickets in a view |
| `list_ticket_fields` | List all ticket fields including custom fields |

### Business Rules
| Tool | Description |
|------|-------------|
| `list_triggers` | List event-based automation rules |
| `get_trigger` | Get full trigger details |
| `search_triggers` | Search triggers by title |
| `list_automations` | List time-based automation rules |
| `list_macros` | List prepared agent responses and actions |

### Groups & Performance
| Tool | Description |
|------|-------------|
| `list_groups` | List agent groups |
| `get_agent_performance_today` | Agent ranking by tickets solved |

## Quick Start

### 1. Install

```bash
git clone https://github.com/labbuilder/zendesk-mcp.git
cd zendesk-mcp
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — choose one authentication mode:

**Option A — API Token (simple, no expiry):**
```env
ZD_SUBDOMAIN=your-company
ZD_EMAIL=agent@company.com
ZD_API_TOKEN=your-token-here
```

**Option B — OAuth Bearer Token (recommended for production):**
```env
ZD_SUBDOMAIN=your-company
ZD_OAUTH_ACCESS_TOKEN=your-access-token
ZD_OAUTH_REFRESH_TOKEN=your-refresh-token
ZD_OAUTH_CLIENT_ID=your-client-id
ZD_OAUTH_CLIENT_SECRET=your-client-secret
```

OAuth tokens are refreshed automatically when they expire (401 → refresh → retry). If only `ZD_OAUTH_ACCESS_TOKEN` is set without refresh credentials, the server works until the token expires.

### 3. Connect to your AI client

See detailed setup for each client below.

---

## Client Setup

### Kiro IDE

**Option A — Stdio (local, recommended for development):**

Create `.kiro/settings/mcp.json` in your workspace root:

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "./venv/bin/python3",
      "args": ["./mcp_server.py"],
      "autoApprove": [
        "count_tickets",
        "search_tickets",
        "get_ticket",
        "get_ticket_comments",
        "get_user",
        "search_users",
        "list_triggers",
        "list_macros",
        "list_groups"
      ]
    }
  }
}
```

Or copy the included example:
```bash
mkdir -p .kiro/settings
cp mcp.json.example .kiro/settings/mcp.json
```

**Option B — SSE (remote Docker server):**

```json
{
  "mcpServers": {
    "zendesk": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

After saving, the server connects automatically. Check the MCP Server panel in Kiro to confirm. If it doesn't connect, use Command Palette → "MCP: Reconnect Server".

### Kiro CLI

Same config file at `~/.kiro/settings/mcp.json` (global) or `.kiro/settings/mcp.json` (workspace):

```bash
cp mcp.json.example ~/.kiro/settings/mcp.json
```

Then start a session:
```bash
kiro
# "How many unsolved tickets do we have?"
# "Show me tickets assigned to john@company.com"
# "Create a ticket about the login issue for customer@example.com"
```

### Claude Code

Add to your Claude Code MCP configuration (`~/.claude/mcp.json` or project-level):

**Stdio (local):**
```json
{
  "mcpServers": {
    "zendesk": {
      "command": "/path/to/mcp-server-zendesk/venv/bin/python3",
      "args": ["/path/to/mcp-server-zendesk/mcp_server.py"]
    }
  }
}
```

**SSE (remote/Docker):**
```json
{
  "mcpServers": {
    "zendesk": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

> **Note for Claude Code stdio**: Use absolute paths since Claude Code may not run from the project directory.

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "zendesk": {
      "command": "/path/to/mcp-server-zendesk/run_mcp.sh",
      "args": []
    }
  }
}
```

The `run_mcp.sh` wrapper auto-detects its directory, so it works regardless of Claude Desktop's working directory.

### Cursor / Windsurf / Other MCP Clients

Any client that supports the MCP protocol works. Use either:
- **Stdio**: point to `./venv/bin/python3` with args `["./mcp_server.py"]`
- **SSE**: connect to `http://localhost:8080/sse` (requires Docker or `python3 mcp_server_http.py` running)

---

## Docker

Run as a remote HTTP/SSE server:

```bash
# With Docker Compose (recommended)
docker compose up -d --build

# Health check
curl http://localhost:8080/health
```

Then connect clients using the SSE URL:
```json
{
  "mcpServers": {
    "zendesk": {
      "url": "http://localhost:8080/sse"
    }
  }
}
```

## Architecture

```
mcp_server.py        — MCP protocol server (stdio transport)
mcp_server_http.py   — HTTP/SSE transport (for Docker/network)
tools.py             — Core ticket tools (async)
tools_extra.py       — User, org, view, trigger, macro, group tools (async)
zendesk_client.py    — Async HTTP client with retry, rate-limit handling, path allowlist
config.py            — Settings from .env via pydantic-settings
```

Key design decisions:
- **Async-first** — all tools use `httpx.AsyncClient` for non-blocking I/O
- **Path allowlisting** — only pre-approved Zendesk API endpoints can be called
- **Rate limit retry** — automatic backoff on 429 and 5xx errors
- **Error masking** — set `MASK_ERRORS=true` in production to hide internal details
- **Lifespan management** — HTTP client properly opened/closed on server start/stop

## Query Syntax

All ticket search queries must include `type:ticket`:

```
type:ticket status<solved                           # All unsolved
type:ticket priority:high status:open               # High priority open
type:ticket assignee:user@company.com               # By assignee
type:ticket created>=2025-01-01 created<2025-02-01  # Date range
type:ticket tags:billing organization:ACME          # Tag + org
type:ticket "exact phrase"                          # Text search
```

## Security

- API credentials stay in `.env` (gitignored)
- Supports both API token and OAuth authentication
- OAuth tokens are refreshed automatically on expiry — no manual intervention
- Path allowlist prevents access to unauthorized Zendesk endpoints
- Write tools require explicit approval (not auto-approved)
- `MASK_ERRORS=true` hides internal error details in production
- Docker Compose reads `.env` at runtime (credentials not baked into image)

## Requirements

- Python 3.10+
- Zendesk account with API access
- One of:
  - API token from Zendesk Admin > Channels > API
  - OAuth access token (from authorization code or client credentials flow)

## License

MIT
# zendesk-mcp
Zendesk MCP Server
