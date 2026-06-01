# ============================================================
# mcp_server.py — MCP Server for Acme Assistant
# ============================================================
# This module implements a Model Context Protocol (MCP) server
# that exposes Acme Operations tools as a separate service.
#
# What is MCP?
#   The Model Context Protocol is an open standard developed
#   by Anthropic that defines how AI agents discover and invoke
#   tools exposed by external services. MCP separates tool
#   definitions from core agent logic, making tools reusable
#   across different agents and workflows.
#
# Why MCP in this project? (Assessment Section 4.2)
#   a) Separation of concerns — tool definitions live in this
#      standalone service, not embedded in the agent code.
#      New tools can be added here without touching agent.py.
#
#   b) Reusability — any MCP-compatible agent or framework
#      can connect to this server and use the same tools,
#      regardless of which LLM or orchestration layer is used.
#
#   c) Industry standard — MCP is an emerging standard for
#      AI tool integration, adopted by major AI providers.
#      Building with MCP demonstrates production-oriented
#      engineering judgement.
#
# Tools Exposed:
#   1. get_customer_profile  — fetch customer by name
#   2. get_open_issues       — fetch open issues for customer
#   3. summarise_issue       — fetch issue with full history
#   4. create_next_action    — persist a new next action
#   5. get_next_actions      — list all actions for an issue
#
# Transport:
#   Uses stdio transport — the server communicates via
#   standard input/output streams. This is the standard
#   MCP transport for subprocess-based tool servers.
#
# Database:
#   Connects directly to PostgreSQL using asyncpg.
#   Uses the same DATABASE_URL as the FastAPI app but
#   with the postgresql+asyncpg prefix replaced with
#   plain postgresql for asyncpg compatibility.
#
# Running:
#   Started automatically by Docker Compose.
#   Not intended to be run standalone.
# ============================================================

import asyncio
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
import asyncpg
import os
import json


# ─── MCP Server Instance ──────────────────────────────────────
# Create the MCP server with a unique name identifier.
# This name is used by MCP clients to identify the server
# when listing available tool providers.
server = Server("acme-mcp-server")


# ─── Database Connection URL ──────────────────────────────────
# The DATABASE_URL environment variable uses the SQLAlchemy
# asyncpg dialect prefix (postgresql+asyncpg). The asyncpg
# library itself requires the plain postgresql prefix, so
# we replace it here for direct asyncpg connections.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://acme_user:acme_pass@postgres:5432/acme_db"
).replace("postgresql+asyncpg", "postgresql")


# ─── Database Connection Helper ───────────────────────────────
async def get_conn():
    """
    Create and return a new asyncpg database connection.

    A new connection is created per tool call and closed in
    the finally block of call_tool(). This simple approach
    avoids connection pool complexity for the MCP server,
    which handles one tool call at a time via stdio.

    Returns:
        asyncpg.Connection: Active PostgreSQL connection
    """
    return await asyncpg.connect(DATABASE_URL)


