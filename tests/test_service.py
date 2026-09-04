import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from drskill import service


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    return tmp_path


def test_credentials_round_trip_with_restrictive_permissions(home):
    service.save_credentials("http://localhost:3000", "drsk_secret")

    path = service.credentials_path()
    assert path == home / ".drskill" / "credentials"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    creds = service.load_credentials()
    assert creds == {"service_url": "http://localhost:3000", "token": "drsk_secret"}


def test_load_credentials_absent_and_delete_idempotent(home):
    assert service.load_credentials() is None
    service.delete_credentials()  # no error on missing file
    service.save_credentials("http://x", "t")
    service.delete_credentials()
    assert service.load_credentials() is None


def test_service_url_env_override_and_default(monkeypatch):
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    assert service.service_url() == "https://loadout.fun"
    monkeypatch.setenv("DRSKILL_SERVICE_URL", "http://127.0.0.1:4000/")
    assert service.service_url() == "http://127.0.0.1:4000"


class _StubHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ok":
            body = json.dumps({"user": {"handle": "drew"}}).encode()
            self.send_response(200)
        elif self.path == "/bad_error_shape":
            # A non-object JSON error body (e.g. a bare array), which
            # api_request must not crash on when reaching for .get("error").
            body = json.dumps(["oops"]).encode()
            self.send_response(400)
        elif self.path == "/invalid_utf8_error":
            # Not valid JSON at all, and not valid UTF-8 either.
            body = b"\xff\xfe not json"
            self.send_response(400)
        elif self.path == "/invalid":
            body = json.dumps(
                {"error": {"code": "revision_invalid", "message": "The revision manifest is invalid.",
                           "details": {"manifest": ["schema_version must be 1"]}}}
            ).encode()
            self.send_response(422)
        elif self.path == "/raw":
            body = b'{"b":1,"a":2}'
            self.send_response(200)
        else:
            body = json.dumps(
                {"error": {"code": "not_found", "message": "Not found."}}
            ).encode()
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_api_request_success(stub_server):
    data = service.api_request("GET", "/ok", base_url=stub_server)
    assert data == {"user": {"handle": "drew"}}


def test_api_request_raises_service_error_with_envelope_code(stub_server):
    with pytest.raises(service.ServiceError) as excinfo:
        service.api_request("GET", "/missing", base_url=stub_server)
    assert excinfo.value.code == "not_found"
    assert "Not found" in excinfo.value.message


def test_api_request_connection_error():
    with pytest.raises(service.ServiceError) as excinfo:
        service.api_request("GET", "/x", base_url="http://127.0.0.1:1")
    assert excinfo.value.code == "connection_error"


def test_api_request_falls_back_on_non_object_error_body(stub_server):
    with pytest.raises(service.ServiceError) as excinfo:
        service.api_request("GET", "/bad_error_shape", base_url=stub_server)
    assert excinfo.value.code == "http_error"
    assert "400" in excinfo.value.message


def test_api_request_falls_back_on_undecodable_error_body(stub_server):
    with pytest.raises(service.ServiceError) as excinfo:
        service.api_request("GET", "/invalid_utf8_error", base_url=stub_server)
    assert excinfo.value.code == "http_error"
    assert "400" in excinfo.value.message


def test_api_request_passes_envelope_details_through(stub_server):
    with pytest.raises(service.ServiceError) as excinfo:
        service.api_request("GET", "/invalid", base_url=stub_server)
    assert excinfo.value.details == {"manifest": ["schema_version must be 1"]}


def test_api_request_raw_returns_the_body_verbatim(stub_server):
    assert service.api_request("GET", "/raw", base_url=stub_server, raw=True) == '{"b":1,"a":2}'
