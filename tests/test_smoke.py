from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).parent.parent


def test_scan_own_repo_does_not_crash():
    r = runner.invoke(app, ["scan", "--root", str(REPO_ROOT)])
    assert r.exit_code in (0, 1, 2)
    assert "Traceback" not in r.output


def test_scan_json_own_repo_parses():
    import json

    r = runner.invoke(app, ["scan", "--root", str(REPO_ROOT), "--json"])
    json.loads(r.output)
