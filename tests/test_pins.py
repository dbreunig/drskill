import json
from pathlib import Path

from drskill import pins


def make_pin(**overrides):
    fields = dict(loadout="drew/pack", revision=3, selector="skill:vector",
                  source_type="drskill", content_hash="sha256:" + "ab" * 32,
                  installed_at="2026-09-06")
    fields.update(overrides)
    return pins.Pin(**fields)


def test_record_and_load_roundtrip(tmp_path):
    target = tmp_path / ".agents" / "skills" / "vector"
    target.mkdir(parents=True)
    pins.record_pin(tmp_path, target, make_pin())
    loaded = pins.load_pins(tmp_path)
    assert loaded[".agents/skills/vector"].loadout == "drew/pack"
    assert loaded[".agents/skills/vector"].revision == 3


def test_record_overwrites_the_same_path(tmp_path):
    target = tmp_path / ".agents" / "skills" / "vector"
    target.mkdir(parents=True)
    pins.record_pin(tmp_path, target, make_pin())
    pins.record_pin(tmp_path, target, make_pin(loadout="drew/other", revision=9))
    loaded = pins.load_pins(tmp_path)
    assert len(loaded) == 1
    assert loaded[".agents/skills/vector"].loadout == "drew/other"


def test_record_outside_base_stores_absolute(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    elsewhere = tmp_path / "elsewhere" / "skills" / "x"
    elsewhere.mkdir(parents=True)
    pins.record_pin(base, elsewhere, make_pin())
    assert str(elsewhere) in pins.load_pins(base)


def test_load_tolerates_missing_and_garbage(tmp_path):
    assert pins.load_pins(tmp_path) == {}
    p = pins.pins_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text("not json")
    assert pins.load_pins(tmp_path) == {}
    p.write_text(json.dumps({"x": {"loadout": 5}}))
    assert pins.load_pins(tmp_path) == {}


def test_load_skips_a_bad_record_and_keeps_the_good_one(tmp_path):
    p = pins.pins_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({
        "x": {"loadout": 5},
        ".agents/skills/vector": make_pin().model_dump(),
    }))
    loaded = pins.load_pins(tmp_path)
    assert list(loaded) == [".agents/skills/vector"]
    assert loaded[".agents/skills/vector"].loadout == "drew/pack"


def test_prune_drops_dead_paths(tmp_path):
    live = tmp_path / ".agents" / "skills" / "vector"
    live.mkdir(parents=True)
    dead = tmp_path / ".agents" / "skills" / "gone"
    dead.mkdir(parents=True)
    pins.record_pin(tmp_path, live, make_pin())
    pins.record_pin(tmp_path, dead, make_pin(selector="skill:gone"))
    dead.rmdir()
    pins.prune_pins(tmp_path)
    assert list(pins.load_pins(tmp_path)) == [".agents/skills/vector"]


def test_resolve_pins_merges_scopes_and_keys_by_primary_file(tmp_path):
    proj, home = tmp_path / "proj", tmp_path / "home"
    for base, name in ((proj, "vector"), (home, "tidy")):
        d = base / ".agents" / "skills" / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text("x")
        pins.record_pin(base, d, make_pin(selector=f"skill:{name}"))
    resolved = pins.resolve_pins(proj, home)
    assert str((proj / ".agents/skills/vector/SKILL.md").resolve()) in resolved
    assert str((home / ".agents/skills/tidy/SKILL.md").resolve()) in resolved
