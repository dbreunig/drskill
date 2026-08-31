import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from drskill import service
from drskill.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures" / "manifests"


class FakeLoadoutService:
    """Mirrors the drskill-web loadout/revision API contract, including the
    server-side runtime_hash check (recomputed with canonical_manifest, which
    the Rails-fixture test proves matches the real server)."""

    def __init__(self):
        self.published: list[tuple[int, str, str]] = []  # (number, hash, canonical)
        outer = self
        # Snapshot the real canonicalizer now, before any test monkeypatches
        # service.canonical_manifest. A real Rails server has its own
        # independent implementation and would never be affected by the
        # CLI process's monkeypatching; capturing the reference here keeps
        # this fake faithful to that isolation.
        canonicalize = service.canonical_manifest

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/v1/loadouts/drew/textbook/revisions":
                    canonical, computed = canonicalize(body["manifest"])
                    client_hash = body.get("runtime_hash")
                    if client_hash and client_hash != computed:
                        self._json(422, {"error": {
                            "code": "revision_invalid",
                            "message": "The revision manifest is invalid.",
                            "details": {"manifest": [
                                f"runtime_hash mismatch: client sent {client_hash}, server computed {computed}"
                            ]},
                        }})
                        return
                    number = len(outer.published) + 1
                    outer.published.append((number, computed, canonical))
                    self._json(201, {"revision": {"number": number, "runtime_hash": computed,
                                                  "schema_version": 1, "reproducible": True,
                                                  "published_at": "2026-08-31T00:00:00Z"}})
                else:
                    self._json(404, {"error": {"code": "not_found", "message": "Not found."}})

            def do_GET(self):
                for number, computed, canonical in outer.published:
                    if self.path in (
                        f"/api/v1/loadouts/drew/textbook/revisions/{number}",
                        f"/api/v1/loadouts/drew/textbook/revisions/{computed}",
                        f"/api/v1/revision_hashes/{computed}",
                    ):
                        self._raw(200, canonical)
                        return
                self._json(404, {"error": {"code": "not_found", "message": "Not found."}})

            def _json(self, status, payload):
                self._raw(status, json.dumps(payload))

            def _raw(self, status, text):
                body = text.encode()
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
def fake_service(tmp_path, monkeypatch):
    fake = FakeLoadoutService()
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    service.save_credentials(fake.url, "drsk_fake")
    yield fake
    fake.stop()


def test_publish_then_fetch_round_trips_byte_identically(fake_service):
    manifest_path = FIXTURES / "basic.json"
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(manifest_path)])
    assert result.exit_code == 0, result.output
    published_hash = fake_service.published[0][1]
    assert f"({published_hash})" in result.output

    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "1"])
    assert result.exit_code == 0
    fetched = result.output.rstrip("\n")
    recomputed = "sha256:" + hashlib.sha256(fetched.encode()).hexdigest()
    assert recomputed == published_hash

    by_hash = runner.invoke(app, ["loadout", "fetch", published_hash])
    assert by_hash.exit_code == 0
    assert by_hash.output.rstrip("\n") == fetched


def test_publish_mismatched_hash_is_rejected_end_to_end(fake_service, tmp_path, monkeypatch):
    real = service.canonical_manifest

    def tampering(manifest):
        canonical, _ = real(manifest)
        return canonical, "sha256:" + "0" * 64

    monkeypatch.setattr(service, "canonical_manifest", tampering)
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(FIXTURES / "basic.json")])
    assert result.exit_code == 1
    assert "runtime_hash mismatch" in result.output
    assert fake_service.published == []
