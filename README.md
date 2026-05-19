# Logs → Jira Ticket

Paste application logs into claude.ai → get a polished Jira ticket as a React artifact.

Built for the Arrive AI Academy hackathon.

## What this is

A prompt-engineered solution that turns raw logs (stack traces, error messages, request traces) into a structured, visually rendered Jira ticket. No API integration required — runs entirely inside claude.ai using Artifacts.

## How to use it

1. Open https://claude.ai
2. Open `claude_prompt.md` and copy its entire contents
3. Paste it as your first message in a new conversation
4. Claude will acknowledge and wait for logs
5. Paste a log sample from `sample_logs/` (or any real logs)
6. Claude generates a Jira-style ticket card as a React artifact

## Demo flow (for the presentation)

| Step | Action | What to show |
|------|--------|--------------|
| 1 | Paste master prompt | "Here's our system instructions" |
| 2 | Paste `oom_payment_service.log` | P0 ticket with `data-integrity` flag — escalated automatically |
| 3 | Paste `db_timeout_search.log` | P1 ticket with `connection-pool` label and fallback noted |
| 4 | Paste a live, never-seen log | Proves it's not memorized |

## Files

```
claude_prompt.md             The master prompt — paste into claude.ai
sample_logs/                 Real-world log samples for demo
  oom_payment_service.log    OOM with manual-reconciliation flag (P0 demo)
  db_timeout_search.log      Connection pool exhaustion (P1 demo)
```

## What makes this hackathon-worthy

- **Schema discipline** — the prompt enforces structured output (title, severity, component, repro_steps, labels, error_signature) so every ticket is consistent
- **Severity rubric** — explicit P0–P3 criteria, with auto-escalation for data-integrity issues
- **Assignee inference** — guesses team from package paths (`com.acme.payments.*` → `payments-team`)
- **Dedup-friendly** — every ticket includes an `error_signature` for matching duplicates
- **Polished output** — Jira/Linear-style React card with color-coded severity badges, not generic AI styling
- **Edge case handling** — multi-error log dumps produce multiple tickets; cryptic logs get `needs-triage` instead of a fabricated repro

## Stretch goals (if time allows)

- [ ] Add a "Dedup against existing tickets" mode — paste a list of open tickets, Claude checks if the new one is a duplicate
- [ ] Mode that produces direct Jira REST API JSON payload (drop into `curl -X POST` to actually create the ticket)
- [ ] CODEOWNERS-style file uploaded as project knowledge for real assignee mapping

## Note on the Python path

We initially scaffolded a Streamlit + Anthropic SDK app (`app.py`, `requirements.txt`) but pivoted to claude.ai Artifacts because Arrive's training only provides claude.ai access. The Python files remain in the repo as a record of the design — the system prompt logic transferred directly to `claude_prompt.md`.
