import html
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from typing import Literal, Optional

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
    suggested_assignee: Optional[str] = Field(description="Team or person guess, or null")
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


CLI_SCHEMA_INSTRUCTIONS = """Respond with ONLY a single JSON object (no markdown fences, no preamble, no commentary) with these exact fields:
- title: string, under 80 chars, action-oriented
- severity: one of "P0", "P1", "P2", "P3"
- component: string
- description: string (markdown allowed; use \\n\\n between paragraphs)
- repro_steps: array of strings
- suggested_assignee: string or null
- labels: array of 3-6 strings (lowercase-kebab-case)
- error_signature: string (the single most diagnostic log line)"""


def _extract_json(text: str) -> str:
    """Strip optional markdown fences and isolate the JSON object in CLI output."""
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def analyze_logs_via_cli(logs: str) -> JiraTicket:
    """Fallback when no API key: shell out to the Claude Code CLI, which uses the user's subscription auth."""
    prompt = f"{SYSTEM_PROMPT}\n\n{CLI_SCHEMA_INSTRUCTIONS}\n\nAnalyze these logs:\n\n```\n{logs}\n```"
    cli_env = {k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}}
    result = subprocess.run(
        ["claude", "-p"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=180,
        encoding="utf-8",
        env=cli_env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"claude CLI exited {result.returncode}: {result.stderr.strip() or 'no stderr'}")
    raw = _extract_json(result.stdout)
    if not raw:
        raise RuntimeError(f"claude CLI returned empty output. stderr: {result.stderr.strip()}")
    data = json.loads(raw)
    return JiraTicket.model_validate(data)


SEVERITY_TO_JIRA_PRIORITY = {"P0": "Highest", "P1": "High", "P2": "Medium", "P3": "Low"}


def create_jira_ticket_via_mcp(ticket: JiraTicket, project_key: str) -> dict:
    """Use Claude CLI + Atlassian MCP to create a Jira issue. Returns {ok, key, url, error}."""
    payload = {
        "project_key": project_key,
        "summary": ticket.title,
        "description": ticket.description,
        "priority": SEVERITY_TO_JIRA_PRIORITY.get(ticket.severity),
        "labels": ticket.labels,
        "issuetype": "Bug",
    }
    prompt = (
        "Use the Atlassian MCP tools to create a Jira issue with this data:\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
        "Steps:\n"
        "1. If needed, fetch the accessible Atlassian cloud id first.\n"
        f"2. Create the issue in project '{project_key}'. Use issuetype 'Bug' (fall back to 'Task' if Bug is unavailable).\n"
        "3. After creation, respond with ONLY a single JSON object (no markdown fences, no prose) with these exact fields:\n"
        "  - ok: boolean\n"
        "  - key: string (issue key like 'HACK-123') or null\n"
        "  - url: string (browse URL) or null\n"
        "  - error: string or null\n"
    )
    cli_env = {k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}}
    try:
        result = subprocess.run(
            ["claude", "-p", "--permission-mode", "bypassPermissions"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            env=cli_env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "key": None, "url": None, "error": "claude CLI timed out after 180s"}
    except FileNotFoundError:
        return {"ok": False, "key": None, "url": None, "error": "`claude` CLI not found on PATH"}
    if result.returncode != 0:
        return {"ok": False, "key": None, "url": None, "error": f"CLI exited {result.returncode}: {result.stderr.strip() or 'no stderr'}"}
    raw = _extract_json(result.stdout)
    if not raw:
        return {"ok": False, "key": None, "url": None, "error": f"empty CLI output. stderr: {result.stderr.strip()}"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"ok": False, "key": None, "url": None, "error": f"couldn't parse CLI response: {result.stdout[:500]}"}


BRAND = {
    "purple_900": "#38003D",
    "purple_800": "#5F016F",
    "pink_500":   "#FF33BB",
    "pink_300":   "#FF80D4",
    "pink_200":   "#FFADE4",
    "pink_100":   "#FFE5F7",
    "off_white":  "#F9F5F4",
    "white":      "#FFFFFF",
    "text":       "#201E1D",
    "muted":      "#716C6A",
    "border":     "#E3DFDD",
    "border_2":   "#C3BDBC",
}

SEVERITY_CSS = f"""
  .severity-badge.sev-p0 {{ background: {BRAND['purple_800']}; color: {BRAND['white']}; border-color: {BRAND['purple_900']}; }}
  .severity-badge.sev-p1 {{ background: {BRAND['pink_500']};   color: {BRAND['white']}; border-color: {BRAND['pink_500']}; }}
  .severity-badge.sev-p2 {{ background: {BRAND['pink_200']};   color: {BRAND['purple_800']}; border-color: {BRAND['pink_300']}; }}
  .severity-badge.sev-p3 {{ background: {BRAND['border']};     color: #545050; border-color: {BRAND['border_2']}; }}
  @media (prefers-color-scheme: dark) {{
    .severity-badge.sev-p0 {{ background: {BRAND['pink_500']}; color: {BRAND['white']}; border-color: {BRAND['pink_500']}; }}
    .severity-badge.sev-p1 {{ background: {BRAND['pink_300']}; color: #2A0231; border-color: {BRAND['pink_300']}; }}
    .severity-badge.sev-p2 {{ background: {BRAND['purple_800']}; color: {BRAND['pink_200']}; border-color: {BRAND['purple_900']}; }}
    .severity-badge.sev-p3 {{ background: #383433; color: #C3BDBC; border-color: #545050; }}
  }}
"""


def ticket_to_html(ticket: JiraTicket) -> str:
    sev_class = f"sev-{ticket.severity.lower()}"

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
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: {BRAND["off_white"]}; --card: {BRAND["white"]};
    --border: {BRAND["border"]}; --border-strong: {BRAND["border_2"]};
    --text: {BRAND["text"]}; --muted: {BRAND["muted"]};
    --accent: {BRAND["purple_800"]}; --accent-strong: {BRAND["purple_900"]};
    --pill-bg: {BRAND["off_white"]}; --pill-text: {BRAND["purple_800"]};
    --error-bg: {BRAND["pink_100"]}; --error-text: {BRAND["purple_800"]};
    --btn-bg: {BRAND["white"]};
    --card-shadow: 0 1px 2px rgba(32, 30, 29, 0.04);
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --bg: #1A1818; --card: #262322;
      --border: #383433; --border-strong: #545050;
      --text: {BRAND["off_white"]}; --muted: #A6A1A0;
      --accent: {BRAND["pink_300"]}; --accent-strong: {BRAND["pink_200"]};
      --pill-bg: #2E2A29; --pill-text: {BRAND["pink_300"]};
      --error-bg: #2E0834; --error-text: {BRAND["pink_200"]};
      --btn-bg: #2E2A29;
      --card-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
    }}
  }}
{SEVERITY_CSS}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 32px 16px; background: var(--bg);
    font-family: "DM Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    color: var(--text); line-height: 1.55; -webkit-font-smoothing: antialiased; }}
  .card {{ max-width: 860px; margin: 0 auto; background: var(--card);
    border: 1px solid var(--border); border-radius: 12px;
    box-shadow: var(--card-shadow); padding: 32px 36px 28px; }}
  .header {{ display: flex; align-items: flex-start; justify-content: space-between;
    gap: 20px; margin-bottom: 22px; }}
  .title-block .key {{ font-size: 11px; color: var(--muted); font-weight: 600;
    letter-spacing: 0.08em; text-transform: uppercase; margin-bottom: 8px; }}
  h1 {{ margin: 0; font-size: 24px; font-weight: 600; line-height: 1.25;
    letter-spacing: -0.01em; color: var(--text); }}
  .severity-badge {{ flex-shrink: 0; padding: 6px 14px; border-radius: 999px;
    font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
    border: 1px solid transparent; }}
  .meta {{ display: grid; grid-template-columns: 110px 1fr; row-gap: 12px;
    column-gap: 18px; padding: 18px 0; border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border); margin-bottom: 24px; font-size: 14px; }}
  .meta dt {{ color: var(--muted); font-weight: 500; font-size: 13px; }}
  .meta dd {{ margin: 0; color: var(--text); }}
  .pill {{ display: inline-block; padding: 3px 11px; margin: 2px 5px 2px 0;
    background: var(--pill-bg); color: var(--pill-text); border-radius: 999px;
    border: 1px solid var(--border); font-size: 12px; font-weight: 500;
    letter-spacing: 0.01em; }}
  h2 {{ font-size: 11px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.1em; color: var(--accent); margin: 24px 0 12px;
    font-family: "DM Sans", sans-serif; }}
  p, ol {{ margin: 0 0 12px; font-size: 15px; color: var(--text); }}
  ol {{ padding-left: 22px; }}
  ol li {{ margin-bottom: 8px; }}
  code, .mono {{ font-family: "DM Mono", "SFMono-Regular", Consolas, Menlo, monospace;
    font-size: 13px; }}
  .error-block {{ position: relative; background: var(--error-bg);
    border: 1px solid var(--border); border-radius: 8px;
    padding: 14px 64px 14px 16px; color: var(--error-text);
    white-space: pre-wrap; word-break: break-all; }}
  .copy-btn {{ position: absolute; top: 10px; right: 10px; background: var(--btn-bg);
    border: 1px solid var(--border-strong); border-radius: 6px; padding: 5px 10px;
    cursor: pointer; color: var(--accent); font-size: 11px; line-height: 1;
    font-family: "DM Sans", sans-serif; font-weight: 600; letter-spacing: 0.04em;
    text-transform: uppercase; transition: all 0.15s ease; }}
  .copy-btn:hover {{ background: var(--accent); color: var(--card); border-color: var(--accent); }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid var(--border);
    font-size: 11px; color: var(--muted); text-align: right;
    letter-spacing: 0.04em; text-transform: uppercase; font-weight: 500; }}
