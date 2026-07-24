import json

from drskill.traces import codex


def _meta(cwd="/proj/x", thread_source=None, source=None):
    payload = {"id": "c1", "session_id": "c1", "cwd": cwd,
               "timestamp": "2026-07-14T15:00:11.377Z"}
    if thread_source is not None:
        payload["thread_source"] = thread_source
    if source is not None:
        payload["source"] = source
    return {"timestamp": "2026-07-14T15:00:11.454Z", "type": "session_meta",
            "payload": payload}


def _write(tmp_path, events, name="rollout-2026-07-14T08-00-11-c1.jsonl"):
    d = tmp_path / ".codex" / "sessions" / "2026" / "07" / "14"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return f


def test_discover_finds_rollouts(tmp_path):
    f = _write(tmp_path, [_meta()])
    assert codex.discover(tmp_path) == [f]


def test_mcp_tool_call_end_is_explicit(tmp_path):
    f = _write(tmp_path, [
        _meta(),
        {"timestamp": "2026-07-14T15:01:00.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "map the coffee shops"}},
        {"timestamp": "2026-07-14T15:01:08.659Z", "type": "event_msg",
         "payload": {"type": "mcp_tool_call_end", "call_id": "x",
                     "invocation": {"server": "overture", "tool": "places",
                                    "arguments": {"q": "coffee"}}}},
    ])
    [inv] = codex.extract(f).invocations
    assert (inv.kind, inv.server, inv.name) == ("mcp_tool", "overture", "places")
    assert inv.detection == "explicit"
    assert inv.query == "map the coffee shops"
    assert inv.reasoning is None
    assert inv.project == "/proj/x"
    assert inv.session_id == "c1"


def test_mcp_tool_call_end_records_source_line(tmp_path):
    f = _write(tmp_path, [
        _meta(),
        {"timestamp": "2026-07-14T15:01:00.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "map the coffee shops"}},
        {"timestamp": "2026-07-14T15:01:08.659Z", "type": "event_msg",
         "payload": {"type": "mcp_tool_call_end", "call_id": "x",
                     "invocation": {"server": "overture", "tool": "places",
                                    "arguments": {"q": "coffee"}}}},
    ])
    [inv] = codex.extract(f).invocations
    assert inv.source_line == 3  # 1-based: line 3 holds mcp_tool_call_end


def test_skill_md_read_in_custom_tool_call_is_heuristic(tmp_path):
    f = _write(tmp_path, [
        _meta(),
        {"timestamp": "2026-07-14T15:01:00.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "use the maps skill"}},
        {"timestamp": "2026-07-14T15:01:05.000Z", "type": "response_item",
         "payload": {"type": "custom_tool_call", "name": "exec",
                     "input": "sed -n '1,240p' /Users/d/.agents/skills/overturemaps/SKILL.md"}},
    ])
    [inv] = codex.extract(f).invocations
    assert (inv.kind, inv.name, inv.detection) == ("skill", "overturemaps", "skill-read")


def test_skill_md_in_function_call_arguments(tmp_path):
    f = _write(tmp_path, [
        _meta(),
        {"timestamp": "2026-07-14T15:01:05.000Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell",
                     "arguments": json.dumps(
                         {"command": "cat /x/skills/pyportal/SKILL.md"})}},
    ])
    [inv] = codex.extract(f).invocations
    assert inv.name == "pyportal"


def test_subagent_session_meta_marks_all_sidechain(tmp_path):
    f = _write(tmp_path, [
        _meta(thread_source="subagent", source={"subagent": {"other": "guardian"}}),
        {"timestamp": "2026-07-14T15:01:08.000Z", "type": "event_msg",
         "payload": {"type": "mcp_tool_call_end",
                     "invocation": {"server": "s", "tool": "t", "arguments": {}}}},
    ])
    [inv] = codex.extract(f).invocations
    assert inv.sidechain is True


def test_main_thread_query_is_user_sourced(tmp_path):
    f = _write(tmp_path, [
        _meta(),
        {"timestamp": "2026-07-14T15:01:00.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "map the coffee shops"}},
        {"timestamp": "2026-07-14T15:01:08.659Z", "type": "event_msg",
         "payload": {"type": "mcp_tool_call_end", "call_id": "x",
                     "invocation": {"server": "overture", "tool": "places",
                                    "arguments": {"q": "coffee"}}}},
    ])
    [inv] = codex.extract(f).invocations
    assert inv.query_source == "user"


def test_subagent_thread_query_is_agent_sourced(tmp_path):
    f = _write(tmp_path, [
        _meta(thread_source="subagent", source={"subagent": {"other": "guardian"}}),
        {"timestamp": "2026-07-14T15:01:00.000Z", "type": "event_msg",
         "payload": {"type": "user_message", "message": "implement task 3"}},
        {"timestamp": "2026-07-14T15:01:08.000Z", "type": "event_msg",
         "payload": {"type": "mcp_tool_call_end",
                     "invocation": {"server": "s", "tool": "t", "arguments": {}}}},
    ])
    [inv] = codex.extract(f).invocations
    assert inv.sidechain is True
    assert inv.query_source == "agent"


def test_malformed_lines_skipped_and_recognized_counts(tmp_path):
    f = _write(tmp_path, [_meta()])
    f.write_text(f.read_text() + "garbage\n")
    result = codex.extract(f)
    assert result.invocations == []
    assert result.recognized == 1  # the session_meta
