# ============================================================
# observability.py — OpenTelemetry Distributed Tracing Setup
# ============================================================
# This module configures OpenTelemetry tracing for the Acme
# Assistant. It satisfies the bonus requirement from Section
# 4.8 of the assessment brief.
#
# What OpenTelemetry provides beyond basic Redis logging:
#   - Distributed traces showing the full request lifecycle
#   - Visual timeline of every step: auth → agent → DB → cache
#   - Automatic instrumentation of FastAPI and SQLAlchemy
#   - Exportable traces viewable in Jaeger UI
#
# Access traces at: http://localhost:16686
#
# Trace hierarchy for a typical agent query:
#   HTTP POST /api/v1/query
#     ├── JWT verification
#     ├── agent.run
#     │   ├── redis.cache_check
#     │   ├── azure_openai.tool_selection
#     │   ├── tool.get_open_issues
#     │   │   └── postgresql.query
#     │   └── azure_openai.final_response
#     └── redis.cache_save
# ============================================================

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter
)
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
import os


# Service name appears in Jaeger UI to identify this application
SERVICE_NAME = "acme-assistant"


def setup_tracing(app, engine=None):
    """
    Configure OpenTelemetry tracing and instrument the application.

    Sets up:
    1. TracerProvider with service name resource
    2. OTLP exporter sending traces to Jaeger (:4317)
    3. BatchSpanProcessor for efficient trace export
    4. FastAPI auto-instrumentation (all HTTP requests traced)
    5. SQLAlchemy auto-instrumentation (all DB queries traced)

    Args:
        app:    FastAPI application instance to instrument
        engine: SQLAlchemy async engine instance (optional)

    Returns:
        tracer: OpenTelemetry tracer for creating custom spans
    """

    # Define service resource — appears as service name in Jaeger
    resource = Resource.create({
        "service.name":    SERVICE_NAME,
        "service.version": "1.0.0",
        "deployment.environment": os.getenv("APP_ENV", "development")
    })

    # Create tracer provider with resource metadata
    provider = TracerProvider(resource=resource)

    # Configure OTLP exporter — sends traces to Jaeger container
    # Jaeger listens on port 4317 for OTLP gRPC protocol
    otlp_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_ENDPOINT", "http://jaeger:4317"),
        insecure=True  # No TLS needed for local development
    )

    # BatchSpanProcessor groups spans for efficient export
    # rather than sending each span individually
    provider.add_span_processor(
        BatchSpanProcessor(otlp_exporter)
    )

    # Register as the global tracer provider
    trace.set_tracer_provider(provider)

    # Auto-instrument FastAPI — every HTTP request gets a span
    # showing method, path, status code, and duration
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=provider
    )

    # Auto-instrument SQLAlchemy — every DB query gets a span
    # showing the SQL statement and execution time
    if engine:
        SQLAlchemyInstrumentor().instrument(
            engine=engine.sync_engine,
            tracer_provider=provider
        )

    # Return tracer for creating custom spans in agent.py
    return trace.get_tracer(SERVICE_NAME)


def get_tracer():
    """
    Get the global tracer instance for creating custom spans.

    Used in agent.py to wrap tool calls and LLM calls
    with custom trace spans for detailed visibility.

    Returns:
        tracer: OpenTelemetry tracer instance
    """
    return trace.get_tracer(SERVICE_NAME)
