from pathlib import Path

from typer.testing import CliRunner

from drskill import deep, deep_llm
from drskill.cli import app

runner = CliRunner()

PILE_A = "Use when the user asks to write project documentation pages."
PILE_B = "Use when the user asks to write project documentation summaries."


def env_for(tmp_path):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # wide console so string assertions never straddle a wrapped line
    return {"DRSKILL_HOME": str(home), "COLUMNS": "200"}


def write(proj: Path, name: str, description: str, body: str):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )


def overlap_project(tmp_path):
    proj = tmp_path / "proj"
    write(proj, "doc-a", PILE_A, "a" * 40)
    write(proj, "doc-b", PILE_B, "b" * 40)
    return proj


def fake_builder(result):
    def build_judge(model_id):
        return lambda a, b: result
    return build_judge


def test_deep_scan_downgrades_and_ci_passes(tmp_path, monkeypatch):
    proj = overlap_project(tmp_path)
    monkeypatch.setattr(
        deep_llm, "build_judge",
        fake_builder(deep.JudgeResult(verdict="distinct", rationale="r", detail="d")),
    )
    r = runner.invoke(
        app, ["scan", "--root", str(proj), "--deep", "--ci"], env=env_for(tmp_path)
    )
    assert r.exit_code == 0, r.output
    assert "NOTES" in r.output
    assert "judged distinct" in r.output
    # cached: a plain --ci scan now also passes, with no judge at all
    r2 = runner.invoke(app, ["scan", "--root", str(proj), "--ci"], env=env_for(tmp_path))
    assert r2.exit_code == 0, r2.output
    assert "judged distinct" in r2.output


def test_deep_scan_collision_keeps_warning_and_evidence(tmp_path, monkeypatch):
    proj = overlap_project(tmp_path)
    monkeypatch.setattr(
        deep_llm, "build_judge",
        fake_builder(deep.JudgeResult(
            verdict="description_collision", rationale="[red]hostile[/red] words",
            detail="write the docs",
        )),
    )
    r = runner.invoke(
        app, ["scan", "--root", str(proj), "--deep", "--ci"], env=env_for(tmp_path)
    )
    assert r.exit_code == 2
    assert "description_collision" in r.output
    # hostile markup in model output renders as text, never as rich markup
    assert "hostile" in r.output
    assert "\x1b[31m" not in r.output


def test_deep_scan_budget_truncation_reported(tmp_path, monkeypatch):
    proj = overlap_project(tmp_path)
    monkeypatch.setattr(
        deep_llm, "build_judge",
        fake_builder(deep.JudgeResult(verdict="distinct", rationale="r", detail="d")),
    )
    r = runner.invoke(
        app,
        ["scan", "--root", str(proj), "--deep", "--max-calls", "0"],
        env=env_for(tmp_path),
    )
    assert "1 flagged pair still unjudged" in r.output


def test_deep_unavailable_exits_one(tmp_path, monkeypatch):
    proj = overlap_project(tmp_path)

    def boom(model_id):
        raise deep_llm.DeepUnavailableError("deep checks need the [deep] extra")

    monkeypatch.setattr(deep_llm, "build_judge", boom)
    r = runner.invoke(app, ["scan", "--root", str(proj), "--deep"], env=env_for(tmp_path))
    assert r.exit_code == 1
    assert "[deep] extra" in r.output


def test_plain_scan_never_touches_deep_llm(tmp_path, monkeypatch):
    proj = overlap_project(tmp_path)

    def boom(model_id):
        raise AssertionError("build_judge must not be called without --deep")

    monkeypatch.setattr(deep_llm, "build_judge", boom)
    r = runner.invoke(app, ["scan", "--root", str(proj)], env=env_for(tmp_path))
    assert r.exit_code == 0
