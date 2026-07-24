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


def test_sidechain_flag_and_query_skips_sidechain_users(tmp_path):
    f = _write(tmp_path / ".claude" / "projects" / "-proj-x", "s1", [
        _user("main thread question"),
        _user("subagent prompt", sidechain=True, ts="2026-07-01T10:00:02.000Z"),
        _assistant([{"type": "tool_use", "id": "t1", "name": "Skill",
                     "input": {"skill": "release"}}], sidechain=True),
    ])
    [inv] = claude_code.extract(f).invocations
    assert inv.sidechain is True
    assert inv.query == "main thread question"


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


def test_malformed_lines_and_unknown_events_are_skipped(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-proj-x"
    d.mkdir(parents=True)
    f = d / "s1.jsonl"
    f.write_text('not json\n{"type":"queue-operation"}\n'
                 + json.dumps(_user("q")) + "\n")
    result = claude_code.extract(f)
    assert result.invocations == []
    assert result.recognized == 1  # only the user event
