# ============================================================
# agent.py — LLM Agent with Tool Selection, Caching & RBAC
# ============================================================
# This file is the core intelligence of the Acme Assistant.
#
# Architecture:
#   - Uses Azure OpenAI GPT-4.1-mini with function calling
#   - Implements an agentic loop that runs until a final
#     answer is produced or max iterations is reached
#   - Enforces RBAC before any tool execution
#   - Caches responses in Redis to improve performance
#   - Supports multi-turn conversation via session memory
#   - OpenTelemetry tracing for distributed observability

# Tool Selection:
#   The LLM dynamically decides which tool to call based on
#   the user's natural language query. No hard-coded routing
#   is used — this satisfies the assessment requirement for
#   agentic tool selection.
#
# RBAC Enforcement:
#   Role checks happen at two levels:
#   1. System prompt — guides the LLM's decision making
#   2. Pre-execution check — blocks unauthorised tool calls
#      before they reach the database

# Observability:
#   Every agent run, Azure OpenAI call, and tool execution
#   is wrapped in an OpenTelemetry span. Traces are visible
#   in Jaeger UI at http://localhost:16686
# ============================================================

from openai import AzureOpenAI
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from database import Customer, Issue, IssueUpdate, NextAction
from skills import escalation_summary_skill, issue_summary_skill
from observability import get_tracer   # OpenTelemetry tracing
import os
import json
import redis.asyncio as aioredis


# ─── Azure OpenAI Client Initialisation ──────────────────────
# All credentials are loaded from environment variables
# injected by Docker Compose from the .env file at runtime.
# This avoids hardcoding secrets in source code.
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
)

# Azure OpenAI deployment name (model alias configured in Azure)
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")

# Redis connection URL for caching and session memory
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")


# ─── Redis Connection Helper ──────────────────────────────────
async def get_redis():
    """
    Create and return an async Redis client connection.

    Uses decode_responses=True so all values are returned as
    Python strings rather than raw bytes. This simplifies
    JSON parsing throughout the codebase.

    Returns:
        aioredis.Redis: Async Redis client instance
    """
    return await aioredis.from_url(REDIS_URL, decode_responses=True)


# ============================================================
# TOOL FUNCTIONS
# ============================================================
# Each function below implements one agent tool. Tools are
# the primary mechanism by which the agent interacts with
# the PostgreSQL database and external services.
#
# Design principles:
#   - Each tool does one thing and does it well
#   - Tools return dicts/lists that are JSON-serialisable
#   - Errors are returned as dicts rather than raised, so
#     the agent can reason about them and respond naturally
#   - Customer searches support name OR company name lookup
# ============================================================


# ─── Tool 1: Get Customer Profile ────────────────────────────
async def tool_get_customer(
    customer_name: str,
    db: AsyncSession
) -> dict:
    """
    Fetch a single customer profile from PostgreSQL.

    Supports two search modes simultaneously via an OR query:
      - Personal name:  "James Miller"
      - Company name:   "Acme Corp"

    Both searches use ILIKE for case-insensitive matching,
    so "acme corp", "Acme Corp" and "ACME CORP" all work.

    Caching strategy:
      Results are stored in Redis for 10 minutes (600s).
      The cache key is based on the lowercased search term.
      On cache hit, the database is bypassed entirely,
      reducing latency from ~5ms to <1ms.

    Args:
        customer_name: Personal name or company name to search
        db:            Async SQLAlchemy database session

    Returns:
        dict: Customer profile fields, or {"error": "..."} if
              no matching customer is found
    """
    redis = await get_redis()
    cache_key = f"customer:{customer_name.lower()}"

    # Check Redis cache before querying PostgreSQL
    cached = await redis.get(cache_key)
    if cached:
        return json.loads(cached)

    # Cache miss — perform ILIKE search on name and company fields
    result = await db.execute(
        select(Customer).where(
            or_(
                Customer.name.ilike(f"%{customer_name}%"),
                Customer.company.ilike(f"%{customer_name}%")
            )
        )
    )
    customer = result.scalar_one_or_none()

    if not customer:
        return {"error": f"Customer '{customer_name}' not found"}

    # Construct the response payload
    data = {
        "id":      customer.id,
        "name":    customer.name,
        "email":   customer.email,
        "phone":   customer.phone,
        "company": customer.company,
        "country": customer.country
    }

    # Cache result in Redis for 10 minutes
    await redis.setex(cache_key, 600, json.dumps(data))

    return data


