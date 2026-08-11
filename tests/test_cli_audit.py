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


def test_audit_file_and_last_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", "x.jsonl", "--last"])
    assert result.exit_code == 1
    assert "cannot be combined" in result.output


def test_audit_file_missing_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(tmp_path / "nope.jsonl")])
    assert result.exit_code == 1
    assert "no such trace file" in result.output


def test_audit_file_bypasses_project_scope(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, "/somewhere/else")  # other project's session
    trace = tmp_path / ".claude" / "projects" / "-a" / "s1.jsonl"
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(trace)])
    assert result.exit_code == 0
    assert "release" in result.output


def test_audit_file_outside_roots_needs_harness(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _claude_trace(tmp_path, str(repo))
    src = tmp_path / ".claude" / "projects" / "-a" / "s1.jsonl"
    moved = tmp_path / "export.jsonl"
    moved.write_text(src.read_text())
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(moved)])
    assert result.exit_code == 1
    assert "--harness" in result.output
    result = runner.invoke(app, ["audit", "--root", str(repo),
                                 "--file", str(moved),
                                 "--harness", "claude-code"])
    assert result.exit_code == 0
    assert "release" in result.output


def test_audit_last_narrows_to_newest_session(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    d = tmp_path / ".claude" / "projects" / "-a"
    d.mkdir(parents=True)
    for session, skill, ts in [
        ("s1", "olderskill", "2026-07-01T10:00:05.000Z"),
        ("s2", "newerskill", "2026-07-02T10:00:05.000Z"),
    ]:
        event = {
            "type": "assistant", "sessionId": session, "timestamp": ts,
            "cwd": str(repo), "isSidechain": False,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t1", "name": "Skill",
                 "input": {"skill": skill}}]},
        }
        (d / f"{session}.jsonl").write_text(json.dumps(event) + "\n")
    result = runner.invoke(app, ["audit", "--root", str(repo), "--last"])
    assert result.exit_code == 0
    assert "newerskill" in result.output
    assert "olderskill" not in result.output
