import datetime as dt
import json

from drskill.traces import cache, pipeline

UTC = dt.timezone.utc


def _claude_event(cwd, skill="release", ts="2026-07-01T10:00:05.000Z"):
    return {
        "type": "assistant", "sessionId": "s1", "timestamp": ts, "cwd": cwd,
        "isSidechain": False,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Skill",
             "input": {"skill": skill}}]},
    }


def _write_claude(home, project_dir, cwd, skill="release", session="s1", **kw):
    d = home / ".claude" / "projects" / project_dir
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{session}.jsonl"
    f.write_text(json.dumps(_claude_event(cwd, skill, **kw)) + "\n")
    return f


def test_project_scope_filters_by_cwd(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _write_claude(tmp_path, "-a", str(root), skill="inproj")
    _write_claude(tmp_path, "-b", "/somewhere/else", skill="outproj", session="s2")
    data = pipeline.run_audit(tmp_path, root, global_mode=False,
                              harness=None, since=None)
    assert [i.name for i in data.invocations] == ["inproj"]


def test_global_scope_keeps_everything(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _write_claude(tmp_path, "-a", str(root), skill="inproj")
    _write_claude(tmp_path, "-b", "/somewhere/else", skill="outproj", session="s2")
    data = pipeline.run_audit(tmp_path, root, global_mode=True,
                              harness=None, since=None)
    assert sorted(i.name for i in data.invocations) == ["inproj", "outproj"]


def test_since_filters_old_invocations(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _write_claude(tmp_path, "-a", str(root), skill="old",
                  ts="2026-01-01T00:00:00.000Z")
    _write_claude(tmp_path, "-b", str(root), skill="new", session="s2",
                  ts="2026-07-20T00:00:00.000Z")
    cutoff = dt.datetime(2026, 6, 1, tzinfo=UTC)
    data = pipeline.run_audit(tmp_path, root, global_mode=False,
                              harness=None, since=cutoff)
    assert [i.name for i in data.invocations] == ["new"]


def test_second_run_hits_cache(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _write_claude(tmp_path, "-a", str(root))
    pipeline.run_audit(tmp_path, root, False, None, None)
    calls = []
    from drskill.traces import claude_code
    real = claude_code.extract
    monkeypatch.setattr(claude_code, "extract",
                        lambda p: calls.append(p) or real(p))
    data = pipeline.run_audit(tmp_path, root, False, None, None)
    assert calls == []  # everything served from cache
    assert len(data.invocations) == 1


def test_harness_filter_never_prunes_other_harnesses(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    trace = _write_claude(tmp_path, "-a", str(root))
    pipeline.run_audit(tmp_path, root, False, None, None)
    cdir = cache.audit_cache_dir(tmp_path)
    before = sorted(p.name for p in cdir.glob("*.json"))
    pipeline.run_audit(tmp_path, root, False, harness="pi", since=None)
    after = sorted(p.name for p in cdir.glob("*.json"))
    assert before == after
    assert cache.load_entry(cdir, trace) is not None


def test_vanished_trace_prunes_its_entry(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    trace = _write_claude(tmp_path, "-a", str(root))
    pipeline.run_audit(tmp_path, root, False, None, None)
    trace.unlink()
    pipeline.run_audit(tmp_path, root, False, None, None)
    cdir = cache.audit_cache_dir(tmp_path)
    assert list(cdir.glob("*.json")) == []


def test_unreadable_file_is_counted_not_fatal(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    _write_claude(tmp_path, "-a", str(root))
    from drskill.traces import claude_code

    def boom(p):
        raise OSError("nope")

    monkeypatch.setattr(claude_code, "extract", boom)
    data = pipeline.run_audit(tmp_path, root, False, None, None)
    assert len(data.unreadable) == 1
    assert data.invocations == []


def test_zero_recognized_counts_as_drift(tmp_path):
    d = tmp_path / ".claude" / "projects" / "-a"
    d.mkdir(parents=True)
    (d / "s1.jsonl").write_text('{"type":"totally-new-format"}\n')
    root = tmp_path / "repo"
    root.mkdir()
    data = pipeline.run_audit(tmp_path, root, True, None, None)
    assert data.drifted.get("claude-code") == 1
