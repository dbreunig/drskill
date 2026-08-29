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


class ServiceError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def service_url() -> str:
    return os.environ.get("DRSKILL_SERVICE_URL", DEFAULT_SERVICE_URL).rstrip("/")


def _home() -> Path:
    env = os.environ.get("DRSKILL_HOME")
    return Path(env) if env else Path.home()


def credentials_path() -> Path:
    return _home() / ".drskill" / "credentials"


def save_credentials(url: str, token: str) -> None:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    path.write_text(tomli_w.dumps({"service_url": url, "token": token}))
    os.chmod(path, 0o600)


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
) -> dict:
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
        raw = error.read().decode()
        try:
            envelope = json.loads(raw).get("error") or {}
            raise ServiceError(
                envelope.get("code", "http_error"),
                envelope.get("message", f"HTTP {error.code}"),
            ) from None
        except json.JSONDecodeError:
            raise ServiceError("http_error", f"HTTP {error.code}") from None
    except urllib.error.URLError as error:
        raise ServiceError("connection_error", f"Could not reach {base}: {error.reason}") from None
    return json.loads(body) if body else {}