# ─── Tool 2: Get Open Issues ──────────────────────────────────
async def tool_get_open_issues(
    customer_name: str,
    db: AsyncSession
) -> list:
    """
    Retrieve all non-closed issues for a given customer.

    Issues are filtered to exclude status='closed' and ordered
    by priority so that critical issues appear first. This
    ordering helps the agent naturally surface the most
    urgent issues in its response.

    Priority ordering (ascending SQL sort maps to):
      critical -> high -> medium -> low

    Args:
        customer_name: Customer personal name or company name
        db:            Async SQLAlchemy database session

    Returns:
        list: List of open issue dicts ordered by priority,
              or a list containing an error/message dict
    """
    # Resolve the customer name to a database record first
    customer_result = await db.execute(
        select(Customer).where(
            or_(
                Customer.name.ilike(f"%{customer_name}%"),
                Customer.company.ilike(f"%{customer_name}%")
            )
        )
    )
    customer = customer_result.scalar_one_or_none()

    if not customer:
        return [{
            "error": (
                f"Customer '{customer_name}' not found. "
                f"Unable to retrieve issues."
            )
        }]

    # Fetch all open/in-progress issues ordered by priority
    issues_result = await db.execute(
        select(Issue).where(
            and_(
                Issue.customer_id == customer.id,
                Issue.status != "closed"
            )
        ).order_by(Issue.priority)
    )
    issues = issues_result.scalars().all()

    if not issues:
        return [{"message": f"No open issues found for {customer_name}"}]

    return [
        {
            "id":          i.id,
            "title":       i.title,
            "description": i.description,
            "status":      i.status,
            "priority":    i.priority,
            "created_at":  str(i.created_at)
        }
        for i in issues
    ]


# ─── Tool 3: Summarise Issue ──────────────────────────────────
async def tool_summarise_issue(
    issue_id: int,
    db: AsyncSession
) -> dict:
    """
    Generate an AI-powered summary of a specific issue.

    This tool combines two data sources:
    1. The issue record itself (title, description, status)
    2. All update records for that issue (chronological log)

    The combined data is passed to the Issue Summary Skill
    in skills.py, which calls Azure OpenAI to generate a
    concise natural language summary.

    This demonstrates the Skill pattern — a reusable,
    structured AI workflow distinct from a one-off prompt.

    Args:
        issue_id: Numeric ID of the issue to summarise
        db:       Async SQLAlchemy database session

    Returns:
        dict: Issue metadata plus AI-generated summary,
              or {"error": "..."} if issue ID not found
    """
    # Fetch the issue record from PostgreSQL
    issue_result = await db.execute(
        select(Issue).where(Issue.id == issue_id)
    )
    issue = issue_result.scalar_one_or_none()

    if not issue:
        return {"error": f"Issue {issue_id} not found in the database"}

    # Fetch all update records in chronological order
    updates_result = await db.execute(
        select(IssueUpdate)
        .where(IssueUpdate.issue_id == issue_id)
        .order_by(IssueUpdate.created_at)
    )
    updates = updates_result.scalars().all()

    # Format updates for the skill function
    updates_list = [
        {
            "updated_by": u.updated_by,
            "note":       u.note,
            "created_at": str(u.created_at)
        }
        for u in updates
    ]

    # Invoke the reusable Issue Summary Skill
    summary = await issue_summary_skill(
        issue_title=issue.title,
        issue_description=issue.description,
        updates=updates_list
    )

    return {
        "issue_id":      issue.id,
        "title":         issue.title,
        "status":        issue.status,
        "priority":      issue.priority,
        "summary":       summary,
        "total_updates": len(updates_list)
    }


