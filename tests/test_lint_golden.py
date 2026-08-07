import json
from pathlib import Path

from typer.testing import CliRunner

from drskill.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "plugins"


def test_valid_plugin_is_clean():
    r = runner.invoke(app, ["lint", str(FIXTURES / "valid-plugin")])
    assert r.exit_code == 0, r.output


def test_kitchen_sink_reports_every_class():
    r = runner.invoke(app, ["lint", str(FIXTURES / "kitchen-sink"), "--json"])
    assert r.exit_code == 1
    got = {f["check_id"] for f in json.loads(r.output)}
    expected = {
        "plugin-manifest-invalid",     # version: 2 is not a string
        "plugin-name-invalid",         # Kitchen--Sink
        "plugin-manifest-unknown-field",  # surprise + extensions
        "plugin-schema-unknown",       # 9.9.9
        "plugin-skill-undiscoverable", # nested/deeper
        "mcp-spec-invalid",            # websocket transport, missing $schema
        "mcp-spec-placeholder",        # reserved env name, bad cwd
        "mcp-secret-in-config",        # API_TOKEN value
        "spec-name-mismatch",          # wrong/ vs mismatched
        "spec-missing-description",
    }
    assert expected <= got, expected - got
