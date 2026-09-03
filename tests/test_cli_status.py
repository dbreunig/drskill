import io
import json
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from typer.testing import CliRunner

from drskill import cli as cli_mod, content, service
from drskill.cli import app
from drskill.models import Contributor, Provenance, TokenCost
from drskill.resolution import World

runner = CliRunner()

FILES = [{"path": "SKILL.md", "data": b"body\n", "executable": False}]
DIR_HASH = content.manifest_hash(FILES)

UPSTREAM_FILES = {"SKILL.md": b"body\n"}
DRIFTED_UPSTREAM = {"SKILL.md": b"new body\n"}


def contributor(name):
    return Contributor(
        id=f"/tmp/{name}", kind="skill", name=name,
        source=Provenance(kind="unmanaged", source=None), scope="project",
        token_cost=TokenCost(catalog_tokens=1, body_tokens=1),
        content_hash="sha256:" + "ab" * 32,
    )


def entry(name="vector", source_type="drskill", content_hash=DIR_HASH, kind="skill",
          metadata=None):
    return {"kind": kind, "selector": f"{kind}:{name}", "name": name,
            "source_type": source_type, "source_reference": source_type,
            "content_hash": content_hash, "local_only": False,
            "metadata": metadata if metadata is not None else {}}


class _CodeloadStub(BaseHTTPRequestHandler):
    upstream = UPSTREAM_FILES

    def do_GET(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for path, data in type(self).upstream.items():
                info = tarfile.TarInfo(f"top/{path}")
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(buf.getvalue())

    def log_message(self, *args):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("DRSKILL_HOME", str(home))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    service.save_credentials("http://svc.test", "drsk_x")
    monkeypatch.setattr(content, "collect_files", lambda c: list(FILES))

    server = HTTPServer(("127.0.0.1", 0), _CodeloadStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("DRSKILL_CODELOAD_URL",
                       f"http://127.0.0.1:{server.server_address[1]}")

    state = {
        "loadouts": [{"owner": "drew", "slug": "pack", "name": "Pack",
                      "visibility": "private", "description": None, "published_at": None,
                      "current_revision": {"number": 2, "runtime_hash": "sha256:" + "ee" * 32}}],
        "manifest": {"schema_version": 1, "entries": [entry()], "harness_mappings": []},
        "identity": "drew",
    }

    def fake_api_request(method, path, token=None, json_body=None, base_url=None,
                         raw=False, raw_body=None, content_type=None, binary=False):
        if path == "/api/v1/loadouts":
            return {"loadouts": state["loadouts"]}
        if path == "/api/v1/identity":
            return {"user": {"handle": state["identity"], "display_name": None}}
        if path.startswith("/api/v1/loadouts/") and path.endswith("/revisions/2"):
            return json.dumps(state["manifest"])
        if path == "/api/v1/loadouts/ana/theirs":
            return {"loadout": {"owner": "ana", "slug": "theirs", "name": "T",
                                "visibility": "public", "description": None, "published_at": None,
                                "current_revision": {"number": 2, "runtime_hash": "sha256:" + "dd" * 32}}}
        raise service.ServiceError("not_found", "Not found.")

    monkeypatch.setattr(service, "api_request", fake_api_request)
    monkeypatch.setattr(cli_mod, "run_scan",
        lambda *a, **k: (World(contributors={c.id: c for c in state["world"]}), []))
    state["world"] = [contributor("vector")]
    yield state
    server.shutdown()
    thread.join(timeout=5)
    server.server_close()


def test_all_matching_exits_zero(env):
    result = runner.invoke(app, ["loadout", "status"])
    assert result.exit_code == 0, result.output
    assert "drew/pack (revision 2)" in result.output
    assert "matches" in result.output
    assert "loadout update" not in result.output


def test_changed_entry_hints_update_and_exits_one(env):
    env["manifest"]["entries"] = [entry(content_hash="sha256:" + "00" * 32)]
    result = runner.invoke(app, ["loadout", "status"])
    assert result.exit_code == 1
    assert "changed locally since publish" in result.output
    assert "drskill loadout update drew/pack" in result.output


def test_line_states_render(env):
    env["manifest"]["entries"] = [
        entry(),
        entry(name="gone"),
        entry(name="papers", kind="mcp"),
    ]
    result = runner.invoke(app, ["loadout", "status"])
    assert "not found on this machine" in result.output
    assert "not checked (mcp)" in result.output


def test_explicit_ref_on_anothers_loadout_has_no_update_hint(env):
    env["manifest"]["entries"] = [entry(content_hash="sha256:" + "00" * 32)]
    result = runner.invoke(app, ["loadout", "status", "ana/theirs"])
    assert result.exit_code == 1
    assert "changed locally since publish" in result.output
    assert "loadout update" not in result.output


def test_revisionless_loadouts_are_skipped(env):
    env["loadouts"].append({"owner": "drew", "slug": "empty", "name": "E",
                            "visibility": "private", "description": None,
                            "published_at": None, "current_revision": None})
    result = runner.invoke(app, ["loadout", "status"])
    assert result.exit_code == 0, result.output
    assert "drew/empty" in result.output and "no published revision" in result.output


def test_remote_reports_upstream_drift(env):
    gh = entry(name="vector", source_type="github",
               metadata={"repo": "friend/pack", "skill_path": "",
                         "files": ["SKILL.md"], "directory_hash": DIR_HASH})
    env["manifest"]["entries"] = [gh]
    _CodeloadStub.upstream = DRIFTED_UPSTREAM
    try:
        result = runner.invoke(app, ["loadout", "status", "--remote"])
        assert result.exit_code == 1
        assert "upstream has changed" in result.output
    finally:
        _CodeloadStub.upstream = UPSTREAM_FILES

    result = runner.invoke(app, ["loadout", "status", "--remote"])
    assert result.exit_code == 0, result.output
    assert "upstream has changed" not in result.output
