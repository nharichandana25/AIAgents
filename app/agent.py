import os
import re
import datetime
import json
from pydantic import BaseModel, Field
from google.adk.agents import LlmAgent
from google.adk.tools import AgentTool
from google.adk.workflow import Workflow, START, node, FunctionNode
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.adk.apps import App
from google.genai import types

import sys
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters
from app.config import config

# Initialize local MCP toolset running our mcp_server module
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
        ),
    ),
)

# --- SCHEMAS ---

class FinanceNavigatorInput(BaseModel):
    user_message: str

class FinanceNavigatorState(BaseModel):
    user_message: str = ""
    orchestrator_response: str = ""
    action_required: bool = False
    action_type: str = ""
    action_details: str = ""
    action_approved: bool = False
    pii_violations: list[str] = Field(default_factory=list)
    security_violations: list[str] = Field(default_factory=list)

class OrchestratorResponse(BaseModel):
    response_text: str = Field(description="The natural language response to the user explaining your answer or the action you are about to take.")
    action_required: bool = Field(description="Set to true if the user requested a cancellation of a subscription or setting/updating a savings goal that requires approval.")
    action_type: str = Field(description="The type of action: 'cancel_subscription', 'set_savings_goal', or '' if none.")
    action_details: str = Field(description="Details of the action to take (e.g. 'Cancel Netflix subscription' or 'Set savings goal of $500').")

# --- SPECIALIZED LLM AGENTS ---

subscription_tracker = LlmAgent(
    name="subscription_tracker",
    model=config.model,
    instruction=(
        "You are a specialized Subscription Tracker agent. Your job is to help users manage their active "
        "subscription services. Here is the user's current subscription profile:\n"
        "- Netflix: $15.99/mo (Active, last used 3 months ago)\n"
        "- Spotify Family: $16.99/mo (Active, used daily)\n"
        "- Gym Membership: $49.99/mo (Active, used weekly)\n"
        "- Adobe Creative Cloud: $54.99/mo (Active, used monthly)\n"
        "\n"
        "You can list active subscriptions, identify unused/wasted ones, check monthly costs, "
        "or help request the cancellation of a subscription. Always be helpful and precise. "
        "Do NOT mention any mock profile data other than these four subscriptions unless asked.\n"
        "\n"
        "You have access to MCP tools. Use them to lookup standard subscription prices or check unused status."
    ),
    tools=[mcp_toolset],
)

savings_planner = LlmAgent(
    name="savings_planner",
    model=config.model,
    instruction=(
        "You are a specialized Savings Planner agent. Your job is to help users budget, save money, "
        "and set financial goals. Here is the user's current financial profile:\n"
        "- Monthly Income: $5,000\n"
        "- Fixed Expenses: $2,500\n"
        "- Discretionary Spending: $1,200\n"
        "- Current Savings Account Balance: $8,500\n"
        "- Active Savings Goal: $15,000 (Target date: Dec 2026)\n"
        "\n"
        "You can analyze income vs expenses, suggest savings options, and create budgeting plans. "
        "Always provide realistic and supportive financial advice.\n"
        "\n"
        "You have access to MCP tools. Use them to project savings growth over time."
    ),
    tools=[mcp_toolset],
)

# --- ORCHESTRATOR AGENT ---

orchestrator = LlmAgent(
    name="orchestrator",
    model=config.model,
    instruction=(
        "You are the Finance Navigator Orchestrator. You help users navigate their subscriptions and savings.\n"
        "You have access to specialized agents via tools:\n"
        "- Use the `subscription_tracker` tool to delegate requests about subscriptions (listing, tracking, canceling).\n"
        "- Use the `savings_planner` tool to delegate requests about savings goals, budgets, and financial planning.\n"
        "\n"
        "Your behavior rules:\n"
        "1. For any request about subscriptions, call `subscription_tracker`.\n"
        "2. For any request about budgeting, goals, or saving money, call `savings_planner`.\n"
        "3. If the user asks to cancel a subscription or set/change a savings goal, you MUST:\n"
        "   a. Set `action_required` to True in your output schema.\n"
        "   b. Set `action_type` to 'cancel_subscription' or 'set_savings_goal' as appropriate.\n"
        "   c. Provide clear details of what will be performed in `action_details`.\n"
        "   d. Explain what will happen in `response_text` (e.g., 'I will request to cancel your Netflix subscription. Let me ask for your approval first.').\n"
        "4. For general queries that don't change states, leave `action_required` as False and provide the answer in `response_text`."
    ),
    tools=[AgentTool(subscription_tracker), AgentTool(savings_planner)],
    output_schema=OrchestratorResponse,
)

# --- WORKFLOW FUNCTION NODES ---

