# ============================================================
# skills.py — Reusable AI Skills
# ============================================================
# This module implements the Skill pattern as required by
# Section 4.3 of the assessment brief.
#
# What is a Skill?
#   A Skill is a structured, reusable AI workflow that can
#   be invoked by the agent. Unlike a one-off prompt, a
#   Skill has a defined input schema, a consistent output
#   schema, and can be called from multiple places in the
#   codebase without duplication.
#
# Skills implemented:
#   1. escalation_summary_skill — generates a structured
#      risk assessment for a customer situation, returning
#      executive summary, risk level, next action, and
#      missing information list.
#
#   2. issue_summary_skill — generates a concise natural
#      language summary of a specific issue and its history
#      of updates.
#
# Design principles:
#   - Each skill has a clearly defined input and output
#   - Skills call Azure OpenAI with low temperature (0.1)
#     for consistent, deterministic outputs
#   - Structured outputs (JSON) are validated before return
#   - Fallback values are returned on parse failures to
#     prevent the agent from crashing on skill errors
#   - max_tokens is capped to keep responses fast and focused
# ============================================================

from openai import AzureOpenAI
import os
import json


# ─── Azure OpenAI Client ──────────────────────────────────────
# Shared client instance used by both skills in this module.
# Credentials are loaded from environment variables injected
# by Docker Compose from the .env file at runtime.
client = AzureOpenAI(
    api_key=os.getenv("AZURE_OPENAI_API_KEY"),
    azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
    api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
)

# Azure OpenAI deployment name configured in Azure portal
DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1-mini")


# ============================================================
# SKILL 1: Customer Escalation Summary
# ============================================================

async def escalation_summary_skill(
    customer_name:   str,
    issues:          list,
    recent_updates:  list
) -> dict:
    """
    Generate a structured escalation assessment for a customer.

    This is the primary Skill in the Acme Assistant, satisfying
    the assessment requirement from Section 4.3. It takes
    customer context as input and returns a consistent
    structured output that the agent formats for the user.

    This Skill is distinct from a one-off prompt because:
      - It has a fixed, validated output schema (4 fields)
      - It is invoked via the escalation_summary agent tool
      - It can be called from multiple workflows
      - It includes fallback handling for malformed outputs

    Input:
        customer_name:   Name of the customer being assessed
        issues:          List of open issue dicts from the DB
        recent_updates:  List of recent issue update dicts

    Output (dict):
        summary:     2-3 sentence executive summary
        risk_level:  One of: Low / Medium / High / Critical
        next_action: Single most important recommended action
        missing_info: List of information gaps identified

    Fallback behaviour:
        If the Azure API returns malformed JSON, a fallback
        dict is returned with risk_level="High" and a manual
        review recommendation. This prevents the agent from
        crashing when the skill encounters an API error.

    Args:
        customer_name:  Customer name string
        issues:         List of open issue dicts
        recent_updates: List of recent update dicts

    Returns:
        dict: Structured escalation assessment
    """

    # Format open issues into a numbered list for the prompt
    issues_text = ""
    for i, issue in enumerate(issues, 1):
        issues_text += (
            f"{i}. {issue['title']}\n"
            f"   Status:   {issue['status']}\n"
            f"   Priority: {issue['priority']}\n"
            f"   Details:  {issue['description']}\n\n"
        )

    # Format recent updates into a chronological activity log
    updates_text = ""
    for update in recent_updates:
        updates_text += (
            f"- [{update['created_at']}] "
            f"{update['updated_by']}: "
            f"{update['note']}\n"
        )

    # Structured prompt that enforces JSON output format.
    # The explicit JSON schema in the prompt ensures the model
    # returns parseable output with all required fields.
    prompt = f"""
You are an expert customer success manager at Acme Operations.
Analyze the following customer situation and provide a structured assessment.

CUSTOMER: {customer_name}

OPEN ISSUES:
{issues_text if issues_text else "No open issues found."}

RECENT ACTIVITY:
{updates_text if updates_text else "No recent updates found."}

Respond ONLY with a valid JSON object in this exact format:
{{
    "summary": "2-3 sentence executive summary of the customer situation",
    "risk_level": "one of: Low / Medium / High / Critical",
    "next_action": "single most important action to take right now",
    "missing_info": ["list", "of", "missing", "information", "items"]
}}
"""

    try:
        # Call Azure OpenAI with strict settings for consistency
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {
                    "role":    "system",
                    "content": "You are a customer success expert. Always respond with valid JSON only."
                },
                {
                    "role":    "user",
                    "content": prompt
                }
            ],
            temperature=0.1,    # Low temperature for consistent structure
            max_tokens=300      # Capped to keep responses concise
        )

        # Extract and clean the response text
        raw = response.choices[0].message.content.strip()

        # Remove markdown code fences if the model added them
        raw = raw.replace("```json", "").replace("```", "").strip()

        # Parse the JSON response
        result = json.loads(raw)

        # Validate all required fields are present
        # Missing fields are filled with a safe default value
        for field in ["summary", "risk_level", "next_action", "missing_info"]:
            if field not in result:
                result[field] = "Not available"

        return result

    except json.JSONDecodeError:
        # Model returned text that could not be parsed as JSON
        # Return a structured fallback to prevent agent crashes
        return {
            "summary":      raw if raw else "Could not generate summary.",
            "risk_level":   "High",
            "next_action":  "Manual review required.",
            "missing_info": ["Structured data unavailable"]
        }

    except Exception as e:
        # Covers API errors, network issues, and unexpected failures
        return {
            "summary":      f"Skill error: {str(e)}",
            "risk_level":   "High",
            "next_action":  "Check system logs and retry.",
            "missing_info": ["System error occurred"]
        }


