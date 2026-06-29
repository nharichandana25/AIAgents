# Finance Navigator — Submission Write-Up

## Problem Statement

Managing personal finances is a daily struggle for millions of people. Most users have
no clear picture of what subscriptions they are paying for, which ones they stopped
using, or how small monthly savings decisions compound over time. There is no
intelligent assistant that handles subscription tracking, savings projections, and
state-changing actions (like cancellations) in one secure, guided conversation.

Finance Navigator solves this by combining multi-agent AI reasoning, real-time MCP
tool data, human-in-the-loop approval, and a security-first architecture — all powered
by Google ADK 2.0.

---

## Solution Architecture

```
User Message
     │
     ▼
security_checkpoint  ── SECURITY_EVENT ──► security_failure
     │ __DEFAULT__
     ▼
orchestrator  (LlmAgent — routes to sub-agents via AgentTool)
     ├──► subscription_tracker  (LlmAgent + MCP tools)
     └──► savings_planner       (LlmAgent + MCP tools)
     │
     ▼
hitl_approval_gate  (RequestInput — pauses for human yes/no)
     │
     ▼
execution_or_final_response  (terminal output node)

                    ┌───────────────────────────┐
                    │   MCP Server (stdio)       │
                    │  get_subscription_catalog  │
                    │  calculate_savings_proj    │
                    │  check_unused_subscriptions│
                    └───────────────────────────┘
```

---

## Concepts Used

| Concept | File | Description |
|---------|------|-------------|
| ADK Workflow | app/agent.py L257–268 | 2.0 graph with START node, RoutingMap conditional edges |
| LlmAgent | app/agent.py L54–117 | orchestrator + subscription_tracker + savings_planner |
| AgentTool | app/agent.py L115 | orchestrator delegates to sub-agents as tools |
| MCP Server | app/mcp_server.py | FastMCP server with 3 financial tools over stdio |
| Security Checkpoint | app/agent.py L121–208 | FunctionNode: PII scrub + injection detect + audit log |
| Agents CLI | Makefile, GEMINI.md | scaffold, playground, run targets |

---

## Security Design

### PII Scrubbing
Detects and redacts:
- Credit card numbers (13–16 digit patterns)
- Email addresses (standard RFC pattern)
- Social Security Numbers (NNN-NN-NNNN)
- Financial account numbers (8–12 digit strings)

All redactions are replaced with `[REDACTED_*]` tokens before any data
reaches an LLM, preventing accidental leakage of sensitive data into model
context or logs.

### Prompt Injection Detection
Scans for 8 injection keywords: "ignore previous instructions", "system prompt",
"override settings", "jailbreak", "bypass security", "pretend you are",
"you must now", "developer mode". Any match routes to the SECURITY_EVENT
branch and the request is blocked entirely.

### Domain-Specific Rule: Transaction Limit
Any request to withdraw, transfer, send, or set a savings goal above $5,000 in
a single message is flagged as a high-risk operation and blocked via the same
SECURITY_EVENT route. This prevents social engineering attacks that attempt to
automate large financial transactions.

### JSON Audit Log
Every request through the security checkpoint emits a structured JSON audit log
with: timestamp, event type, raw text length, PII violation list, security
violation list, and final decision (ALLOW / ALLOW_WITH_REDACTION / BLOCK).
Severity is tagged as INFO / WARNING / CRITICAL.

---

## MCP Server Design

File: `app/mcp_server.py` — FastMCP over stdio transport.

| Tool | Purpose |
|------|---------|
| `get_subscription_catalog` | Returns a catalog of popular subscription services and standard pricing tiers. Used by subscription_tracker to compare user costs to market rates. |
| `calculate_savings_projection` | Compound interest calculator: given balance, monthly deposit, APR, and duration returns projected final balance and interest earned. Used by savings_planner. |
| `check_unused_subscriptions` | Flags a service as CRITICAL (≥90 days), WARNING (≥30 days), or OK based on days since last login. Used by subscription_tracker to surface waste. |

Both `subscription_tracker` and `savings_planner` agents are wired to the MCP
toolset so they can independently call the tools relevant to their domain.

---

## HITL Flow

The `hitl_approval_gate` function node implements human-in-the-loop approval for
any state-changing action flagged by the orchestrator:

1. **Trigger**: orchestrator sets `action_required=True` in its output schema
   (e.g. cancel a subscription or change a savings goal).
2. **Pause**: the node emits a `RequestInput` event with `interrupt_id="approve_action"`,
   halting the workflow and showing the user an approval prompt.
3. **Resume**: the user replies "yes" or "no" in the playground UI. The workflow
   resumes with `ctx.resume_inputs["approve_action"]`.
4. **Decision**: "yes" → action confirmed and `action_approved=True`; "no" → action
   cancelled, user informed.

This ensures no irreversible financial action (cancellation, goal change) happens
without explicit human consent — a critical trust requirement for financial agents.

---

## Demo Walkthrough

### Test 1 — Subscription Lookup (happy path, MCP tool call)
```
Send: "What subscriptions am I paying for?"
Path: security_checkpoint (ALLOW) → orchestrator → subscription_tracker → MCP get_subscription_catalog
See:  Itemised list: Netflix $15.99, Spotify $16.99, Gym $49.99, Adobe $54.99
```

### Test 2 — Savings Projection (MCP calculation)
```
Send: "If I save $300/month at 4.5% interest for 12 months, what will I have?"
Path: security_checkpoint (ALLOW) → orchestrator → savings_planner → MCP calculate_savings_projection
See:  Projected final balance with total deposited and interest earned breakdown
```

### Test 3 — Cancel Subscription (HITL approval gate)
```
Send: "Cancel my Netflix subscription"
Path: security_checkpoint (ALLOW) → orchestrator (action_required=True) → hitl_approval_gate
See:  "Do you want to proceed with: Cancel Netflix subscription? (Reply yes/no)"
Then: Reply "yes" → "Action Approved & Executed"
```

---

## Impact / Value Statement

Finance Navigator addresses a real gap in personal financial awareness. It:

- **Saves money**: surfaces unused subscriptions costing users hundreds per year
- **Builds wealth**: provides compound savings projections to motivate goal-setting
- **Protects users**: PII scrubbing and injection detection prevent data leakage
  and adversarial misuse
- **Builds trust**: HITL approval gate ensures users remain in control of every
  state-changing action — no AI acts unilaterally on their finances

The agent demonstrates that production-grade AI financial tooling is achievable
with Google ADK 2.0 — multi-agent reasoning, secure data handling, and human
oversight working together in a single cohesive workflow.
