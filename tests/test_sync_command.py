import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from typer.testing import CliRunner

from drskill import ledger, service, sync
from drskill.cli import app
from drskill.ledger import Ack

runner = CliRunner()
FP_A = "sha256:" + "aa" * 32


class FakeSyncService:
    """The acknowledgment_sync contract: idempotent POST, cursor GET."""

    def __init__(self):
        self.events: list[dict] = []
        self.seen_ids: set[str] = set()
        self.devices: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path != "/api/v1/acknowledgment_sync":
                    self._json(404, {"error": {"code": "not_found", "message": "Not found."}})
                    return
                outer.devices.append(body.get("device") or {})
                accepted = duplicates = 0
                for event in body.get("events", []):
                    if event["client_event_id"] in outer.seen_ids:
                        duplicates += 1
                        continue
                    outer.seen_ids.add(event["client_event_id"])
                    stored = dict(event)
                    stored["server_sequence"] = len(outer.events) + 1
                    outer.events.append(stored)
                    accepted += 1
                self._json(200, {"accepted": accepted, "duplicates": duplicates,
                                 "cursor": len(outer.events)})

            def do_GET(self):
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(self.path)
                if parsed.path != "/api/v1/acknowledgment_sync":
                    self._json(404, {"error": {"code": "not_found", "message": "Not found."}})
                    return
                after = int((parse_qs(parsed.query).get("after") or ["0"])[0])
                tail = [e for e in outer.events if e["server_sequence"] > after]
                self._json(200, {"events": tail,
                                 "cursor": tail[-1]["server_sequence"] if tail else after,
                                 "has_more": False})

            def _json(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


@pytest.fixture
def fake_service():
    fake = FakeSyncService()
    yield fake
    fake.stop()


def machine_home(tmp_path, name, url, monkeypatch):
    home = tmp_path / name
    home.mkdir()
    (home / ".drskill").mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    service.save_credentials(url, "drsk_fake")
    return home


def use_home(monkeypatch, home):
    monkeypatch.setenv("DRSKILL_HOME", str(home))


def test_sync_round_trips_between_two_machines(fake_service, tmp_path, monkeypatch):
    home_a = machine_home(tmp_path, "a", fake_service.url, monkeypatch)
    home_b = machine_home(tmp_path, "b", fake_service.url, monkeypatch)

    # Machine A acks and syncs.
    use_home(monkeypatch, home_a)
    ledger.append_ack(home_a / ".drskill.toml",
        Ack(check="injection-egress", skills=["citation-style"], fingerprint=FP_A))
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "Pushed 1 ack" in result.output

    # Machine B syncs and gains the ack.
    use_home(monkeypatch, home_b)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "Pulled 1 ack" in result.output
    config_b = ledger.load_config(home_b / ".drskill.toml")
    assert {a.fingerprint for a in config_b.ack} == {FP_A}

    # Machine B reopens (deletes the line) and syncs.
    (home_b / ".drskill.toml").write_text("")
    result = runner.invoke(app, ["sync"])
    assert "Pushed 1 reopen" in result.output or "1 reopen" in result.output

    # Machine A syncs and the ack is removed.
    use_home(monkeypatch, home_a)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    config_a = ledger.load_config(home_a / ".drskill.toml")
    assert config_a.ack == []

    # Steady state: both up to date.
    result = runner.invoke(app, ["sync"])
    assert "Already up to date." in result.output


def test_failed_upload_retries_the_same_event_ids(fake_service, tmp_path, monkeypatch):
    home_a = machine_home(tmp_path, "a", fake_service.url, monkeypatch)
    use_home(monkeypatch, home_a)
    ledger.append_ack(home_a / ".drskill.toml",
        Ack(check="c", skills=["s"], fingerprint=FP_A))

    real_api_request = service.api_request

    def failing_post(method, path, **kwargs):
        if method == "POST":
            raise service.ServiceError("connection_error", "down")
        return real_api_request(method, path, **kwargs)

    monkeypatch.setattr(service, "api_request", failing_post)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1

    pending = sync.load_state()["pending"]
    assert len(pending) == 1
    minted_id = pending[0]["client_event_id"]

    monkeypatch.setattr(service, "api_request", real_api_request)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0
    assert [e["client_event_id"] for e in fake_service.events] == [minted_id]
    assert sync.load_state()["pending"] == []


def test_download_survives_a_failed_device_registration_post(fake_service, tmp_path, monkeypatch):
    home_a = machine_home(tmp_path, "a", fake_service.url, monkeypatch)
    home_b = machine_home(tmp_path, "b", fake_service.url, monkeypatch)

    # Machine A acks and syncs normally, seeding an event on the server.
    use_home(monkeypatch, home_a)
    ledger.append_ack(home_a / ".drskill.toml",
        Ack(check="c", skills=["s"], fingerprint=FP_A))
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output

    # Machine B has nothing pending, so its POST is a bare device
    # registration. That POST fails, but B still has remote events to
    # download and must not be blocked from getting them.
    use_home(monkeypatch, home_b)
    real_api_request = service.api_request

    def failing_post(method, path, **kwargs):
        if method == "POST":
            raise service.ServiceError("connection_error", "down")
        return real_api_request(method, path, **kwargs)

    monkeypatch.setattr(service, "api_request", failing_post)
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    assert "device registration failed" in result.output
    config_b = ledger.load_config(home_b / ".drskill.toml")
    assert {a.fingerprint for a in config_b.ack} == {FP_A}


def test_sync_requires_sign_in(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 1
    assert "drskill login" in result.output


def test_sync_sends_the_device_block(fake_service, tmp_path, monkeypatch):
    home_a = machine_home(tmp_path, "a", fake_service.url, monkeypatch)
    use_home(monkeypatch, home_a)
    runner.invoke(app, ["sync"])
    assert fake_service.devices, "no device block received"
    assert fake_service.devices[0].get("name")
