"""Tests for the pure helpers in app.py: timestamp parsing, window filtering,
JSON extraction, and HTML rendering. The Streamlit UI and CLI subprocess are
not exercised here — those are integration concerns."""

from datetime import datetime

import pytest

from app import (
    JiraTicket,
    _extract_json,
    _filter_logs_by_window,
    _parse_log_timestamps,
    _parse_ts,
    ticket_to_html,
)


# ---------- _parse_ts ----------

class TestParseTs:
    def test_full_iso_with_z(self):
        assert _parse_ts("2026-05-19T14:32:11.234Z INFO foo") == datetime(2026, 5, 19, 14, 32, 11, 234000)

    def test_iso_with_space(self):
        assert _parse_ts("2026-05-19 14:32:11.234 INFO foo") == datetime(2026, 5, 19, 14, 32, 11, 234000)

    def test_no_year_colon_micros(self):
        # The format that triggered broadening the regex
        assert _parse_ts("05-15 00:00:07:908439 ERROR bar") == datetime(2000, 5, 15, 0, 0, 7, 908439)

    def test_no_year_dot_ms(self):
        assert _parse_ts("05-15T00:00:07.908 WARN baz") == datetime(2000, 5, 15, 0, 0, 7, 908000)

    def test_comma_subsecond_separator(self):
        # Python's logging module uses this
        assert _parse_ts("2026-05-19 14:32:11,234 DEBUG hi") == datetime(2026, 5, 19, 14, 32, 11, 234000)

    def test_leading_whitespace(self):
        assert _parse_ts("   2026-05-19T14:32:11.234Z indented") == datetime(2026, 5, 19, 14, 32, 11, 234000)

    def test_timezone_offset(self):
        assert _parse_ts("2026-05-19T14:32:11.234+02:00 INFO foo") == datetime(2026, 5, 19, 14, 32, 11, 234000)

    def test_no_subsecond(self):
        assert _parse_ts("2026-05-19T14:32:11Z msg") == datetime(2026, 5, 19, 14, 32, 11)

    def test_returns_none_for_plain_text(self):
        assert _parse_ts("no timestamp here") is None

    def test_returns_none_for_stack_trace(self):
        assert _parse_ts("    at com.acme.payments.Foo.bar(Foo.java:42)") is None

    def test_returns_none_for_invalid_date(self):
        # month 13 — regex matches but datetime() rejects
        assert _parse_ts("2026-13-01T00:00:00Z") is None


# ---------- _parse_log_timestamps ----------

class TestParseLogTimestamps:
    def test_preserves_line_order(self):
        text = "a\n2026-01-01T00:00:01Z b\n2026-01-01T00:00:02Z c"
        result = _parse_log_timestamps(text)
        assert [line for line, _ in result] == ["a", "2026-01-01T00:00:01Z b", "2026-01-01T00:00:02Z c"]

    def test_marks_untimestamped_lines_as_none(self):
        text = "no timestamp\n2026-01-01T00:00:01Z has one"
        result = _parse_log_timestamps(text)
        assert result[0][1] is None
        assert result[1][1] == datetime(2026, 1, 1, 0, 0, 1)


# ---------- _filter_logs_by_window ----------

