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


from drskill.lint import build_lint_world


def write_skill(d: Path, name: str):
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Use when testing {name}.\n---\nbody\n"
    )


def test_build_world_skill_target(tmp_path):
    write_skill(tmp_path / "s", "s")
    w = build_lint_world(classify(tmp_path / "s"))
    assert len(w.contributors) == 1
    c = next(iter(w.contributors.values()))
    assert c.name == "s" and c.deployments == []
    assert w.plugin is None


def test_build_world_plugin_target(tmp_path):
    root = tmp_path / "p"
    make_plugin(root)
    write_skill(root / "skills" / "alpha", "alpha")
    write_skill(root / "skills" / "beta", "beta")
    # nested too deep: not discovered as a contributor
    write_skill(root / "skills" / "group" / "gamma", "gamma")
    (root / "mcp.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"srv": {"type": "stdio", "command": "server-bin",
                               "env": {"API_KEY": "sk-live-1234567890abcdef"}}},
    }))
    w = build_lint_world(classify(root))
    assert w.plugin is not None and w.plugin.name == "demo-plugin"
    assert sorted(c.name for c in w.contributors.values()) == ["alpha", "beta"]
    assert len(w.mcp_servers) == 1
    s = w.mcp_servers[0]
    assert s.harness == "" and s.in_project is True
    assert w.plugin_mcp is not None and w.plugin_mcp.data is not None


def test_build_world_bad_manifest_and_bad_mcp(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "plugin.json").write_text("{broken")
    (root / "mcp.json").write_text("{also broken")
    w = build_lint_world(classify(root))
    assert w.plugin.parse_error is not None
    assert w.plugin_mcp.data is None
    assert len(w.mcp_config_errors) == 1


def test_build_world_standalone_mcp_provisional_root(tmp_path):
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {},
    }))
    w = build_lint_world(classify(f))
    assert w.plugin_mcp.provisional_root is True
    assert w.plugin_mcp.root == str(tmp_path.resolve())


def test_build_world_harness_mcp(tmp_path):
    f = tmp_path / ".mcp.json"
    f.write_text(json.dumps({"mcpServers": {"a": {"command": "foo"}}}))
    w = build_lint_world(classify(f))
    assert w.plugin_mcp is None
    assert [s.name for s in w.mcp_servers] == ["a"]


def test_suites_only_name_registered_checks():
    from drskill.checks import REGISTRY, run_all  # noqa: F401  (run_all imports all modules)
    from drskill.resolution import World
    from drskill.ledger import Config
    from drskill import lint as lint_mod

    run_all(World(), Config())  # force-register every check module
    # plugin_spec / mcp_spec ids only exist after Tasks 6-7; tolerate both
    # phases by checking the suites that must already resolve.
    assert set(lint_mod.SKILL_CONTENT_CHECKS) <= set(REGISTRY)
    assert set(lint_mod.MCP_STATIC_CHECKS) <= set(REGISTRY)
    assert set(lint_mod.MCP_CONNECT_CHECKS) <= set(REGISTRY)


def test_run_checks_runs_only_named(tmp_path):
    from drskill.checks import run_checks
    from drskill.ledger import Config

    write_skill(tmp_path / "s", "other-name")  # folder 's', name mismatch
    w = build_lint_world(classify(tmp_path / "s"))
    findings = run_checks(w, Config(), ["spec-name-mismatch"])
    assert {f.check_id for f in findings} == {"spec-name-mismatch"}


def test_checks_for_shapes():
    from drskill.lint import LintTarget, checks_for, SKILL_CONTENT_CHECKS

    skill = LintTarget(kind="skill", path=Path("."))
    assert checks_for(skill, mcp_connect=False) == SKILL_CONTENT_CHECKS
    plug = LintTarget(kind="plugin", path=Path("."))
    ids = checks_for(plug, mcp_connect=False)
    assert "exact-duplicate" in ids and "mcp-secret-in-config" in ids
    assert "name-shadow" not in ids and "lockfile-drift" not in ids
    harness = LintTarget(kind="mcp", path=Path("."), mcp_flavor="harness")
    ids = checks_for(harness, mcp_connect=False)
    assert "mcp-dead-server" in ids and "mcp-insecure-url" in ids
    agent = LintTarget(kind="mcp", path=Path("."), mcp_flavor="agent-plugins")
    assert "mcp-dead-server" not in checks_for(agent, mcp_connect=False)
    assert "mcp-tool-poisoning" in checks_for(agent, mcp_connect=True)
