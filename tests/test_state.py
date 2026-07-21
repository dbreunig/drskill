import datetime as dt

from drskill import state


def test_state_path_hashes_project_root(tmp_path):
    p1 = state.state_path(tmp_path / "a", tmp_path / "home", False)
    p2 = state.state_path(tmp_path / "b", tmp_path / "home", False)
    assert p1 != p2
    assert p1.parent == tmp_path / "home" / ".drskill" / "state"
    assert p1.suffix == ".json"


def test_state_path_global_mode(tmp_path):
    p = state.state_path(tmp_path, tmp_path / "home", True)
    assert p.name == "global.json"


def test_mark_and_load_roundtrip(tmp_path):
    p = tmp_path / "state" / "x.json"
    state.mark_seen(p, ["sha256:aa", "sha256:bb"], dt.date(2026, 7, 20))
    seen = state.load_seen(p)
    assert set(seen) == {"sha256:aa", "sha256:bb"}
    assert seen["sha256:aa"] == "2026-07-20"


def test_mark_seen_prunes_and_keeps_first_date(tmp_path):
    p = tmp_path / "x.json"
    state.mark_seen(p, ["sha256:aa", "sha256:bb"], dt.date(2026, 7, 20))
    state.mark_seen(p, ["sha256:aa", "sha256:cc"], dt.date(2026, 7, 21))
    seen = state.load_seen(p)
    assert set(seen) == {"sha256:aa", "sha256:cc"}
    assert seen["sha256:aa"] == "2026-07-20"  # first-seen date survives
    assert seen["sha256:cc"] == "2026-07-21"


def test_load_seen_tolerates_missing_and_garbage(tmp_path):
    assert state.load_seen(tmp_path / "absent.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert state.load_seen(bad) == {}


def test_mark_seen_swallows_unwritable_dir(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        state.mark_seen(ro / "sub" / "x.json", ["sha256:aa"], dt.date(2026, 7, 20))
    finally:
        ro.chmod(0o755)
