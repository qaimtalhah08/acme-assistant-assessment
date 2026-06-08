# Acme Assistant
### Agentic Enterprise Solution

An agentic AI assistant for Acme Operations that enables sales, support, and admin staff to query customer issues, generate AI-powered escalation summaries, and manage recommended actions — through a secure, role-aware chat interface.

---

## Quick Start

```bash
git clone <your-repo-url>
cd acme-assistant

# Copy the environment template and add your Azure OpenAI credentials
cp .env.example .env
# Open .env and fill in your AZURE_OPENAI_API_KEY,
# AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_DEPLOYMENT values

docker compose up --build
```

Open **http://localhost:8000** in your browser.

> All six services start automatically with a single command.
> No manual database setup or seeding required.

---

## Demo Credentials

| Username | Password | Role | Permissions |
|----------|----------|------|-------------|
| salesuser | sales123 | sales_user | Read customers and issues |
| supportuser | support123 | support_user | Read + create next actions |
| adminuser | admin123 | admin | Full access + system logs |

---

## Environment Variables

Create a `.env` file in the project root:

```env
# PostgreSQL
POSTGRES_USER=acme_user
POSTGRES_PASSWORD=acme_pass
POSTGRES_DB=acme_db
DATABASE_URL=postgresql+asyncpg://acme_user:acme_pass@postgres:5432/acme_db

# Redis
REDIS_URL=redis://redis:6379

# Keycloak
KEYCLOAK_URL=http://keycloak:8080
REALM=acme

# Azure OpenAI
AZURE_OPENAI_API_KEY=your_key_here
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT=gpt-4.1-mini
AZURE_OPENAI_API_VERSION=2024-02-01

# Application
APP_ENV=development
LOG_LEVEL=INFO

# OpenTelemetry
OTEL_ENDPOINT=http://jaeger:4317
```

> `.env` is listed in `.gitignore` and is never committed to version control.

---

## Architecture

User Browser
│
│  HTTP + JWT Bearer Token
▼
┌────────────────────────────────────────────────────────────────┐
│  Docker Compose Environment                                    │
│                                                                │
│  ┌─────────────────┐      ┌─────────────────┐                 │
│  │   FastAPI App   │─────▶│    Keycloak     │                 │
│  │   Port: 8000    │◀─────│   Port: 8080    │                 │
│  │                 │      └─────────────────┘                 │
│  │  auth.py        │                                          │
│  │  routes.py      │─────▶ Azure OpenAI (GPT-4.1-mini)        │
│  │  agent.py       │       Function Calling / Tool Selection   │
│  │  skills.py      │                                          │
│  │  observability  │─────▶ Jaeger (:16686)                    │
│  └────────┬────────┘       OpenTelemetry Traces               │
│           │                                                    │
│     ┌─────┼──────────────────────┐                            │
│     ▼     ▼                      ▼                            │
│  ┌──────┐ ┌───────────┐ ┌──────────────┐                      │
│  │Redis │ │PostgreSQL │ │  MCP Server  │                      │
│  │:6379 │ │  :5432    │ │   :8001      │                      │
│  │      │ │           │ │              │                      │
│  │Cache │ │customers  │ │ Tools as     │                      │
│  │Session│ │issues     │ │ Protocol     │                      │
│  │Logs  │ │next_actions│ │              │                      │
│  └──────┘ └───────────┘ └──────────────┘                      │
└────────────────────────────────────────────────────────────────┘

See `docs/architecture.png` for the full system diagram.

---

## Project Structure

acme-assistant/
├── docker-compose.yml        # All six services
├── .env                      # Secrets — not committed
├── .env.example              # Environment template
├── seed.sql                  # PostgreSQL sample data
├── README.md                 # This file
├── AI-USAGE.md               # AI tool usage notes
│
├── app/
│   ├── main.py               # FastAPI entry point
│   ├── auth.py               # Keycloak JWT + RBAC
│   ├── agent.py              # LLM agent
│   ├── skills.py             # Reusable AI skills
│   ├── database.py           # SQLAlchemy models
│   ├── routes.py             # API endpoints
│   ├── observability.py      # OpenTelemetry tracing
│   ├── Dockerfile
│   ├── requirements.txt
│   └── static/
│       └── index.html        # Chat UI
│
├── mcp/
│   ├── mcp_server.py      # MCP server — all tool logic lives here
│   │                      # HTTP interface + stdio MCP protocol
│   │                      # Agent calls tools via HTTP
│   ├── Dockerfile
│   └── requirements.txt
│
├── eval/
│   ├── eval.py               # 10-test eval suite
│   ├── eval_results.json     # Results
│   └── EVAL-COMMENTARY.md    # Commentary
│
├── docs/
│   ├── architecture.html     # Diagram source
│   └── architecture.png      # Diagram image
│
└── keycloak/
    └── realm.json            # Realm + roles + users

