# ============================================================
# mcp_server.py — MCP Server with HTTP Interface
# ============================================================
import asyncio
import asyncpg
import os
import json
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.requests import Request
import uvicorn

server = Server("acme-mcp-server")
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://acme_user:acme_pass@postgres:5432/acme_db"
).replace("postgresql+asyncpg", "postgresql")


async def get_conn():
    return await asyncpg.connect(DATABASE_URL)


# ── HTTP Endpoints ────────────────────────────────────────────

async def health(request: Request):
    return JSONResponse({"status": "ok", "service": "acme-mcp-server"})


async def http_get_customer_profile(request: Request):
    body = await request.json()
    conn = await get_conn()
    try:
        row = await conn.fetchrow(
            """
            SELECT id, name, email, phone, company, country
            FROM customers
            WHERE LOWER(name)    LIKE LOWER($1)
               OR LOWER(company) LIKE LOWER($1)
            LIMIT 1
            """,
            f"%{body['customer_name']}%"
        )
        if not row:
            return JSONResponse({"error": "Customer not found"})
        return JSONResponse({
            "id":      row["id"],
            "name":    row["name"],
            "email":   row["email"],
            "phone":   row["phone"],
            "company": row["company"],
            "country": row["country"]
        })
    finally:
        await conn.close()


async def http_get_open_issues(request: Request):
    body = await request.json()
    conn = await get_conn()
    try:
        customer = await conn.fetchrow(
            """
            SELECT id FROM customers
            WHERE LOWER(name)    LIKE LOWER($1)
               OR LOWER(company) LIKE LOWER($1)
            LIMIT 1
            """,
            f"%{body['customer_name']}%"
        )
        if not customer:
            return JSONResponse(
                [{"error": f"Customer '{body['customer_name']}' not found"}]
            )
        rows = await conn.fetch(
            """
            SELECT id, title, description, status, priority, created_at
            FROM issues
            WHERE customer_id = $1 AND status != 'closed'
            ORDER BY CASE priority
                WHEN 'critical' THEN 1
                WHEN 'high'     THEN 2
                WHEN 'medium'   THEN 3
                WHEN 'low'      THEN 4
            END
            """,
            customer["id"]
        )
        if not rows:
            return JSONResponse(
                [{"message": f"No open issues for {body['customer_name']}"}]
            )
        return JSONResponse([
            {
                "id":          r["id"],
                "title":       r["title"],
                "description": r["description"],
                "status":      r["status"],
                "priority":    r["priority"],
                "created_at":  str(r["created_at"])
            }
            for r in rows
        ])
    finally:
        await conn.close()


async def http_summarise_issue(request: Request):
    body = await request.json()
    conn = await get_conn()
    try:
        issue = await conn.fetchrow(
            """
            SELECT i.id, i.title, i.description,
                   i.status, i.priority,
                   c.name as customer_name
            FROM issues i
            JOIN customers c ON c.id = i.customer_id
            WHERE i.id = $1
            """,
            body["issue_id"]
        )
        if not issue:
            return JSONResponse({"error": "Issue not found"})
        updates = await conn.fetch(
            """
            SELECT updated_by, note, created_at
            FROM issue_updates
            WHERE issue_id = $1
            ORDER BY created_at ASC
            """,
            body["issue_id"]
        )
        return JSONResponse({
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
        })
    finally:
        await conn.close()


async def http_create_next_action(request: Request):
    body = await request.json()
    conn = await get_conn()
    try:
        issue = await conn.fetchrow(
            "SELECT id FROM issues WHERE id = $1",
            body["issue_id"]
        )
        if not issue:
            return JSONResponse({"error": "Issue not found"})
        row = await conn.fetchrow(
            """
            INSERT INTO next_actions
                (issue_id, action_text, created_by, status)
            VALUES ($1, $2, $3, 'pending')
            RETURNING id, issue_id, action_text, status
            """,
            body["issue_id"],
            body["action_text"],
            body["created_by"]
        )
        return JSONResponse({
            "success":     True,
            "action_id":   row["id"],
            "issue_id":    row["issue_id"],
            "action_text": row["action_text"],
            "status":      row["status"],
            "message":     f"Next action created for issue {body['issue_id']}."
        })
    finally:
        await conn.close()


async def http_list_all_customers(request: Request):
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            "SELECT id, name, company, email, country FROM customers"
        )
        return JSONResponse([
            {
                "id":      r["id"],
                "name":    r["name"],
                "company": r["company"],
                "email":   r["email"],
                "country": r["country"]
            }
            for r in rows
        ])
    finally:
        await conn.close()


