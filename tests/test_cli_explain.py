import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def write(proj: Path, name: str, description: str, body: str):
    d = proj / ".claude" / "skills" / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    # wide console so string assertions never straddle a wrapped line
    monkeypatch.setenv("COLUMNS", "200")
    proj = tmp_path / "proj"
    proj.mkdir()
    write(proj, "pdf", "Use when the user asks to summarize pdf documents.", "a" * 40)
    write(proj, "deploy", "Use when the user asks to deploy kubernetes clusters.", "b" * 40)
    monkeypatch.chdir(proj)
    return proj


def test_explain_prints_ranking_and_disclaimer(project):
    result = runner.invoke(app, ["explain", "summarize a pdf document"])
    assert result.exit_code == 0, result.output
    assert "routes to pdf" in result.output
    assert "pdf" in result.output
    assert "drskill's own similarity model" in result.output


def test_explain_json_shape(project):
    result = runner.invoke(app, ["explain", "summarize a pdf document", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["query"] == "summarize a pdf document"
    assert data["harnesses"][0]["rows"][0]["name"] == "pdf"


def test_explain_unknown_harness_errors(project):
    result = runner.invoke(app, ["explain", "x", "--harness", "not-real"])
    assert result.exit_code == 1


def test_explain_is_read_only(project):
    runner.invoke(app, ["explain", "summarize a pdf document"])
    assert not (project / ".drskill").exists()


def test_explain_deep_prints_model_verdict(project, monkeypatch):
    from drskill import deep_llm, explain as explain_mod

    def fake_builder(model_id):
        def judge(query, candidates):
            return explain_mod.QueryJudgeResult(
                routed="pdf", contested=False,
                rationale="the pdf skill names the format")
        return judge

    monkeypatch.setattr(deep_llm, "build_query_judge", fake_builder)
    result = runner.invoke(app, ["explain", "summarize a pdf document", "--deep"])
    assert result.exit_code == 0, result.output
    assert "model verdict: routes to pdf" in result.output
    assert "the pdf skill names the format" in result.output
    assert "drskill's own similarity model" not in result.output
    assert "model's judgment" in result.output


def test_explain_deep_unavailable_exits_one(project, monkeypatch):
    from drskill import deep_llm

    def boom(model_id):
        raise deep_llm.DeepUnavailableError("no key configured")

    monkeypatch.setattr(deep_llm, "build_query_judge", boom)
    result = runner.invoke(app, ["explain", "x", "--deep"])
    assert result.exit_code == 1
    assert "no key configured" in result.output


def test_explain_plain_never_touches_deep_llm(project, monkeypatch):
    from drskill import deep_llm

    def boom(model_id):
        raise AssertionError("build_query_judge must not be called without --deep")

    monkeypatch.setattr(deep_llm, "build_query_judge", boom)
    result = runner.invoke(app, ["explain", "summarize a pdf document"])
    assert result.exit_code == 0, result.output


def _two_harness_world():
    from drskill.harnesses import HarnessDef
    from drskill.models import Deployment
    from drskill.resolution import World
    from tests.test_models import make_contributor

    c = make_contributor(id="/x/pdf", name="pdf", kind="skill",
                         routing_text="summarize pdf documents")
    for i, h in enumerate(("h1", "h2")):
        c.deployments.append(Deployment(harness=h, path="/x/pdf", scope="project",
                                        via_symlink=False, order=i))
    return World(contributors={c.id: c},
                harnesses={h: HarnessDef(id=h, display_name=h) for h in ("h1", "h2")})


def test_explain_json_deep_error_key(project, monkeypatch):
    # A judge that fails must surface as a top-level "deep_error" JSON key,
    # never as a rich console line mixed into the machine-readable stdout.
    from drskill import deep_llm

    def fake_builder(model_id):
        def judge(query, candidates):
            judge.last_error = "RuntimeError: boom"
            return None
        judge.last_error = None
        return judge

    monkeypatch.setattr(deep_llm, "build_query_judge", fake_builder)
    result = runner.invoke(
        app, ["explain", "summarize a pdf document", "--json", "--deep"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip().startswith("{")
    data = json.loads(result.output)
    assert data["deep_error"] == "RuntimeError: boom"


def test_explain_json_deep_error_key_absent_without_deep(project):
    result = runner.invoke(app, ["explain", "summarize a pdf document", "--json"])
    data = json.loads(result.output)
    assert "deep_error" not in data


def test_explain_json_deep_judges_once_per_group(project, monkeypatch):
    # Two harnesses sharing one ranking must share one judge call and one
    # "model" verdict, not one call each with possibly contradictory verdicts.
    from drskill import cli, deep_llm, explain as explain_mod

    world = _two_harness_world()
    monkeypatch.setattr(cli, "run_scan", lambda *a, **k: (world, []))

    calls = []

    def fake_builder(model_id):
        def judge(query, candidates):
            calls.append(candidates)
            return explain_mod.QueryJudgeResult(
                routed="pdf", contested=False, rationale="ok")
        return judge

    monkeypatch.setattr(deep_llm, "build_query_judge", fake_builder)
    result = runner.invoke(
        app, ["explain", "summarize a pdf document", "--json", "--deep"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert len(calls) == 1
    models = [h["model"] for h in data["harnesses"]]
    assert len(models) == 2
    assert models[0] == models[1] and models[0] is not None


def test_explain_deep_flattens_multiline_verdict_text(project, monkeypatch):
    # A judge's rationale or routed name containing a newline must not
    # break the single-line verdict rendering.
    from drskill import deep_llm, explain as explain_mod

    def fake_builder(model_id):
        def judge(query, candidates):
            return explain_mod.QueryJudgeResult(
                routed="pdf", contested=False,
                rationale="line one\nline two")
        return judge

    monkeypatch.setattr(deep_llm, "build_query_judge", fake_builder)
    result = runner.invoke(app, ["explain", "summarize a pdf document", "--deep"])
    assert result.exit_code == 0, result.output
    verdict_lines = [l for l in result.output.splitlines() if "model verdict" in l]
    assert len(verdict_lines) == 1
    assert "line one line two" in verdict_lines[0]


def test_explain_warns_on_undetected_harness(project):
    result = runner.invoke(
        app, ["explain", "summarize a pdf document", "--harness", "cursor"]
    )
    assert result.exit_code == 0, result.output
    assert "not detected on this machine" in result.output