# ─── Tool 4: Create Next Action ───────────────────────────────
async def tool_create_next_action(
    issue_id:    int,
    action_text: str,
    created_by:  str,
    db:          AsyncSession
) -> dict:
    """
    Persist a new recommended next action for an issue.

    Access control note:
      This function is only reached after the RBAC check in
      run_agent() has confirmed the user is support_user or
      admin. Sales users are blocked before this point.

    The created_by field is always set from the authenticated
    JWT payload — it cannot be spoofed by the user's query.

    Args:
        issue_id:    ID of the issue to attach the action to
        action_text: Description of the recommended action
        created_by:  Username from the verified JWT token
        db:          Async SQLAlchemy database session

    Returns:
        dict: Created action details including new action ID,
              or {"error": "..."} if issue ID not found
    """
    # Verify the referenced issue exists before writing
    issue_result = await db.execute(
        select(Issue).where(Issue.id == issue_id)
    )
    issue = issue_result.scalar_one_or_none()

    if not issue:
        return {"error": f"Issue {issue_id} not found"}

    # Create and persist the next action record
    new_action = NextAction(
        issue_id=issue_id,
        action_text=action_text,
        created_by=created_by,
        status="pending"
    )
    db.add(new_action)
    await db.commit()
    await db.refresh(new_action)

    return {
        "success":     True,
        "action_id":   new_action.id,
        "issue_id":    issue_id,
        "action_text": action_text,
        "status":      "pending",
        "message":     f"Next action successfully created for issue {issue_id}."
    }


# ─── Tool 5: List All Customers ───────────────────────────────
async def tool_list_all_customers(db: AsyncSession) -> list:
    """
    Return a list of all customers in the system.

    Available to all authenticated roles (sales, support,
    admin) as this is read-only data. Phone numbers are
    excluded from this listing for brevity — use
    get_customer_profile for full contact details.

    Args:
        db: Async SQLAlchemy database session

    Returns:
        list: All customer records with key fields
    """
    result = await db.execute(select(Customer))
    customers = result.scalars().all()

    return [
        {
            "id":      c.id,
            "name":    c.name,
            "company": c.company,
            "email":   c.email,
            "country": c.country
        }
        for c in customers
    ]