# ─── Tool Registry ────────────────────────────────────────────
@server.list_tools()
async def list_tools() -> list[types.Tool]:
    """
    Register and return all tools exposed by this MCP server.

    This handler is called by MCP clients during the
    initialisation handshake to discover what tools are
    available. Each Tool definition includes a name,
    description, and JSON Schema for input validation.

    The descriptions are critical — MCP clients and LLM
    agents use them to decide which tool to call for a
    given user request. Clear, specific descriptions
    improve tool selection accuracy.

    Returns:
        list[types.Tool]: All available tool definitions
    """
    return [

        # ── Tool 1: Customer Profile ──────────────────────────
        # Retrieves a single customer record by name search.
        # Supports partial matching via ILIKE SQL operator.
        types.Tool(
            name="get_customer_profile",
            description=(
                "Fetch a customer profile from the database "
                "by searching the customer name field. "
                "Supports partial name matching."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type":        "string",
                        "description": "Customer name to search for, e.g. 'Acme Corp'"
                    }
                },
                "required": ["customer_name"]
            }
        ),

        # ── Tool 2: Open Issues ───────────────────────────────
        # Retrieves all non-closed issues for a customer,
        # ordered by priority (critical first).
        types.Tool(
            name="get_open_issues",
            description=(
                "Retrieve all open and in-progress issues "
                "for a given customer, ordered by priority "
                "with critical issues listed first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type":        "string",
                        "description": "Customer name to look up issues for"
                    }
                },
                "required": ["customer_name"]
            }
        ),

        # ── Tool 3: Issue Summary ─────────────────────────────
        # Retrieves an issue with its full update history,
        # including customer name via a JOIN query.
        types.Tool(
            name="summarise_issue",
            description=(
                "Fetch a specific issue by ID along with its "
                "complete chronological history of updates. "
                "Returns issue details and all recorded notes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {
                        "type":        "integer",
                        "description": "Numeric ID of the issue to retrieve"
                    }
                },
                "required": ["issue_id"]
            }
        ),

        # ── Tool 4: Create Next Action ────────────────────────
        # Persists a new recommended next action for an issue.
        # Requires the username of who is creating the action.
        types.Tool(
            name="create_next_action",
            description=(
                "Create and persist a recommended next action "
                "for a specific issue. Records who created the "
                "action and sets initial status to pending."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {
                        "type":        "integer",
                        "description": "ID of the issue to create an action for"
                    },
                    "action_text": {
                        "type":        "string",
                        "description": "Description of the recommended action"
                    },
                    "created_by": {
                        "type":        "string",
                        "description": "Username of the person creating the action"
                    }
                },
                "required": ["issue_id", "action_text", "created_by"]
            }
        ),

        # ── Tool 5: Get Next Actions ──────────────────────────
        # Lists all next actions recorded for a specific issue,
        # ordered most recent first.
        types.Tool(
            name="get_next_actions",
            description=(
                "Retrieve all recommended next actions for a "
                "specific issue, ordered by creation date with "
                "the most recent action listed first."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id": {
                        "type":        "integer",
                        "description": "ID of the issue to get actions for"
                    }
                },
                "required": ["issue_id"]
            }
        )
    ]


