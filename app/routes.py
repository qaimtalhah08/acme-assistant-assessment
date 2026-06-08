# ============================================================
# routes.py — All API Route Handlers
# ============================================================
# This module defines all HTTP endpoints for the Acme
# Assistant API. Each route is protected by FastAPI
# dependency injection that enforces authentication and
# role-based access control before the handler executes.
#
# Endpoint Summary:
#   GET  /health                    — Public health check
#   POST /query                     — Main agent endpoint
#   GET  /customers                 — List all customers
#   GET  /customers/{id}/issues     — Get issues by customer
#   POST /next-actions              — Create a next action
#   GET  /logs                      — View request logs
#
# Authentication:
#   All routes except /health require a valid Keycloak JWT
#   token passed in the Authorization: Bearer header.
#
# RBAC Summary:
#   sales_user   → /health, /query, /customers, /issues
#   support_user → all above + /next-actions
#   admin        → all above + /logs
#
# Observability:
#   Every /query request is logged to Redis with endpoint,
#   username, status, latency, and tools used by the agent.
#   Logs are accessible via GET /logs (admin only).
# ============================================================

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from typing import Optional
import uuid
import json
import redis.asyncio as aioredis
import os
import time

from database import get_db, Customer, Issue, NextAction
from auth import (
    require_sales_or_above,
    require_support_or_above,
    require_admin
)
from agent import run_agent

# Create the API router — registered in main.py with /api/v1 prefix
router = APIRouter()
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


# ─── Request Body Models ──────────────────────────────────────
# Pydantic models validate and parse incoming JSON request bodies.
# FastAPI uses these for automatic request validation and
# Swagger UI documentation generation.

class QueryRequest(BaseModel):
    """
    Request body for the POST /query endpoint.

    Fields:
        query:      Natural language question for the agent
        session_id: Optional conversation session ID for memory.
                    If not provided, a new UUID is generated.
                    Pass the same session_id across requests to
                    maintain conversation context in Redis.
    """
    query:      str
    session_id: Optional[str] = None


class NextActionRequest(BaseModel):
    """
    Request body for the POST /next-actions endpoint.

    Fields:
        issue_id:    ID of the issue to attach the action to
        action_text: Description of the recommended action
    """
    issue_id:    int
    action_text: str