def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    """Security node to check input for PII and injection."""
    text = ""
    if node_input and node_input.parts:
        text = "".join(p.text for p in node_input.parts if p.text)
    
    pii_violations = []
    security_violations = []
    scrubbed_text = text
    
    # 1. PII Scrubbing
    cc_pattern = r'\b(?:\d[ -]*?){13,16}\b'
    if re.search(cc_pattern, scrubbed_text):
        scrubbed_text = re.sub(cc_pattern, "[REDACTED_CC]", scrubbed_text)
        pii_violations.append("Credit Card Number")
        
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    if re.search(email_pattern, scrubbed_text):
        scrubbed_text = re.sub(email_pattern, "[REDACTED_EMAIL]", scrubbed_text)
        pii_violations.append("Email Address")
        
    ssn_pattern = r'\b\d{3}-\d{2}-\d{4}\b'
    if re.search(ssn_pattern, scrubbed_text):
        scrubbed_text = re.sub(ssn_pattern, "[REDACTED_SSN]", scrubbed_text)
        pii_violations.append("SSN")

    account_pattern = r'\b\d{8,12}\b'
    if re.search(account_pattern, scrubbed_text):
        scrubbed_text = re.sub(account_pattern, "[REDACTED_ACCOUNT]", scrubbed_text)
        pii_violations.append("Financial Account Number")

    ctx.state["pii_violations"] = pii_violations
    
    # 2. Prompt Injection Detection
    injection_keywords = [
        "ignore previous instructions", 
        "system prompt", 
        "override settings", 
        "jailbreak", 
        "bypass security",
        "pretend you are",
        "you must now",
        "developer mode"
    ]
    
    detected_injection = False
    for kw in injection_keywords:
        if kw in text.lower():
            security_violations.append(f"Prompt injection: '{kw}'")
            detected_injection = True
            
    # 3. Domain Specific Rule: High-Risk Transaction Limit ($5,000)
    high_risk_pattern = r'(?:withdraw|transfer|send|savings goal)\s+\$?(?:[5-9]\d{3}|\d{5,})'
    if re.search(high_risk_pattern, text.lower()):
        security_violations.append("Exceeded single-transaction limit ($5,000)")
        detected_injection = True
        
    ctx.state["security_violations"] = security_violations
    ctx.state["user_message"] = scrubbed_text
    
    # 4. JSON Audit Log
    audit_log = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event": "security_checkpoint_evaluation",
        "raw_text_length": len(text),
        "pii_redacted": len(pii_violations) > 0,
        "pii_violations": pii_violations,
        "security_violations": security_violations,
        "decision": "BLOCK" if detected_injection else ("ALLOW_WITH_REDACTION" if pii_violations else "ALLOW")
    }
    
    severity = "INFO"
    if detected_injection:
        severity = "CRITICAL"
    elif pii_violations:
        severity = "WARNING"
        
    # Output the audit log using Python print to standard output
    print(f"AUDIT_LOG [{severity}] {json.dumps(audit_log)}", flush=True)
    
    if detected_injection:
        return Event(output="SECURITY ALERT: Request blocked by security policy.", route="SECURITY_EVENT")
        
    return Event(output=scrubbed_text, route="__DEFAULT__")

def security_failure(ctx: Context, node_input: str) -> Event:
    """Security failure terminal node (triggered if security fails)."""
    return Event(output="SECURITY ALERT: Request blocked by security policy.")

async def hitl_approval_gate(ctx: Context, node_input: dict):
    """Workflow node for human approval of state-changing transactions."""
    response_text = node_input.get("response_text", "")
    action_required = node_input.get("action_required", False)
    action_type = node_input.get("action_type", "")
    action_details = node_input.get("action_details", "")
    
    ctx.state["orchestrator_response"] = response_text
    ctx.state["action_required"] = action_required
    ctx.state["action_type"] = action_type
    ctx.state["action_details"] = action_details

    if action_required:
        # Check if we already received the user's response from resume
        if ctx.resume_inputs and "approve_action" in ctx.resume_inputs:
            user_decision = ctx.resume_inputs["approve_action"].strip().lower()
            if user_decision in ["yes", "y", "approve", "confirm"]:
                ctx.state["action_approved"] = True
                msg = (
                    f"✅ **Action Approved & Executed**\n\n"
                    f"Successfully performed: **{action_details}**\n\n"
                    f"Orchestrator notes: {response_text}"
                )
                yield Event(output=msg, state=ctx.state)
            else:
                ctx.state["action_approved"] = False
                msg = f"❌ **Action Denied**\n\nRequest for **{action_details}** was cancelled."
                yield Event(output=msg, state=ctx.state)
        else:
            # Pause workflow and ask the user for approval
            yield RequestInput(
                interrupt_id="approve_action",
                message=f"⚠️ **Approval Required**\n\nDo you want to proceed with: **{action_details}**? (Reply yes/no)"
            )
    else:
        yield Event(output=response_text, state=ctx.state)

def execution_or_final_response(ctx: Context, node_input: str):
    """Formats and prints final output to the user/UI."""
    # Emit content event for the web UI/playground rendering
    yield Event(content=types.Content(role="model", parts=[types.Part.from_text(text=node_input)]))
    # Final output
    yield Event(output=node_input)

# --- WORKFLOW INIT ---

# Define the workflow graph
root_agent = Workflow(
    name="finance_navigator_workflow",
    state_schema=FinanceNavigatorState,
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"SECURITY_EVENT": security_failure, "__DEFAULT__": orchestrator}),
        (orchestrator, hitl_approval_gate),
        (hitl_approval_gate, execution_or_final_response),
        (security_failure, execution_or_final_response),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)
