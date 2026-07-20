from drskill.harnesses import detect_harnesses, load_harnesses


def get(harnesses, hid):
    return next(h for h in harnesses if h.id == hid)


def test_loads_verified_core():
    hs = load_harnesses()
    assert {"claude-code", "pi"} <= {h.id for h in hs}
    assert get(hs, "claude-code").verified is True
    assert get(hs, "pi").verified is True


def test_pi_rules_match_docs():
    pi = get(load_harnesses(), "pi")
    assert pi.search_order == "global-first"
    assert pi.project_paths == [".pi/skills", ".agents/skills"]
    assert pi.global_paths == ["~/.pi/agent/skills", "~/.agents/skills"]
    assert set(pi.root_md_paths) == {".pi/skills", "~/.pi/agent/skills"}


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
    core = {"claude-code", "cursor", "codex", "copilot", "gemini-cli", "pi"}
    for h in hs:
        if h.id not in core:
            assert h.verified is False, f"{h.id} must stay best-effort"


def test_every_entry_has_at_least_one_path():
    for h in load_harnesses():
        assert h.project_paths or h.global_paths, h.id