# ─── Observability: Request Logger ────────────────────────────
async def log_request(
    endpoint:    str,
    user:        str,
    status:      str,
    duration_ms: float,
    detail:      str = ""
):
    """
    Persist a request log entry to Redis for observability.

    Each log entry captures the key metrics needed to monitor
    the application in production: who made the request, how
    long it took, whether it succeeded, and which agent tools
    were invoked.

    Log entries are stored in a Redis list (request_logs) using
    lpush so the most recent entries appear first. The list is
    trimmed to 100 entries to prevent unbounded memory growth.

    Logs are retrievable by admin users via GET /api/v1/logs
    or by asking the agent "show me the request logs".

    Failure behaviour:
        Log failures are silently suppressed. A logging failure
        must never cause an API request to fail — the core
        business logic takes priority over observability.

    Args:
        endpoint:    API path, e.g. "/query"
        user:        Authenticated username from JWT token
        status:      "success" or "error"
        duration_ms: End-to-end request latency in milliseconds
        detail:      Additional context, e.g. tools used
    """
    try:
        redis = await aioredis.from_url(REDIS_URL, decode_responses=True)

        log_entry = {
            "endpoint":    endpoint,
            "user":        user,
            "status":      status,
            "duration_ms": round(duration_ms, 2),
            "detail":      detail,
            "timestamp":   time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # Prepend to list so newest entries appear first
        await redis.lpush("request_logs", json.dumps(log_entry))

        # Keep only the 100 most recent log entries
        await redis.ltrim("request_logs", 0, 99)

    except Exception:
        # Logging failure must never crash the API
        pass


# ─── Route 1: Health Check ────────────────────────────────────
@router.get("/health", tags=["System"])
async def health_check():
    """
    Public health check endpoint — no authentication required.

    Used by:
      - Docker Compose health checks to verify the service
        is running before dependent services start
      - Load balancers to determine if the instance is healthy
      - Developers to confirm the API server is reachable

    Returns:
        dict: Service name, version, and status string
    """
    return {
        "status":  "ok",
        "service": "acme-assistant",
        "version": "1.0.0"
    }


# ─── Route 2: Agent Query — Main Endpoint ────────────────────
@router.post("/query", tags=["Agent"])
async def agent_query(
    body:    QueryRequest,
    request: Request,
    payload: dict = Depends(require_sales_or_above),
    db:      AsyncSession = Depends(get_db)
):
    """
    Submit a natural language query to the LLM agent.

    This is the primary endpoint of the Acme Assistant.
    It accepts a plain English question, passes it to the
    agentic loop in agent.py, and returns the agent's
    response along with metadata about tool usage.

    The agent dynamically selects which tools to call based
    on the query content. No hard-coded routing is used.

    Session Memory:
        If session_id is provided, the agent loads prior
        conversation turns from Redis and includes them as
        context. This enables multi-turn conversations where
        the agent can reference earlier messages.

    Access Control:
        Requires any authenticated role (sales, support, admin).
        Role-specific restrictions (e.g. create_next_action)
        are enforced inside the agent loop in agent.py.

    Args:
        body:    QueryRequest with query string and session_id
        request: FastAPI Request object (for future use)
        payload: Decoded JWT payload from require_sales_or_above
        db:      Async PostgreSQL session from get_db

    Returns:
        dict: {
            session_id:  UUID for conversation continuity,
            query:       The original query string,
            response:    Agent's natural language answer,
            tools_used:  List of tool names called,
            duration_ms: End-to-end latency in milliseconds
        }
    """
    start_time = time.time()
    username = payload.get("preferred_username", "unknown")

    # Generate a new session ID if one was not provided
    # The client should store this and pass it on subsequent
    # requests to maintain conversation memory in Redis
    session_id = body.session_id or str(uuid.uuid4())

    try:
        # Invoke the agentic loop — this is where the LLM
        # reasons, selects tools, executes them, and produces
        # a final natural language response
        result = await run_agent(
            query=body.query,
            user_payload=payload,
            db=db,
            session_id=session_id
        )

        duration = (time.time() - start_time) * 1000

        # Log the successful request for observability
        await log_request(
            endpoint="/query",
            user=username,
            status="success",
            duration_ms=duration,
            detail=f"Tools used: {result['tools_used']}"
        )

        return {
            "session_id":  session_id,
            "query":       body.query,
            "response":    result["response"],
            "tools_used":  result["tools_used"],
            "duration_ms": round(duration, 2)
        }

    except Exception as e:
        # Log the failure before re-raising
        duration = (time.time() - start_time) * 1000
        await log_request(
            endpoint="/query",
            user=username,
            status="error",
            duration_ms=duration,
            detail=str(e)
        )
        raise HTTPException(
            status_code=500,
            detail=f"Agent error: {str(e)}"
        )


# ─── Route 3: List All Customers ─────────────────────────────
@router.get("/customers", tags=["Customers"])
async def get_customers(
    payload: dict = Depends(require_sales_or_above),
    db:      AsyncSession = Depends(get_db)
):
    """
    Retrieve a list of all customers in the system.

    Returns basic profile information for all customers
    seeded in the PostgreSQL database. This endpoint is
    also called by the agent's list_all_customers tool.

    Access Control:
        Minimum role: sales_user (read-only)
        All authenticated roles may access this endpoint.

    Returns:
        list: All customer records with id, name, email,
              company, and country fields
    """
    result = await db.execute(select(Customer))
    customers = result.scalars().all()

    return [
        {
            "id":      c.id,
            "name":    c.name,
            "email":   c.email,
            "company": c.company,
            "country": c.country
        }
        for c in customers
    ]


# ─── Route 4: Get Issues by Customer ID ──────────────────────
@router.get("/customers/{customer_id}/issues", tags=["Issues"])
async def get_customer_issues(
    customer_id: int,
    payload:     dict = Depends(require_sales_or_above),
    db:          AsyncSession = Depends(get_db)
):
    """
    Retrieve all issues (open and closed) for a customer.

    Unlike the agent's get_open_issues tool which filters
    to exclude closed issues, this REST endpoint returns
    all issues regardless of status for complete visibility.

    Access Control:
        Minimum role: sales_user (read-only)

    Args:
        customer_id: The numeric customer ID from the URL path

    Returns:
        list: All issue records for the specified customer,
              including id, title, status, priority, description
    """
    result = await db.execute(
        select(Issue).where(Issue.customer_id == customer_id)
    )
    issues = result.scalars().all()

    return [
        {
            "id":          i.id,
            "title":       i.title,
            "status":      i.status,
            "priority":    i.priority,
            "description": i.description
        }
        for i in issues
    ]


# ─── Route 5: Create Next Action ─────────────────────────────
@router.post("/next-actions", tags=["Actions"])
async def create_next_action(
    body:    NextActionRequest,
    payload: dict = Depends(require_support_or_above),
    db:      AsyncSession = Depends(get_db)
):
    """
    Create a recommended next action for a specific issue.

    This REST endpoint provides a direct way to create next
    actions without going through the agent. It is also used
    internally when the agent calls the create_next_action
    tool after RBAC verification.

    The created_by field is always populated from the verified
    JWT token — it cannot be set by the request body. This
    ensures accurate attribution of who created each action.

    Access Control:
        Minimum role: support_user
        sales_user is blocked — HTTP 403 returned

    Args:
        body:    NextActionRequest with issue_id and action_text
        payload: Decoded JWT payload from require_support_or_above
        db:      Async PostgreSQL session from get_db

    Returns:
        dict: Created action details including the new action_id

    Raises:
        HTTPException 404: If the referenced issue does not exist
    """
    username = payload.get("preferred_username", "unknown")

    # Verify the referenced issue exists before creating the action
    result = await db.execute(
        select(Issue).where(Issue.id == body.issue_id)
    )
    issue = result.scalar_one_or_none()

    if not issue:
        raise HTTPException(
            status_code=404,
            detail=f"Issue {body.issue_id} not found"
        )

    # Persist the next action with username from JWT token
    action = NextAction(
        issue_id=body.issue_id,
        action_text=body.action_text,
        created_by=username,   # From JWT — not from request body
        status="pending"
    )
    db.add(action)
    await db.commit()
    await db.refresh(action)

    return {
        "success":     True,
        "action_id":   action.id,
        "issue_id":    body.issue_id,
        "action_text": body.action_text,
        "created_by":  username
    }


# ─── Route 6: Get Request Logs ───────────────────────────────
@router.get("/logs", tags=["Admin"])
async def get_logs(
    payload: dict = Depends(require_admin)
):
    """
    Retrieve the most recent API request logs from Redis.

    Returns the last 50 request log entries in reverse
    chronological order (most recent first). Each entry
    contains the endpoint, username, status, latency,
    tools used, and timestamp.

    This endpoint satisfies the assessment observability
    requirement from Section 4.8. Logs can also be
    retrieved by asking the agent: "show me the request logs"

    Access Control:
        Admin role required — HTTP 403 for other roles

    Returns:
        dict: {
            total: Number of log entries returned,
            logs:  List of log entry dicts
        }

    Raises:
        HTTPException 500: If Redis is unreachable
    """
    try:
        redis = await aioredis.from_url(REDIS_URL, decode_responses=True)
        logs_raw = await redis.lrange("request_logs", 0, 49)
        logs = [json.loads(log) for log in logs_raw]

        return {"total": len(logs), "logs": logs}

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Could not fetch logs: {e}"
        )
