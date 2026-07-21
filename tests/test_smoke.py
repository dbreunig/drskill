from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).parent.parent


def _tmp_home_env(tmp_path):
    # keep tests from writing seen-state (or anything else) into the real home
    home = tmp_path / "home"
    home.mkdir()
    return {"DRSKILL_HOME": str(home)}


def test_scan_own_repo_does_not_crash(tmp_path):
    r = runner.invoke(
        app, ["scan", "--root", str(REPO_ROOT)], env=_tmp_home_env(tmp_path)
    )
    assert r.exit_code in (0, 1, 2)
    assert "Traceback" not in r.output


def test_scan_json_own_repo_parses(tmp_path):
    import json

    r = runner.invoke(
        app,
        ["scan", "--root", str(REPO_ROOT), "--json"],
        env=_tmp_home_env(tmp_path),
    )
    json.loads(r.output)