</style>
</head>
<body>
  <article class="card">
    <header class="header">
      <div class="title-block">
        <div class="key">Ticket &middot; {html.escape(ticket.component)}</div>
        <h1>{html.escape(ticket.title)}</h1>
      </div>
      <span class="severity-badge {sev_class}">{html.escape(ticket.severity)}</span>
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

    <div class="footer">Generated by Arrive &middot; Logs to Jira</div>
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


# Year is optional (some logs use MM-DD only). Sub-second part accepts `.`, `,`, or `:`
# as separator (covers ISO, Python logging, and some Java/custom formats).
_TIMESTAMP_RE = re.compile(
    r"^\s*(?:(\d{4})-)?(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:[.,:](\d+))?(?:Z|[+-]\d{2}:?\d{2})?"
)


def _parse_ts(line: str) -> datetime | None:
    m = _TIMESTAMP_RE.match(line)
    if not m:
        return None
    year_s, month, day, hour, minute, second, frac = m.groups()
    year = int(year_s) if year_s else 2000  # placeholder when year is missing
    frac_us = int(((frac or "") + "000000")[:6])
    try:
        return datetime(year, int(month), int(day), int(hour), int(minute), int(second), frac_us)
    except ValueError:
        return None


def _parse_log_timestamps(text: str) -> list[tuple[str, datetime | None]]:
    """For each line, return (line, parsed_timestamp_or_None). Untimestamped lines inherit
    the previous line's timestamp at filter time, so stack traces ride along with their parent."""
    return [(line, _parse_ts(line)) for line in text.splitlines()]