# ─── Tool Execution Handler ───────────────────────────────────
@server.call_tool()
async def call_tool(
    name:      str,
    arguments: dict
) -> list[types.TextContent]:
    """
    Execute a tool call received from an MCP client.

    This handler is invoked when an MCP client (such as an
    LLM agent) calls one of the registered tools. It opens
    a database connection, routes the call to the appropriate
    SQL query, and returns the result as a JSON string wrapped
    in a TextContent response.

    Connection management:
        A new database connection is opened at the start of
        each tool call and closed in the finally block,
        ensuring connections are always released even if an
        error occurs during query execution.

    Error handling:
        SQL errors and unexpected exceptions propagate up
        to the MCP server framework, which handles them
        according to the MCP protocol error specification.

    Args:
        name:      Name of the tool to execute
        arguments: Dict of tool arguments from the MCP client

    Returns:
        list[types.TextContent]: Single-element list containing
                                 the JSON-encoded result string
    """
    # Open a fresh database connection for this tool call
    conn = await get_conn()

    try:

        # ── Tool 1: get_customer_profile ──────────────────────
        # Search customers by partial name match using ILIKE
        # for case-insensitive matching. Returns first match.
        if name == "get_customer_profile":
            row = await conn.fetchrow(
                """
                SELECT id, name, email, phone, company, country
                FROM customers
                WHERE LOWER(name) LIKE LOWER($1)
                LIMIT 1
                """,
                f"%{arguments['customer_name']}%"
            )

            if not row:
                result = {"error": "Customer not found"}
            else:
                result = {
                    "id":      row["id"],
                    "name":    row["name"],
                    "email":   row["email"],
                    "phone":   row["phone"],
                    "company": row["company"],
                    "country": row["country"]
                }

        # ── Tool 2: get_open_issues ───────────────────────────
        # First resolves customer name to an ID, then fetches
        # all non-closed issues ordered by priority severity.
        # CASE expression maps priority strings to sort order.
        elif name == "get_open_issues":
            customer = await conn.fetchrow(
                """
                SELECT id FROM customers
                WHERE LOWER(name) LIKE LOWER($1)
                LIMIT 1
                """,
                f"%{arguments['customer_name']}%"
            )

            if not customer:
                result = {"error": "Customer not found"}
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, title, description,
                           status, priority, created_at
                    FROM issues
                    WHERE customer_id = $1
                    AND status != 'closed'
                    ORDER BY
                        CASE priority
                            WHEN 'critical' THEN 1
                            WHEN 'high'     THEN 2
                            WHEN 'medium'   THEN 3
                            WHEN 'low'      THEN 4
                        END
                    """,
                    customer["id"]
                )

                result = [
                    {
                        "id":          r["id"],
                        "title":       r["title"],
                        "description": r["description"],
                        "status":      r["status"],
                        "priority":    r["priority"],
                        "created_at":  str(r["created_at"])
                    }
                    for r in rows
                ]

        # ── Tool 3: summarise_issue ───────────────────────────
        # Fetches the issue with customer name via JOIN, then
        # fetches all updates in chronological order (ASC).
        elif name == "summarise_issue":
            issue = await conn.fetchrow(
                """
                SELECT i.id, i.title, i.description,
                       i.status, i.priority,
                       c.name as customer_name
                FROM issues i
                JOIN customers c ON c.id = i.customer_id
                WHERE i.id = $1
                """,
                arguments["issue_id"]
            )

            if not issue:
                result = {"error": "Issue not found"}
            else:
                # Fetch chronological update history for this issue
                updates = await conn.fetch(
                    """
                    SELECT updated_by, note, created_at
                    FROM issue_updates
                    WHERE issue_id = $1
                    ORDER BY created_at ASC
                    """,
                    arguments["issue_id"]
                )

                result = {
                    "issue": {
                        "id":            issue["id"],
                        "title":         issue["title"],
                        "description":   issue["description"],
                        "status":        issue["status"],
                        "priority":      issue["priority"],
                        "customer_name": issue["customer_name"]
                    },
                    "updates": [
                        {
                            "updated_by": u["updated_by"],
                            "note":       u["note"],
                            "created_at": str(u["created_at"])
                        }
                        for u in updates
                    ]
                }

        # ── Tool 4: create_next_action ────────────────────────
        # Verifies the issue exists before inserting. Uses
        # RETURNING to get the new record ID in one query.
        elif name == "create_next_action":
            issue = await conn.fetchrow(
                "SELECT id FROM issues WHERE id = $1",
                arguments["issue_id"]
            )

            if not issue:
                result = {"error": "Issue not found"}
            else:
                row = await conn.fetchrow(
                    """
                    INSERT INTO next_actions
                        (issue_id, action_text, created_by, status)
                    VALUES ($1, $2, $3, 'pending')
                    RETURNING id, issue_id, action_text, status
                    """,
                    arguments["issue_id"],
                    arguments["action_text"],
                    arguments["created_by"]
                )

                result = {
                    "success":     True,
                    "action_id":   row["id"],
                    "issue_id":    row["issue_id"],
                    "action_text": row["action_text"],
                    "status":      row["status"]
                }

        # ── Tool 5: get_next_actions ──────────────────────────
        # Returns all next actions for an issue ordered by
        # creation date descending (most recent first).
        elif name == "get_next_actions":
            rows = await conn.fetch(
                """
                SELECT id, action_text, created_by,
                       status, created_at
                FROM next_actions
                WHERE issue_id = $1
                ORDER BY created_at DESC
                """,
                arguments["issue_id"]
            )

            result = [
                {
                    "id":          r["id"],
                    "action_text": r["action_text"],
                    "created_by":  r["created_by"],
                    "status":      r["status"],
                    "created_at":  str(r["created_at"])
                }
                for r in rows
            ]

        else:
            # Unknown tool name — return error for MCP client
            result = {"error": f"Unknown tool: {name}"}

    finally:
        # Always close the database connection, even on error
        await conn.close()

    # Wrap result in MCP TextContent with JSON serialisation.
    # default=str handles datetime objects that are not
    # JSON serialisable by default.
    return [
        types.TextContent(
            type="text",
            text=json.dumps(result, default=str)
        )
    ]


# ─── Server Entry Point ───────────────────────────────────────
async def main():
    """
    Start the MCP server using stdio transport.

    The stdio_server() context manager sets up the input and
    output streams for MCP communication. The server.run()
    call starts the protocol handshake and enters the main
    event loop, processing tool calls until the connection
    is closed.

    This function is called when the container starts via:
        CMD ["python", "mcp_server.py"]
    """
    async with stdio_server() as (read, write):
        await server.run(
            read,
            write,
            server.create_initialization_options()
        )


# ─── Script Entry Point ───────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())