---

## Components

### 1. LLM Agent (`agent.py`)

The agent uses Azure GPT-4.1-mini with OpenAI function calling. It dynamically selects which tools to invoke based on the user's natural language query — no hard-coded routing. This satisfies the agentic tool selection requirement from Section 4.1.

**Agentic loop:**
1. Receives user query with role context from JWT
2. Calls Azure OpenAI with tool definitions
3. Executes tool(s) selected by the model
4. Feeds results back into context
5. Repeats until a final text answer is produced

**Tools available:**

| Tool | Description |
|------|-------------|
| `get_customer_profile` | Fetch customer by name or company name |
| `get_open_issues` | All open issues for a customer, priority ordered |
| `summarise_issue` | AI summary of issue history via Issue Summary Skill |
| `create_next_action` | Persist a recommended action (support/admin only) |
| `escalation_summary` | Run the Escalation Summary Skill |
| `list_all_customers` | Return all customers in the system |
| `get_request_logs` | View recent API logs (admin only) |

**Performance optimisations:**
- Query cache: repeated questions return instantly from Redis (5-min TTL)
- Customer cache: PostgreSQL lookups cached per customer name (10-min TTL)
- `temperature=0.1` and `max_tokens=500` for consistent, fast responses

---

### 2. MCP Server (`mcp/mcp_server.py`)

Implements a standalone Model Context Protocol server exposing Acme-specific tools. This satisfies Section 4.2 of the assessment.

**Why MCP is useful here:**

**(a) Separation of concerns** — Tool definitions live in a standalone service, independently of the agent code. New tools can be added to the MCP server without modifying `agent.py`.

**(b) Reusability** — Any MCP-compatible agent or framework can connect to this server and use the same tools, regardless of which LLM or orchestration layer is used.

The MCP server runs as a separate Docker container and connects directly to PostgreSQL via asyncpg.

---

### 3. Skills (`skills.py`)

Two reusable AI skills implement the Skill pattern from Section 4.3:

**Escalation Summary Skill**

| | |
|---|---|
| Input | Customer name, open issues, recent activity |
| Output | Executive summary (2-3 sentences) |
| Output | Risk level: Low / Medium / High / Critical |
| Output | Single most important recommended action |
| Output | List of missing information gaps |

**Issue Summary Skill**

| | |
|---|---|
| Input | Issue title, description, chronological updates |
| Output | 2-3 sentence professional summary |

Both skills use structured prompts with JSON output schemas, low temperature for consistency, and fallback handling for malformed API responses. They are clearly distinct from one-off prompts — they have defined input/output contracts and are invoked from multiple places in the codebase.

---

### 4. Authentication (`auth.py`)

Keycloak is running within the Docker Compose environment as required by Section 4.4. It is not mocked.

**Flow:**
1. User submits credentials to Keycloak via the chat UI
2. Keycloak returns a signed RS256 JWT token
3. All API requests include the token in the Authorization header
4. `auth.py` fetches Keycloak's public keys (JWKS), verifies the signature, and extracts roles

**RBAC enforcement:**

| Role | View Data | Create Actions | View Logs |
|------|-----------|---------------|-----------|
| sales_user | ✅ | ❌ | ❌ |
| support_user | ✅ | ✅ | ❌ |
| admin | ✅ | ✅ | ✅ |

Role checks are enforced at two levels:
1. **FastAPI dependencies** in `routes.py` — block at the HTTP layer
2. **Agent-level RBAC gates** in `agent.py` — block before tool execution

---

### 5. PostgreSQL (`seed.sql`, `database.py`)

Five tables as required by Section 4.6:

| Table | Purpose |
|-------|---------|
| `customers` | Customer profiles with contact and company details |
| `issues` | Support tickets with status and priority |
| `issue_updates` | Chronological update history per issue |
| `next_actions` | Recommended actions created by support/admin |
| `user_roles` | Local mirror of Keycloak role assignments |

