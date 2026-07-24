import json

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def _claude_trace(home, cwd, skill="release"):
    d = home / ".claude" / "projects" / "-a"
    d.mkdir(parents=True, exist_ok=True)
    event = {
        "type": "assistant", "sessionId": "s1",
        "timestamp": "2026-07-01T10:00:05.000Z", "cwd": cwd,
        "isSidechain": False,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Skill",
             "input": {"skill": skill}}]},
    }
    (d / "s1.jsonl").write_text(json.dumps(event) + "\n")


def test_audit_report_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, str(repo))
    result = runner.invoke(app, ["audit", "--root", str(repo)])
    assert result.exit_code == 0
    assert "release" in result.output
    assert "coverage:" in result.output


def test_audit_drilldown(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, str(repo))
    result = runner.invoke(app, ["audit", "release", "--root", str(repo)])
    assert result.exit_code == 0
    assert "trace:" in result.output


def test_audit_json(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, str(repo))
    result = runner.invoke(app, ["audit", "--root", str(repo), "--json"])
    data = json.loads(result.output)
    assert data["invocations"][0]["name"] == "release"
    assert "coverage" in data and "unreadable" in data


def test_audit_bad_since_and_bad_harness_exit_one(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    assert runner.invoke(app, ["audit", "--root", str(repo),
                               "--since", "yesterday"]).exit_code == 1
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--harness", "cursor"])
    assert result.exit_code == 1
    assert "claude-code" in result.output  # valid ids listed


def test_cache_stats_and_prune_cover_audit(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, str(repo))
    runner.invoke(app, ["audit", "--root", str(repo)])
    result = runner.invoke(app, ["cache", "stats", "--root", str(repo)])
    assert "audit extraction" in result.output
    trace = tmp_path / ".claude" / "projects" / "-a" / "s1.jsonl"
    trace.unlink()
    result = runner.invoke(app, ["cache", "prune", "--root", str(repo)])
    assert "stale audit extraction" in result.output
