import datetime as dt

from drskill import pins as pins_mod
from drskill.harnesses import HarnessDef
from drskill.models import Deployment
from drskill.resolution import World
from drskill.traces import crossref
from drskill.traces.model import Invocation
from tests.test_models import make_contributor

TODAY = dt.date(2026, 9, 6)


def inv(name, days_ago, kind="skill", server=None, harness="claude-code"):
    ts = dt.datetime(2026, 9, 6, 12) - dt.timedelta(days=days_ago)
    return Invocation(harness=harness, session_id="s", timestamp=ts, kind=kind,
                      name=name, server=server, detection="explicit",
                      source_file="/t.jsonl")


def contributor(name, kind="skill", suite=None, harness="claude-code", id=None):
    c = make_contributor(id=id or f"/x/{name}/SKILL.md", name=name, kind=kind)
    c.suite = suite
    c.deployments.append(Deployment(harness=harness, path=c.id, scope="project",
                                    via_symlink=False, order=0))
    return c


def world_of(*cs, servers=()):
    w = World(contributors={c.id: c for c in cs},
              harnesses={"claude-code": HarnessDef(id="claude-code", display_name="CC")})
    w.mcp_servers = list(servers)
    return w


def test_unused_skill_is_reported_with_old_coverage():
    w = world_of(contributor("used"), contributor("dusty"))
    invs = [inv("used", days_ago=100), inv("used", days_ago=1)]
    result = crossref.unused_contributors(w, invs, {}, 90, TODAY)
    out = result.unused
    assert [(u.kind, u.name) for u in out] == [("skill", "dusty")]
    assert out[0].where == "claude-code"
    assert result.checked == 2


def test_young_coverage_returns_none():
    w = world_of(contributor("dusty"))
    invs = [inv("whatever", days_ago=10)]
    assert crossref.unused_contributors(w, invs, {}, 90, TODAY) is None


def test_no_invocations_returns_none():
    w = world_of(contributor("dusty"))
    assert crossref.unused_contributors(w, [], {}, 90, TODAY) is None


def test_plugin_qualified_name_counts_as_used():
    w = world_of(contributor("brainstorming", suite="superpowers"))
    invs = [inv("superpowers:brainstorming", days_ago=100)]
    assert crossref.unused_contributors(w, invs, {}, 90, TODAY).unused == []


def test_fresh_pin_skips_the_contributor():
    c = contributor("newish")
    w = world_of(c, contributor("anchor"))
    invs = [inv("anchor", days_ago=100)]
    pin = pins_mod.Pin(loadout="d/p", selector="skill:newish", source_type="drskill",
                       content_hash="sha256:" + "ab" * 32,
                       installed_at=(TODAY - dt.timedelta(days=5)).isoformat())
    result = crossref.unused_contributors(w, invs, {c.id: pin}, 90, TODAY)
    assert [(u.kind, u.name) for u in result.unused] == []
    assert result.checked == 1  # the fresh pin was skipped, only "anchor" was judged


def test_mcp_tool_matches_by_server_and_name():
    from drskill.mcp import MCPServer

    cfg = "cc" * 32
    server = MCPServer(name="pencil", harness="claude-code", scope="project",
                       source="/x", transport="stdio", command="npx", args=[],
                       env_names=[], config_hash=cfg)
    used = contributor("get_screenshot", kind="mcp_tool", id=f"{cfg}:get_screenshot")
    dusty = contributor("export_html", kind="mcp_tool", id=f"{cfg}:export_html")
    w = world_of(used, dusty, servers=[server])
    invs = [inv("get_screenshot", days_ago=100, kind="mcp_tool", server="pencil")]
    result = crossref.unused_contributors(w, invs, {}, 90, TODAY)
    out = result.unused
    assert [(u.kind, u.name) for u in out] == [("mcp tool", "export_html")]
    assert out[0].where == "pencil"
    assert out[0].server == "pencil"
    assert result.checked == 2


def test_unresolvable_server_is_skipped():
    dusty = contributor("tool", kind="mcp_tool", id=("dd" * 32) + ":tool")
    anchor = contributor("anchor")
    w = world_of(dusty, anchor)  # no mcp_servers registered
    invs = [inv("anchor", days_ago=100)]
    result = crossref.unused_contributors(w, invs, {}, 90, TODAY)
    assert result.unused == []
    assert result.checked == 1  # only "anchor" was judged; the tool's server never resolved


def test_system_contributors_are_skipped():
    c = contributor("vendored")
    c.system = True
    w = world_of(c, contributor("anchor"))
    invs = [inv("anchor", days_ago=100)]
    result = crossref.unused_contributors(w, invs, {}, 90, TODAY)
    assert result.unused == []
    assert result.checked == 1


def test_checked_is_zero_when_every_contributor_is_skipped_by_guards():
    # the covered harness only sees a fresh pin -- nothing was actually judged
    c = contributor("newish")
    w = world_of(c)
    invs = [inv("newish", days_ago=100)]
    pin = pins_mod.Pin(loadout="d/p", selector="skill:newish", source_type="drskill",
                       content_hash="sha256:" + "ab" * 32,
                       installed_at=(TODAY - dt.timedelta(days=5)).isoformat())
    result = crossref.unused_contributors(w, invs, {c.id: pin}, 90, TODAY)
    assert result.unused == []
    assert result.checked == 0


def test_unused_sort_ranks_skills_before_commands_before_mcp_tools():
    from drskill.mcp import MCPServer

    cfg = "cc" * 32
    server = MCPServer(name="pencil", harness="claude-code", scope="project",
                       source="/x", transport="stdio", command="npx", args=[],
                       env_names=[], config_hash=cfg)
    tool = contributor("ztool", kind="mcp_tool", id=f"{cfg}:ztool")
    cmd = contributor("bcommand", kind="command")
    skill = contributor("askill", kind="skill")
    w = world_of(tool, cmd, skill, servers=[server])
    invs = [inv("anchor", days_ago=100)]
    out = crossref.unused_contributors(w, invs, {}, 90, TODAY).unused
    assert [(u.kind, u.name) for u in out] == [
        ("skill", "askill"), ("command", "bcommand"), ("mcp tool", "ztool"),
    ]
