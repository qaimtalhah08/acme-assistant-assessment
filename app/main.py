# ============================================================
# main.py — FastAPI Application Entry Point
# ============================================================
# This is the top-level entry point for the Acme Assistant
# API server. It is responsible for:
#
#   1. Loading environment variables from the .env file
#   2. Creating and configuring the FastAPI application
#   3. Registering middleware (CORS)
#   4. Mounting the API router with versioned prefix
#   5. Serving the static chat UI at the root URL
#   6. Managing application startup and shutdown lifecycle
#   7. Setting up OpenTelemetry distributed tracing
#
# Server:
#   The application is served by Uvicorn (ASGI server),
#   configured in the Docker Compose command:
#   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
#
# URL Structure:
#   /                    → Chat UI (index.html)
#   /static/*            → Static assets
#   /api/v1/*            → All API endpoints
#   /api/v1/health       → Health check
#   /api/v1/query        → Agent query endpoint
#   /docs                → Auto-generated Swagger UI
#   http://localhost:16686 → Jaeger distributed tracing UI
# ============================================================

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from routes import router
from observability import setup_tracing  # OpenTelemetry setup
import os


# ─── Environment Variables ────────────────────────────────────
# Load variables from .env file into the process environment.
# This must be called before any os.getenv() calls elsewhere.
# In Docker, variables are also injected via env_file in
# docker-compose.yml, so load_dotenv() acts as a fallback
# for local development outside of Docker.
load_dotenv()


# ─── Application Lifespan ─────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown events.

    This async context manager replaces the deprecated
    @app.on_event("startup") and @app.on_event("shutdown")
    patterns. Code before yield runs on startup, code after
    yield runs on shutdown.

    Current behaviour:
      Startup:  Prints a startup message to confirm the
                application has initialised successfully.
                Also prints the Jaeger tracing URL.
      Shutdown: Prints a shutdown message for log clarity.

    Extension points:
      In production, startup could pre-warm the Redis cache
      with frequently accessed customer records, and shutdown
      could flush pending log entries or close connections.
    """
    print("Acme Assistant starting...")
    print("Distributed tracing available at: http://localhost:16686")
    yield
    print("Acme Assistant shutting down...")


# ─── FastAPI Application Instance ─────────────────────────────
# The FastAPI instance is the central object that ties together
# all routes, middleware, and lifecycle management.
#
# Metadata fields (title, description, version) appear in:
#   - The auto-generated Swagger UI at /docs
#   - The OpenAPI schema at /openapi.json
app = FastAPI(
    title="Acme Assistant API",
    description=(
        "Agentic Enterprise Assistant — EY Assessment\n\n"
        "Distributed tracing available at: "
        "http://localhost:16686 (Jaeger UI)"
    ),
    version="1.0.0",
    lifespan=lifespan
)


# ─── OpenTelemetry Tracing Setup ──────────────────────────────
# Initialise distributed tracing with Jaeger as the backend.
# This instruments FastAPI and SQLAlchemy automatically so
# every HTTP request and database query gets a trace span.
# Custom spans are added in agent.py for tool calls and
# Azure OpenAI calls for granular visibility.
#
# Traces are exported to Jaeger via OTLP gRPC on port 4317.
# View traces at: http://localhost:16686
tracer = setup_tracing(app)


# ─── CORS Middleware ──────────────────────────────────────────
# Cross-Origin Resource Sharing (CORS) headers allow the
# browser-based chat UI to make API calls to FastAPI even
# when they are served from different origins.
#
# Current configuration (allow_origins=["*"]) permits requests
# from any origin. This is appropriate for a local development
# and demo environment.
#
# Production trade-off:
#   In a production deployment, allow_origins should be
#   restricted to specific trusted domains (e.g. the frontend
#   application URL) to prevent cross-site request forgery.
#   This trade-off is documented in the README.

# allow_origins=["*"] allows all origins — suitable for demo.
# In production, replace "*" with specific domain:
# e.g. allow_origins=["https://acme-operations.com"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],    # All origins allowed — dev/demo only
    allow_methods=["*"],    # All HTTP methods allowed
    allow_headers=["*"],    # All request headers allowed
)


# ─── API Router ───────────────────────────────────────────────
# All API routes are defined in routes.py and registered here
# under the /api/v1 prefix. This versioning convention allows
# future API versions (/api/v2) to coexist without breaking
# existing integrations.
#
# Registered endpoints (defined in routes.py):
#   GET  /api/v1/health                  — Health check
#   POST /api/v1/query                   — Agent query
#   GET  /api/v1/customers               — List customers
#   GET  /api/v1/customers/{id}/issues   — Customer issues
#   POST /api/v1/next-actions            — Create next action
#   GET  /api/v1/logs                    — Request logs (admin)
app.include_router(router, prefix="/api/v1")


# ─── Static File Serving ──────────────────────────────────────
# Mount the static directory so the browser can load CSS,
# JavaScript, and other assets referenced by index.html.
# Files are served at /static/* URLs.
#
# Example: /static/logo.png serves app/static/logo.png
app.mount("/static", StaticFiles(directory="static"), name="static")


# ─── Root URL — Chat UI ───────────────────────────────────────
@app.get("/")
async def serve_ui():
    """
    Serve the chat UI HTML file at the root URL.

    When a user navigates to http://localhost:8000, this
    endpoint returns the index.html file from the static
    directory. The HTML file contains the complete chat
    interface including login, sidebar, and message display.

    The UI communicates with the agent via the POST
    /api/v1/query endpoint using fetch() API calls with
    the user's Keycloak JWT token in the Authorization header.

    Returns:
        FileResponse: The index.html file with appropriate
                      Content-Type: text/html headers
    """
    return FileResponse("static/index.html")
