import json
from pathlib import Path

import pytest

from drskill.lint import LintUsageError, classify


def make_plugin(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "demo-plugin",
    }))


def test_classify_plugin_dir(tmp_path):
    make_plugin(tmp_path / "p")
    t = classify(tmp_path / "p")
    assert t.kind == "plugin"


def test_classify_skill_dir_and_file(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nb\n")
    assert classify(d).kind == "skill"
    assert classify(d / "SKILL.md").kind == "skill"


def test_plugin_wins_over_skill(tmp_path):
    d = tmp_path / "both"
    make_plugin(d)
    (d / "SKILL.md").write_text("---\nname: both\ndescription: d\n---\nb\n")
    assert classify(d).kind == "plugin"


def test_classify_mcp_agent_plugins_flavor(tmp_path):
    f = tmp_path / "anything.json"
    f.write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {},
    }))
    t = classify(f)
    assert t.kind == "mcp" and t.mcp_flavor == "agent-plugins"


def test_classify_mcp_next_to_plugin_json(tmp_path):
    make_plugin(tmp_path)
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({"mcpServers": {}}))
    assert classify(f).mcp_flavor == "agent-plugins"


def test_classify_mcp_harness_flavor(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"s": {"command": "srv"}}}))
    t = classify(f)
    assert t.kind == "mcp" and t.mcp_flavor == "harness"


def test_classify_unparseable_mcp_json_still_mcp(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text("{not json")
    assert classify(f).kind == "mcp"


def test_classify_rejects_unknown(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(LintUsageError):
        classify(d)
    with pytest.raises(LintUsageError):
        classify(tmp_path / "missing")


def test_forced_type_overrides(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: s\ndescription: d\n---\nb\n")
    make_plugin(d)
    assert classify(d, forced="skill").kind == "skill"
