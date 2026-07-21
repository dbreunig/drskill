from drskill import mcp_connect as mc


def snap(config_hash="abc", date="2026-07-21", tools=(("t", "desc", 3),)):
    return mc.ServerSnapshot(
        server="srv", config_hash=config_hash, date=date,
        tools=[mc.ToolInfo(name=n, description=d, schema_tokens=s) for n, d, s in tools],
    )


def test_snapshot_round_trip(tmp_path):
    sdir = tmp_path / "mcp-tools"
    s = snap()
    mc.save_snapshot(sdir, s)
    loaded = mc.load_snapshots(sdir)
    assert loaded == {"abc": s}


def test_load_snapshots_skips_corrupt(tmp_path):
    sdir = tmp_path / "mcp-tools"
    sdir.mkdir()
    (sdir / "bad.json").write_text("{nope")
    assert mc.load_snapshots(sdir) == {}


def test_changed_tools_detects_description_edit_add_remove():
    old = snap(tools=(("a", "old", 1), ("b", "keep", 1)))
    new = snap(tools=(("a", "new", 1), ("b", "keep", 1), ("c", "added", 1)))
    assert set(mc.changed_tools(old, new)) == {"a", "c"}
    assert mc.changed_tools(None, new) == []  # first snapshot: nothing to compare


def test_fingerprint_base_is_order_independent():
    a = snap(tools=(("x", "1", 1), ("y", "2", 1)))
    b = snap(tools=(("y", "2", 9), ("x", "1", 9)))  # token count differs, text same
    assert mc.tool_fingerprint_base(a) == mc.tool_fingerprint_base(b)
