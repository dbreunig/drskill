import json

from drskill.traces import copilot


def _write(tmp_path, session, ws="h1", folder="file:///proj/x"):
    d = (tmp_path / "Library" / "Application Support" / "Code" / "User"
         / "workspaceStorage" / ws)
    (d / "chatSessions").mkdir(parents=True, exist_ok=True)
    (d / "workspace.json").write_text(json.dumps({"folder": folder}))
    f = d / "chatSessions" / "u1.json"
    f.write_text(json.dumps(session))
    return f


def _session(requests):
    return {"version": 3, "sessionId": "u1", "creationDate": 1782900000000,
            "requests": requests}


def test_discover(tmp_path):
    f = _write(tmp_path, _session([]))
    assert copilot.discover(tmp_path) == [f]


def test_mcp_tool_invocation(tmp_path):
    f = _write(tmp_path, _session([{
        "message": {"text": "grab a screenshot"},
        "timestamp": 1782900060000,
        "response": [
            {"kind": "prepareToolInvocation", "toolName": "x"},
            {"kind": "toolInvocationSerialized",
             "toolId": "mcp_pencil_get_screenshot", "toolCallId": "tc1",
             "invocationMessage": "Getting screenshot"},
        ],
    }]))
    [inv] = copilot.extract(f).invocations
    assert (inv.kind, inv.server, inv.name) == ("mcp_tool", "pencil", "get_screenshot")
    assert inv.query == "grab a screenshot"
    assert inv.reasoning is None
    assert inv.project == "/proj/x"
    assert inv.timestamp.year == 2026


def test_builtin_copilot_tools_ignored(tmp_path):
    f = _write(tmp_path, _session([{
        "message": {"text": "read it"},
        "timestamp": 1782900060000,
        "response": [{"kind": "toolInvocationSerialized",
                      "toolId": "copilot_readFile", "toolCallId": "tc1"}],
    }]))
    assert copilot.extract(f).invocations == []


def test_request_without_timestamp_uses_creation_date(tmp_path):
    f = _write(tmp_path, _session([{
        "message": {"text": "q"},
        "response": [{"kind": "toolInvocationSerialized",
                      "toolId": "mcp_s_t", "toolCallId": "tc1"}],
    }]))
    [inv] = copilot.extract(f).invocations
    assert inv.timestamp.year == 2025 or inv.timestamp.year == 2026


def test_malformed_session_file_raises_oserror_free_valueerror(tmp_path):
    f = _write(tmp_path, _session([]))
    f.write_text("{not json")
    result = copilot.extract(f)
    assert result.invocations == [] and result.recognized == 0


def test_skill_invocation_with_name(tmp_path):
    """Skill invocation with toolSpecificData as non-empty string."""
    f = _write(tmp_path, _session([{
        "message": {"text": "simplify this"},
        "timestamp": 1782900060000,
        "response": [
            {"kind": "toolInvocationSerialized",
             "toolId": "skill", "toolCallId": "tc1",
             "toolSpecificData": "plain-writing"},
        ],
    }]))
    [inv] = copilot.extract(f).invocations
    assert inv.kind == "skill"
    assert inv.name == "plain-writing"
    assert inv.detection == "explicit"
    assert inv.server is None


def test_skill_invocation_skips_missing_or_invalid_toolspecificdata(tmp_path):
    """Skill invocation without or with non-string toolSpecificData produces no invocation."""
    # Test 1: missing toolSpecificData
    f1 = _write(tmp_path, _session([{
        "message": {"text": "do something"},
        "timestamp": 1782900060000,
        "response": [
            {"kind": "toolInvocationSerialized",
             "toolId": "skill", "toolCallId": "tc1"},
        ],
    }]))
    assert copilot.extract(f1).invocations == []

    # Test 2: non-string toolSpecificData
    f2 = _write(tmp_path, _session([{
        "message": {"text": "do something"},
        "timestamp": 1782900060000,
        "response": [
            {"kind": "toolInvocationSerialized",
             "toolId": "skill", "toolCallId": "tc2",
             "toolSpecificData": {"x": 1}},
        ],
    }]))
    assert copilot.extract(f2).invocations == []


def test_non_dict_workspace_json_crash(tmp_path):
    """workspace.json with list content should not crash; project should be None."""
    f = _write(tmp_path, _session([{
        "message": {"text": "invoke mcp"},
        "timestamp": 1782900060000,
        "response": [
            {"kind": "toolInvocationSerialized",
             "toolId": "mcp_server_tool", "toolCallId": "tc1"},
        ],
    }]))
    # Overwrite workspace.json with array content
    (f.parent.parent / "workspace.json").write_text(json.dumps([]))
    result = copilot.extract(f)
    assert len(result.invocations) == 1
    assert result.invocations[0].project is None
