import json

from drskill.traces import claude_code


def _write(dirpath, session, events):
    dirpath.mkdir(parents=True, exist_ok=True)
    f = dirpath / f"{session}.jsonl"
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return f


def _user(text, ts="2026-07-01T10:00:00.000Z", sidechain=False, cwd="/proj/x"):
    return {
        "type": "user", "sessionId": "s1", "timestamp": ts, "cwd": cwd,
        "isSidechain": sidechain,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _assistant(blocks, ts="2026-07-01T10:00:05.000Z", sidechain=False, cwd="/proj/x"):
    return {
        "type": "assistant", "sessionId": "s1", "timestamp": ts, "cwd": cwd,
        "isSidechain": sidechain,
        "message": {"role": "assistant", "content": blocks},
    }


def test_discover_finds_project_jsonl(tmp_path):
    _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [_user("hi")])
    found = claude_code.discover(tmp_path)
    assert [f.name for f in found] == ["s1.jsonl"]


def test_skill_tool_use_is_explicit_with_query_and_reasoning(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("please brainstorm the feature"),
        _assistant([
            {"type": "thinking", "thinking": "The brainstorming skill applies here."},
            {"type": "tool_use", "id": "t1", "name": "Skill",
             "input": {"skill": "superpowers:brainstorming"}},
        ]),
    ])
    result = claude_code.extract(f)
    [inv] = result.invocations
    assert inv.kind == "skill"
    assert inv.name == "superpowers:brainstorming"
    assert inv.detection == "explicit"
    assert inv.query == "please brainstorm the feature"
    assert inv.reasoning == "The brainstorming skill applies here."
    assert inv.project == "/proj/x"
    assert inv.session_id == "s1"
    assert inv.sidechain is False
    assert result.recognized >= 2


def test_query_is_stored_verbatim_not_truncated(tmp_path):
    long_query = "please brainstorm the feature " + ("x" * 400)
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user(long_query),
        _assistant([
            {"type": "tool_use", "id": "t1", "name": "Skill",
             "input": {"skill": "superpowers:brainstorming"}},
        ]),
    ])
    result = claude_code.extract(f)
    [inv] = result.invocations
    assert inv.query == long_query
    assert len(inv.query) > 400


def test_mcp_tool_use_splits_server_and_tool(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("screenshot please"),
        _assistant([{"type": "tool_use", "id": "t1",
                     "name": "mcp__pencil__get_screenshot", "input": {}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert (inv.kind, inv.server, inv.name) == ("mcp_tool", "pencil", "get_screenshot")
    assert inv.detection == "explicit"


def test_command_marker_becomes_skill_but_builtins_skipped(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("<command-name>/clear</command-name>"),
        _user("<command-name>/release</command-name> now"),
    ])
    [inv] = claude_code.extract(f).invocations
    assert (inv.kind, inv.name, inv.detection) == ("skill", "release", "command-marker")


def test_empty_thinking_block_yields_none_reasoning(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("go"),
        _assistant([
            {"type": "thinking", "thinking": "", "signature": "xxx"},
            {"type": "tool_use", "id": "t1", "name": "Skill", "input": {"skill": "release"}},
        ]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.reasoning is None


def test_reasoning_falls_back_to_previous_assistant_message(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("go"),
        _assistant([{"type": "thinking", "thinking": "Prior thought."},
                    {"type": "text", "text": "ok"}],
                   ts="2026-07-01T10:00:03.000Z"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.reasoning == "Prior thought."


def test_sidechain_flag_prefers_sidechain_dispatch_query(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("main thread question"),
        _user("subagent prompt", sidechain=True, ts="2026-07-01T10:00:02.000Z"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}], sidechain=True),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.sidechain is True
    assert inv.query == "subagent prompt"
    assert inv.query_source == "agent"


def test_tool_result_user_events_do_not_become_queries(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("real question"),
        {"type": "user", "sessionId": "s1", "timestamp": "2026-07-01T10:00:02.000Z",
         "cwd": "/proj/x", "isSidechain": False,
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "t0", "content": "output"}]}},
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.query == "real question"


def test_string_content_user_message_is_tracked_as_query(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        {"type": "user", "sessionId": "s1", "timestamp": "2026-07-01T10:00:00.000Z",
         "cwd": "/proj/x", "isSidechain": False,
         "message": {"role": "user", "content": "please release it"}},
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.query == "please release it"


def test_string_content_user_message_detects_command_marker(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        {"type": "user", "sessionId": "s1", "timestamp": "2026-07-01T10:00:00.000Z",
         "cwd": "/proj/x", "isSidechain": False,
         "message": {"role": "user",
                      "content": "<command-name>/release</command-name>"}},
    ])
    [inv] = claude_code.extract(f).invocations
    assert (inv.kind, inv.name, inv.detection) == ("skill", "release", "command-marker")


def test_malformed_lines_and_unknown_events_are_skipped(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-proj-x"
    d.mkdir(parents=True)
    f = d / "s1.jsonl"
    f.write_text('not json\n{"type":"queue-operation"}\n'
                 + json.dumps(_user("q")) + "\n")
    result = claude_code.extract(f)
    assert result.invocations == []
    assert result.recognized == 1  # only the user event


def test_meta_user_events_do_not_become_queries(tmp_path):
    real_user = _user("do the release")
    meta_user = _user("Base directory for this skill: /x/y # Skill body...",
                      ts="2026-07-01T10:00:02.000Z")
    meta_user["isMeta"] = True
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        real_user,
        meta_user,
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}], ts="2026-07-01T10:00:03.000Z"),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.query == "do the release"


def test_skill_invocation_records_source_line(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("first"),
        _assistant([{"type": "text", "text": "ok"}], ts="2026-07-01T10:00:03.000Z"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.source_line == 3  # 1-based: line 3 holds the tool_use event


def test_command_markers_still_detected_in_meta_events(tmp_path):
    meta_user = _user("<command-name>/release</command-name>")
    meta_user["isMeta"] = True
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [meta_user])
    [inv] = claude_code.extract(f).invocations
    assert inv.kind == "skill"
    assert inv.name == "release"
    assert inv.detection == "command-marker"


def test_main_thread_skill_invocation_is_user_sourced(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("please brainstorm the feature"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.query_source == "user"


def test_sidechain_dispatch_prompt_is_agent_sourced(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("main thread question"),
        _user("Implement task 3 exactly as specified in the plan.",
              sidechain=True, ts="2026-07-01T10:00:02.000Z"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}], sidechain=True),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.sidechain is True
    assert inv.query == "Implement task 3 exactly as specified in the plan."
    assert inv.query_source == "agent"


def test_sidechain_without_prior_sidechain_query_falls_back_to_user(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("main thread question"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}], sidechain=True),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.sidechain is True
    assert inv.query == "main thread question"
    assert inv.query_source == "user"


def test_command_marker_invocation_is_user_sourced(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("<command-name>/release</command-name> now"),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.query_source == "user"


def test_task_notification_events_do_not_become_queries(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("build the thing"),
        _user("<task-notification>\n<task-id>x</task-id>\n</task-notification>",
              ts="2026-07-01T10:00:02.000Z"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.query == "build the thing"
    assert inv.query_source == "user"


def test_system_reminder_events_do_not_become_queries(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("build the thing"),
        _user("<system-reminder>\nSome system message\n</system-reminder>",
              ts="2026-07-01T10:00:02.000Z"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}]),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.query == "build the thing"
    assert inv.query_source == "user"