# ============================================================
# SKILL 2: Issue Summary
# ============================================================

async def issue_summary_skill(
    issue_title:       str,
    issue_description: str,
    updates:           list
) -> str:
    """
    Generate a concise AI summary of a specific issue.

    This Skill combines the issue description with its full
    chronological update history and passes them to Azure
    OpenAI for summarisation. The output is a 2-3 sentence
    natural language summary focused on current status.

    This Skill is invoked by the agent's summarise_issue tool
    in agent.py after fetching the issue and its updates from
    PostgreSQL. It is designed to be fast (max_tokens=150)
    and focused (temperature=0.1).

    Input:
        issue_title:       Short title of the issue
        issue_description: Full description of the problem
        updates:           List of update dicts with notes
                           and timestamps in chronological order

    Output:
        str: 2-3 sentence summary of the issue and its history.
             Returns an error string on failure — never raises.

    Args:
        issue_title:       Issue title string
        issue_description: Issue description string
        updates:           List of issue update dicts

    Returns:
        str: Natural language summary or error message
    """

    # Format update history as a chronological activity log
    updates_text = ""
    for update in updates:
        updates_text += (
            f"- [{update['created_at']}] "
            f"{update['updated_by']}: "
            f"{update['note']}\n"
        )

    # Focused prompt requesting a brief, professional summary
    prompt = f"""
Summarize this customer issue and its history in 2-3 sentences.

ISSUE: {issue_title}
DESCRIPTION: {issue_description}

HISTORY:
{updates_text if updates_text else "No updates recorded yet."}

Be concise and professional. Focus on current status and what has been done.
"""

    try:
        # Call Azure OpenAI with tight token limit for speed
        response = client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[
                {
                    "role":    "system",
                    "content": "You are a technical support expert. Be very concise."
                },
                {
                    "role":    "user",
                    "content": prompt
                }
            ],
            temperature=0.1,    # Low temperature for factual consistency
            max_tokens=150      # Short limit — summary should be brief
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        # Return error string rather than raising to prevent
        # the calling agent tool from crashing on skill failure
        return f"Could not summarize issue: {str(e)}"
 
 