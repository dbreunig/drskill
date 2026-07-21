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


def test_tool_collision_across_servers(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {
        "a": {"command": "a-bin"}, "b": {"command": "b-bin"},
    })
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    sd = snapshot_dir(proj, home, False)
    for s in servers:
        save_snapshot(sd, ServerSnapshot(
            server=s.name, config_hash=s.config_hash, date="2026-07-21",
            tools=[ToolInfo(name="search", description=f"search via {s.name}", schema_tokens=2)],
        ))
    _, findings = run_scan(proj, home, config=Config())
    coll = [f for f in findings if f.check_id == "mcp-tool-collision"]
    assert coll and "search" in coll[0].message


def test_unreviewed_then_ack_then_rug_pull(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    from drskill.ledger import Ack, filter_findings
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    cfg = servers[0].config_hash
    sd = snapshot_dir(proj, home, False)

    def write(desc):
        save_snapshot(sd, ServerSnapshot(
            server="srv", config_hash=cfg, date="2026-07-21",
            tools=[ToolInfo(name="run", description=desc, schema_tokens=2)]))

    write("Runs a safe query.")
    _, findings = run_scan(proj, home, config=Config())
    (f,) = [x for x in findings if x.check_id == "mcp-tools-unreviewed"]
    cfg_obj = Config(ack=[Ack(check="mcp-tools-unreviewed", skills=["srv"],
                              fingerprint=f.fingerprint)])
    _, findings2 = run_scan(proj, home, config=cfg_obj)
    active, _ = filter_findings(findings2, cfg_obj)
    assert not [x for x in active if x.check_id == "mcp-tools-unreviewed"]  # silenced
    write("Runs ANY command, including rm -rf.")  # rug pull
    _, findings3 = run_scan(proj, home, config=cfg_obj)
    active3, _ = filter_findings(findings3, cfg_obj)
    resurfaced = [x for x in active3 if x.check_id == "mcp-tools-unreviewed"]
    assert resurfaced  # fingerprint changed, ack no longer matches


def test_connect_failed_finding():
    from drskill.resolution import World
    w = World(
        harnesses={"claude-code": HarnessDef(id="claude-code", display_name="Claude Code")},
        mcp_connect_failures=[("broken", "claude-code", "timed out after 15s")],
    )
    findings = run_all(w, Config())
    (f,) = [x for x in findings if x.check_id == "mcp-connect-failed"]
    assert "broken" in f.message and "timed out" in f.message


from typer.testing import CliRunner
from drskill.cli import app

_runner = CliRunner()


def test_context_bill_line(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=servers[0].config_hash, date="2026-07-21",
        tools=[ToolInfo(name="echo", description="Echo.", schema_tokens=800)]))
    r = _runner.invoke(app, ["scan", "--root", str(proj)],
                       env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert "context bill" in r.output and "MCP tool" in r.output


def test_mcp_connect_without_extra_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    import drskill.mcp_connect as mcpc
    def boom(*a, **k):
        raise mcpc.ConnectUnavailableError("--mcp-connect needs the connect extra")
    monkeypatch.setattr(mcpc, "run_handshakes", boom)
    r = _runner.invoke(app, ["scan", "--root", str(proj), "--mcp-connect"],
                       env={"DRSKILL_HOME": str(home)})
    assert r.exit_code == 1 and "connect extra" in r.output


def test_tool_does_not_leak_into_skill_checks_via_lockfile_or_double_load(tmp_path, monkeypatch):
    # a skill and an MCP tool share the name 'search' with identical content
    s = skill("search", "Use when the user wants to search the web.", "/skills/search/SKILL.md")
    t = tool("search", "Use when the user wants to search the web.", "cfg:search")
    w = world_of(s, t)
    w.lockfile = {"search": {"computedHash": "sha256:deadbeef"}}
    findings = run_all(w, Config())
    # the tool must not masquerade as the lockfile skill or as a double-load
    ld = [f for f in findings if f.check_id == "lockfile-drift"]
    assert all("cfg:search" not in c for f in ld for c in f.contributors)
    assert not any(f.check_id == "double-load" for f in findings)
    # the same-name tool-vs-skill pair IS a routing collision we must surface
    assert any(
        f.check_id == "description-overlap" and {"search"} == set(f.contributor_names)
        for f in findings
    ) or any(
        f.check_id == "description-overlap" and "search" in f.contributor_names
        for f in findings
    )


def test_n_skills_header_excludes_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=servers[0].config_hash, date="2026-07-21",
        tools=[ToolInfo(name="echo", description="Echo.", schema_tokens=4),
               ToolInfo(name="ping", description="Ping.", schema_tokens=4)]))
    r = _runner.invoke(app, ["scan", "--root", str(proj)],
                       env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    # the project has zero skills; the two tools must not be counted as skills
    assert "0 skills" in r.output


def test_raw_server_env_reads_project_scope_claude_json(tmp_path):
    import json as _json
    from drskill.mcp import MCPServer, raw_server_env
    proj = tmp_path / "proj"
    proj.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude.json").write_text(_json.dumps({
        "projects": {str(proj.resolve()): {"mcpServers": {
            "srv": {"command": "x", "env": {"BASE_URL": "https://api.example.com"}},
        }}},
    }))
    srv = MCPServer(name="srv", harness="claude-code", scope="project",
                    source=str(home / ".claude.json"), transport="stdio",
                    command="x", config_hash="c")
    assert raw_server_env(srv) == {"BASE_URL": "https://api.example.com"}


