import datetime as dt

from rich.console import Console

from drskill.traces import report as areport
from drskill.traces.model import Invocation
from drskill.traces.pipeline import AuditData

UTC = dt.timezone.utc


def _inv(harness="claude-code", name="release", kind="skill", server=None,
         day=1, sidechain=False, detection="explicit", session="s1",
         query="the question", reasoning=None):
    return Invocation(
        harness=harness, session_id=session, project="/p",
        timestamp=dt.datetime(2026, 7, day, tzinfo=UTC), kind=kind, name=name,
        server=server, query=query, reasoning=reasoning, sidechain=sidechain,
        detection=detection, source_file="/t/s.jsonl",
    )


def _render(fn, *args):
    console = Console(record=True, width=200)
    fn(console, *args)
    return console.export_text()


def test_aggregate_counts_and_sidechain_split():
    invs = [_inv(), _inv(day=2), _inv(sidechain=True, day=3),
            _inv(name="other", session="s2")]
    stats = areport.aggregate(invs)["claude-code"]
    top = stats[0]
    assert (top.name, top.count, top.sidechain) == ("release", 2, 1)
    assert top.last_used.day == 3
    assert stats[1].name == "other"


def test_heuristic_flag_set_by_detection():
    stats = areport.aggregate([_inv(detection="skill-read")])["claude-code"]
    assert stats[0].heuristic is True


def test_coverage_span_floors_at_seven_days():
    cov = areport.coverage([_inv(day=1), _inv(day=2)])["claude-code"]
    assert cov.span_days == 7.0
    assert cov.sessions == 1


def test_rollup_ranks_by_rate_not_raw_count():
    # copilot: 3 uses in a 7d floor window; claude: 4 uses across 28 days
    invs = [_inv(harness="copilot", name="a", day=d) for d in (1, 2, 3)]
    invs += [_inv(name="b", day=1), _inv(name="b", day=28),
             _inv(name="b", day=14), _inv(name="b", day=20)]
    ranked = areport.rollup(invs)
    assert ranked[0][0].name == "a"  # 3/wk beats 1/wk despite fewer raw uses


def test_render_audit_shows_coverage_marker_and_legend():
    data = AuditData(invocations=[
        _inv(detection="skill-read", name="overturemaps", harness="codex")])
    text = _render(areport.render_audit, data)
    assert "coverage:" in text
    assert "overturemaps ~" in text or "overturemaps~" in text.replace(" ", "")
    assert "SKILL.md reads" in text


def test_render_audit_footer_reports_unreadable_and_drift():
    data = AuditData(invocations=[_inv()], unreadable=["/x/y.jsonl"],
                     drifted={"codex": 2})
    text = _render(areport.render_audit, data)
    assert "1 trace file" in text and "unreadable" in text
    assert "codex: 2 session files held no recognized events" in text


def test_render_audit_window_mismatch_note():
    invs = [_inv(day=1), _inv(day=28),
            _inv(harness="copilot", name="a", day=1)]
    text = _render(areport.render_audit, AuditData(invocations=invs))
    assert "windows differ" in text


def test_drilldown_prints_contexts_newest_first_with_evidence():
    data = AuditData(invocations=[
        _inv(day=1, query="first ask", reasoning="thought one"),
        _inv(day=5, query="later ask")])
    text = _render(areport.render_drilldown, "release", data)
    assert text.index("later ask") < text.index("first ask")
    assert "thought one" in text
    assert "/t/s.jsonl" in text


def test_drilldown_ambiguous_name_prints_both_groups():
    data = AuditData(invocations=[
        _inv(name="pencil"),
        _inv(name="pencil", kind="mcp_tool", server="srv")])
    text = _render(areport.render_drilldown, "pencil", data)
    assert "skill" in text and "srv" in text


def test_drilldown_server_tool_form_filters():
    data = AuditData(invocations=[
        _inv(name="shot", kind="mcp_tool", server="a"),
        _inv(name="shot", kind="mcp_tool", server="b", query="from b")])
    text = _render(areport.render_drilldown, "b:shot", data)
    assert "from b" in text and "the question" not in text


def test_drilldown_finds_plugin_qualified_skill_name():
    data = AuditData(invocations=[
        _inv(name="superpowers:brainstorming", kind="skill", query="brainstorm ask")])
    text = _render(areport.render_drilldown, "superpowers:brainstorming", data)
    assert "brainstorm ask" in text


def test_drilldown_colon_name_matches_both_skill_and_mcp_tool():
    data = AuditData(invocations=[
        _inv(name="b:shot", kind="skill", query="skill query"),
        _inv(name="shot", kind="mcp_tool", server="b", query="mcp query")])
    text = _render(areport.render_drilldown, "b:shot", data)
    assert "skill query" in text and "mcp query" in text


def test_trace_text_is_escaped_and_sanitized():
    evil = "[red]x[/red] hidden​ end"
    data = AuditData(invocations=[_inv(query=evil, day=5)])
    text = _render(areport.render_drilldown, "release", data)
    assert "​" not in text
    assert "[red]x" in text  # markup neutralized, printed literally


def test_drilldown_unknown_name_says_so():
    text = _render(areport.render_drilldown, "nope", AuditData())
    assert "no invocations" in text.lower()


def test_harness_names_escaped_in_audit_and_drilldown():
    # Hostile harness name with markup and invisible character
    evil = "evil[red]h[/red]​x"
    invs = [
        _inv(harness=evil, name="test", day=1),
        _inv(harness=evil, name="test", day=28),
        _inv(harness="copilot", name="other", day=1),
    ]
    data = AuditData(invocations=invs)

    # Test render_audit: window-mismatch note should have escaped harness names
    audit_text = _render(areport.render_audit, data)
    assert "​" not in audit_text  # invisible character should be removed
    assert "[red]h" in audit_text  # markup should be escaped/literal

    # Test render_drilldown: per-group counts should have escaped harness names
    drilldown_text = _render(areport.render_drilldown, "test", data)
    assert "​" not in drilldown_text
    assert "[red]h" in drilldown_text


def test_drilldown_shows_trigger_via_line():
    data = AuditData(invocations=[
        _inv(name="release", detection="explicit", day=1),
        _inv(name="release", detection="command-marker", day=2),
        _inv(name="release", detection="skill-read", day=3),
    ])
    text = _render(areport.render_drilldown, "release", data)
    assert "via: explicit tool call" in text
    assert "via: /release slash command" in text
    assert "via: SKILL.md read" in text
