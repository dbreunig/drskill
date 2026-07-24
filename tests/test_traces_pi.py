import json

from drskill.traces import pi


def _write(tmp_path, events, slug="--proj-x--", name="2026-07-11T18-03-13-846Z_p1.jsonl"):
    d = tmp_path / ".pi" / "agent" / "sessions" / slug
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return f


def _header(cwd="/proj/x"):
    return {"type": "session", "version": 3, "id": "p1",
            "timestamp": "2026-07-11T18:03:13.846Z", "cwd": cwd}


def _msg(role, content, ts="2026-07-11T18:04:00.000Z"):
    return {"type": "message", "id": "m", "parentId": None, "timestamp": ts,
            "message": {"role": role, "content": content}}


def test_discover(tmp_path):
    f = _write(tmp_path, [_header()])
    assert pi.discover(tmp_path) == [f]


def test_read_of_skill_md_is_heuristic_skill_with_reasoning(tmp_path):
    f = _write(tmp_path, [
        _header(),
        _msg("user", [{"type": "text", "text": "use the maps skill"}]),
        _msg("assistant", [
            {"type": "thinking", "thinking": "Load overturemaps first."},
            {"type": "toolCall", "id": "read_0", "name": "read",
             "arguments": {"path": "/Users/d/.pi/agent/skills/overturemaps/SKILL.md"}},
        ]),
    ])
    [inv] = pi.extract(f).invocations
    assert (inv.kind, inv.name, inv.detection) == ("skill", "overturemaps", "skill-read")
    assert inv.query == "use the maps skill"
    assert inv.reasoning == "Load overturemaps first."
    assert inv.project == "/proj/x"
    assert inv.session_id == "p1"


def test_skill_read_records_source_line(tmp_path):
    f = _write(tmp_path, [
        _header(),
        _msg("user", [{"type": "text", "text": "use the maps skill"}]),
        _msg("assistant", [
            {"type": "thinking", "thinking": "Load overturemaps first."},
            {"type": "toolCall", "id": "read_0", "name": "read",
             "arguments": {"path": "/Users/d/.pi/agent/skills/overturemaps/SKILL.md"}},
        ]),
    ])
    [inv] = pi.extract(f).invocations
    assert inv.source_line == 3  # 1-based: line 3 holds the assistant message


def test_bash_touching_skill_md_counts(tmp_path):
    f = _write(tmp_path, [
        _header(),
        _msg("assistant", [
            {"type": "toolCall", "id": "bash_1", "name": "bash",
             "arguments": {"command": "cat /x/skills/plain-writing/SKILL.md"}},
        ]),
    ])
    [inv] = pi.extract(f).invocations
    assert inv.name == "plain-writing"


def test_mcp_style_toolcall_reserved_pattern(tmp_path):
    f = _write(tmp_path, [
        _header(),
        _msg("assistant", [
            {"type": "toolCall", "id": "x_0", "name": "mcp__pencil__get_screenshot",
             "arguments": {}},
        ]),
    ])
    [inv] = pi.extract(f).invocations
    assert (inv.kind, inv.server, inv.name) == ("mcp_tool", "pencil", "get_screenshot")
    assert inv.detection == "explicit"


def test_builtin_tools_ignored_and_toolresults_not_queries(tmp_path):
    f = _write(tmp_path, [
        _header(),
        _msg("user", [{"type": "text", "text": "the question"}]),
        _msg("toolResult", [{"type": "text", "text": "file contents"}]),
        _msg("assistant", [
            {"type": "toolCall", "id": "bash_9", "name": "bash",
             "arguments": {"command": "ls"}},
            {"type": "toolCall", "id": "read_1", "name": "read",
             "arguments": {"path": "/x/skills/foo/SKILL.md"}},
        ]),
    ])
    [inv] = pi.extract(f).invocations
    assert inv.name == "foo"
    assert inv.query == "the question"
