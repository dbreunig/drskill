import datetime as dt
import json

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()


def _claude_trace(home, cwd, skill="release", ts="2026-07-01T10:00:05.000Z"):
    d = home / ".claude" / "projects" / "-a"
    d.mkdir(parents=True, exist_ok=True)
    event = {
        "type": "assistant", "sessionId": "s1",
        "timestamp": ts, "cwd": cwd,
        "isSidechain": False,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Skill",
             "input": {"skill": skill}}]},
    }
    (d / "s1.jsonl").write_text(json.dumps(event) + "\n")


def _write_skill(root, name):
    d = root / ".claude" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody\n")


def _days_ago(days):
    ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.000Z")


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


def test_audit_reports_unused_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_skill(repo, "used")
    _write_skill(repo, "dusty")
    # trace with skill "used" invoked long ago (old coverage)
    _claude_trace(tmp_path, str(repo), skill="used", ts=_days_ago(60))
    result = runner.invoke(app, ["audit", "--root", str(repo), "--unused-days", "30"])
    assert result.exit_code == 0, result.output
    assert "Unused" in result.output
    assert "dusty" in result.output
    assert "threshold 30 days" in result.output


def test_audit_unused_respects_config_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_skill(repo, "used")
    _write_skill(repo, "dusty")
    # young coverage -> the not-enough-coverage line with the 90-day default
    _claude_trace(tmp_path, str(repo), skill="used", ts=_days_ago(5))
    result = runner.invoke(app, ["audit", "--root", str(repo)])
    assert result.exit_code == 0, result.output
    assert "not enough trace coverage" in result.output
    assert "90 days" in result.output


def test_audit_json_gains_unused_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_skill(repo, "used")
    _write_skill(repo, "dusty")
    _claude_trace(tmp_path, str(repo), skill="used", ts=_days_ago(60))
    result = runner.invoke(
        app, ["audit", "--root", str(repo), "--unused-days", "30", "--json"]
    )
    data = json.loads(result.output)
    dusty = next(u for u in data["unused"] if u["name"] == "dusty")
    assert dusty == {"kind": "skill", "name": "dusty", "harnesses": ["claude-code"]}


def _write_codex_skill(root, name):
    d = root / ".codex" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nbody\n")


def test_audit_harness_filter_skips_crossref(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_skill(repo, "used")
    _write_skill(repo, "dusty")
    _claude_trace(tmp_path, str(repo), skill="used", ts=_days_ago(60))
    result = runner.invoke(
        app, ["audit", "--root", str(repo), "--harness", "claude-code",
              "--unused-days", "30"]
    )
    assert result.exit_code == 0, result.output
    assert "Unused" not in result.output


def test_audit_last_skips_crossref(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_skill(repo, "used")
    _write_skill(repo, "dusty")
    _claude_trace(tmp_path, str(repo), skill="used", ts=_days_ago(60))
    result = runner.invoke(
        app, ["audit", "--root", str(repo), "--last", "--unused-days", "30"]
    )
    assert result.exit_code == 0, result.output
    assert "Unused" not in result.output


def test_audit_reports_not_enough_coverage_when_contributor_harness_uncovered(
    tmp_path, monkeypatch
):
    # "dusty" is only deployed to codex, which never appears in trace
    # history; claude-code has old, covered trace history but no
    # claude-code contributors at all. Nothing was actually checked, so
    # the report must say so rather than claim everything is used.
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_codex_skill(repo, "dusty")
    _claude_trace(tmp_path, str(repo), skill="used", ts=_days_ago(60))
    result = runner.invoke(app, ["audit", "--root", str(repo), "--unused-days", "30"])
    assert result.exit_code == 0, result.output
    assert "not enough trace coverage" in result.output


def test_audit_drilldown_skips_crossref(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    repo = tmp_path / "repo"
    repo.mkdir()
    _write_skill(repo, "used")
    _write_skill(repo, "dusty")
    _claude_trace(tmp_path, str(repo), skill="used", ts=_days_ago(60))
    result = runner.invoke(
        app, ["audit", "used", "--root", str(repo), "--unused-days", "30"]
    )
    assert result.exit_code == 0, result.output
    assert "Unused" not in result.output
