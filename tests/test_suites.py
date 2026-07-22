import json
from pathlib import Path

from drskill import suites
from drskill.resolution import content_hash


def write_skill(path: Path, name: str, description: str, body: str = "b") -> str:
    path.mkdir(parents=True, exist_ok=True)
    text = f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    (path / "SKILL.md").write_text(text)
    return content_hash(text)


def plugin_cache(home: Path, marketplace: str, plugin: str, version: str):
    return home / ".claude" / "plugins" / "cache" / marketplace / plugin / version / "skills"


def test_registry_maps_plugin_skill_by_content_hash(tmp_path):
    home = tmp_path / "home"
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    h = write_skill(skills / "brainstorming", "brainstorming", "Use when planning.")
    by_hash, _ = suites.build_registry(home)
    assert by_hash[h] == "superpowers"


def test_registry_indexes_every_cached_version(tmp_path):
    home = tmp_path / "home"
    old = plugin_cache(home, "official", "superpowers", "4.3.1")
    h_old = write_skill(old / "brainstorming", "brainstorming", "Old wording.")
    new = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(new / "brainstorming", "brainstorming", "New wording.")
    by_hash, _ = suites.build_registry(home)
    assert by_hash[h_old] == "superpowers"  # a match against the old version still counts


def test_registry_reads_lockfile_source(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "skills-lock.json").write_text(json.dumps({
        "version": 1,
        "skills": {"scaffold-docs": {"source": "dbreunig/scaffold-docs-skill",
                                     "sourceType": "github"}},
    }))
    _, by_name = suites.build_registry(home)
    assert by_name["scaffold-docs"] == "dbreunig/scaffold-docs-skill"


def test_registry_skips_corrupt_files(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "skills-lock.json").write_text("{not json")
    by_hash, by_name = suites.build_registry(home)
    assert by_hash == {} and by_name == {}


def test_suite_for_prefers_hash_then_name(tmp_path):
    by_hash = {"sha256:aa": "superpowers"}
    by_name = {"brainstorming": "someone/repo"}
    assert suites.suite_for("sha256:aa", "brainstorming", by_hash, by_name) == "superpowers"
    assert suites.suite_for("sha256:zz", "brainstorming", by_hash, by_name) == "someone/repo"
    assert suites.suite_for("sha256:zz", "unknown", by_hash, by_name) is None