The database is seeded automatically on first startup from `seed.sql`, providing five customers, nine issues across multiple priorities, thirteen issue updates, and four next actions — sufficient to demonstrate all agent capabilities.

Customer search supports both personal name and company name via a case-insensitive SQL OR query, so "James Miller" and "Acme Corp" return the same record.

---

### 6. Redis

Redis is used for four purposes as required by Section 4.7:

| Purpose | TTL | Key Pattern |
|---------|-----|-------------|
| Conversation session memory | 1 hour | `session:{uuid}` |
| Customer profile cache | 10 minutes | `customer:{name}` |
| Query response cache | 5 minutes | `query_cache:{user}:{query}` |
| Request logs | 100 entries max | `request_logs` (list) |

**Redis vs PostgreSQL rationale:**

Redis is used for data that is short-lived, high-frequency, and does not require durability. Conversation history expires naturally and does not need to survive a server restart. Customer lookups benefit from sub-millisecond cache reads versus the ~5ms PostgreSQL roundtrip. PostgreSQL is used for all data that must persist permanently — customer records, issues, actions, and audit trails.

---

### 7. Observability (`observability.py`)

Two levels of observability are implemented:

**Level 1 — Redis Request Logs (Required)**

Every API request is logged to Redis with:
- Endpoint called
- Authenticated username
- HTTP status (success / error)
- Latency in milliseconds
- Agent tools used

Logs are viewable by admin users at `GET /api/v1/logs` or by asking the agent "show me the request logs".

**Level 2 — OpenTelemetry Distributed Tracing (Bonus)**

Full OpenTelemetry tracing is implemented with Jaeger as the backend. Every request produces a detailed trace showing the full execution timeline.

What is traced:
- Every HTTP request — automatically via FastAPI instrumentation
- Each Azure OpenAI API call — duration per iteration
- Every tool execution — name, arguments, success/failure
- Redis cache checks — hit or miss recorded
- RBAC denials — recorded as span attributes

Access traces at: **http://localhost:16686**

Example trace for "Show me all customers":

HTTP POST /api/v1/query          2.17s
├── agent.run                  2.15s
│   ├── redis.cache_check      9ms   (miss)
│   ├── azure_openai.call      692ms (iteration 1 — tool selection)
│   ├── tool.list_all_customers 21ms  (PostgreSQL query)
│   └── azure_openai.call      1.42s (iteration 2 — final answer)
└── HTTP Response 200

---

## API Reference

| Method | Endpoint | Auth Required | Description |
|--------|----------|---------------|-------------|
| GET | `/health` | None | Service health check |
| GET | `/` | None | Chat UI |
| POST | `/api/v1/query` | Any role | Submit agent query |
| GET | `/api/v1/customers` | Any role | List all customers |
| GET | `/api/v1/customers/{id}/issues` | Any role | Customer issues |
| POST | `/api/v1/next-actions` | Support / Admin | Create next action |
| GET | `/api/v1/logs` | Admin only | Request logs |
| GET | `/docs` | None | Swagger UI |

---

## Evaluation

```bash
# Ensure docker compose is running
docker compose up -d

# Run the evaluation suite
cd eval
python eval.py

# Results saved to eval/eval_results.json
```

**Result: 10/10 tests passed (100%)**

| Test | Description | Result |
|------|-------------|--------|
| 1 | Customer profile lookup by company name | PASS |
| 2 | Open issues retrieval | PASS |
| 3 | Issue history summarisation | PASS |
| 4 | Escalation Summary Skill | PASS |
| 5 | Admin creates next action | PASS |
| 6 | Sales RBAC enforcement | PASS |
| 7 | Critical issue detection | PASS |
| 8 | Multi-step tool calling | PASS |
| 9 | Full customer list | PASS |
| 10 | Unknown customer handling | PASS |

See `eval/EVAL-COMMENTARY.md` for detailed commentary on each test.

---

## Performance

| Query Type | Cold (first call) | Warm (cached) |
|------------|------------------|---------------|
| Customer profile | ~2,000ms | <5ms |
| Open issues | ~2,000ms | <5ms |
| Issue summary | ~3,000ms | <5ms |
| Escalation summary | ~4,000ms | <5ms |

