import pytest

from drskill import content, loadout_drift, pins
from drskill.models import Contributor, Provenance, TokenCost

FILES = [{"path": "SKILL.md", "data": b"body\n", "executable": False}]
DIR_HASH = content.manifest_hash(FILES)
SKILL_HASH = "sha256:" + "ab" * 32


def contributor(name, kind="skill", content_hash=SKILL_HASH, id=None):
    return Contributor(
        id=id or f"/tmp/{name}", kind=kind, name=name,
        source=Provenance(kind="unmanaged", source=None), scope="project",
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash=content_hash,
    )


def entry(name="citation", source_type="drskill", content_hash=DIR_HASH,
          kind="skill", metadata=None):
    return {"kind": kind, "selector": f"{kind}:{name}", "name": name,
            "source_type": source_type, "source_reference": source_type,
            "content_hash": content_hash, "local_only": source_type == "local",
            "metadata": metadata if metadata is not None else {}}


@pytest.fixture
def collected(monkeypatch):
    monkeypatch.setattr(content, "collect_files", lambda c: list(FILES))


def classify_one(e, contributors):
    return loadout_drift.classify_entries([e], contributors)[0]


def skill_contributor(id, name, content_hash):
    return contributor(name, content_hash=content_hash, id=id)


def skill_entry(selector, content_hash):
    _, name = selector.split(":", 1)
    return {"kind": "skill", "selector": selector, "name": name,
            "source_type": "local", "source_reference": "local",
            "content_hash": content_hash, "local_only": True,
            "metadata": {}}


def test_hosted_entry_matches_and_changes(collected):
    assert classify_one(entry(), [contributor("citation")]).state == "matches"
    changed = entry(content_hash="sha256:" + "00" * 32)
    assert classify_one(changed, [contributor("citation")]).state == "changed"


def test_github_entry_uses_the_directory_hash(collected):
    e = entry(source_type="github", content_hash=SKILL_HASH,
              metadata={"directory_hash": DIR_HASH})
    assert classify_one(e, [contributor("citation")]).state == "matches"
    e["metadata"]["directory_hash"] = "sha256:" + "00" * 32
    assert classify_one(e, [contributor("citation")]).state == "changed"


def test_legacy_github_entry_uses_the_skill_hash():
    e = entry(source_type="github", content_hash=SKILL_HASH, metadata={})
    assert classify_one(e, [contributor("citation")]).state == "matches"
    other = contributor("citation", content_hash="sha256:" + "cd" * 32)
    assert classify_one(e, [other]).state == "changed"


def test_local_entry_uses_the_contributor_hash():
    e = entry(source_type="local", content_hash=SKILL_HASH)
    assert classify_one(e, [contributor("citation")]).state == "matches"
    drifted = contributor("citation", content_hash="sha256:" + "cd" * 32)
    assert classify_one(e, [drifted]).state == "changed"


def test_missing_and_unreadable(monkeypatch):
    assert classify_one(entry(), []).state == "missing"

    def boom(c):
        raise OSError("gone")

    monkeypatch.setattr(content, "collect_files", boom)
    assert classify_one(entry(), [contributor("citation")]).state == "unreadable"


def test_mcp_entries_are_unchecked():
    e = entry(name="papers", kind="mcp", source_type="github")
    assert classify_one(e, [contributor("papers", kind="mcp_tool")]).state == "unchecked"


def test_name_normalization_matches(collected):
    st = classify_one(entry(name="my-skill"), [contributor("My Skill")])
    assert st.state == "matches"


def test_duplicate_names_hash_tiebreak(monkeypatch):
    stale = contributor("citation", id="/tmp/a")
    fresh = contributor("citation", id="/tmp/b")
    monkeypatch.setattr(content, "collect_files",
        lambda c: list(FILES) if c.id == "/tmp/b" else
        [{"path": "SKILL.md", "data": b"old\n", "executable": False}])
    st = classify_one(entry(), [stale, fresh])
    assert st.state == "matches"
    assert st.contributor.id == "/tmp/b"
    assert st.note is None


def test_duplicate_names_without_a_match_note_ambiguity(monkeypatch):
    a = contributor("citation", id="/tmp/a")
    b = contributor("citation", id="/tmp/b")
    monkeypatch.setattr(content, "collect_files",
        lambda c: [{"path": "SKILL.md", "data": c.id.encode(), "executable": False}])
    st = classify_one(entry(), [a, b])
    assert st.state == "changed"
    assert st.contributor.id == "/tmp/a"
    assert st.note and "share this name" in st.note


def drift_server(name="Notion", config_hash="cc" * 32):
    from drskill.mcp import MCPServer

    return MCPServer(name=name, harness="claude-code", scope="project",
                     source="/tmp/.mcp.json", transport="stdio",
                     command="npx", args=["-y", "notion-mcp"],
                     env_names=[], config_hash=config_hash)


def drift_mcp_entry(content_hash="sha256:" + "cc" * 32, server_name="Notion"):
    return {"kind": "mcp", "selector": "mcp:notion", "name": "notion",
            "source_type": "mcp", "source_reference": "npx -y notion-mcp",
            "content_hash": content_hash, "local_only": False,
            "metadata": {"server_name": server_name, "transport": "stdio",
                         "command": "npx", "args": ["-y", "notion-mcp"],
                         "url": None, "env_names": [], "tools": []}}


