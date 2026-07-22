import json
from pathlib import Path

from drskill import suites
from drskill.models import Contributor, Provenance, TokenCost
from drskill.resolution import World, content_hash
from drskill.harnesses import HarnessDef


def write_skill(path: Path, name: str, description: str, body: str = "b") -> str:
    path.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    (path / "SKILL.md").write_text(text)
    return content_hash(text)


def plugin_cache(home: Path, marketplace: str, plugin: str, version: str):
    return home / ".claude" / "plugins" / "cache" / marketplace / plugin / version / "skills"


def _contrib(name, chash, source_kind="unmanaged", source=None):
    return Contributor(
        id=f"/skills/{name}/SKILL.md", name=name, scope="user",
        routing_text="desc", token_cost=TokenCost(catalog_tokens=1, body_tokens=0),
        content_hash=chash, source=Provenance(kind=source_kind, source=source),
    )


def _world(*cs):
    return World(
        contributors={c.id: c for c in cs},
        harnesses={"claude-code": HarnessDef(id="claude-code", display_name="Claude Code")},
    )


def test_registry_maps_plugin_skill_by_content_hash(tmp_path):
    home = tmp_path / "home"
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    h = write_skill(skills / "brainstorming", "brainstorming", "Use when planning.")
    assert suites.build_registry(home)[h] == "superpowers"


def test_registry_indexes_every_cached_version(tmp_path):
    home = tmp_path / "home"
    old = plugin_cache(home, "official", "superpowers", "4.3.1")
    h_old = write_skill(old / "brainstorming", "brainstorming", "Old wording.")
    new = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(new / "brainstorming", "brainstorming", "New wording.")
    assert suites.build_registry(home)[h_old] == "superpowers"


def test_registry_finds_nested_plugin_skills(tmp_path):
    home = tmp_path / "home"
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    h = write_skill(skills / "group" / "nested", "nested", "Use when nested.")
    assert suites.build_registry(home)[h] == "superpowers"


def test_registry_is_deterministic_across_duplicate_plugins(tmp_path):
    home = tmp_path / "home"
    # two plugins ship byte-identical content; the label must be stable
    a = plugin_cache(home, "official", "alpha-suite", "1.0")
    b = plugin_cache(home, "official", "beta-suite", "1.0")
    h1 = write_skill(a / "shared", "shared", "Use when shared.")
    write_skill(b / "shared", "shared", "Use when shared.")
    assert suites.build_registry(home)[h1] == "alpha-suite"  # sorted, first wins


def test_registry_empty_when_no_cache(tmp_path):
    assert suites.build_registry(tmp_path / "nohome") == {}


def test_assign_uses_plugin_match(tmp_path):
    home = tmp_path / "home"
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    h = write_skill(skills / "brainstorming", "brainstorming", "Use when planning.")
    w = _world(_contrib("brainstorming", h))
    suites.assign_suites(w, home)
    assert next(iter(w.contributors.values())).suite == "superpowers"


def test_assign_falls_back_to_lockfile_provenance(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # no plugin match, but drskill already marked this skill skills-lock
    w = _world(_contrib("scaffold-docs", "sha256:zz",
                        source_kind="skills-lock", source="dbreunig/scaffold-docs-skill"))
    suites.assign_suites(w, home)
    assert next(iter(w.contributors.values())).suite == "dbreunig/scaffold-docs-skill"


def test_assign_leaves_unmanaged_skill_blank(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # a local skill with no plugin match and no lockfile provenance: no suite
    w = _world(_contrib("solo", "sha256:zz", source_kind="unmanaged"))
    suites.assign_suites(w, home)
    assert next(iter(w.contributors.values())).suite is None


def test_assign_ignores_mcp_tools(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    t = Contributor(
        id="cfg:tool", name="tool", kind="mcp_tool", scope="user",
        routing_text="d", token_cost=TokenCost(catalog_tokens=1, body_tokens=0),
        content_hash="sha256:zz",
    )
    w = _world(t)
    suites.assign_suites(w, home)
    assert next(iter(w.contributors.values())).suite is None


from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def test_list_shows_plugin_suite_for_flat_copy(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(skills / "brainstorming", "brainstorming", "Use when planning a feature.")
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "brainstorming",
                "brainstorming", "Use when planning a feature.")
    r = runner.invoke(app, ["list", "--root", str(proj)],
                      env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "suite" in r.output and "superpowers" in r.output


def test_list_suite_column_escapes_lockfile_source(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "weird", "weird", "Use when doing a weird task.")
    # a project lockfile gives this skill a source; the suite surfaces it, escaped
    (proj / "skills-lock.json").write_text(json.dumps({
        "version": 1,
        "skills": {"weird": {"source": "[red]x[/red]/repo"}},
    }))
    r = runner.invoke(app, ["list", "--root", str(proj)],
                      env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert "[red]x[/red]/repo" in r.output and "\x1b[31m" not in r.output


from drskill.mcp_connect import ServerSnapshot, ToolInfo, save_snapshot, snapshot_dir


def test_list_includes_mcp_tools_and_sorts_by_suite(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    # a superpowers plugin skill in the cache
    sp = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(sp / "brainstorming", "brainstorming", "Use when planning a feature.")
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "brainstorming",
                "brainstorming", "Use when planning a feature.")
    write_skill(proj / ".claude" / "skills" / "solo", "solo", "Use when solo.")
    # a configured MCP server with a snapshot of one tool
    proj_mcp = {"mcpServers": {"memory": {"command": "mem-bin"}}}
    (proj / ".mcp.json").write_text(json.dumps(proj_mcp))
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    cfg = next(s.config_hash for s in servers if s.name == "memory")
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="memory", config_hash=cfg, date="2026-07-21",
        tools=[ToolInfo(name="read_graph", description="Read the graph.", schema_tokens=3)]))
    r = runner.invoke(app, ["list", "--root", str(proj)],
                      env={"DRSKILL_HOME": str(home), "COLUMNS": "220"})
    assert r.exit_code == 0, r.output
    # the tool is listed, marked as an mcp tool, grouped under its server
    assert "read_graph" in r.output and "mcp tool" in r.output
    assert "memory" in r.output  # the tool's suite is its server
    # skills still show their suite
    assert "superpowers" in r.output
