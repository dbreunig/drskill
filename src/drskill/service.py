"""Client for the drskill web service (login, identity, token lifecycle).

Stdlib only. The service URL comes from DRSKILL_SERVICE_URL (default
http://localhost:3000). Credentials live at <home>/.drskill/credentials
(TOML, 0600), where <home> honors DRSKILL_HOME like the rest of the CLI.
"""

from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets
import threading
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

import tomli_w

DEFAULT_SERVICE_URL = "http://localhost:3000"
LOGIN_TIMEOUT_SECONDS = 120.0


SUCCESS_PAGE = b"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>drskill</title>
<style>
  body { margin: 0; display: flex; align-items: center; justify-content: center;
         min-height: 100vh; background: #f9fafb; color: #111827;
         font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  main { text-align: center; padding: 3rem; background: white;
         border: 1px solid #e5e7eb; border-radius: 0.75rem; }
  .check { font-size: 2.5rem; }
  h1 { font-size: 1.25rem; margin: 0.75rem 0 0.25rem; }
  p { margin: 0; color: #6b7280; font-size: 0.9rem; }
</style>
</head>
<body>
<main>
  <div class="check">&#10003;</div>
  <h1>Signed in to drskill</h1>
  <p>You can close this tab and return to your terminal.</p>
</main>
</body>
</html>
"""


class ServiceError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def service_url() -> str:
    return os.environ.get("DRSKILL_SERVICE_URL", DEFAULT_SERVICE_URL).rstrip("/")


def _sorted_deep(node):
    if isinstance(node, dict):
        return {key: _sorted_deep(node[key]) for key in sorted(node)}
    if isinstance(node, list):
        return [_sorted_deep(item) for item in node]
    return node


def canonical_manifest(manifest: dict) -> tuple[str, str]:
    """Mirror the server's Loadouts::CanonicalizeRevision byte-for-byte:
    drop any client runtime_hash, sort object keys recursively, emit compact
    UTF-8 JSON, and hash it. Verified against the Rails implementation by
    tests/fixtures/manifests/basic.canonical.json."""
    working = {key: value for key, value in manifest.items() if key != "runtime_hash"}
    canonical = json.dumps(_sorted_deep(working), ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    return canonical, f"sha256:{digest}"


def _home() -> Path:
    env = os.environ.get("DRSKILL_HOME")
    return Path(env) if env else Path.home()


def credentials_path() -> Path:
    return _home() / ".drskill" / "credentials"


def save_credentials(url: str, token: str) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(tomli_w.dumps({"service_url": url, "token": token}))
    os.chmod(path, 0o600)  # correct pre-existing files regardless of creation mode


def load_credentials() -> dict | None:
    path = credentials_path()
    if not path.exists():
        return None
    data = tomllib.loads(path.read_text())
    return data if data.get("token") else None


def delete_credentials() -> None:
    credentials_path().unlink(missing_ok=True)


def api_request(
    method: str,
    path: str,
    token: str | None = None,
    json_body: dict | None = None,
    base_url: str | None = None,
    raw: bool = False,
) -> dict | str:
    base = base_url or service_url()
    data = json.dumps(json_body).encode() if json_body is not None else None
    request = urllib.request.Request(f"{base}{path}", data=data, method=method)
    request.add_header("Accept", "application/json")
    if json_body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode()
    except urllib.error.HTTPError as error:
        raw_error = error.read().decode(errors="replace")
        try:
            parsed = json.loads(raw_error)
        except json.JSONDecodeError:
            raise ServiceError("http_error", f"HTTP {error.code}") from None
        envelope = parsed.get("error") if isinstance(parsed, dict) else None
        envelope = envelope or {}
        raise ServiceError(
            envelope.get("code", "http_error"),
            envelope.get("message", f"HTTP {error.code}"),
            details=envelope.get("details"),
        ) from None
    except urllib.error.URLError as error:
        raise ServiceError("connection_error", f"Could not reach {base}: {error.reason}") from None
    if raw:
        return body
    return json.loads(body) if body else {}


def browser_login(
    base_url: str | None = None,
    open_browser=webbrowser.open,
    timeout: float = LOGIN_TIMEOUT_SECONDS,
) -> tuple[str, str]:
    """Run the loopback authorization flow; returns (token, handle)."""
    base = base_url or service_url()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(32)
    challenge = hashlib.sha256(verifier.encode()).hexdigest()

    received: dict = {}
    done = threading.Event()

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            params = urllib.parse.parse_qs(parsed.query)
            received["grant"] = (params.get("grant") or [None])[0]
            received["state"] = (params.get("state") or [None])[0]
            # Signal completion before writing the response body: a dropped
            # connection while writing must not cause a spurious timeout.
            done.set()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(SUCCESS_PAGE)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _CallbackHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        authorize = f"{base}/cli/authorize?" + urllib.parse.urlencode(
            {"port": port, "state": state, "challenge": challenge}
        )
        opened = open_browser(authorize)
        if opened is False:
            print(
                "Could not open a browser. Visit this URL to continue:\n"
                f"  {authorize}"
            )
        if not done.wait(timeout):
            raise ServiceError("timeout", "Timed out waiting for browser authorization.")
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()

    if received.get("state") != state or not received.get("grant"):
        raise ServiceError("state_mismatch", "Authorization response did not match this login attempt.")

    exchange = api_request(
        "POST", "/api/v1/cli_handoffs",
        json_body={"grant": received["grant"], "verifier": verifier},
        base_url=base,
    )
    token = exchange["token"]
    identity = api_request("GET", "/api/v1/identity", token=token, base_url=base)
    return token, identity["user"]["handle"]
