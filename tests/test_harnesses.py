from drskill.harnesses import detect_harnesses, load_harnesses


def get(harnesses, hid):
    return next(h for h in harnesses if h.id == hid)


def test_loads_verified_core():
    hs = load_harnesses()
    assert {"claude-code", "pi"} <= {h.id for h in hs}
    cc, pi = get(hs, "claude-code"), get(hs, "pi")
    assert cc.paths_verified and cc.precedence_verified
    assert pi.paths_verified and pi.precedence_verified


def test_pi_rules_match_source():
    # pi 1.1.0 source: addAutoDiscoveredResources() adds the project dirs to
    # the accumulator before the user dirs, and toResolvedPaths() sorts by
    # resourcePrecedenceRank (project auto=1 < user auto=3), so skillPaths
    # reach loadSkills project-first; loadSkills is first-wins on name.
    pi = get(load_harnesses(), "pi")
    assert pi.search_order == "project-first"
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
    # project-first: the two project paths come before the two global paths
    assert [t[1] for t in triples] == ["project", "project", "user", "user"]
    assert triples[0][0] == tmp_path / "proj" / ".pi/skills"
    assert triples[0][2] == ".pi/skills"
    assert triples[2][0] == tmp_path / "home" / ".pi/agent/skills"


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
    # The verified-core allowlist: entries proven against the harness's own source or by
    # probing it. Everything else stays best-effort (seeded from vercel-labs/skills).
    core = {
        "claude-code",
        "cline",
        "cursor",
        "codex",
        "copilot",
        "dsh",
        "dsh-desktop-app",
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


def test_claude_code_declares_command_paths():
    h = next(h for h in load_harnesses() if h.id == "claude-code")
    assert h.command_project_paths == [".claude/commands"]
    assert h.command_global_paths == ["~/.claude/commands"]


def test_command_paths_default_empty():
    h = next(h for h in load_harnesses() if h.id == "codex")
    assert h.command_project_paths == []
    assert h.command_global_paths == []


def test_dsh_rules_match_probes():
    """Pins the 2026-10-14 empirical results (DeepSeek Harness 0.2.0).

    The CLI has no skill-enumeration verb, so the engine itself was the probe: a
    fixture tree with one uniquely-marked skill per candidate root, booted with
    `dsh --profile headless --json` under a fixture DSH_HOME and DSH_AGENTS_HOME,
    and the model's answer names what the catalog holds. Five runs, identical.

    All four roots are read; discovery is ONE level only (a *.md file directly in
    a root is a skill, anything two or three levels down is not); collisions are
    deterministic and rank-ordered (100 project .dsh/skills < 200 project
    .agents/skills < 400 user .dsh/skills < 500 user .agents/skills).
    """
    dsh = get(load_harnesses(), "dsh")
    assert dsh.paths_verified and dsh.precedence_verified
    assert dsh.search_order == "project-first"
    assert not dsh.recursive
    assert dsh.project_paths == [".dsh/skills", ".agents/skills"]
    assert dsh.global_paths == ["~/.dsh/skills", "~/.agents/skills"]
    assert set(dsh.root_md_paths) == set(dsh.project_paths + dsh.global_paths)

    app = get(load_harnesses(), "dsh-desktop-app")
    assert app.paths_verified and app.precedence_verified
    assert app.search_order == "project-first"
    assert not app.recursive
    assert app.project_paths == [".dsh/skills", ".agents/skills"]
    # The app keeps its own DSH_HOME but shares the machine's ~/.agents/skills.
    assert app.global_paths == ["AppData/Roaming/dsh-desktop/harness/skills", "~/.agents/skills"]


def test_dsh_detect_markers(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    home.mkdir()
    ids = {h.id for h in detect_harnesses(proj, home)}
    assert "dsh" not in ids  # nothing installed yet
    (proj / ".dsh").mkdir(parents=True)
    ids = {h.id for h in detect_harnesses(proj, home)}
    assert "dsh" in ids
    (home / "AppData" / "Roaming" / "dsh-desktop" / "harness").mkdir(parents=True)
    ids = {h.id for h in detect_harnesses(proj, home)}
    assert {"dsh", "dsh-desktop-app"} <= ids