class TestFilterLogsByWindow:
    def test_basic_window(self):
        text = (
            "2026-05-19T14:32:11.000Z first\n"
            "2026-05-19T14:32:12.000Z second\n"
            "2026-05-19T14:32:13.000Z third"
        )
        start = datetime(2026, 5, 19, 14, 32, 11, 500000)
        end = datetime(2026, 5, 19, 14, 32, 12, 500000)
        assert _filter_logs_by_window(text, start, end) == "2026-05-19T14:32:12.000Z second"

    def test_stack_trace_lines_inherit_parent_timestamp(self):
        text = (
            "2026-05-19T14:32:11.000Z outside\n"
            "2026-05-19T14:32:12.000Z error here\n"
            "    at com.acme.Foo.bar(Foo.java:1)\n"
            "    at com.acme.Foo.baz(Foo.java:2)\n"
            "2026-05-19T14:32:13.000Z after"
        )
        start = datetime(2026, 5, 19, 14, 32, 12, 0)
        end = datetime(2026, 5, 19, 14, 32, 12, 999000)
        out = _filter_logs_by_window(text, start, end).splitlines()
        assert out == [
            "2026-05-19T14:32:12.000Z error here",
            "    at com.acme.Foo.bar(Foo.java:1)",
            "    at com.acme.Foo.baz(Foo.java:2)",
        ]

    def test_lines_before_first_timestamp_are_dropped(self):
        text = (
            "preamble without a timestamp\n"
            "more header\n"
            "2026-05-19T14:32:11.000Z first real line"
        )
        start = datetime(2026, 5, 19, 14, 32, 10)
        end = datetime(2026, 5, 19, 14, 32, 12)
        out = _filter_logs_by_window(text, start, end)
        assert "preamble" not in out and "header" not in out
        assert "first real line" in out

    def test_empty_when_window_does_not_overlap(self):
        text = "2026-05-19T14:32:11.000Z only line"
        out = _filter_logs_by_window(text, datetime(2030, 1, 1), datetime(2031, 1, 1))
        assert out == ""

    def test_window_endpoints_inclusive(self):
        text = (
            "2026-05-19T14:32:11.000Z exactly start\n"
            "2026-05-19T14:32:12.000Z exactly end"
        )
        start = datetime(2026, 5, 19, 14, 32, 11)
        end = datetime(2026, 5, 19, 14, 32, 12)
        out = _filter_logs_by_window(text, start, end).splitlines()
        assert len(out) == 2


# ---------- _extract_json ----------

class TestExtractJson:
    def test_returns_plain_json_unchanged(self):
        assert _extract_json('{"a": 1}') == '{"a": 1}'

    def test_strips_json_tagged_fence(self):
        assert _extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_strips_plain_fence(self):
        assert _extract_json('```\n{"a": 1}\n```') == '{"a": 1}'

    def test_isolates_object_from_preamble_and_postamble(self):
        raw = 'Here you go:\n{"a": 1}\nHope that helps!'
        assert _extract_json(raw) == '{"a": 1}'

    def test_handles_nested_braces(self):
        raw = 'noise {"a": {"b": 2}} more noise'
        assert _extract_json(raw) == '{"a": {"b": 2}}'


# ---------- ticket_to_html ----------

SAMPLE_TICKET = JiraTicket(
    title="Test title",
    severity="P0",
    component="test-svc",
    description="First paragraph.\n\nSecond paragraph.",
    repro_steps=["step one", "step two"],
    suggested_assignee="me",
    labels=["bug", "test"],
    error_signature="java.lang.NullPointerException at Foo.java:10",
)


class TestTicketToHtml:
    def test_title_appears(self):
        assert "Test title" in ticket_to_html(SAMPLE_TICKET)

    def test_p0_palette_renders(self):
        out = ticket_to_html(SAMPLE_TICKET)
        assert "P0" in out
        assert "#ffe5e5" in out  # P0 background
        assert "#b3261e" in out  # P0 text color

    @pytest.mark.parametrize("severity,bg", [("P0", "#ffe5e5"), ("P1", "#fff1e0"), ("P2", "#e5f1ff"), ("P3", "#ececec")])
    def test_each_severity_uses_its_palette(self, severity, bg):
        ticket = SAMPLE_TICKET.model_copy(update={"severity": severity})
        assert bg in ticket_to_html(ticket)

    def test_labels_become_pill_spans(self):
        out = ticket_to_html(SAMPLE_TICKET)
        assert '<span class="pill">bug</span>' in out
        assert '<span class="pill">test</span>' in out

    def test_repro_steps_are_list_items(self):
        out = ticket_to_html(SAMPLE_TICKET)
        assert "<li>step one</li>" in out
        assert "<li>step two</li>" in out

    def test_description_split_into_paragraphs(self):
        out = ticket_to_html(SAMPLE_TICKET)
        assert "<p>First paragraph.</p>" in out
        assert "<p>Second paragraph.</p>" in out

    def test_user_content_is_html_escaped(self):
        ticket = SAMPLE_TICKET.model_copy(update={"title": "<script>alert('xss')</script>"})
        out = ticket_to_html(ticket)
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_missing_assignee_falls_back_to_unassigned(self):
        ticket = SAMPLE_TICKET.model_copy(update={"suggested_assignee": None})
        assert "unassigned" in ticket_to_html(ticket)