def test_mcp_entry_matches_a_configured_server():
    statuses = loadout_drift.classify_entries(
        [drift_mcp_entry()], [], servers=[drift_server()])
    assert statuses[0].state == "matches"


def test_mcp_entry_with_a_drifted_config_is_changed():
    statuses = loadout_drift.classify_entries(
        [drift_mcp_entry()], [], servers=[drift_server(config_hash="dd" * 32)])
    assert statuses[0].state == "changed"


def test_mcp_entry_with_no_server_is_missing():
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[])
    assert statuses[0].state == "missing"


def test_mcp_entry_without_servers_stays_unchecked():
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [])
    assert statuses[0].state == "unchecked"


def test_legacy_per_tool_entry_stays_unchecked():
    entry = {"kind": "mcp", "selector": "mcp:search", "name": "search",
             "source_type": "local", "source_reference": "x",
             "content_hash": "sha256:" + "ab" * 32, "local_only": True,
             "metadata": {}}
    statuses = loadout_drift.classify_entries([entry], [], servers=[drift_server()])
    assert statuses[0].state == "unchecked"


def test_changed_mcp_entry_carries_the_matched_server():
    server = drift_server(config_hash="dd" * 32)
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[server])
    assert statuses[0].state == "changed"
    assert statuses[0].server is server


def test_matched_mcp_entry_carries_the_server_too():
    server = drift_server()
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[server])
    assert statuses[0].state == "matches"
    assert statuses[0].server is server


def test_missing_mcp_entry_has_no_server():
    statuses = loadout_drift.classify_entries([drift_mcp_entry()], [], servers=[])
    assert statuses[0].state == "missing"
    assert statuses[0].server is None


def test_mcp_entry_with_non_dict_metadata_falls_back_to_name_match():
    entry = drift_mcp_entry(content_hash="sha256:" + "00" * 32)
    entry["metadata"] = "surprise"
    statuses = loadout_drift.classify_entries(
        [entry], [], servers=[drift_server(config_hash="dd" * 32)])
    assert statuses[0].state == "changed"


def test_mcp_entry_with_non_dict_metadata_and_no_server_is_missing():
    entry = drift_mcp_entry(content_hash="sha256:" + "00" * 32)
    entry["metadata"] = "surprise"
    statuses = loadout_drift.classify_entries([entry], [], servers=[])
    assert statuses[0].state == "missing"


def test_pinned_entry_matches_the_pinned_contributor_not_the_name_twin():
    # two local skills share the name; only one is the pinned install
    pinned = skill_contributor(id="/store/.agents/skills/vector/SKILL.md",
                               name="vector", content_hash="sha256:" + "aa" * 32)
    twin = skill_contributor(id="/elsewhere/vector/SKILL.md",
                             name="vector", content_hash="sha256:" + "bb" * 32)
    entry = skill_entry(selector="skill:vector", content_hash="sha256:" + "aa" * 32)
    pin = pins.Pin(loadout="drew/pack", revision=2, selector="skill:vector",
                   source_type="local", content_hash="sha256:" + "aa" * 32)
    statuses = loadout_drift.classify_entries(
        [entry], [twin, pinned],
        pins={pinned.id: pin}, ref="drew/pack")
    assert statuses[0].contributor is pinned
    assert statuses[0].state == "matches"
    assert statuses[0].note is None


def test_pin_for_another_loadout_is_ignored():
    c = skill_contributor(id="/store/x/SKILL.md", name="vector",
                          content_hash="sha256:" + "aa" * 32)
    entry = skill_entry(selector="skill:vector", content_hash="sha256:" + "aa" * 32)
    pin = pins.Pin(loadout="other/pack", selector="skill:vector",
                   source_type="local", content_hash="sha256:" + "aa" * 32)
    statuses = loadout_drift.classify_entries(
        [entry], [c], pins={c.id: pin}, ref="drew/pack")
    assert statuses[0].state == "matches"  # via the name fallback


def test_multiple_bound_contributors_fall_back_to_the_name_heuristic():
    # two scopes pinned the same loadout+selector to different installs;
    # guessing which one is right would hide the conflict, so this falls
    # through to the same name heuristic used for unpinned installs.
    stale = skill_contributor(id="/tmp/a/SKILL.md", name="vector",
                              content_hash="sha256:" + "aa" * 32)
    fresh = skill_contributor(id="/tmp/b/SKILL.md", name="vector",
                              content_hash="sha256:" + "bb" * 32)
    entry = skill_entry(selector="skill:vector", content_hash="sha256:" + "bb" * 32)
    pin = pins.Pin(loadout="drew/pack", selector="skill:vector",
                   source_type="local", content_hash="sha256:" + "bb" * 32)
    statuses = loadout_drift.classify_entries(
        [entry], [stale, fresh],
        pins={stale.id: pin, fresh.id: pin}, ref="drew/pack")
    assert statuses[0].contributor is fresh
    assert statuses[0].state == "matches"
    assert statuses[0].note is None


def test_no_pins_behaves_as_before():
    c = skill_contributor(id="/x/SKILL.md", name="vector",
                          content_hash="sha256:" + "aa" * 32)
    entry = skill_entry(selector="skill:vector", content_hash="sha256:" + "aa" * 32)
    assert loadout_drift.classify_entries([entry], [c])[0].state == "matches"
