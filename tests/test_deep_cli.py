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


def seed_cache(cdir, key, verdict="distinct", model="m", date="2026-07-21"):
    deep.save_verdict(cdir, key, deep.Verdict(
        verdict=verdict, rationale="r", detail="d",
        model=model, program_version="v", date=date,
    ))


def test_cache_stats(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    cdir = proj / ".drskill" / "cache"
    seed_cache(cdir, "aa" * 32, verdict="distinct")
    seed_cache(cdir, "bb" * 32, verdict="scope_overlap", date="2026-07-20")
    r = runner.invoke(app, ["cache", "stats", "--root", str(proj)], env=env_for(tmp_path))
    assert r.exit_code == 0
    assert "2 cached verdicts" in r.output
    assert "distinct: 1" in r.output
    assert "scope_overlap: 1" in r.output
    assert "oldest 2026-07-20, newest 2026-07-21" in r.output


def test_cache_prune_drops_stale_keeps_flagged(tmp_path):
    proj = overlap_project(tmp_path)
    # find the currently flagged pair's key by scanning
    from drskill.ledger import Config
    from drskill.pipeline import run_scan

    home = tmp_path / "home"
    world, findings = run_scan(proj, home, config=Config())
    (pair,) = deep.flagged_pairs(world, findings)
    live_key = deep.pair_key(*pair)
    cdir = proj / ".drskill" / "cache"
    seed_cache(cdir, live_key)
    seed_cache(cdir, "ee" * 32)  # stale: no such pair anymore
    r = runner.invoke(app, ["cache", "prune", "--root", str(proj)], env=env_for(tmp_path))
    assert r.exit_code == 0
    assert "removed 1" in r.output and "kept 1" in r.output
    assert set(deep.load_cache(cdir)) == {live_key}


def test_cache_unknown_action(tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    r = runner.invoke(app, ["cache", "flush", "--root", str(proj)], env=env_for(tmp_path))
    assert r.exit_code == 1
    assert "stats or prune" in r.output


def _distinct_cached(tmp_path, proj):
    """Seed the cache so the overlap finding downgrades to a note."""
    from drskill.ledger import Config
    from drskill.pipeline import run_scan

    home = tmp_path / "home"
    world, findings = run_scan(proj, home, config=Config())
    (pair,) = deep.flagged_pairs(world, findings)
    seed_cache(proj / ".drskill" / "cache", deep.pair_key(*pair))


def test_ack_all_skips_notes(tmp_path):
    proj = overlap_project(tmp_path)
    _distinct_cached(tmp_path, proj)
    r = runner.invoke(app, ["ack", "--all", "--root", str(proj)], env=env_for(tmp_path))
    # the only finding is a note, so there is nothing ackable
    assert r.exit_code == 1
    assert "No active finding" in r.output
    assert not (proj / "drskill.toml").exists()


def test_ack_note_by_check_id_refused(tmp_path):
    proj = overlap_project(tmp_path)
    _distinct_cached(tmp_path, proj)
    r = runner.invoke(
        app, ["ack", "description-overlap", "--root", str(proj)], env=env_for(tmp_path)
    )
    assert r.exit_code == 1
    assert not (proj / "drskill.toml").exists()


def test_cache_prune_removes_corrupt_files(tmp_path):
    proj = overlap_project(tmp_path)
    cdir = proj / ".drskill" / "cache"
    cdir.mkdir(parents=True)
    (cdir / ("dd" * 32 + ".json")).write_text("{truncated")
    r = runner.invoke(app, ["cache", "prune", "--root", str(proj)], env=env_for(tmp_path))
    assert r.exit_code == 0
    assert "removed 1" in r.output
    assert list(cdir.glob("*.json")) == []


def test_deep_scan_surfaces_last_call_error(tmp_path, monkeypatch):
    proj = overlap_project(tmp_path)

    def build_judge(model_id):
        def judge(a, b):
            judge.last_error = "AuthenticationError: invalid x-api-key"
            return None
        judge.last_error = None
        return judge

    monkeypatch.setattr(deep_llm, "build_judge", build_judge)
    r = runner.invoke(app, ["scan", "--root", str(proj), "--deep"], env=env_for(tmp_path))
    assert "deep: model calls are failing" in r.output
    assert "AuthenticationError" in r.output
