import hashlib
import json
import threading
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from drskill import service


class FakeService:
    """Implements /cli/authorize (302 to the CLI loopback), /api/v1/cli_handoffs,
    /api/v1/identity, and DELETE /api/v1/token like the Rails side."""

    def __init__(self):
        self.exchanges: list[dict] = []
        self.revoked = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
                if parsed.path == "/cli/authorize":
                    grant = json.dumps({"challenge": params["challenge"]})
                    query = urllib.parse.urlencode({"grant": grant, "state": params["state"]})
                    self.send_response(302)
                    self.send_header(
                        "Location", f"http://127.0.0.1:{params['port']}/callback?{query}"
                    )
                    self.end_headers()
                elif parsed.path == "/api/v1/identity":
                    self._json(200, {"user": {"handle": "drew"},
                                     "token": {"name": "CLI login"}})
                else:
                    self._json(404, {"error": {"code": "not_found", "message": "Not found."}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/v1/cli_handoffs":
                    outer.exchanges.append(body)
                    grant = json.loads(body["grant"])
                    expected = hashlib.sha256(body["verifier"].encode()).hexdigest()
                    if grant["challenge"] == expected:
                        self._json(201, {"token": "drsk_fake", "user": {"handle": "drew"}})
                    else:
                        self._json(401, {"error": {"code": "invalid_grant", "message": "bad"}})
                else:
                    self._json(404, {"error": {"code": "not_found", "message": "Not found."}})

            def do_DELETE(self):
                if self.path == "/api/v1/token":
                    outer.revoked = True
                    self._json(200, {"revoked": True})
                else:
                    self._json(404, {"error": {"code": "not_found", "message": "Not found."}})

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
    fake = FakeService()
    yield fake
    fake.stop()


def test_browser_login_completes_the_loopback_flow(fake_service):
    captured_body: list[bytes] = []

    def browser_that_captures_the_callback_body(url):
        captured_body.append(urllib.request.urlopen(url, timeout=10).read())
        return True

    token, handle = service.browser_login(
        base_url=fake_service.url, open_browser=browser_that_captures_the_callback_body
    )
    assert token == "drsk_fake"
    assert handle == "drew"
    # The exchange carried the verifier whose hash matches the challenge.
    assert len(fake_service.exchanges) == 1
    assert "verifier" in fake_service.exchanges[0]
    # The loopback callback served a styled success page, not bare HTML.
    assert b"Signed in to drskill" in captured_body[0]


def test_browser_login_times_out_when_nothing_calls_back(fake_service):
    with pytest.raises(service.ServiceError) as excinfo:
        service.browser_login(
            base_url=fake_service.url, open_browser=lambda url: True, timeout=0.3
        )
    assert excinfo.value.code == "timeout"


def test_browser_login_rejects_a_state_mismatch(fake_service):
    def tampering_browser(url):
        tampered = url.replace("state=", "state=evil")
        urllib.request.urlopen(tampered, timeout=10).read()
        return True

    with pytest.raises(service.ServiceError) as excinfo:
        service.browser_login(base_url=fake_service.url, open_browser=tampering_browser)
    assert excinfo.value.code == "state_mismatch"
