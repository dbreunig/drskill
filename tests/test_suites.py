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


from drskill.ledger import Config
from drskill.pipeline import run_scan


def test_pipeline_assigns_plugin_suite_to_a_flat_copy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    # a plugin cache defines 'brainstorming'
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(skills / "brainstorming", "brainstorming", "Use when planning a feature.")
    # the same skill is installed flat for claude-code, with identical content
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "brainstorming",
                "brainstorming", "Use when planning a feature.")
    world, _ = run_scan(proj, home, config=Config())
    c = next(c for c in world.contributors.values() if c.name == "brainstorming")
    assert c.suite == "superpowers"


def test_pipeline_leaves_suite_none_when_unknown(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "solo", "solo", "Use when doing a solo task.")
    world, _ = run_scan(proj, home, config=Config())
    c = next(c for c in world.contributors.values() if c.name == "solo")
    assert c.suite is None


from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def test_list_shows_suite_column(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    skills = plugin_cache(home, "official", "superpowers", "6.1.1")
    write_skill(skills / "brainstorming", "brainstorming", "Use when planning a feature.")
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "brainstorming",
                "brainstorming", "Use when planning a feature.")
    r = runner.invoke(app, ["list", "--root", str(proj)],
                      env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert r.exit_code == 0, r.output
    assert "suite" in r.output and "superpowers" in r.output


def test_list_suite_column_escapes_markup(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "skills-lock.json").write_text(json.dumps({
        "version": 1,
        "skills": {"weird": {"source": "[red]x[/red]/repo"}},
    }))
    proj = tmp_path / "proj"
    write_skill(proj / ".claude" / "skills" / "weird", "weird", "Use when doing a weird task.")
    r = runner.invoke(app, ["list", "--root", str(proj)],
                      env={"DRSKILL_HOME": str(home), "COLUMNS": "200"})
    assert "[red]x[/red]/repo" in r.output and "\x1b[31m" not in r.output
