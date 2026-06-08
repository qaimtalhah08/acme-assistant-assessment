# ============================================================
# database.py — PostgreSQL Connection + SQLAlchemy Models
# ============================================================
# This module defines the database connection, session
# management, and ORM models for the Acme Assistant.
#
# Responsibilities:
#   1. Create and configure the async SQLAlchemy engine
#   2. Provide a session factory for database operations
#   3. Define ORM models that map to PostgreSQL tables
#   4. Expose a FastAPI dependency for session injection
#
# Database Design:
#   The schema mirrors the assessment requirement from
#   Section 4.6, with five tables covering customers,
#   issues, issue history, next actions, and user roles.
#
# Async Architecture:
#   All database operations use asyncpg as the driver and
#   SQLAlchemy's async session to avoid blocking the
#   FastAPI event loop during database queries.
#
# Connection:
#   The DATABASE_URL is injected from the .env file via
#   Docker Compose at runtime. The default value points to
#   the postgres service within the Docker network.
# ============================================================

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from datetime import datetime, timezone
import os


# ─── Database Connection URL ──────────────────────────────────
# Uses postgresql+asyncpg driver for non-blocking async queries.
# The hostname 'postgres' resolves to the PostgreSQL container
# within the Docker Compose network.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://acme_user:acme_pass@postgres:5432/acme_db"
)


# ─── Async Engine ─────────────────────────────────────────────
# The engine manages the connection pool and communicates with
# PostgreSQL. echo=True logs all SQL statements to stdout,
# which is useful for debugging during development.
# In production, echo=False would be set to reduce log noise.
engine = create_async_engine(DATABASE_URL, echo=True)


# ─── Session Factory ──────────────────────────────────────────
# AsyncSessionLocal is a factory that creates new AsyncSession
# instances on demand. expire_on_commit=False prevents
# SQLAlchemy from expiring loaded objects after a commit,
# which would cause errors when accessing attributes after
# the session has been committed and closed.
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)


# ─── Declarative Base ─────────────────────────────────────────
# All ORM model classes inherit from Base. SQLAlchemy uses this
# to track all mapped tables and generate schema metadata.
class Base(DeclarativeBase):
    pass


# ============================================================
# ORM MODELS
# ============================================================
# Each class below maps to one PostgreSQL table. The schema
# is defined by the Column declarations, which SQLAlchemy
# uses to construct and validate SQL queries at runtime.
#
# All tables include a created_at timestamp with UTC timezone
# awareness for accurate audit trail logging.
# ============================================================


