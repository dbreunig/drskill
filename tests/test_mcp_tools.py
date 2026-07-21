from drskill.checks import run_all
from drskill.ledger import Config
from drskill.models import Contributor, Deployment, TokenCost
from drskill.resolution import World
from drskill.harnesses import HarnessDef


def skill(name, desc, cid, harness="claude-code", body="body text here"):
    return Contributor(
        id=cid, name=name, scope="project", routing_text=desc, body=body,
        token_cost=TokenCost(catalog_tokens=10, body_tokens=5), content_hash=cid,
        deployments=[Deployment(harness=harness, path=cid, scope="project",
                                via_symlink=False, order=0)],
    )


def tool(name, desc, cid, harness="claude-code"):
    return Contributor(
        id=cid, name=name, kind="mcp_tool", scope="user", routing_text=desc,
        token_cost=TokenCost(catalog_tokens=8, body_tokens=0), content_hash=cid,
        deployments=[Deployment(harness=harness, path=cid, scope="user",
                                via_symlink=False, order=0)],
    )


def world_of(*contribs):
    return World(
        contributors={c.id: c for c in contribs},
        harnesses={"claude-code": HarnessDef(
            id="claude-code", display_name="Claude Code",
            paths_verified=True, precedence_verified=True)},
    )


SKILL_CHECKS = {
    "spec-name-mismatch", "spec-missing-description", "spec-description-too-long",
    "spec-invalid-frontmatter", "missing-activation", "generic-description",
    "opposing-imperatives", "budget-body-tokens", "exact-duplicate",
    "near-duplicate", "frontmatter-angle-brackets",
}


def test_skill_checks_ignore_tools():
    # a tool whose text would trip skill checks if it were treated as a skill
    t = tool("vague", "Helps.", "hash1:vague")  # generic + missing-activation bait
    findings = run_all(world_of(t), Config())
    assert not any(f.check_id in SKILL_CHECKS for f in findings)


def test_two_identical_tools_are_not_exact_duplicate():
    a = tool("search", "Search the web.", "h1:search")
    b = tool("search", "Search the web.", "h2:search")
    findings = run_all(world_of(a, b), Config())
    assert not any(f.check_id == "exact-duplicate" for f in findings)


def test_description_overlap_sees_tool_vs_skill():
    s = skill("web-search", "Use when the user wants to search the web for pages.",
              "/skills/web-search/SKILL.md")
    t = tool("search", "Use when the user wants to search the web for pages.",
             "h1:search")
    findings = run_all(world_of(s, t), Config())
    overlaps = [f for f in findings if f.check_id == "description-overlap"]
    assert overlaps and {"web-search", "search"} <= set(overlaps[0].contributor_names)


import json
from drskill.mcp_connect import ServerSnapshot, ToolInfo, save_snapshot, snapshot_dir
from drskill.pipeline import run_scan


def _mcp_project(tmp_path, servers: dict):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude" / "skills").mkdir(parents=True)
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": servers}))
    return proj, home


def test_snapshot_builds_tool_contributors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    cfg = next(s.config_hash for s in servers if s.name == "srv")
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=cfg, date="2026-07-21",
        tools=[ToolInfo(name="echo", description="Echo it.", schema_tokens=4)],
    ))
    world, _ = run_scan(proj, home, config=Config())
    tools = [c for c in world.contributors.values() if c.kind == "mcp_tool"]
    assert [c.name for c in tools] == ["echo"]
    assert tools[0].routing_text == "Echo it."
    assert tools[0].deployments[0].harness == "claude-code"
