# AI Tool Usage Notes
## EY Applied AI Engineer Assessment

---

## Overview

This project was developed with assistance from Claude
(Anthropic) as the primary AI coding tool. This document
describes how AI tools were used, what was reviewed
manually, and what would not be trusted to AI without
human oversight in a client engagement.

---

## What Was Delegated to AI Tools

### Code Generation
- FastAPI route boilerplate (routes.py)
- SQLAlchemy model definitions (database.py)
- Docker Compose service configuration
- Redis cache integration patterns
- Keycloak JWT verification structure (auth.py)
- HTML/CSS for the chat UI (static/index.html)
- SQL seed data for representative sample records
- MCP server tool schema definitions

### Prompt Engineering
- System prompt structure for the LLM agent
- Escalation Summary Skill prompt template
- Issue Summary Skill prompt template
- Tool description strings for function calling

### Documentation
- README structure and content drafts
- Code comment drafts
- Architecture overview text

---

## How AI-Generated Code Was Reviewed and Validated

Every piece of AI-generated code was reviewed using the
following process:

1. **Read and understand** — every function was read line
   by line before being accepted into the codebase.

2. **Test against live services** — all routes were tested
   manually via the UI and via the eval script.

3. **Check security logic** — RBAC checks in auth.py and
   agent.py were reviewed carefully to ensure role
   enforcement could not be bypassed.

4. **Validate database queries** — all SQLAlchemy queries
   were checked for correctness, including the OR condition
   for name/company search.

5. **Run the evaluation suite** — the 10-test eval script
   was used to verify tool selection, grounding, RBAC, and
   latency across all user roles.

---

## Errors and Hallucinations Identified and Corrected

### 1. OpenAI Library Version Conflict
**Issue:** AI generated code using `openai==1.30.0` which
caused a `TypeError: unexpected keyword argument 'proxies'`
due to an httpx version incompatibility.

**Fix:** Pinned `openai==1.55.3` and `httpx==0.27.2` in
requirements.txt after identifying the root cause.

### 2. Keycloak Issuer URL Mismatch
**Issue:** AI used `keycloak:8080` (internal Docker hostname)
as the expected JWT issuer, but tokens issued to the browser
contain `localhost:8080` as the issuer claim.

**Fix:** Set `EXPECTED_ISSUER` to `http://localhost:8080/realms/acme`
to match the actual token issuer seen by the client.

### 3. Redis Cache Causing Eval False Failures
**Issue:** The query cache caused eval tests to return
cached responses with empty `tools_used` lists, producing
false FAIL results for tool selection checks.

**Fix:** Added cache-bypass logic for action and summarise
queries, and added timestamp suffixes to eval queries to
guarantee fresh agent responses during evaluation runs.

### 4. Customer Search by Company Name
**Issue:** Initial implementation only searched by personal
name, so queries like "Show issues for Acme Corp" failed.

**Fix:** Updated all customer search queries to use an OR
condition across both the `name` and `company` fields using
SQLAlchemy's `or_()` function.

### 5. MCP Server Import Error
**Issue:** AI suggested `from mcp.server.fastmcp import FastMCP`
which caused an import error in the installed mcp==1.0.0 package.

**Fix:** Replaced with the correct low-level MCP server API
using `from mcp.server import Server` and stdio transport.

---

## What Would Not Be Trusted to AI Without Human Oversight

In a real client engagement at EY, the following would
require human review and sign-off before deployment:

### Security-Critical Code
- JWT token verification logic (auth.py)
- RBAC enforcement — role checks before tool execution
- Any code that gates access to sensitive customer data
- Token expiry and refresh handling

**Reason:** AI tools can generate plausible-looking auth
code that contains subtle logic errors. A security review
by a qualified engineer is essential before production use.

### Database Schema Design
- Table relationships and foreign key constraints
- Index strategy for production query performance
- Data retention and audit trail requirements

**Reason:** Schema decisions have long-term consequences
that are difficult to reverse. Business requirements must
be validated with stakeholders before implementation.

### Production Infrastructure Configuration
- Docker secrets management (not .env files)
- Keycloak realm configuration for production
- Network security policies and service isolation

**Reason:** AI-generated configs often use development
defaults (e.g. `sslRequired: none`) that are unsafe in
production. Each setting requires deliberate human review.

### Prompt Engineering for Production Skills
- Escalation Summary Skill prompt in skills.py
- Any prompt that drives business decisions

**Reason:** LLM outputs for business-critical workflows
must be validated against real data with domain experts
before deployment. AI-drafted prompts are a starting point,
not a finished product.

---

## AI Tool Productivity Assessment

| Task | Time Without AI | Time With AI | Saving |
|------|----------------|--------------|--------|
| FastAPI boilerplate | 2 hours | 20 minutes | 83% |
| Docker Compose setup | 1 hour | 10 minutes | 83% |
| SQL seed data | 45 minutes | 5 minutes | 89% |
| Skill prompt drafting | 1 hour | 15 minutes | 75% |
| UI HTML/CSS | 3 hours | 30 minutes | 83% |

AI tools significantly accelerated development of
boilerplate and configuration code, freeing time for
architecture decisions, debugging, and testing.

---

## Conclusion

AI tools were used as a productive accelerator throughout
this assessment. All generated code was reviewed, tested,
and in several cases corrected before acceptance. The
engineering judgement applied to architecture decisions,
security logic, and debugging was human-led throughout.