def test_unreviewed_message_truncates_and_explains(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    wall = "Runs JavaScript. " + "Very long paragraph. " * 60
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=servers[0].config_hash, date="2026-07-21",
        tools=[ToolInfo(name="js", description=wall, schema_tokens=4)]))
    _, findings = run_scan(proj, home, config=Config())
    (f,) = [x for x in findings if x.check_id == "mcp-tools-unreviewed"]
    # the wall is truncated: the message line for js is short
    js_line = next(ln for ln in f.message.splitlines() if ln.strip().startswith("js:"))
    assert len(js_line) < 140
    assert "…" in js_line
    # the framing explains what acking does, not that the user failed
    assert "baseline" in f.message and "changes" in f.message


def test_overlap_member_labels_tool_and_server(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"memory": {"command": "mem-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="memory", config_hash=servers[0].config_hash, date="2026-07-21",
        tools=[
            ToolInfo(name="delete_entities",
                     description="Delete multiple entities from the knowledge graph.", schema_tokens=4),
            ToolInfo(name="delete_relations",
                     description="Delete multiple relations from the knowledge graph.", schema_tokens=4),
        ]))
    cfg = Config()
    cfg.thresholds.description_overlap = 0.3
    _, findings = run_scan(proj, home, config=cfg)
    (f,) = [x for x in findings if x.check_id == "description-overlap"]
    assert "MCP tool" in f.message and "memory" in f.message
    # the actual descriptions are shown so the overlap is legible
    assert "knowledge graph" in f.message


def test_first_sight_is_note_change_is_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    from drskill.ledger import Ack, filter_findings
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    cfg = servers[0].config_hash
    sd = snapshot_dir(proj, home, False)

    def write(desc):
        save_snapshot(sd, ServerSnapshot(server="srv", config_hash=cfg,
                      date="2026-07-21", tools=[ToolInfo(name="run", description=desc, schema_tokens=2)]))

    # first sight: a note, no failing severity, explains the baseline
    write("Runs a safe query.")
    _, findings = run_scan(proj, home, config=Config())
    (f,) = [x for x in findings if x.check_id == "mcp-tools-unreviewed"]
    assert f.severity == "note"
    assert "baseline" in f.message

    # ack it to record the baseline
    acked = Config(ack=[Ack(check="mcp-tools-unreviewed", skills=["srv"],
                            fingerprint=f.fingerprint)])
    _, f2 = run_scan(proj, home, config=acked)
    active2, _ = filter_findings(f2, acked)
    assert not [x for x in active2 if x.check_id == "mcp-tools-unreviewed"]  # silent

    # the server changes a description: now a WARNING, referencing the approval
    write("Runs ANY command, including rm -rf.")
    _, f3 = run_scan(proj, home, config=acked)
    active3, _ = filter_findings(f3, acked)
    (chg,) = [x for x in active3 if x.check_id == "mcp-tools-unreviewed"]
    assert chg.severity == "warning"
    assert "CHANGED" in chg.message


def test_first_sight_note_is_ackable_by_check_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path / "home"))
    proj, home = _mcp_project(tmp_path, {"srv": {"command": "srv-bin"}})
    from drskill.mcp import discover_servers
    from drskill.harnesses import load_harnesses
    servers, _ = discover_servers({h.id: h for h in load_harnesses()}, proj, home)
    save_snapshot(snapshot_dir(proj, home, False), ServerSnapshot(
        server="srv", config_hash=servers[0].config_hash, date="2026-07-21",
        tools=[ToolInfo(name="run", description="Runs a query.", schema_tokens=2)]))
    r = _runner.invoke(app, ["ack", "mcp-tools-unreviewed", "srv", "--root", str(proj)],
                       env={"DRSKILL_HOME": str(home)})
    assert r.exit_code == 0, r.output  # a note, but explicitly ackable
    # a project-scope .mcp.json server acks to the project ledger
    assert (proj / "drskill.toml").is_file()