Latency is dominated by the Azure OpenAI API call (~1,500ms). PostgreSQL queries and Redis operations each add less than 10ms. Cache hits return sub-5ms responses by bypassing both.

OpenTelemetry traces in Jaeger provide per-span latency breakdown, making it straightforward to identify bottlenecks in production.

---

## Trade-offs and Decisions


### MCP Integration
The agent calls all tools exclusively through the MCP server
HTTP interface via `call_mcp_tool()`. There is no direct
database access in `agent.py` — all tool logic lives in
`mcp_server.py`.

The MCP server exposes both an HTTP interface (used by the
agent) and the standard stdio MCP protocol (for MCP-compatible
clients).

In production, the MCP server would be deployed and versioned
independently of the agent, allowing tool improvements without
agent redeployment.
### 2. MCP Server (`mcp/mcp_server.py`)

Implements a standalone Model Context Protocol server exposing
Acme-specific tools. This satisfies Section 4.2 of the assessment.

**Why MCP is useful here:**

**(a) Separation of concerns** — Tool definitions and database
logic live entirely in `mcp_server.py`. The agent in `agent.py`
calls tools exclusively through the MCP server HTTP interface
via `call_mcp_tool()` — no direct database access from the agent.

**(b) Reusability** — Any MCP-compatible agent or framework
can connect to this server and use the same tools, regardless
of which LLM or orchestration layer is used.

**(c) Industry standard** — MCP is an emerging standard for
AI tool integration adopted by major AI providers.

**How the agent connects to MCP:**

User Query
↓
Agent (agent.py) — reasons about which tool to call
↓ HTTP POST /tools/{tool_name}
MCP Server (:8001) — executes the tool
↓ SQL query
PostgreSQL (:5432) — returns dat

The MCP server exposes two interfaces:
1. **HTTP REST API on port 8001** — used by the agent
2. **stdio MCP protocol** — standard MCP transport for
   MCP-compatible clients

**Tools exposed (7 total):**

| Tool | Description |
|------|-------------|
| `get_customer_profile` | Fetch customer by name or company |
| `get_open_issues` | Open issues for a customer |
| `summarise_issue` | Issue with full update history |
| `create_next_action` | Persist a new next action |
| `get_next_actions` | List actions for an issue |
| `list_all_customers` | All customers in system |
| `get_recent_updates` | Recent updates across all issues |


### CORS Policy
`allow_origins=["*"]` is used for local development and demo simplicity. In production this would be restricted to specific trusted domains via an environment variable, preventing unauthorised cross-origin API access.

### Redis Cache and Evaluation
The query cache improves production performance but can interfere with evaluation if identical queries are cached from prior UI interactions. The eval script flushes Redis before each test and appends unique timestamps to cache-sensitive queries to guarantee fresh tool calls. This is a standard evaluation technique and does not affect production behaviour.

### No Frontend Framework
The UI is plain HTML/CSS/JavaScript without React or Vue. This was intentional — it eliminates build tooling complexity, makes the demo straightforward to run, and keeps the focus on the backend agentic architecture rather than frontend implementation.

### Azure OpenAI vs Local LLM
Azure GPT-4.1-mini was chosen because it was already provisioned in the assessment environment. In production, model selection would depend on data residency requirements, cost constraints, and latency targets.

### OpenTelemetry
Full OpenTelemetry integration is implemented with Jaeger as the trace backend. All HTTP requests, Azure OpenAI calls, tool executions, and Redis cache checks are traced with custom span attributes. This satisfies the bonus observability requirement from Section 4.8.

In production, the Jaeger endpoint would be configured via environment variable to point to a managed tracing backend such as Grafana Tempo or AWS X-Ray.

---

## AI Tool Usage

This project was developed with Claude (Anthropic) as the primary AI coding assistant. See `AI-USAGE.md` for a full account of:
- What was delegated to AI tools and why
- How AI-generated code was reviewed and validated
- Errors and hallucinations identified and corrected
- What would not be trusted to AI without human oversight

---

## Services and Ports

| Service | Port | URL |
|---------|------|-----|
| FastAPI + Chat UI | 8000 | http://localhost:8000 |
| Keycloak Admin | 8080 | http://localhost:8080 |
| PostgreSQL | 5432 | localhost:5432 |
| Redis | 6379 | localhost:6379 |
| MCP Server | 8001 | localhost:8001 |
| Jaeger Tracing UI | 16686 | http://localhost:16686 |