# ─── Customer Model ───────────────────────────────────────────
class Customer(Base):
    """
    Represents an Acme Operations customer account.

    Customers are the primary entity in the system. Each
    customer record holds contact details and company
    information. Issues are linked to customers via the
    customer_id foreign key in the Issue model.

    Search behaviour:
        The agent searches customers by both the personal
        name field and the company field using an OR query,
        so "James Miller" and "Acme Corp" return the same
        record. See agent.py tool_get_customer() for details.

    Table: customers
    """
    __tablename__ = "customers"

    # Primary key — auto-incremented by PostgreSQL
    id = Column(Integer, primary_key=True)

    # Customer's full name — required, used for personal search
    name = Column(String(100), nullable=False)

    # Contact details — optional but included in seed data
    email = Column(String(100))
    phone = Column(String(20))

    # Company name — used for company-name search in agent
    company = Column(String(100))

    # Geographic location — informational
    country = Column(String(50))

    # Record creation timestamp — UTC timezone aware
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# ─── Issue Model ──────────────────────────────────────────────
class Issue(Base):
    """
    Represents a support issue or ticket for a customer.

    Issues track problems reported by or on behalf of customers.
    Each issue has a status (open/in_progress/closed) and a
    priority (critical/high/medium/low) that the agent uses
    to surface the most urgent problems first.

    The agent's get_open_issues tool filters to exclude
    closed issues and orders results by priority so critical
    issues appear at the top of agent responses.

    Table: issues
    """
    __tablename__ = "issues"

    # Primary key — used by agent to reference specific issues
    id = Column(Integer, primary_key=True)

    # Foreign key linking this issue to its customer
    customer_id = Column(Integer, ForeignKey("customers.id"))

    # Short description of the problem — shown in issue lists
    title = Column(String(200), nullable=False)

    # Detailed description — used in summarisation prompts
    description = Column(Text)

    # Current state: "open", "in_progress", or "closed"
    # Agent filters to exclude "closed" when showing open issues
    status = Column(String(20), default="open")

    # Urgency level: "critical", "high", "medium", or "low"
    # Used for ordering in get_open_issues tool results
    priority = Column(String(20), default="medium")

    # Timestamps for audit trail
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# ─── Issue Update Model ───────────────────────────────────────
class IssueUpdate(Base):
    """
    Represents a single update or note added to an issue.

    Issue updates form the chronological history of an issue,
    recording who did what and when. The Issue Summary Skill
    in skills.py uses this history to generate AI-powered
    summaries of issue progress and current status.

    Each update records:
      - Which issue it belongs to (issue_id)
      - Who made the update (updated_by — email or username)
      - What was noted or done (note — free text)
      - When it was recorded (created_at — UTC timestamp)

    Table: issue_updates
    """
    __tablename__ = "issue_updates"

    # Primary key
    id = Column(Integer, primary_key=True)

    # Foreign key linking this update to its parent issue
    issue_id = Column(Integer, ForeignKey("issues.id"))

    # Who added this update — typically an email address
    updated_by = Column(String(100))

    # The actual update content — passed to the summary skill
    note = Column(Text)

    # When this update was recorded — used for ordering
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# ─── Next Action Model ────────────────────────────────────────
class NextAction(Base):
    """
    Represents a recommended next action for a specific issue.

    Next actions are created by support_user or admin roles
    (enforced by RBAC in agent.py). They represent the
    recommended steps to resolve or progress an issue.

    The created_by field is always set from the authenticated
    JWT token in agent.py, not from user input, preventing
    unauthorised attribution.

    Table: next_actions
    """
    __tablename__ = "next_actions"

    # Primary key — returned to the agent after creation
    id = Column(Integer, primary_key=True)

    # Foreign key linking this action to its issue
    issue_id = Column(Integer, ForeignKey("issues.id"))

    # Description of the recommended action — free text
    action_text = Column(Text, nullable=False)

    # Username from JWT token — set by agent.py, not user input
    created_by = Column(String(100))

    # Current state of the action: "pending" or "done"
    status = Column(String(20), default="pending")

    # When this action was created
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# ─── User Role Model ──────────────────────────────────────────
class UserRole(Base):
    """
    Local record of Keycloak users and their assigned roles.

    This table provides a local mirror of user-role assignments
    for auditing and reporting purposes. The authoritative
    source of roles is always the Keycloak JWT token — this
    table is informational and not used for access control.

    Role values correspond to Keycloak realm roles:
      sales_user   — read-only access
      support_user — read + create next actions
      admin        — full access including system logs

    Table: user_roles
    """
    __tablename__ = "user_roles"

    # Primary key
    id = Column(Integer, primary_key=True)

    # Keycloak username — e.g. "salesuser", "adminuser"
    username = Column(String(100), nullable=False)

    # User email address from Keycloak
    email = Column(String(100))

    # Assigned role name — mirrors Keycloak realm role
    role = Column(String(50))

    # When this record was created
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )


# ─── Database Session Dependency ──────────────────────────────
async def get_db():
    """
    FastAPI dependency that provides a database session per request.

    This async context manager is injected into route handlers
    via FastAPI's Depends() mechanism. It ensures that:
      1. Each request gets its own isolated database session
      2. Successful requests are automatically committed
      3. Failed requests trigger an automatic rollback
      4. Sessions are always closed after the request completes

    Usage in routes:
        @router.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Customer))

    Transaction behaviour:
      - yield: passes session control to the route handler
      - commit: called automatically on success
      - rollback: called automatically on any exception
      - close: always called in the finally block

    Yields:
        AsyncSession: Active SQLAlchemy async database session
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            # Commit all changes if the request completed successfully
            await session.commit()
        except Exception:
            # Roll back any partial changes if an error occurred
            await session.rollback()
            raise
        finally:
            # Always close the session to return it to the pool
            await session.close()
 
 