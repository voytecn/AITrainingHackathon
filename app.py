import html
import os
from typing import Literal

import anthropic
import streamlit as st
import streamlit.components.v1 as components
from dotenv import load_dotenv
from pydantic import BaseModel, Field

load_dotenv()

MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """You are an expert SRE/on-call engineer. You analyze application logs and create high-quality Jira tickets.

Given a chunk of logs (stack traces, error messages, request traces), produce a single well-formed Jira ticket.

Severity guide:
- P0: production down, data loss, security breach, payment/auth completely broken
- P1: major feature broken, significant user impact, no workaround
- P2: minor feature broken, workaround exists, degraded experience
- P3: cosmetic, edge case, low impact

For the component, infer from package names, file paths, or service names in the logs (e.g. "auth", "payments", "checkout", "database", "api-gateway").

For repro_steps, reconstruct what happened from the logs — request IDs, user actions, timestamps. If you cannot reconstruct steps, write ["Unable to reconstruct from logs — investigate from error signature."].

For suggested_assignee, infer from CODEOWNERS-style hints in package paths (e.g. com.acme.payments.* -> "payments-team"). If unclear, leave as null.

The error_signature should be the single most diagnostic line — usually the deepest exception or root error message. Used for deduplication."""


class JiraTicket(BaseModel):
    title: str = Field(description="Concise, action-oriented title under 80 chars")
    severity: Literal["P0", "P1", "P2", "P3"]
    component: str = Field(description="System component or service affected")
    description: str = Field(description="Markdown summary of what's broken and why it matters")
    repro_steps: list[str] = Field(description="Ordered steps to reproduce, reconstructed from logs")
    suggested_assignee: str | None = Field(description="Team or person guess, or null")
    labels: list[str] = Field(description="3-6 tags like 'bug', 'production', 'payments', 'memory-leak'")
    error_signature: str = Field(description="The single most diagnostic error line for dedup")


@st.cache_resource
def get_client():
    return anthropic.Anthropic()


def analyze_logs(logs: str) -> JiraTicket:
    client = get_client()
    response = client.messages.parse(
        model=MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": f"Analyze these logs:\n\n```\n{logs}\n```"}],
        output_format=JiraTicket,
    )
    return response.parsed_output


SEVERITY_PALETTE = {
    "P0": {"bg": "#ffe5e5", "text": "#b3261e", "border": "#f5a3a3"},
    "P1": {"bg": "#fff1e0", "text": "#a45200", "border": "#f5c98a"},
    "P2": {"bg": "#e5f1ff", "text": "#1b4d8a", "border": "#a3c4f3"},
    "P3": {"bg": "#ececec", "text": "#444444", "border": "#c9c9c9"},
}


def ticket_to_html(ticket: JiraTicket) -> str:
    pal = SEVERITY_PALETTE.get(ticket.severity, SEVERITY_PALETTE["P3"])

    labels_html = "\n        ".join(
        f'<span class="pill">{html.escape(l)}</span>' for l in ticket.labels
    ) or '<span class="pill">unlabeled</span>'

    steps_html = "\n      ".join(
        f"<li>{html.escape(step)}</li>" for step in ticket.repro_steps
    )

    paragraphs = [p.strip() for p in ticket.description.split("\n\n") if p.strip()] or [ticket.description]
    desc_html = "\n    ".join(f"<p>{html.escape(p)}</p>" for p in paragraphs)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>{html.escape(ticket.title)}</title>
