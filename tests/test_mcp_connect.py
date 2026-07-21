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


import sys
from pathlib import Path

from drskill.mcp import MCPServer

FAKE = str(Path(__file__).parent / "fixtures" / "fake_mcp_server.py")


def stdio_server(name="fake", args=None, config_hash="cfg1"):
    return MCPServer(
        name=name, harness="claude-code", scope="user", source="/x/config.json",
        transport="stdio", command=sys.executable, args=[FAKE, *(args or [])],
        config_hash=config_hash,
    )


def test_connect_server_enumerates_tools():
    snap = mc.connect_server(stdio_server())
    names = {t.name for t in snap.tools}
    assert names == {"echo", "ping"}
    assert all(t.schema_tokens >= 0 for t in snap.tools)
    assert snap.config_hash == "cfg1"


def test_connect_server_times_out_and_is_killed():
    import pytest
    with pytest.raises(mc.ConnectError):
        mc.connect_server(stdio_server(args=["hang"]), timeout=1.0)


def test_run_handshakes_writes_snapshots_and_collects_failures(tmp_path):
    good = stdio_server(config_hash="good")
    bad = MCPServer(name="broken", harness="claude-code", scope="user",
                    source="/x", transport="stdio",
                    command="definitely-not-real-xyz", args=[], config_hash="bad")
    connected, failures = mc.run_handshakes([good, bad], tmp_path / "snaps", timeout=5.0)
    assert connected == 1
    assert [f[0] for f in failures] == ["broken"]
    saved = mc.load_snapshots(tmp_path / "snaps")
    assert "good" in saved and {t.name for t in saved["good"].tools} == {"echo", "ping"}


def test_run_handshakes_reports_progress(tmp_path):
    seen = []
    mc.run_handshakes([stdio_server(config_hash="p1")], tmp_path / "s",
                      timeout=5.0, progress=seen.append)
    assert any("fake" in m for m in seen)