def _filter_logs_by_window(text: str, start: datetime, end: datetime) -> str:
    """Keep lines whose effective timestamp (own, or inherited from the most recent
    timestamped line above) falls in [start, end]."""
    kept: list[str] = []
    current: datetime | None = None
    for line, ts in _parse_log_timestamps(text):
        if ts is not None:
            current = ts
        if current is not None and start <= current <= end:
            kept.append(line)
    return "\n".join(kept)


def _run_analysis(logs: str) -> JiraTicket:
    try:
        return analyze_logs_via_cli(logs)
    except subprocess.TimeoutExpired:
        st.error("Analysis timed out after 180s. Showing canned demo ticket instead.")
    except FileNotFoundError:
        st.error("`claude` CLI not found on PATH. Showing canned demo ticket instead.")
    except (json.JSONDecodeError, RuntimeError) as e:
        st.error(f"Couldn't parse the response: {e}\n\nShowing canned demo ticket instead.")
    except Exception as e:
        st.error(f"Analysis failed: {e}\n\nShowing canned demo ticket instead.")
    return DEMO_TICKET


HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(HERE, "arrive_logo.png")
SYMBOL_PATH = os.path.join(HERE, "arrive_symbol.png")


def inject_brand_css():
    st.markdown(
        f"""
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=DM+Mono:wght@400;500&display=swap" rel="stylesheet">
        <style>
          :root {{
            --a-bg: {BRAND["off_white"]};
            --a-surface: {BRAND["white"]};
            --a-text: {BRAND["text"]};
            --a-muted: {BRAND["muted"]};
            --a-border: {BRAND["border"]};
            --a-border-strong: {BRAND["border_2"]};
            --a-accent: {BRAND["purple_800"]};
            --a-accent-hover: {BRAND["purple_900"]};
            --a-accent-soft: {BRAND["pink_100"]};
            --a-on-accent: {BRAND["white"]};
            --a-logo-filter: none;
          }}
          @media (prefers-color-scheme: dark) {{
            :root {{
              --a-bg: #1A1818;
              --a-surface: #262322;
              --a-text: {BRAND["off_white"]};
              --a-muted: #A6A1A0;
              --a-border: #383433;
              --a-border-strong: #545050;
              --a-accent: {BRAND["pink_300"]};
              --a-accent-hover: {BRAND["pink_200"]};
              --a-accent-soft: #2E0834;
              --a-on-accent: #2A0231;
              --a-logo-filter: brightness(0) saturate(100%) invert(78%) sepia(33%) saturate(2089%) hue-rotate(283deg) brightness(102%) contrast(101%);
            }}
          }}
          html, body, [class*="css"] {{
            font-family: "DM Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif !important;
            color: var(--a-text);
          }}
          .stApp {{ background-color: var(--a-bg) !important; }}
          [data-testid="stHeader"] {{ background: transparent; }}
          h1, h2, h3, h4 {{ color: var(--a-text) !important; letter-spacing: -0.01em; font-weight: 600; }}
          .arrive-divider {{
            height: 1px; background: var(--a-border); margin: 14px 0 26px;
          }}
          .arrive-title {{
            font-size: 13px; font-weight: 600; letter-spacing: 0.14em;
            color: var(--a-muted); text-transform: uppercase;
            padding-left: 18px; border-left: 1px solid var(--a-border);
          }}
          [data-testid="stImage"] img {{ filter: var(--a-logo-filter); }}
          .stButton > button {{
            font-family: "DM Sans", sans-serif !important;
            font-weight: 600; letter-spacing: 0.02em;
            border-radius: 8px; padding: 10px 18px;
            transition: all 0.15s ease;
          }}
          .stButton > button[kind="primary"] {{
            background: var(--a-accent); color: var(--a-on-accent);
            border: 1px solid var(--a-accent);
          }}
          .stButton > button[kind="primary"]:hover:not(:disabled) {{
            background: var(--a-accent-hover); border-color: var(--a-accent-hover);
          }}
          .stButton > button[kind="primary"]:disabled {{
            background: var(--a-border); color: var(--a-muted);
            border-color: var(--a-border);
          }}
          .stButton > button:not([kind="primary"]) {{
            background: var(--a-surface); color: var(--a-accent);
            border: 1px solid var(--a-border-strong);
          }}
          .stButton > button:not([kind="primary"]):hover {{
            border-color: var(--a-accent);
            background: var(--a-accent-soft);
          }}
          [data-testid="stTextArea"] textarea {{
            font-family: "DM Mono", "SFMono-Regular", Consolas, Menlo, monospace !important;
            font-size: 13px; background: var(--a-surface) !important;
            border: 1px solid var(--a-border) !important; border-radius: 8px !important;
            color: var(--a-text) !important;
          }}
          [data-testid="stTextArea"] textarea:focus {{
            border-color: var(--a-accent) !important;
            box-shadow: 0 0 0 1px var(--a-accent) !important;
          }}
          [data-testid="stSelectbox"] > div > div {{
            background: var(--a-surface) !important; border: 1px solid var(--a-border) !important;
            border-radius: 8px !important; color: var(--a-text) !important;
          }}
          [data-testid="stFileUploader"] section {{
            background: var(--a-surface) !important; border: 1px dashed var(--a-border-strong) !important;
            border-radius: 8px !important;
          }}
          [data-testid="stFileUploader"] section:hover {{ border-color: var(--a-accent) !important; }}
          [data-testid="stFileUploader"] small {{ color: var(--a-muted) !important; }}
          [data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {{
            background-color: var(--a-accent) !important;
            border-color: var(--a-accent) !important;
          }}
          [data-testid="stSlider"] [data-baseweb="slider"] > div > div > div {{
            background: var(--a-accent) !important;
          }}
          [data-testid="stAlert"] {{
            border-radius: 8px; border: 1px solid var(--a-border);
            background: var(--a-surface); color: var(--a-text);
          }}
          .panel-label {{
            font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
            color: var(--a-accent); text-transform: uppercase;
            margin-bottom: 10px;
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_header():
    col_logo, col_title = st.columns([1, 6], gap="small")
    with col_logo:
        if os.path.exists(LOGO_PATH):
            st.image(LOGO_PATH, width=140)
        else:
            accent = BRAND["purple_800"]
            st.markdown(f"<h2 style='color:{accent};margin:0'>Arrive</h2>", unsafe_allow_html=True)
    with col_title:
        st.markdown(
            "<div style='display:flex;align-items:center;height:100%;'>"
            "<span class='arrive-title'>Logs Analyser &rarr; Jira</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    st.markdown("<div class='arrive-divider'></div>", unsafe_allow_html=True)


def main():
    icon = SYMBOL_PATH if os.path.exists(SYMBOL_PATH) else None
    st.set_page_config(page_title="Arrive · Logs Analyser to Jira", page_icon=icon, layout="wide")
    inject_brand_css()
    render_header()
    st.caption("Drop a log file, pick a sample, or paste manually → get a structured Jira ticket.")

    if "logs_input" not in st.session_state:
        st.session_state.logs_input = ""
    if "current_ticket" not in st.session_state:
        st.session_state.current_ticket = None
    if "history" not in st.session_state:
        st.session_state.history = []  # list[tuple[str, JiraTicket]]
    if "uploader_key" not in st.session_state:
        st.session_state.uploader_key = 0

    sample_dir = "sample_logs"
    samples = {}
    if os.path.isdir(sample_dir):
        for fname in sorted(os.listdir(sample_dir)):
            with open(os.path.join(sample_dir, fname), encoding="utf-8") as f:
                samples[fname] = f.read()

    def _on_clear():
        st.session_state.logs_input = ""
        st.session_state.uploader_key += 1  # forces st.file_uploader to reset
        st.session_state.pop("_last_uploaded_sig", None)
        st.session_state.pop("_last_sample", None)

    col_input, col_output = st.columns([1, 1], gap="large")

    with col_input:
        st.markdown("<div class='panel-label'>Logs</div>", unsafe_allow_html=True)
        uploaded = st.file_uploader(
            "Drop a log file (or browse)",
            type=["log", "txt", "out", "json", "err"],
            key=f"uploader_{st.session_state.uploader_key}",
        )
        if uploaded is not None:
            sig = (uploaded.name, uploaded.size)
            if st.session_state.get("_last_uploaded_sig") != sig:
                st.session_state._last_uploaded_sig = sig
                st.session_state.logs_input = uploaded.getvalue().decode("utf-8", errors="replace")
                st.rerun()

        if samples:
            choice = st.selectbox(
                "Or pick a sample",
                ["(none)"] + list(samples.keys()),
                key="sample_choice",
            )
            if choice != "(none)" and st.session_state.get("_last_sample") != choice:
                st.session_state._last_sample = choice
                st.session_state.logs_input = samples[choice]
                st.rerun()

        st.text_area("Or paste here", key="logs_input", height=320)

        timestamps = [ts for _, ts in _parse_log_timestamps(st.session_state.logs_input) if ts is not None]
        filtered_logs = st.session_state.logs_input
        if len(timestamps) >= 2 and min(timestamps) < max(timestamps):
            ts_min, ts_max = min(timestamps), max(timestamps)
            stored = st.session_state.get("time_window")
            if stored is not None and (stored[0] < ts_min or stored[1] > ts_max):
                del st.session_state["time_window"]
            range_secs = (ts_max - ts_min).total_seconds()
            if range_secs < 10:
                step, fmt = timedelta(milliseconds=100), "HH:mm:ss.SSS"
            elif range_secs < 600:
                step, fmt = timedelta(seconds=1), "HH:mm:ss"
            elif range_secs < 7200:
                step, fmt = timedelta(seconds=10), "HH:mm:ss"
            elif range_secs < 86400:
                step, fmt = timedelta(minutes=1), "HH:mm"
            else:
                step, fmt = timedelta(minutes=10), "YYYY-MM-DD HH:mm"
            window = st.slider(
                "Time window",
                min_value=ts_min,
                max_value=ts_max,
                value=(ts_min, ts_max),
                step=step,
                format=fmt,
                key="time_window",
            )
            filtered_logs = _filter_logs_by_window(st.session_state.logs_input, window[0], window[1])
            total = len(st.session_state.logs_input.splitlines())
            kept = len(filtered_logs.splitlines())
            if (window[0], window[1]) != (ts_min, ts_max):
                st.caption(f"Analyzing **{kept} of {total}** lines · {window[0].time()} → {window[1].time()}")
        elif len(timestamps) == 1:
            st.caption(f"Single timestamp detected ({timestamps[0]}); time filter disabled.")

        btn_analyze, btn_clear = st.columns([3, 1])
        with btn_analyze:
            analyze = st.button(
                "Analyze →",
                type="primary",
                use_container_width=True,
                disabled=not filtered_logs.strip(),
            )
        with btn_clear:
            st.button("Clear", on_click=_on_clear, use_container_width=True)

    with col_output:
        st.markdown("<div class='panel-label'>Generated Ticket</div>", unsafe_allow_html=True)
        st.markdown("<div style='height: 30px'></div>", unsafe_allow_html=True)

        if analyze:
            with st.spinner("Analyzing logs…"):
                ticket = _run_analysis(filtered_logs)
            st.session_state.current_ticket = ticket
            st.session_state.history.insert(0, (datetime.now().strftime("%H:%M:%S"), ticket))
            st.session_state.history = st.session_state.history[:5]
            st.session_state.pop("jira_result", None)

        if st.session_state.current_ticket is not None:
            render_ticket(st.session_state.current_ticket)

            st.markdown("### Push to Jira")
            project_key = st.text_input(
                "Jira project key",
                value=st.session_state.get("jira_project_key", ""),
                placeholder="DEMO",
                help="Project key to file the issue under, e.g. HACK or DEMO.",
                key="jira_project_key_input",
            ).strip()
            if st.button("Create Jira ticket", type="primary", disabled=not project_key, key="create_jira"):
                st.session_state["jira_project_key"] = project_key
                with st.spinner(f"Creating issue in {project_key} via Atlassian MCP…"):
                    st.session_state["jira_result"] = create_jira_ticket_via_mcp(
                        st.session_state.current_ticket, project_key
                    )

            jira_result = st.session_state.get("jira_result")
            if jira_result:
                if jira_result.get("ok") and jira_result.get("key"):
                    key = jira_result["key"]
                    url = jira_result.get("url")
                    if url:
                        st.success(f"Created [{key}]({url})")
                    else:
                        st.success(f"Created {key}")
                else:
                    st.error(f"Failed to create issue: {jira_result.get('error') or 'unknown error'}")
        else:
            st.info("Load logs on the left and click **Analyze** to generate a ticket.")

        if st.session_state.history:
            st.markdown("---")
            st.caption("Recent")
            for i, (ts, t) in enumerate(st.session_state.history):
                label = f"{ts} · {t.severity} · {t.title[:60]}"
                if st.button(label, key=f"hist_{i}", use_container_width=True):
                    st.session_state.current_ticket = t
                    st.session_state.pop("jira_result", None)
                    st.rerun()


if __name__ == "__main__":
    main()
