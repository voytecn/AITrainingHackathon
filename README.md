# Logs Analyser to Jira

A Streamlit app that turns raw application logs into a polished Jira-style ticket. Paste, upload, or pick a sample log; narrow to a specific time window; click Analyze; download the result as HTML or JSON.

Built for the Arrive AI Academy hackathon. The original artifact-mode prompt (paste into claude.ai → React card) is still supported as a fallback — see [Alternative: pure prompt mode](#alternative-pure-prompt-mode-in-claudeai) below.

## Quickstart

```bash
git clone https://github.com/voytecn/AITrainingHackathon
cd AITrainingHackathon
pip install -r requirements.txt
streamlit run app.py
```

Visit http://localhost:8501.

### Auth — two paths

| Path | When | How |
|---|---|---|
| **Claude Code CLI** (default) | No setup beyond having `claude` installed and logged in | Works out of the box; uses your `claude.ai` subscription quota; ~15s per analysis |
| **Anthropic API** | You have API credits | `cp .env.example .env`, add `ANTHROPIC_API_KEY=sk-ant-...`; ~3–5s with strict-schema output |

The app prefers the API path if a key is set and the workspace has credits; otherwise it silently falls back to the CLI.

## How to use

1. **Load logs** — drag-drop a `.log` / `.txt` / `.out` / `.json` / `.err`, pick a sample from `sample_logs/`, or paste into the textarea.
2. **(Optional) Narrow with the time slider** — appears when the input has ≥2 parseable timestamps. Stack-trace lines ride along with their parent timestamp, so you never orphan a trace.
3. **Click Analyze →** — spinner, then the ticket renders in the right column as a Jira-style HTML card.
4. **Download or revisit** — HTML / JSON buttons sit below the card. The last 5 analyses appear under "Recent" and are one click away.

## Features

- Single editable textarea fed by upload, sample, or paste — all three update the same source.
- Adaptive time-window slider (100 ms step for sub-10 s logs up to 10 min for multi-day spans).
- Lenient timestamp parser: ISO 8601 with or without year, `.` / `,` / `:` sub-second separators.
- Two analysis backends with automatic failover.
- Self-contained HTML output — no CDN, no JavaScript dependencies; opens anywhere.
- Last 5 tickets cached in session state; revisit without re-running.

## Tests

```bash
pytest
```

Covers timestamp parsing, window filtering, CLI-output JSON extraction, and HTML rendering (including escaping). The Streamlit UI and `claude -p` subprocess are not exercised — those are integration concerns.

## Project layout

```
app.py                       Streamlit app + analysis backends + HTML renderer
build_pdf.py                 Renders SPEC.md to SPEC.pdf via Edge/Chrome headless
claude_prompt.md             Master prompt for the original claude.ai Artifacts flow
sample_logs/                 Public log fixtures used by the sample dropdown
tests/                       Pytest suite
SPEC.md  / SPEC.pdf          Architecture, data model, future-work roadmap
```

## Alternative: pure prompt mode in claude.ai

If you don't want to run the Streamlit app, the original demo flow still works:

1. Open https://claude.ai
2. Open `claude_prompt.md` and copy its contents into a new conversation
3. Paste a log sample from `sample_logs/` (or any real logs)
4. Claude generates a Jira-style ticket card as a React artifact

This was the original hackathon submission. The Streamlit path was added so the same prompt logic could drive a self-hostable tool with file upload, time-window filtering, and downloadable artifacts.

## Limitations & roadmap

- CLI fallback is slow (~15s) and not schema-validated; malformed JSON falls back to a canned demo ticket.
- Timestamp parser is ISO-only — syslog / nginx / journalctl formats won't trigger the slider.
- One ticket per log dump (no multi-incident grouping yet).
- History is in-memory; reloading wipes it.

See `SPEC.md` (§ Future work) for the prioritized roadmap.
