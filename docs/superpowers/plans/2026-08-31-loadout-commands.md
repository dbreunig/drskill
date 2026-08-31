# drskill loadout Commands Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `drskill loadout` command group (list, create, show, revisions, publish, fetch) consuming the drskill-web API, with client-side runtime-hash canonicalization proven byte-identical to the Rails canonicalizer.

**Architecture:** `service.py` gains a pure `canonical_manifest` (spike-proven against Rails), `ServiceError.details`, and a `raw=` mode on `api_request` for byte-stable document fetches. `cli.py` gains a Typer sub-app plus a shared `_service_credentials()` helper (whoami/logout refactored onto it). Tests: a load-bearing cross-implementation fixture pair generated from the real Rails canonicalizer, plus a fake loadout service for CLI round-trip tests.

**Tech Stack:** Python 3.11+/Typer/rich (existing deps only), pytest via `uv run pytest`.

**Spec:** `docs/superpowers/specs/2026-08-31-loadout-commands-design.md` (this repo)

## Global Constraints

- Repo: `/Users/dbreunig/Development/drskill`, branch `loadout-commands` (create from main in Task 1: `git checkout -b loadout-commands`). All commands relative to the repo root.
- TDD: failing tests first, verify, implement, verify. Run the focused new test file while iterating; full `uv run pytest -q` before every commit (baseline: 742 passed, 11 pre-existing warnings).
- Canonicalization contract (already spike-verified): drop top-level `runtime_hash`, recursively sort dict keys (lists keep order), `json.dumps(..., ensure_ascii=False, separators=(",", ":"))`, hash `"sha256:" + sha256(utf8).hexdigest()`. The Rails-generated fixture pair pins it; the expected hash constant is `sha256:f6d5415881682c9cc3a911eb849b9a583d68f036a71635e8afb08be35658f6cc`.
- All commands resolve the base URL as `creds.get("service_url") or service.service_url()` and pass it as `base_url=` — never rely on the env var alone (the login feature's stored-URL rule).
- Error UX: `ServiceError` → `typer.echo(err.message)` (+ indented `details` lines where the command uses `_echo_service_error`) and `raise typer.Exit(1)`. Not-signed-in → `"Not signed in. Run: drskill login"` + exit 1.
- Commands call through the module (`service.api_request(...)`) — never `from drskill.service import X` — so test monkeypatching works.
- Existing interfaces reused: `service.load_credentials/save_credentials/service_url`, `ServiceError`, `api_request(method, path, token=None, json_body=None, base_url=None)`; CLI test convention `typer.testing.CliRunner`; rich `Table` used as in `cli.py`'s MCP table (`from rich.table import Table` locally, printed via the module-level `Console()`; follow whatever console instance pattern `cli.py` already uses).
- Fixture source files (copy byte-exact with `cp`): input `/Users/dbreunig/Development/drskill-web/test/fixtures/files/manifests/basic.json`; Rails canonical output `/private/tmp/claude-501/-Users-dbreunig-Development-drskill-web/48ed6ad6-89e0-4d82-b233-a126fbc5b3c8/scratchpad/canonical.json`.

---

### Task 1: canonical_manifest, ServiceError.details, api_request raw mode, fixtures

**Files:**
- Modify: `src/drskill/service.py`
- Create: `tests/fixtures/manifests/basic.json`, `tests/fixtures/manifests/basic.canonical.json` (copied byte-exact from the sources above)
- Create: `tests/test_canonical.py`
- Modify: `tests/test_service.py` (details-passthrough + raw-mode tests)

**Interfaces:**
- Produces: `service.canonical_manifest(manifest: dict) -> tuple[str, str]` (canonical_json, runtime_hash); `ServiceError(code, message, details=None)` with `.details`; `api_request(..., raw=False)` — `raw=True` returns the response body as `str` (no JSON parse). Tasks 2–3 consume all three.

- [ ] **Step 1: Create the branch and copy the fixtures**

```bash
git checkout -b loadout-commands
mkdir -p tests/fixtures/manifests
cp /Users/dbreunig/Development/drskill-web/test/fixtures/files/manifests/basic.json tests/fixtures/manifests/basic.json
cp "/private/tmp/claude-501/-Users-dbreunig-Development-drskill-web/48ed6ad6-89e0-4d82-b233-a126fbc5b3c8/scratchpad/canonical.json" tests/fixtures/manifests/basic.canonical.json
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_canonical.py`:

```python
import json
from pathlib import Path

from drskill import service

FIXTURES = Path(__file__).parent / "fixtures" / "manifests"
RAILS_HASH = "sha256:f6d5415881682c9cc3a911eb849b9a583d68f036a71635e8afb08be35658f6cc"


def load_manifest():
    return json.loads((FIXTURES / "basic.json").read_text())


def test_matches_the_rails_canonicalizer_byte_for_byte():
    canonical, runtime_hash = service.canonical_manifest(load_manifest())
    assert canonical == (FIXTURES / "basic.canonical.json").read_text()
    assert runtime_hash == RAILS_HASH


def test_key_order_independent():
    manifest = load_manifest()
    shuffled = dict(reversed(list(manifest.items())))
    assert service.canonical_manifest(shuffled) == service.canonical_manifest(manifest)


def test_drops_a_client_supplied_runtime_hash():
    manifest = load_manifest()
    tagged = {**manifest, "runtime_hash": "sha256:" + "0" * 64}
    assert service.canonical_manifest(tagged) == service.canonical_manifest(manifest)


def test_preserves_unicode_unescaped():
    canonical, _ = service.canonical_manifest({"name": "café"})
    assert '"café"' in canonical


def test_service_error_carries_details():
    err = service.ServiceError("revision_invalid", "bad", details={"manifest": ["msg"]})
    assert err.details == {"manifest": ["msg"]}
    assert service.ServiceError("x", "y").details is None
```

In `tests/test_service.py`, extend `_StubHandler.do_GET` with two branches before the 404 fallback:

```python
        elif self.path == "/invalid":
            body = json.dumps(
                {"error": {"code": "revision_invalid", "message": "The revision manifest is invalid.",
                           "details": {"manifest": ["schema_version must be 1"]}}}
            ).encode()
            self.send_response(422)
        elif self.path == "/raw":
            body = b'{"b":1,"a":2}'
            self.send_response(200)
```

and add two tests:

```python
def test_api_request_passes_envelope_details_through(stub_server):
    with pytest.raises(service.ServiceError) as excinfo:
        service.api_request("GET", "/invalid", base_url=stub_server)
    assert excinfo.value.details == {"manifest": ["schema_version must be 1"]}


def test_api_request_raw_returns_the_body_verbatim(stub_server):
    assert service.api_request("GET", "/raw", base_url=stub_server, raw=True) == '{"b":1,"a":2}'
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_canonical.py tests/test_service.py -q`
Expected: FAIL (`canonical_manifest` missing; `details`/`raw` unsupported).

- [ ] **Step 4: Implement**

In `src/drskill/service.py`:

1. Replace `ServiceError`:

```python
class ServiceError(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
```

2. In `api_request`, add the `raw: bool = False` keyword; in the `HTTPError` envelope branch pass details through (`ServiceError(envelope.get("code", ...), envelope.get("message", ...), details=envelope.get("details"))` — keeping the existing isinstance guard on the parsed body); and change the return line to:

```python
    if raw:
        return body
    return json.loads(body) if body else {}
```

3. Add the canonicalizer (near the top-level helpers):

```python
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_canonical.py tests/test_service.py -q`, then `uv run pytest -q`.
Expected: PASS (742 + 7 new).

- [ ] **Step 6: Commit**

```bash
git add src/drskill/service.py tests
git commit -m "feat: add rails-identical manifest canonicalization to the service client"
```

---

### Task 2: loadout sub-app — list, create, show (+ credentials helper refactor)

**Files:**
- Modify: `src/drskill/cli.py`
- Test: `tests/test_cli_loadouts.py`

**Interfaces:**
- Consumes: Task 1's `canonical_manifest` (not yet — Task 3), `ServiceError.details`; existing service functions.
- Produces: `loadout_app` Typer sub-app registered as `drskill loadout`; helpers `_service_credentials() -> tuple[dict, str]` (creds, base_url — exits 1 with login hint when signed out), `_parse_ref(ref) -> tuple[str, str]`, `_echo_service_error(err)`. Task 3 adds three more commands to the same sub-app using the same helpers. `whoami`/`logout` now use `_service_credentials` (behavior unchanged — their existing tests must stay green).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_loadouts.py`:

```python
import json

import pytest
from typer.testing import CliRunner

from drskill import service
from drskill.cli import app

runner = CliRunner()


@pytest.fixture
def signed_in(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    monkeypatch.delenv("DRSKILL_SERVICE_URL", raising=False)
    service.save_credentials("http://svc.test", "drsk_x")


@pytest.fixture
def api(monkeypatch):
    calls = []

    def fake_api_request(method, path, token=None, json_body=None, base_url=None, raw=False):
        calls.append({"method": method, "path": path, "token": token,
                      "json_body": json_body, "base_url": base_url, "raw": raw})
        return fake_api_request.response

    fake_api_request.response = {}
    monkeypatch.setattr(service, "api_request", fake_api_request)
    return calls, fake_api_request


LOADOUT = {
    "owner": "drew", "slug": "textbook", "name": "Textbook", "description": None,
    "visibility": "private", "published_at": None,
    "current_revision": {"number": 3, "runtime_hash": "sha256:" + "ab" * 32},
}


def test_list_renders_a_table(signed_in, api):
    calls, fake = api
    fake.response = {"loadouts": [LOADOUT]}
    result = runner.invoke(app, ["loadout", "list"])
    assert result.exit_code == 0
    assert "drew/textbook" in result.output
    assert "#3" in result.output
    assert calls[0]["path"] == "/api/v1/loadouts"
    assert calls[0]["base_url"] == "http://svc.test"


def test_list_json_emits_the_raw_response(signed_in, api):
    calls, fake = api
    fake.response = {"loadouts": [LOADOUT]}
    result = runner.invoke(app, ["loadout", "list", "--json"])
    assert json.loads(result.output)["loadouts"][0]["slug"] == "textbook"


def test_list_when_signed_out_hints_at_login(tmp_path, monkeypatch):
    monkeypatch.setenv("DRSKILL_HOME", str(tmp_path))
    result = runner.invoke(app, ["loadout", "list"])
    assert result.exit_code == 1
    assert "drskill login" in result.output


def test_create_posts_and_prints_the_ref(signed_in, api):
    calls, fake = api
    fake.response = {"loadout": LOADOUT}
    result = runner.invoke(
        app, ["loadout", "create", "textbook", "--name", "Textbook", "--description", "d"]
    )
    assert result.exit_code == 0
    assert "drew/textbook" in result.output
    assert calls[0]["method"] == "POST"
    assert calls[0]["json_body"] == {"loadout": {"slug": "textbook", "name": "Textbook", "description": "d"}}


def test_create_validation_failure_prints_details(signed_in, monkeypatch):
    def failing(*args, **kwargs):
        raise service.ServiceError("loadout_invalid", "The loadout is invalid.",
                                   details={"slug": ["is invalid"]})

    monkeypatch.setattr(service, "api_request", failing)
    result = runner.invoke(app, ["loadout", "create", "Bad Slug", "--name", "X"])
    assert result.exit_code == 1
    assert "The loadout is invalid." in result.output
    assert "slug: is invalid" in result.output


def test_show_prints_metadata_and_provenance(signed_in, api):
    calls, fake = api
    fake.response = {"loadout": {**LOADOUT, "forked_from": {"owner": "ann", "slug": "orig", "revision_number": 2}}}
    result = runner.invoke(app, ["loadout", "show", "drew/textbook"])
    assert result.exit_code == 0
    assert "drew/textbook" in result.output
    assert "Forked from ann/orig" in result.output
    assert calls[0]["path"] == "/api/v1/loadouts/drew/textbook"


def test_show_rejects_a_bad_ref(signed_in, api):
    result = runner.invoke(app, ["loadout", "show", "no-slash"])
    assert result.exit_code == 1
    assert "owner/slug" in result.output
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_loadouts.py -q`
Expected: FAIL (no `loadout` command).

- [ ] **Step 3: Implement**

In `src/drskill/cli.py` (ensure `import json` is present at the top — add it if missing), after the existing `app = typer.Typer(...)` line add:

```python
loadout_app = typer.Typer(add_completion=False, help="Manage loadouts on the drskill service")
app.add_typer(loadout_app, name="loadout")
```

Add the helpers (near the other private helpers):

```python
def _service_credentials() -> tuple[dict, str]:
    creds = service.load_credentials()
    if not creds:
        typer.echo("Not signed in. Run: drskill login")
        raise typer.Exit(1)
    return creds, creds.get("service_url") or service.service_url()


def _parse_ref(ref: str) -> tuple[str, str]:
    owner, _, slug = ref.partition("/")
    if not owner or not slug or "/" in slug:
        typer.echo(f"Expected owner/slug, got {ref!r}")
        raise typer.Exit(1)
    return owner, slug


def _echo_service_error(err: "service.ServiceError") -> None:
    typer.echo(err.message)
    for field, messages in (err.details or {}).items():
        for message in messages:
            typer.echo(f"  {field}: {message}")
```

Refactor `whoami` and `logout` to open with `creds, base = _service_credentials()` (dropping their inline `load_credentials`/hint blocks) and pass `base_url=base` — output and behavior otherwise unchanged.

Add the three commands:

```python
@loadout_app.command("list")
def loadout_list(
    as_json: bool = typer.Option(False, "--json", help="emit the raw API response"),
) -> None:
    """List your loadouts on the drskill service."""
    creds, base = _service_credentials()
    try:
        data = service.api_request("GET", "/api/v1/loadouts", token=creds["token"], base_url=base)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    loadouts = data.get("loadouts", [])
    if not loadouts:
        typer.echo("No loadouts yet. Create one with: drskill loadout create <slug> --name <name>")
        return
    from rich.table import Table

    table = Table(title="Your loadouts")
    table.add_column("ref")
    table.add_column("name")
    table.add_column("visibility")
    table.add_column("current rev")
    for loadout in loadouts:
        revision = loadout.get("current_revision")
        rev_text = f"#{revision['number']} {revision['runtime_hash'][:17]}…" if revision else "—"
        table.add_row(
            f"{loadout['owner']}/{loadout['slug']}",
            loadout.get("name") or "",
            loadout.get("visibility") or "",
            rev_text,
        )
    Console().print(table)


@loadout_app.command()
def create(
    slug: str = typer.Argument(..., help="URL slug for the new loadout"),
    name: str = typer.Option(..., "--name", help="display name"),
    description: str | None = typer.Option(None, "--description", help="optional description"),
) -> None:
    """Create a private loadout on the drskill service."""
    creds, base = _service_credentials()
    body: dict = {"loadout": {"slug": slug, "name": name}}
    if description is not None:
        body["loadout"]["description"] = description
    try:
        data = service.api_request(
            "POST", "/api/v1/loadouts", token=creds["token"], json_body=body, base_url=base
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    loadout = data["loadout"]
    typer.echo(f"Created {loadout['owner']}/{loadout['slug']} ({loadout['visibility']})")


@loadout_app.command()
def show(
    ref: str = typer.Argument(..., help="owner/slug"),
    as_json: bool = typer.Option(False, "--json", help="emit the raw API response"),
) -> None:
    """Show a loadout's metadata and current revision."""
    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    try:
        data = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}", token=creds["token"], base_url=base
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    loadout = data["loadout"]
    typer.echo(f"{loadout['owner']}/{loadout['slug']} — {loadout.get('name') or ''}")
    typer.echo(f"  visibility: {loadout.get('visibility')}")
    if loadout.get("description"):
        typer.echo(f"  description: {loadout['description']}")
    revision = loadout.get("current_revision")
    if revision:
        typer.echo(f"  current revision: #{revision['number']} {revision['runtime_hash']}")
    else:
        typer.echo("  current revision: none")
    if loadout.get("published_at"):
        typer.echo(f"  published: {loadout['published_at']}")
    forked = loadout.get("forked_from")
    if forked:
        suffix = f" · revision {forked['revision_number']}" if forked.get("revision_number") else ""
        typer.echo(f"  Forked from {forked['owner']}/{forked['slug']}{suffix}")
```

(If `cli.py` uses a shared module-level `Console()` instance elsewhere, reuse that instead of constructing a new one — match the file's existing pattern.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_loadouts.py tests/test_cli_login.py -q` (login tests prove the whoami/logout refactor kept behavior), then `uv run pytest -q`.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/drskill/cli.py tests/test_cli_loadouts.py
git commit -m "feat: add drskill loadout list, create, and show"
```

---

### Task 3: revisions, publish, fetch + fake-service round trip

**Files:**
- Modify: `src/drskill/cli.py`
- Create: `tests/test_service_loadouts.py`
- Test: `tests/test_cli_loadouts.py` (extend)

**Interfaces:**
- Consumes: Task 1's `canonical_manifest` + `raw=`; Task 2's sub-app and helpers.
- Produces: `drskill loadout revisions|publish|fetch` per the spec's command table.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli_loadouts.py`:

```python
def test_revisions_renders_a_table(signed_in, api):
    calls, fake = api
    fake.response = {"revisions": [
        {"number": 2, "runtime_hash": "sha256:" + "cd" * 32, "published_at": "2026-08-31T00:00:00Z",
         "reproducible": True, "schema_version": 1},
        {"number": 1, "runtime_hash": "sha256:" + "ab" * 32, "published_at": "2026-08-30T00:00:00Z",
         "reproducible": False, "schema_version": 1},
    ]}
    result = runner.invoke(app, ["loadout", "revisions", "drew/textbook"])
    assert result.exit_code == 0
    assert "2" in result.output and "1" in result.output
    assert calls[0]["path"] == "/api/v1/loadouts/drew/textbook/revisions"


def test_publish_sends_the_computed_hash(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = {"revision": {"number": 1, "runtime_hash": "sha256:" + "ee" * 32}}
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text('{"schema_version":1,"entries":[],"harness_mappings":[]}')

    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(manifest_path)])
    assert result.exit_code == 0
    assert "Published revision 1" in result.output
    body = calls[0]["json_body"]
    _, expected_hash = service.canonical_manifest(json.loads(manifest_path.read_text()))
    assert body["runtime_hash"] == expected_hash
    assert body["manifest"]["schema_version"] == 1


def test_publish_no_verify_omits_the_hash(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = {"revision": {"number": 1, "runtime_hash": "sha256:" + "ee" * 32}}
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text('{"schema_version":1,"entries":[],"harness_mappings":[]}')
    runner.invoke(app, ["loadout", "publish", "drew/textbook", str(manifest_path), "--no-verify"])
    assert "runtime_hash" not in calls[0]["json_body"]


def test_publish_hash_mismatch_prints_both_hashes(signed_in, monkeypatch, tmp_path):
    def failing(*args, **kwargs):
        raise service.ServiceError(
            "revision_invalid", "The revision manifest is invalid.",
            details={"manifest": ["runtime_hash mismatch: client sent x, server computed y"]},
        )

    monkeypatch.setattr(service, "api_request", failing)
    manifest_path = tmp_path / "m.json"
    manifest_path.write_text('{"schema_version":1,"entries":[],"harness_mappings":[]}')
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(manifest_path)])
    assert result.exit_code == 1
    assert "runtime_hash mismatch" in result.output
    assert "client runtime_hash: sha256:" in result.output


def test_publish_rejects_an_unreadable_or_invalid_manifest(signed_in, api, tmp_path):
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(tmp_path / "missing.json")])
    assert result.exit_code == 1
    assert "Could not read manifest" in result.output

    bad = tmp_path / "bad.json"
    bad.write_text("[1,2]")
    result = runner.invoke(app, ["loadout", "publish", "drew/textbook", str(bad)])
    assert result.exit_code == 1
    assert "JSON object" in result.output


def test_fetch_by_number_prints_the_raw_document(signed_in, api):
    calls, fake = api
    fake.response = '{"a":1}'
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "3"])
    assert result.exit_code == 0
    assert '{"a":1}' in result.output
    assert calls[0]["path"] == "/api/v1/loadouts/drew/textbook/revisions/3"
    assert calls[0]["raw"] is True


def test_fetch_bare_hash_uses_the_global_lookup(signed_in, api):
    calls, fake = api
    fake.response = '{"a":1}'
    target = "sha256:" + "ab" * 32
    result = runner.invoke(app, ["loadout", "fetch", target])
    assert result.exit_code == 0
    assert calls[0]["path"] == f"/api/v1/revision_hashes/{target}"


def test_fetch_ref_without_revision_errors(signed_in, api):
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook"])
    assert result.exit_code == 1
    assert "revision number" in result.output


def test_fetch_output_writes_the_file(signed_in, api, tmp_path):
    calls, fake = api
    fake.response = '{"a":1}'
    out = tmp_path / "manifest.json"
    result = runner.invoke(app, ["loadout", "fetch", "drew/textbook", "3", "-o", str(out)])
    assert result.exit_code == 0
    assert out.read_text() == '{"a":1}'
```

Create `tests/test_service_loadouts.py` — fake loadout service + end-to-end round trip:

```python
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

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/v1/loadouts/drew/textbook/revisions":
                    canonical, computed = service.canonical_manifest(body["manifest"])
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_loadouts.py tests/test_service_loadouts.py -q`
Expected: FAIL (missing commands).

- [ ] **Step 3: Implement the three commands**

Add to the `loadout_app` section of `src/drskill/cli.py` (note `from pathlib import Path` is already imported):

```python
@loadout_app.command()
def revisions(
    ref: str = typer.Argument(..., help="owner/slug"),
    as_json: bool = typer.Option(False, "--json", help="emit the raw API response"),
) -> None:
    """List a loadout's revision history."""
    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    try:
        data = service.api_request(
            "GET", f"/api/v1/loadouts/{owner}/{slug}/revisions", token=creds["token"], base_url=base
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if as_json:
        typer.echo(json.dumps(data, indent=2))
        return
    from rich.table import Table

    table = Table(title=f"Revisions of {owner}/{slug}")
    table.add_column("rev")
    table.add_column("runtime hash")
    table.add_column("published")
    table.add_column("reproducible")
    for revision in data.get("revisions", []):
        table.add_row(
            str(revision["number"]),
            revision["runtime_hash"],
            str(revision.get("published_at") or ""),
            "yes" if revision.get("reproducible") else "no",
        )
    Console().print(table)


@loadout_app.command()
def publish(
    ref: str = typer.Argument(..., help="owner/slug"),
    manifest_path: Path = typer.Argument(..., help="path to a resolved manifest JSON file"),
    no_verify: bool = typer.Option(False, "--no-verify", help="do not send a client-computed runtime hash"),
) -> None:
    """Publish a manifest file as a new immutable revision."""
    creds, base = _service_credentials()
    owner, slug = _parse_ref(ref)
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as err:
        typer.echo(f"Could not read manifest: {err}")
        raise typer.Exit(1)
    if not isinstance(manifest, dict):
        typer.echo("Manifest must be a JSON object.")
        raise typer.Exit(1)
    body: dict = {"manifest": manifest}
    client_hash = None
    if not no_verify:
        _, client_hash = service.canonical_manifest(manifest)
        body["runtime_hash"] = client_hash
    try:
        data = service.api_request(
            "POST", f"/api/v1/loadouts/{owner}/{slug}/revisions",
            token=creds["token"], json_body=body, base_url=base,
        )
    except service.ServiceError as err:
        _echo_service_error(err)
        if client_hash and err.code == "revision_invalid":
            typer.echo(f"  client runtime_hash: {client_hash}")
        raise typer.Exit(1)
    revision = data["revision"]
    typer.echo(f"Published revision {revision['number']} ({revision['runtime_hash']})")


@loadout_app.command()
def fetch(
    target: str = typer.Argument(..., help="owner/slug, or a bare sha256:<hash>"),
    revision: str = typer.Argument(None, help="revision number or sha256:<hash> (with owner/slug)"),
    output: Path = typer.Option(None, "-o", "--output", help="write the document to a file"),
) -> None:
    """Fetch a revision's canonical manifest, byte-stable."""
    creds, base = _service_credentials()
    if target.startswith("sha256:"):
        path = f"/api/v1/revision_hashes/{target}"
    else:
        owner, slug = _parse_ref(target)
        if not revision:
            typer.echo("Provide a revision number or sha256:<hash> after owner/slug.")
            raise typer.Exit(1)
        path = f"/api/v1/loadouts/{owner}/{slug}/revisions/{revision}"
    try:
        document = service.api_request("GET", path, token=creds["token"], base_url=base, raw=True)
    except service.ServiceError as err:
        _echo_service_error(err)
        raise typer.Exit(1)
    if output:
        output.write_text(document)
        typer.echo(f"Wrote {output}")
    else:
        typer.echo(document)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_loadouts.py tests/test_service_loadouts.py -q`, then `uv run pytest -q`.
Expected: PASS. (In the fetch-with-`-o` test, "Wrote" appears on stdout and the file holds the document.)

- [ ] **Step 5: Commit**

```bash
git add src/drskill/cli.py tests
git commit -m "feat: add drskill loadout revisions, publish, and fetch"
```

---

### Task 4: Verification and wrap-up

- [ ] **Step 1: Full suite**

Run: `uv run pytest -q` — expected all green (~760).

- [ ] **Step 2: Live smoke against the dev server** (controller-driven; requires the drskill-web dev server on localhost:3000 and a minted token)

```bash
# with credentials in a scratch DRSKILL_HOME as in earlier smokes:
drskill loadout create cli-smoke --name "CLI Smoke"
drskill loadout publish <handle>/cli-smoke tests/fixtures/manifests/basic.json   # must print the f6d5… hash
drskill loadout fetch <handle>/cli-smoke 1                                       # bytes must re-hash to f6d5…
drskill loadout list
```

- [ ] **Step 3: Finish**

Use superpowers:finishing-a-development-branch (branch `loadout-commands`).