async def http_get_next_actions(request: Request):
    body = await request.json()
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT id, action_text, created_by, status, created_at
            FROM next_actions
            WHERE issue_id = $1
            ORDER BY created_at DESC
            """,
            body["issue_id"]
        )
        return JSONResponse([
            {
                "id":          r["id"],
                "action_text": r["action_text"],
                "created_by":  r["created_by"],
                "status":      r["status"],
                "created_at":  str(r["created_at"])
            }
            for r in rows
        ])
    finally:
        await conn.close()


async def http_get_recent_updates(request: Request):
    """
    HTTP endpoint for get_recent_updates tool.
    Returns the 10 most recent issue updates across all issues.
    Used by the Escalation Summary Skill in agent.py.
    All tool calls now go through MCP — no direct DB access
    from the agent.
    """
    conn = await get_conn()
    try:
        rows = await conn.fetch(
            """
            SELECT updated_by, note, created_at
            FROM issue_updates
            ORDER BY created_at DESC
            LIMIT 10
            """
        )
        return JSONResponse([
            {
                "updated_by": r["updated_by"],
                "note":       r["note"],
                "created_at": str(r["created_at"])
            }
            for r in rows
        ])
    finally:
        await conn.close()


# ── Starlette App ─────────────────────────────────────────────
http_app = Starlette(routes=[
    Route("/health",
          health),
    Route("/tools/get_customer_profile",
          http_get_customer_profile,  methods=["POST"]),
    Route("/tools/get_open_issues",
          http_get_open_issues,       methods=["POST"]),
    Route("/tools/summarise_issue",
          http_summarise_issue,       methods=["POST"]),
    Route("/tools/create_next_action",
          http_create_next_action,    methods=["POST"]),
    Route("/tools/list_all_customers",
          http_list_all_customers,    methods=["POST"]),
    Route("/tools/get_next_actions",
          http_get_next_actions,      methods=["POST"]),
    Route("/tools/get_recent_updates",
          http_get_recent_updates,    methods=["POST"]),
])


# ── MCP Tool Registry ─────────────────────────────────────────
@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_customer_profile",
            description="Fetch customer profile by name or company.",
            inputSchema={
                "type": "object",
                "properties": {"customer_name": {"type": "string"}},
                "required": ["customer_name"]
            }
        ),
        types.Tool(
            name="get_open_issues",
            description="Get open issues for a customer.",
            inputSchema={
                "type": "object",
                "properties": {"customer_name": {"type": "string"}},
                "required": ["customer_name"]
            }
        ),
        types.Tool(
            name="summarise_issue",
            description="Get issue with full update history.",
            inputSchema={
                "type": "object",
                "properties": {"issue_id": {"type": "integer"}},
                "required": ["issue_id"]
            }
        ),
        types.Tool(
            name="create_next_action",
            description="Create a next action for an issue.",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_id":    {"type": "integer"},
                    "action_text": {"type": "string"},
                    "created_by":  {"type": "string"}
                },
                "required": ["issue_id", "action_text", "created_by"]
            }
        ),
        types.Tool(
            name="get_next_actions",
            description="List all next actions for an issue.",
            inputSchema={
                "type": "object",
                "properties": {"issue_id": {"type": "integer"}},
                "required": ["issue_id"]
            }
        ),
        types.Tool(
            name="list_all_customers",
            description="List all customers.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        types.Tool(
            name="get_recent_updates",
            description="Get 10 most recent issue updates across all issues.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": []
            }
        )
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[types.TextContent]:
    conn = await get_conn()
    try:
        if name == "get_customer_profile":
            row = await conn.fetchrow(
                """
                SELECT id, name, email, phone, company, country
                FROM customers
                WHERE LOWER(name)    LIKE LOWER($1)
                   OR LOWER(company) LIKE LOWER($1)
                LIMIT 1
                """,
                f"%{arguments['customer_name']}%"
            )
            result = dict(row) if row else {"error": "Customer not found"}

        elif name == "get_open_issues":
            customer = await conn.fetchrow(
                """
                SELECT id FROM customers
                WHERE LOWER(name)    LIKE LOWER($1)
                   OR LOWER(company) LIKE LOWER($1)
                LIMIT 1
                """,
                f"%{arguments['customer_name']}%"
            )
            if not customer:
                result = [{"error": "Customer not found"}]
            else:
                rows = await conn.fetch(
                    """
                    SELECT id, title, description,
                           status, priority, created_at
                    FROM issues
                    WHERE customer_id = $1 AND status != 'closed'
                    ORDER BY CASE priority
                        WHEN 'critical' THEN 1
                        WHEN 'high'     THEN 2
                        WHEN 'medium'   THEN 3
                        WHEN 'low'      THEN 4
                    END
                    """,
                    customer["id"]
                )
                result = [dict(r) for r in rows]

        elif name == "list_all_customers":
            rows = await conn.fetch(
                "SELECT id, name, company, email, country FROM customers"
            )
            result = [dict(r) for r in rows]

        elif name == "get_next_actions":
            rows = await conn.fetch(
                """
                SELECT id, action_text, created_by, status, created_at
                FROM next_actions WHERE issue_id = $1
                ORDER BY created_at DESC
                """,
                arguments["issue_id"]
            )
            result = [dict(r) for r in rows]

        elif name == "get_recent_updates":
            rows = await conn.fetch(
                """
                SELECT updated_by, note, created_at
                FROM issue_updates
                ORDER BY created_at DESC
                LIMIT 10
                """
            )
            result = [
                {
                    "updated_by": r["updated_by"],
                    "note":       r["note"],
                    "created_at": str(r["created_at"])
                }
                for r in rows
            ]

        else:
            result = {"error": f"Unknown tool: {name}"}

    finally:
        await conn.close()

    return [
        types.TextContent(
            type="text",
            text=json.dumps(result, default=str)
        )
    ]


# ── Entry Point ───────────────────────────────────────────────
async def main():
    config = uvicorn.Config(
        http_app,
        host="0.0.0.0",
        port=8001,
        log_level="info"
    )
    srv = uvicorn.Server(config)
    await srv.serve()


if __name__ == "__main__":
    asyncio.run(main())
