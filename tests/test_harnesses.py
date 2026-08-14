from drskill.harnesses import detect_harnesses, load_harnesses


def get(harnesses, hid):
    return next(h for h in harnesses if h.id == hid)


def test_loads_verified_core():
    hs = load_harnesses()
    assert {"claude-code", "pi"} <= {h.id for h in hs}
    cc, pi = get(hs, "claude-code"), get(hs, "pi")
    assert cc.paths_verified and cc.precedence_verified
    assert pi.paths_verified and pi.precedence_verified


def test_pi_rules_match_docs():
    pi = get(load_harnesses(), "pi")
    assert pi.search_order == "global-first"
    assert pi.project_paths == [".pi/skills", ".agents/skills"]
    assert pi.global_paths == ["~/.pi/agent/skills", "~/.agents/skills"]
    assert set(pi.root_md_paths) == {".pi/skills", "~/.pi/agent/skills"}


def test_copilot_rules_match_probes():
    """Pins the 2026-08-13 empirical results (Copilot CLI 1.0.80,
    `copilot skill list` over collision fixtures): project shadows
    personal, and .github > .agents > .claude within project scope."""
    cp = get(load_harnesses(), "copilot")
    assert cp.paths_verified and cp.precedence_verified
    assert cp.search_order == "project-first"
    assert cp.recursive
    assert cp.project_paths == [".github/skills", ".agents/skills", ".claude/skills"]
    assert cp.global_paths == ["~/.copilot/skills", "~/.agents/skills"]


def test_opencode_rules_match_probes():
    """Pins the 2026-08-13 empirical results (opencode-ai 1.18.18,
    `opencode debug skill` over collision fixtures): singular and plural
    dirs in both scopes plus external .claude/.agents trees, all
    recursive. Precedence stays UNVERIFIED by design: repeated runs
    showed collision winners flipping between runs (parallel-scan
    race), so the path order encodes modal winners only."""
    oc = get(load_harnesses(), "opencode")
    assert oc.paths_verified and not oc.precedence_verified
    assert oc.search_order == "project-first"
    assert oc.recursive
    assert oc.project_paths == [
        ".opencode/skills",
        ".opencode/skill",
        ".agents/skills",
        ".claude/skills",
    ]
    assert oc.global_paths == [
        "~/.config/opencode/skills",
        "~/.config/opencode/skill",
        "~/.agents/skills",
        "~/.claude/skills",
    ]


def test_search_paths_order_and_scope(tmp_path):
    pi = get(load_harnesses(), "pi")
    triples = pi.search_paths(tmp_path / "proj", tmp_path / "home")
    # global-first: the two global paths come before the two project paths
    assert [t[1] for t in triples] == ["user", "user", "project", "project"]
    assert triples[0][0] == tmp_path / "home" / ".pi/agent/skills"
    assert triples[2][0] == tmp_path / "proj" / ".pi/skills"
    assert triples[2][2] == ".pi/skills"


def test_global_only_drops_project_paths(tmp_path):
    cc = get(load_harnesses(), "claude-code")
    triples = cc.search_paths(tmp_path, tmp_path / "home", global_only=True)
    assert all(scope == "user" for _, scope, _ in triples)


def test_detect_by_marker(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    (proj / ".claude").mkdir(parents=True)
    home.mkdir()
    ids = {h.id for h in detect_harnesses(proj, home)}
    assert ids == {"claude-code"}
    # global mode ignores project markers
    assert detect_harnesses(proj, home, global_only=True) == []


def test_core_six_present():
    ids = {h.id for h in load_harnesses()}
    assert {"claude-code", "cursor", "codex", "copilot", "gemini-cli", "pi"} <= ids


def test_vendored_entries_are_unverified_by_default():
    hs = load_harnesses()
    core = {
        "claude-code",
        "cline",
        "cursor",
        "codex",
        "copilot",
        "gemini-cli",
        "opencode",
        "pi",
    }
    for h in hs:
        if h.id not in core:
            assert not h.paths_verified and not h.precedence_verified, (
                f"{h.id} must stay best-effort"
            )


MCP_ONLY_HARNESSES = {"claude-desktop"}


def test_every_entry_has_at_least_one_path():
    """Skill-bearing harnesses must keep skill paths; only the explicit
    MCP-only entries may omit them. A blanket or-clause here would let a
    data edit silently strip a real harness's skill paths."""
    for h in load_harnesses():
        if h.id in MCP_ONLY_HARNESSES:
            assert h.mcp_project_configs or h.mcp_global_configs, h.id
            assert not h.project_paths and not h.global_paths, h.id
        else:
            assert h.project_paths or h.global_paths, h.id