<style>
  :root {{
    --bg: #f4f5f7; --card: #ffffff; --border: #e4e6ea; --border-strong: #c1c7d0;
    --text: #172b4d; --muted: #5e6c84; --code-bg: #f7f8fa;
    --pill-bg: #ebecf0; --pill-text: #42526e;
    --sev-bg: {pal["bg"]}; --sev-text: {pal["text"]}; --sev-border: {pal["border"]};
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 24px 16px; background: var(--bg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    color: var(--text); line-height: 1.5; }}
  .card {{ max-width: 820px; margin: 0 auto; background: var(--card);
    border: 1px solid var(--border); border-radius: 8px;
    box-shadow: 0 1px 2px rgba(9, 30, 66, 0.05); padding: 28px 32px 24px; }}
  .header {{ display: flex; align-items: flex-start; justify-content: space-between;
    gap: 16px; margin-bottom: 18px; }}
  .title-block .key {{ font-size: 12px; color: var(--muted); font-weight: 600;
    letter-spacing: 0.04em; text-transform: uppercase; margin-bottom: 6px; }}
  h1 {{ margin: 0; font-size: 22px; font-weight: 600; line-height: 1.3; }}
  .severity-badge {{ flex-shrink: 0; padding: 5px 12px; border-radius: 999px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.05em;
    background: var(--sev-bg); color: var(--sev-text); border: 1px solid var(--sev-border); }}
  .meta {{ display: grid; grid-template-columns: 120px 1fr; row-gap: 10px;
    column-gap: 16px; padding: 16px 0; border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border); margin-bottom: 22px; font-size: 14px; }}
  .meta dt {{ color: var(--muted); font-weight: 500; }}
  .meta dd {{ margin: 0; color: var(--text); }}
  .pill {{ display: inline-block; padding: 2px 10px; margin: 2px 4px 2px 0;
    background: var(--pill-bg); color: var(--pill-text); border-radius: 3px;
    font-size: 12px; font-weight: 500; }}
  h2 {{ font-size: 14px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--muted); margin: 22px 0 10px; }}
  p, ol {{ margin: 0 0 12px; font-size: 15px; }}
  ol {{ padding-left: 22px; }}
  ol li {{ margin-bottom: 6px; }}
  code, .mono {{ font-family: "SFMono-Regular", Consolas, Menlo, monospace; font-size: 13px; }}
  .error-block {{ position: relative; background: var(--code-bg);
    border: 1px solid var(--border); border-radius: 4px;
    padding: 12px 56px 12px 14px; color: #c00f0c;
    white-space: pre-wrap; word-break: break-all; }}
  .copy-btn {{ position: absolute; top: 8px; right: 8px; background: transparent;
    border: 1px solid var(--border-strong); border-radius: 3px; padding: 4px 8px;
    cursor: pointer; color: var(--muted); font-size: 11px; line-height: 1; }}
  .copy-btn:hover {{ background: #fff; color: var(--text); }}
  .footer {{ margin-top: 28px; padding-top: 14px; border-top: 1px solid var(--border);
    font-size: 12px; color: var(--muted); text-align: right; }}
</style>
</head>
<body>
  <article class="card">
    <header class="header">
      <div class="title-block">
        <div class="key">Ticket &middot; {html.escape(ticket.component)}</div>
        <h1>{html.escape(ticket.title)}</h1>
      </div>
      <span class="severity-badge">{html.escape(ticket.severity)}</span>
    </header>

    <dl class="meta">
      <dt>Component</dt>
      <dd><span class="pill">{html.escape(ticket.component)}</span></dd>
      <dt>Assignee</dt>
      <dd><span class="pill">{html.escape(ticket.suggested_assignee or "unassigned")}</span></dd>
      <dt>Labels</dt>
      <dd>
        {labels_html}
      </dd>
    </dl>

    <h2>Description</h2>
    {desc_html}

    <h2>Steps to Reproduce</h2>
    <ol>
      {steps_html}
    </ol>

    <h2>Error Signature</h2>
    <div class="error-block">
      <button class="copy-btn" onclick="navigator.clipboard.writeText(this.nextElementSibling.innerText); this.textContent='copied'; setTimeout(()=>this.textContent='copy', 1200);">copy</button>
      <span class="mono">{html.escape(ticket.error_signature)}</span>
    </div>

    <div class="footer">Generated from logs by Claude</div>
  </article>
</body>
</html>"""


def render_ticket(ticket: JiraTicket):
    ticket_html = ticket_to_html(ticket)
    components.html(ticket_html, height=900, scrolling=True)

    col_dl, col_json = st.columns(2)
    with col_dl:
        st.download_button(
            "Download HTML",
            data=ticket_html,
            file_name="ticket.html",
            mime="text/html",
            use_container_width=True,
        )
    with col_json:
        st.download_button(
            "Download JSON",
            data=ticket.model_dump_json(indent=2),
            file_name="ticket.json",
            mime="application/json",
            use_container_width=True,
        )

    with st.expander("Raw JSON (for Jira API push)"):
        st.json(ticket.model_dump())


DEMO_TICKET = JiraTicket(
    title="Fix OOM in payment-service causing Stripe/ledger desync",
    severity="P0",
    component="payment-service",
    description=(
        "Active production incident: PaymentBatch.aggregate is OOM-ing under load "
        "(heap at 87% just before crash), causing charges to succeed at Stripe but "
        "never commit to the local ledger. User u_4421 was charged $129.99 with no "
        "local transaction record — flagged for MANUAL RECONCILIATION.\n\n"
        "Pattern has recurred 47 times in the last 30 minutes; every occurrence "
        "produces a new payment/ledger mismatch."
    ),
    repro_steps=[
        "Client calls POST /charge (PaymentController.charge:54) — req_id=7f3a9b, user_id=u_4421, amount=129.99 USD",
        "PaymentService.handleCharge invokes Stripe gateway — charge succeeds at Stripe",
        "PaymentBatch.aggregate (PaymentBatch.java:142) attempts to build the batch — heap climbs past 87%",
        "JVM throws java.lang.OutOfMemoryError before the local transaction is committed",
        "Service returns 500 to client; Stripe charge stands, local ledger has no record",
        "Reconciliation flag fires: 'charged on stripe but local txn missing'",
    ],
    suggested_assignee="payments-team",
    labels=["incident", "production", "payments", "memory-leak", "data-integrity", "needs-rollback"],
    error_signature="java.lang.OutOfMemoryError: Java heap space at com.acme.payments.processor.PaymentBatch.aggregate(PaymentBatch.java:142)",
)


def main():
    st.set_page_config(page_title="Logs to Jira", page_icon="🎫", layout="wide")
    st.title("🎫 Logs to Jira")
    st.caption("Paste application logs → get a structured Jira ticket.")

    demo_mode = not os.getenv("ANTHROPIC_API_KEY")
    if demo_mode:
        st.warning("**Demo mode** — `ANTHROPIC_API_KEY` not set, so Analyze returns a canned ticket. Set the key in `.env` to call the real model.")

    sample_dir = "sample_logs"
    samples = {}
    if os.path.isdir(sample_dir):
        for fname in sorted(os.listdir(sample_dir)):
            with open(os.path.join(sample_dir, fname), encoding="utf-8") as f:
                samples[fname] = f.read()

    col_input, col_output = st.columns([1, 1])

    with col_input:
        st.subheader("Logs")
        if samples:
            choice = st.selectbox("Load sample", ["(none)"] + list(samples.keys()))
            default_text = samples[choice] if choice != "(none)" else ""
        else:
            default_text = ""

        logs = st.text_area("Paste logs here", value=default_text, height=400, label_visibility="collapsed")

        analyze = st.button("Analyze →", type="primary", disabled=not logs.strip())

    with col_output:
        st.subheader("Generated Ticket")
        if analyze:
            if demo_mode:
                st.info("Returning demo ticket (no API call).")
                render_ticket(DEMO_TICKET)
            else:
                with st.spinner(f"Analyzing with {MODEL}..."):
                    try:
                        ticket = analyze_logs(logs)
                        render_ticket(ticket)
                    except anthropic.APIError as e:
                        st.error(f"API error: {e}")
                    except Exception as e:
                        st.error(f"Failed to parse response: {e}")
        else:
            st.info("Paste logs on the left and click **Analyze** to generate a ticket.")


if __name__ == "__main__":
    main()
