import json

from typer.testing import CliRunner

from drskill import service
from drskill.cli import app

runner = CliRunner()


def env_for(tmp_path, url="http://127.0.0.1:1"):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {"DRSKILL_HOME": str(home), "DRSKILL_SERVICE_URL": url}


def test_login_browser_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "browser_login", lambda **kw: ("drsk_fake", "drew"))
    result = runner.invoke(app, ["login"], env=env_for(tmp_path))
    assert result.exit_code == 0
    assert "Signed in as drew" in result.output


def test_login_falls_back_to_paste_prompt(tmp_path, monkeypatch):
    def failing_browser_login(**kw):
        raise service.ServiceError("timeout", "no browser here")

    monkeypatch.setattr(service, "browser_login", failing_browser_login)
    monkeypatch.setattr(
        service, "api_request",
        lambda *a, **kw: {"user": {"handle": "drew"}, "token": {"name": "pasted"}},
    )
    result = runner.invoke(app, ["login"], env=env_for(tmp_path), input="drsk_pasted\n")
    assert result.exit_code == 0
    assert "Signed in as drew" in result.output


def test_whoami_without_credentials_hints_at_login(tmp_path):
    result = runner.invoke(app, ["whoami"], env=env_for(tmp_path))
    assert result.exit_code == 1
    assert "drskill login" in result.output


def test_whoami_and_logout_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "browser_login", lambda **kw: ("drsk_fake", "drew"))
    monkeypatch.setattr(
        service, "api_request",
        lambda *a, **kw: {"user": {"handle": "drew"}, "token": {"name": "CLI login"}},
    )
    env = env_for(tmp_path)
    assert runner.invoke(app, ["login"], env=env).exit_code == 0

    result = runner.invoke(app, ["whoami"], env=env)
    assert result.exit_code == 0
    assert "drew" in result.output

    result = runner.invoke(app, ["logout"], env=env)
    assert result.exit_code == 0
    assert runner.invoke(app, ["whoami"], env=env).exit_code == 1


def test_logout_deletes_credentials_even_when_revoke_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "browser_login", lambda **kw: ("drsk_fake", "drew"))
    env = env_for(tmp_path)
    runner.invoke(app, ["login"], env=env)

    def failing_revoke(*a, **kw):
        raise service.ServiceError("connection_error", "down")

    monkeypatch.setattr(service, "api_request", failing_revoke)
    result = runner.invoke(app, ["logout"], env=env)
    assert result.exit_code == 0
    assert runner.invoke(app, ["whoami"], env=env).exit_code == 1