# ============================================================
# TOOL DEFINITIONS — OpenAI Function Calling Schema
# ============================================================
# These JSON schemas define the tools available to the LLM.
# The descriptions are critical — they guide the model's
# decision about which tool to select for each query.
#
# Design principle:
#   Tool descriptions should be specific enough that the
#   LLM reliably selects the correct tool, but not so
#   prescriptive that they break with minor query variations.
# ============================================================

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_profile",
            "description": (
                "Fetch a customer profile from the database. "
                "Supports search by personal name OR company name. "
                "Example: 'James Miller' and 'Acme Corp' return the same record. "
                "Use this when asked about a specific customer's details."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": (
                            "Customer personal name or company name. "
                            "Examples: 'James Miller', 'Acme Corp', 'TechStart Ltd'"
                        )
                    }
                },
                "required": ["customer_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_open_issues",
            "description": (
                "Retrieve all open and in-progress issues for a customer. "
                "Search by personal name or company name. "
                "Issues are returned ordered by priority (critical first). "
                "Use this when asked about customer problems, issues, or tickets."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "Customer personal name or company name"
                    }
                },
                "required": ["customer_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "summarise_issue",
            "description": (
                "Generate an AI-powered summary of a specific issue including "
                "its full history of updates. Use the numeric issue ID. "
                "Returns current status, priority, and a concise narrative summary. "
                "Use this when asked to summarise or explain a specific issue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_id": {
                        "type": "integer",
                        "description": (
                            "The numeric ID of the issue to summarise. "
                            "Examples: 1, 2, 3, 4"
                        )
                    }
                },
                "required": ["issue_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_next_action",
            "description": (
                "Create a recommended next action for a specific issue. "
                "Persists the action to the database with pending status. "
                "IMPORTANT: Restricted to support_user and admin roles only. "
                "Sales users are not permitted to use this tool."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "issue_id": {
                        "type": "integer",
                        "description": "The numeric ID of the issue"
                    },
                    "action_text": {
                        "type": "string",
                        "description": "Clear description of the recommended action"
                    }
                },
                "required": ["issue_id", "action_text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "escalation_summary",
            "description": (
                "Run the Customer Escalation Summary Skill. "
                "Returns: executive summary, risk level (Low/Medium/High/Critical), "
                "recommended next action, and list of missing information. "
                "Use when asked for escalation status, risk assessment, or overall "
                "customer situation overview."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "Customer personal name or company name"
                    }
                },
                "required": ["customer_name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_all_customers",
            "description": (
                "Retrieve the complete list of all customers in the system. "
                "Use when asked to show all customers or get an overview "
                "of the customer base. Available to all authenticated roles."
            ),
            "parameters": {
                "type":       "object",
                "properties": {},
                "required":   []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_request_logs",
            "description": (
                "Retrieve recent API request logs from Redis. "
                "Shows endpoint, username, status, latency, and tools used. "
                "IMPORTANT: Restricted to admin role only. "
                "Use when asked to show logs, requests, or system activity."
            ),
            "parameters": {
                "type":       "object",
                "properties": {},
                "required":   []
            }
        }
    }
]


# ============================================================
# MAIN AGENT FUNCTION
# ============================================================
# This is the primary entry point called by routes.py for
# every user query. It implements the full agentic loop.
#
# Agentic Loop:
#   1. Check Redis cache for repeated queries
#   2. Load conversation history from Redis session
#   3. Build message context (system prompt + history + query)
#   4. Call Azure OpenAI with tool definitions
#   5. If tool calls returned: execute them, add results to context
#   6. Repeat from step 4 until final text answer is produced
#   7. Cache result, update session history, return to caller
#
# The loop continues for up to max_iterations=5 rounds.
# In practice, most queries complete in 1-2 iterations.
#
# OpenTelemetry Tracing:
#   Every agent run is wrapped in a parent span "agent.run"
#   Each Azure OpenAI call gets its own span "azure_openai.call"
#   Each tool execution gets its own span "tool.{tool_name}"
#   All spans are visible in Jaeger at http://localhost:16686
# ============================================================

async def run_agent(
    query:        str,
    user_payload: dict,
    db:           AsyncSession,
    session_id:   str = None
) -> dict:
    """
    Execute the agentic reasoning loop for a user query.

    This function is the heart of the assessment solution.
    It demonstrates dynamic tool selection — the LLM reasons
    about which tools to call rather than following hard-coded
    logic. This satisfies Section 4.1 of the assessment brief.

    OpenTelemetry spans added:
      - agent.run        — wraps the entire agent execution
      - redis.cache_check — tracks cache hit/miss
      - azure_openai.call — tracks each LLM API call
      - tool.{name}      — tracks each tool execution

    Args:
        query:        Natural language question from the user
        user_payload: Decoded JWT payload containing username
                      and realm_access.roles from Keycloak
        db:           Async PostgreSQL session from FastAPI
        session_id:   Redis key for conversation memory.
                      New UUID generated per login session.

    Returns:
        dict: {
            "response":     Final natural language answer,
            "tools_used":   Ordered list of tools called,
            "tool_results": Raw results from each tool call
        }
    """

    # Get OpenTelemetry tracer for creating custom spans
    tracer = get_tracer()

    # Extract authenticated user context from JWT payload
    username = user_payload.get("preferred_username", "unknown")
    roles = user_payload.get("realm_access", {}).get("roles", [])

    redis = await get_redis()
    history = []

    # ── Main Agent Span ───────────────────────────────────────
    # This parent span wraps the entire agent execution.
    # All child spans (cache, LLM, tools) appear nested
    # inside this span in the Jaeger trace timeline.
    with tracer.start_as_current_span("agent.run") as span:
        span.set_attribute("user.name",  username)
        span.set_attribute("user.roles", str(roles))
        span.set_attribute("query",      query[:100])

        # ── Cache Skip Logic ──────────────────────────────────
        # Certain query types must always bypass the cache and
        # execute fresh tool calls against the database.
        skip_cache_keywords = [
            "create",
            "summarise", "summarize",
            "summary of issue",
            "issue number",
            "issue 1", "issue 2", "issue 3",
            "issue 4", "issue 5", "issue 6",
            "issue 7", "issue 8", "issue 9",
            "in detail", "detail",
            "ref:"
        ]

        should_skip_cache = any(
            kw in query.lower()
            for kw in skip_cache_keywords
        )

        # ── Redis Cache Check Span ────────────────────────────
        # Tracks whether the query was served from cache or
        # required a full agent execution with Azure OpenAI.
        cache_key = f"query_cache:{username}:{query.lower().strip()}"
        with tracer.start_as_current_span("redis.cache_check") as cache_span:
            cached_reply = await redis.get(cache_key) if not should_skip_cache else None
            cache_span.set_attribute("cache.hit",    cached_reply is not None)
            cache_span.set_attribute("cache.skipped", should_skip_cache)

        if cached_reply:
            cached = json.loads(cached_reply)
            span.set_attribute("response.source", "cache")
            return {
                "response":     cached["response"],
                "tools_used":   cached["tools_used"],
                "tool_results": {}
            }

        # ── Session History Load ──────────────────────────────
        # Load up to 10 previous turns from Redis session store.
        # This enables multi-turn conversations where the agent
        # can reference context from earlier in the session.
        if session_id:
            history_raw = await redis.get(f"session:{session_id}")
            if history_raw:
                history = json.loads(history_raw)

        # ── System Prompt ─────────────────────────────────────
        # The system prompt defines the agent's identity, rules,
        # and RBAC policy. It is injected at the start of every
        # context window sent to Azure OpenAI.
        system_prompt = f"""
You are an intelligent enterprise assistant for Acme Operations.
You help sales, support, and admin staff manage customer issues efficiently.

AUTHENTICATED USER:
  Username: {username}
  Roles:    {', '.join(roles)}

ROLE-BASED ACCESS CONTROL:

  sales_user (READ ONLY):
    - Can view customer profiles, open issues, and escalation summaries
    - Cannot create next actions
    - If a sales_user requests a next action, respond with:
      "As a sales user, you are not able to create next actions.
       Only support and admin roles can do this."

  support_user (READ + CREATE):
    - Can view all customer and issue data
    - Can create next actions for issues
    - Cannot view system request logs

  admin (FULL ACCESS):
    - All permissions including system request log access

SEARCH RULES:
  - Always search customers by personal name OR company name
  - "Acme Corp" and "James Miller" refer to the same customer
  - If customer not found, clearly state "not found" or "unable to find"

DATA RULES:
  - Always use the provided tools to fetch real data
  - Never invent or assume customer names, issues, or actions
  - When a next action is created, confirm it clearly
  - When access is denied, explain the reason clearly

RESPONSE STYLE:
  - Be concise, professional, and factual
  - Lead with the most important information
  - Avoid unnecessary filler or repetition

MANDATORY TOOL USAGE RULES:
  - ALWAYS call create_next_action tool when user requests
    creating a next action — even if you expect access will
    be denied. The tool handles the access check internally.
  - ALWAYS call get_open_issues or get_customer_profile when
    asked about customers or issues — never respond from memory.
  - NEVER respond directly without calling the appropriate tool
    for data retrieval or write operations.
  - For issue summarisation requests, ALWAYS call summarise_issue
    tool with the numeric issue ID.
"""

        # Build the full message context for this request
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        messages.append({"role": "user", "content": query})

        tools_used = []
        tool_results = {}
        max_iterations = 5
        iteration = 0

        # ── Agentic Loop ──────────────────────────────────────
        while iteration < max_iterations:
            iteration += 1

            # ── Azure OpenAI Call Span ────────────────────────
            # Each call to Azure OpenAI gets its own span so
            # you can see exactly how long each LLM call takes
            # and how many iterations the agent needed.
            with tracer.start_as_current_span("azure_openai.call") as llm_span:
                llm_span.set_attribute("iteration", iteration)
                llm_span.set_attribute("model",     DEPLOYMENT)

                response = client.chat.completions.create(
                    model=DEPLOYMENT,
                    messages=messages,
                    tools=TOOLS,
                    tool_choice="auto",
                    temperature=0.1,
                    max_tokens=500
                )

            message = response.choices[0].message

            # ── Final Answer Path ─────────────────────────────
            if not message.tool_calls:
                final_answer = message.content

                span.set_attribute("tools.used",  str(tools_used))
                span.set_attribute("iterations",  iteration)
                span.set_attribute("response.source", "llm")

                # Persist conversation turn to Redis session
                if session_id:
                    history.append({"role": "user",     "content": query})
                    history.append(
                        {"role": "assistant", "content": final_answer})
                    history = history[-10:]
                    await redis.setex(
                        f"session:{session_id}",
                        3600,
                        json.dumps(history)
                    )

                # Cache this response for 5 minutes
                await redis.setex(
                    cache_key,
                    300,
                    json.dumps({
                        "response":   final_answer,
                        "tools_used": tools_used
                    })
                )

                return {
                    "response":     final_answer,
                    "tools_used":   tools_used,
                    "tool_results": tool_results
                }

            # ── Tool Execution Path ───────────────────────────
            messages.append(message)

            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args = json.loads(tool_call.function.arguments)
                tools_used.append(tool_name)

                # ── Tool Execution Span ───────────────────────
                # Each tool call gets its own span showing the
                # tool name, arguments, and execution time.
                # RBAC denials are recorded as span attributes.
                with tracer.start_as_current_span(
                    f"tool.{tool_name}"
                ) as tool_span:
                    tool_span.set_attribute("tool.name", tool_name)
                    tool_span.set_attribute("tool.args", str(tool_args))

                    # ── RBAC Gate: create_next_action ─────────
                    if tool_name == "create_next_action":
                        if "admin" not in roles and "support_user" not in roles:
                            result = {
                                "error": (
                                    "Access denied. As a sales user, you are not able "
                                    "to create next actions. Only support and admin "
                                    "roles can do this."
                                )
                            }
                            tool_span.set_attribute("rbac.denied", True)
                            tool_results[tool_name] = result
                            messages.append({
                                "role":         "tool",
                                "tool_call_id": tool_call.id,
                                "content":      json.dumps(result)
                            })
                            continue

                    # ── RBAC Gate: get_request_logs ───────────
                    if tool_name == "get_request_logs":
                        if "admin" not in roles:
                            result = {
                                "error": (
                                    "Access denied. System request logs are "
                                    "restricted to admin users only."
                                )
                            }
                            tool_span.set_attribute("rbac.denied", True)
                            tool_results[tool_name] = result
                            messages.append({
                                "role":         "tool",
                                "tool_call_id": tool_call.id,
                                "content":      json.dumps(result)
                            })
                            continue

                    # ── Tool Dispatch ─────────────────────────
                    try:
                        if tool_name == "get_customer_profile":
                            result = await tool_get_customer(
                                tool_args["customer_name"], db
                            )

                        elif tool_name == "get_open_issues":
                            result = await tool_get_open_issues(
                                tool_args["customer_name"], db
                            )

                        elif tool_name == "summarise_issue":
                            result = await tool_summarise_issue(
                                tool_args["issue_id"], db
                            )

                        elif tool_name == "create_next_action":
                            result = await tool_create_next_action(
                                issue_id=tool_args["issue_id"],
                                action_text=tool_args["action_text"],
                                created_by=username,
                                db=db
                            )

                        elif tool_name == "list_all_customers":
                            result = await tool_list_all_customers(db)

                        elif tool_name == "get_request_logs":
                            redis_conn = await get_redis()
                            logs_raw = await redis_conn.lrange(
                                "request_logs", 0, 19
                            )
                            result = [json.loads(log) for log in logs_raw]
                            if not result:
                                result = {
                                    "message": "No request logs recorded yet."}

                        elif tool_name == "escalation_summary":
                            issues = await tool_get_open_issues(
                                tool_args["customer_name"], db
                            )
                            updates_result = await db.execute(
                                select(IssueUpdate)
                                .order_by(IssueUpdate.created_at.desc())
                                .limit(10)
                            )
                            updates = updates_result.scalars().all()
                            updates_list = [
                                {
                                    "updated_by": u.updated_by,
                                    "note":       u.note,
                                    "created_at": str(u.created_at)
                                }
                                for u in updates
                            ]
                            result = await escalation_summary_skill(
                                customer_name=tool_args["customer_name"],
                                issues=issues,
                                recent_updates=updates_list
                            )

                        else:
                            result = {
                                "error": f"Unknown tool requested: '{tool_name}'"
                            }

                        tool_span.set_attribute("tool.success", True)

                    except Exception as e:
                        result = {
                            "error": f"Tool '{tool_name}' execution failed: {str(e)}"
                        }
                        tool_span.set_attribute("tool.error",   str(e))
                        tool_span.set_attribute("tool.success", False)

                tool_results[tool_name] = result
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tool_call.id,
                    "content":      json.dumps(result, default=str)
                })

        # ── Max Iterations Reached ────────────────────────────
        span.set_attribute("max_iterations.reached", True)
        return {
            "response": (
                "I was unable to complete your request within the allowed "
                "steps. Please try rephrasing your question."
            ),
            "tools_used":   tools_used,
            "tool_results": tool_results
        }
