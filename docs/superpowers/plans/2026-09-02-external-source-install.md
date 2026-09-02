# External Source Install Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill loadout install` fetches, verifies, and installs `github` entries, with an interactive review-and-republish (or fork) flow when upstream has changed.

**Architecture:** Publish writes fetch coordinates and a directory hash into github entries' metadata. A new `gh_source.py` module fetches repo tarballs from codeload, locates the skill directory, and verifies it. The install command routes github entries through it and, on a hash mismatch, runs lint on the fetched content, offers acks, and republishes (owner) or forks via a new server endpoint (non-owner).

**Tech Stack:** Python stdlib (`urllib`, `tarfile`) in the drskill CLI; one Rails endpoint in drskill-web reusing `Loadouts::Fork`.

**Spec:** `docs/superpowers/specs/2026-09-02-external-source-install-design.md`

## Global Constraints

- CLI repo: `~/Development/drskill` (pytest via `uv run pytest`). Server repo: `~/Development/drskill-web` (`bin/rails test`).
- Only `codeload.github.com` is contacted for fetches, https, 100 MB compressed cap enforced while streaming, `GITHUB_TOKEN` sent as a bearer header when set.
- Extraction gate identical to hosted downloads: regular files only, safe relative paths, at most 200 files and 20 MB unpacked (constants exist in `src/drskill/content.py`).
- Verification: `directory_hash` exact match, else legacy SKILL.md check via `resolution.content_hash`; no bypass flag.
- Mismatch message, verbatim: "The remote skill has been updated since this loadout was created and the original version was not pinned."
- Acks append to the machine ledger `home / ".drskill.toml"` via `ledger.append_ack`.
- Exit code 1 only when at least one entry failed and nothing was installed or already present.
- Commits end with the Co-Authored-By and Claude-Session trailer used in recent commits (`git log -3`).
- TDD throughout: failing test, watch it fail, implement, watch it pass, commit.

---

### Task 1: Fork endpoint (drskill-web)

**Files (all in `~/Development/drskill-web`):**
- Modify: `config/routes.rb` (api v1 loadouts block)
- Modify: `app/controllers/api/v1/loadouts_controller.rb`
- Modify: `public/api/v1/openapi.yml`, `test/openapi_test.rb`
- Test: `test/requests/api/v1/loadouts_test.rb` (append tests)

**Interfaces:**
- Consumes: `Loadouts::Fork.call(user:, origin:, attributes:)` (raises `Loadouts::Fork::NoRevisionError`; returns an unpersisted loadout with errors on validation failure).
- Produces: `POST /api/v1/loadouts/:owner_handle/:slug/fork`, body optional `{loadout: {slug, name, description}}`, 201 with the create endpoint's loadout JSON; 404 uniform when not viewable; 422 `loadout_invalid` with details on collision; 422 `fork_invalid` when the origin has no revision.

- [ ] **Step 1: Write the failing request tests** (append to `test/requests/api/v1/loadouts_test.rb`, reusing its local helpers for tokens and loadout setup; read the file first and follow its fixtures)

```ruby
test "fork copies a viewable loadout with the origin's revision" do
  # origin: another user's loadout, visibility :unlisted, with one published revision
  post fork_api_v1_loadout_path(@origin.owner.handle, @origin.slug),
    headers: api_headers(@token.plaintext), as: :json
  assert_response :created
  body = JSON.parse(response.body)["loadout"]
  assert_equal @user.handle, body["owner"]
  assert_equal @origin.slug, body["slug"]
  assert_equal "private", body["visibility"]
  fork = @user.loadouts.find_by!(slug: @origin.slug)
  assert_equal @origin.current_revision.runtime_hash, fork.current_revision.runtime_hash
end

test "fork accepts attribute overrides" do
  post fork_api_v1_loadout_path(@origin.owner.handle, @origin.slug),
    headers: api_headers(@token.plaintext),
    params: { loadout: { slug: "my-copy", name: "My copy" } }, as: :json
  assert_response :created
  assert_equal "my-copy", JSON.parse(response.body).dig("loadout", "slug")
end

test "fork slug collision returns loadout_invalid with details" do
  @user.loadouts.create!(slug: @origin.slug, name: "Taken")
  post fork_api_v1_loadout_path(@origin.owner.handle, @origin.slug),
    headers: api_headers(@token.plaintext), as: :json
  assert_response :unprocessable_entity
  body = JSON.parse(response.body)
  assert_equal "loadout_invalid", body.dig("error", "code")
  assert body.dig("error", "details", "slug")
end

test "fork of a revisionless loadout returns fork_invalid" do
  bare = @origin.owner.loadouts.create!(slug: "bare", name: "Bare", visibility: :unlisted)
  post fork_api_v1_loadout_path(bare.owner.handle, "bare"),
    headers: api_headers(@token.plaintext), as: :json
  assert_response :unprocessable_entity
  assert_equal "fork_invalid", JSON.parse(response.body).dig("error", "code")
end

test "fork of an invisible loadout is a uniform 404" do
  # origin private, requester is not the owner
  post fork_api_v1_loadout_path(@private_origin.owner.handle, @private_origin.slug),
    headers: api_headers(@token.plaintext), as: :json
  assert_response :not_found
end

test "forking a loadout with hosted entries succeeds for a viewer" do
  # origin unlisted, revision has a source_type "drskill" entry whose
  # ContentArchive + blob exist (create via Content::Ingest or fixtures)
  post fork_api_v1_loadout_path(@hosted_origin.owner.handle, @hosted_origin.slug),
    headers: api_headers(@token.plaintext), as: :json
  assert_response :created
end
```

- [ ] **Step 2: Run to verify failure** — `bin/rails test test/requests/api/v1/loadouts_test.rb`: FAIL (undefined route helper).

- [ ] **Step 3: Route and action.** In `config/routes.rb`, inside `resources :loadouts` collection block after the named show route:

```ruby
post ":owner_handle/:slug/fork", action: :fork, as: :fork
```

In `app/controllers/api/v1/loadouts_controller.rb`:

```ruby
def fork
  origin = find_loadout
  return render_not_found unless origin && LoadoutPolicy.new(Current.user, origin).fork?

  attributes = { slug: origin.slug, name: origin.name, description: origin.description }
    .merge(fork_params)
  forked = Loadouts::Fork.call(user: Current.user, origin: origin, attributes: attributes)
  if forked.persisted?
    render json: { loadout: loadout_json(forked) }, status: :created
  else
    render_error("loadout_invalid", "The loadout is invalid.",
      status: :unprocessable_entity, details: forked.errors.to_hash)
  end
rescue Loadouts::Fork::NoRevisionError => error
  render_error("fork_invalid", error.message, status: :unprocessable_entity)
end

# in private:
def fork_params
  params.fetch(:loadout, {}).permit(:slug, :name, :description).to_h.symbolize_keys
end
```

Check `Loadouts::Fork`'s transaction: if `fork.save` succeeds but `PublishRevision` raises, ensure the error propagates (it does; the transaction rolls back). Verify `LoadoutPolicy#fork?` requires `show?`.

- [ ] **Step 4: Run to verify pass** — `bin/rails test test/requests/api/v1/loadouts_test.rb`.

- [ ] **Step 5: OpenAPI.** Add the operation to `public/api/v1/openapi.yml` in the document's style (201/404/422 responses, optional request body) and `["/api/v1/loadouts/{owner_handle}/{slug}/fork", "post"]` to `EXPECTED_OPERATIONS` in `test/openapi_test.rb`. Run `bin/rails test test/openapi_test.rb` (fail first, then pass).

- [ ] **Step 6: Full suite and commit** — `bin/rails test`, then commit in drskill-web: `feat: add fork over the API`.

---

### Task 2: Provenance carries path and ref (CLI)

**Files (all in `~/Development/drskill` from here on):**
- Modify: `src/drskill/models.py` (Provenance), `src/drskill/pipeline.py` (lockfile pass), `src/drskill/resolution.py` (frontmatter provenance)
- Test: `tests/test_discovery.py` or `tests/test_scan_pipeline.py` — find where the lockfile pass and frontmatter provenance are currently tested (`grep -rn "skills-lock" tests/`) and add there.

**Interfaces:**
- Produces: `Provenance.path: str | None` (skill directory path inside the repo, no trailing `/SKILL.md`), `Provenance.ref: str | None`.

- [ ] **Step 1: Failing tests.** Where the lockfile pass is tested, add: a lock entry `{"source": "friend/pack", "skillPath": "skills/citation/SKILL.md"}` yields `contributor.source.path == "skills/citation"`; a root-level `"skillPath": "SKILL.md"` yields `path == ""`. Where frontmatter provenance is tested, add: frontmatter `{"source": "friend/pack", "ref": "v1.2.0"}` yields `source.ref == "v1.2.0"` and `source.kind == "gh-skill"`.

- [ ] **Step 2: Verify failure**, then implement:

```python
# models.py Provenance gains:
path: str | None = None  # skill directory inside the repo
ref: str | None = None   # pinned git ref when known
```

```python
# pipeline.py lockfile pass: build Provenance with path
skill_path = entry.get("skillPath")
path = None
if isinstance(skill_path, str):
    path = skill_path.removesuffix("/SKILL.md").removesuffix("SKILL.md").rstrip("/")
... Provenance(kind="skills-lock", source=entry.get("source"), path=path)
```

```python
# resolution.py frontmatter provenance:
provenance = Provenance(kind="gh-skill", source=fm.get("source"),
                        ref=fm.get("ref") if isinstance(fm.get("ref"), str) else None)
```

- [ ] **Step 3: Verify pass, run `uv run pytest -q`, commit** — `feat: carry skill path and ref in provenance`.

---

### Task 3: Manifest build records github metadata (CLI)

**Files:**
- Modify: `src/drskill/manifest_build.py`
- Test: `tests/test_manifest_build.py`

**Interfaces:**
- Consumes: `content.collect_files(contributor)`, `content.manifest_hash(files)`, `Provenance.path/.ref`, `contributor.frontmatter.get("tree_sha")`.
- Produces: github entries' `metadata` may contain `directory_hash`, `repo`, `skill_path`, `ref`, `tree_sha` (each omitted when unknown). Helper `parse_repo(source: str) -> str | None` returning `owner/repo` or None.

- [ ] **Step 1: Failing tests** (extend `tests/test_manifest_build.py`; its `contributor()` helper uses fake `/tmp/<name>` ids, so monkeypatch `content.collect_files` where a hash is expected):

```python
def test_github_entries_record_fetch_metadata(tmp_path, monkeypatch):
    from drskill import content
    monkeypatch.setattr(content, "collect_files",
        lambda c: [{"path": "SKILL.md", "data": b"x", "executable": False}])
    c = contributor("citation")
    c.source.path = "skills/citation"
    c.source.ref = "v1.2.0"
    manifest, _ = manifest_build.contributors_to_manifest([c])
    md = manifest["entries"][0]["metadata"]
    assert md["repo"] == "friend/skill"          # from "friend/skill@v1" source
    assert md["skill_path"] == "skills/citation"
    assert md["ref"] == "v1.2.0"
    assert md["directory_hash"].startswith("sha256:")

def test_unreadable_directory_omits_the_hash(monkeypatch):
    from drskill import content
    def boom(c): raise OSError("gone")
    monkeypatch.setattr(content, "collect_files", boom)
    manifest, _ = manifest_build.contributors_to_manifest([contributor("citation")])
    assert "directory_hash" not in manifest["entries"][0]["metadata"]
    assert manifest["entries"][0]["metadata"]["repo"] == "friend/skill"

def test_parse_repo():
    assert manifest_build.parse_repo("friend/skill@v1") == "friend/skill"
    assert manifest_build.parse_repo("friend/skill") == "friend/skill"
    assert manifest_build.parse_repo("https://github.com/friend/skill.git") == "friend/skill"
    assert manifest_build.parse_repo("not a repo") is None

def test_local_and_hosted_entries_get_no_fetch_metadata(...):
    # unmanaged contributor and a hosted-map contributor both keep metadata == {}
```

- [ ] **Step 2: Verify failure, implement.** In `manifest_build.py`:

```python
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

def parse_repo(source: str | None) -> str | None:
    if not isinstance(source, str):
        return None
    s = source.strip()
    s = s.removeprefix("https://github.com/").removeprefix("github.com/").removeprefix("github:")
    s = s.removesuffix(".git")
    s = s.split("@", 1)[0]
    parts = s.split("/")
    if len(parts) >= 2 and _REPO.match("/".join(parts[:2])):
        return "/".join(parts[:2])
    return None

def _github_metadata(contributor: Contributor) -> dict:
    from drskill import content  # local import; content imports service

    md: dict = {}
    repo = parse_repo(contributor.source.source)
    if repo:
        md["repo"] = repo
    if contributor.source.path is not None:
        md["skill_path"] = contributor.source.path
    if contributor.source.ref:
        md["ref"] = contributor.source.ref
    tree_sha = contributor.frontmatter.get("tree_sha")
    if isinstance(tree_sha, str) and tree_sha:
        md["tree_sha"] = tree_sha
    try:
        md["directory_hash"] = content.manifest_hash(content.collect_files(contributor))
    except OSError:
        pass
    return md
```

In the entry loop, when the entry is not hosted and `source_type == "github"`, set `"metadata": _github_metadata(contributor)`, else keep `{}`.

- [ ] **Step 3: Verify pass, run wizard tests too (`uv run pytest tests/test_manifest_build.py tests/test_loadout_wizard.py -q`), commit** — `feat: record fetch metadata on github entries`.

Note: the wizard tests' contributors have fake paths; `_github_metadata` swallows OSError so they publish without `directory_hash` — assert nothing breaks.

---

### Task 4: GitHub fetcher (CLI)

**Files:**
- Create: `src/drskill/gh_source.py`
- Test: `tests/test_gh_source.py`

**Interfaces:**
- Produces:
  - `class FetchError(Exception)` with a `.message` str (subclassing Exception; `str(e)` is the message).
  - `coordinates(entry: dict) -> tuple[str, str] | None` — (repo, ref) from metadata/source_reference/source_version, None when unparseable.
  - `fetch_tarball(repo: str, ref: str, base_url: str | None = None) -> bytes` — base_url defaults to `os.environ.get("DRSKILL_CODELOAD_URL", "https://codeload.github.com")`; raises FetchError on HTTP errors and on exceeding `MAX_TARBALL_BYTES = 100 * 1024 * 1024` (checked while reading in chunks).
  - `extract_skill(tar_bytes: bytes, entry: dict) -> list[dict]` — returns content.py-shaped files `[{path, data, executable}]`; raises FetchError on locate failure or unsafe members.
  - `verify(files: list[dict], entry: dict) -> str` — `"ok"` (directory_hash match), `"legacy_ok"` (no directory_hash, SKILL.md content_hash match), `"mismatch"`.

- [ ] **Step 1: Failing tests.** Build repo tarballs in tests with stdlib tarfile (top-level dir like a codeload tarball):

```python
def repo_tarball(files: dict[str, bytes], top="repo-abc123") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for path, data in files.items():
            info = tarfile.TarInfo(f"{top}/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()
```

Tests: `coordinates` prefers `metadata.ref` over `source_version` over `"HEAD"`, uses `metadata.repo` else parses `source_reference`, returns None for junk. `extract_skill` finds the dir via `metadata.skill_path`; via hash search when `skill_path` is missing (one SKILL.md dir matching `directory_hash`); root-level skill (`skill_path == ""`); FetchError when no SKILL.md matches or when two candidate dirs have identical content and no hash discriminates... several-match rule: FetchError when zero or several dirs match. Symlink member under the skill dir raises FetchError; files outside the skill dir are ignored. `verify` covers all three outcomes — build the legacy case with an entry whose `content_hash` is `resolution.content_hash(<skill md text>)` and no `directory_hash`. `fetch_tarball` against `pytest`'s `httpserver`? No such fixture exists in this repo — use the same `HTTPServer` thread pattern as `tests/test_service.py::stub_server` serving a tarball, plus a 404 case and an oversized-response case (serve > cap? too big; instead set `gh_source.MAX_TARBALL_BYTES` small via monkeypatch and serve a body over it).

- [ ] **Step 2: Verify failure, implement.** Core of `gh_source.py`:

```python
"""Fetch and verify github-sourced skills for loadout install."""
from __future__ import annotations

import io
import os
import tarfile
import urllib.request

from drskill import content, resolution
from drskill.manifest_build import parse_repo

MAX_TARBALL_BYTES = 100 * 1024 * 1024
_CHUNK = 1024 * 1024


class FetchError(Exception):
    pass


def coordinates(entry: dict) -> tuple[str, str] | None:
    md = entry.get("metadata") or {}
    repo = md.get("repo") or parse_repo(entry.get("source_reference"))
    if not repo:
        return None
    ref = md.get("ref") or entry.get("source_version") or "HEAD"
    return repo, ref


def fetch_tarball(repo: str, ref: str, base_url: str | None = None) -> bytes:
    base = (base_url or os.environ.get("DRSKILL_CODELOAD_URL",
                                       "https://codeload.github.com")).rstrip("/")
    request = urllib.request.Request(f"{base}/{repo}/tar.gz/{ref}")
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            chunks, total = [], 0
            while chunk := response.read(_CHUNK):
                total += len(chunk)
                if total > MAX_TARBALL_BYTES:
                    raise FetchError(f"{repo}@{ref} exceeds the 100 MB download cap")
                chunks.append(chunk)
            return b"".join(chunks)
    except urllib.error.HTTPError as error:
        raise FetchError(f"GitHub returned HTTP {error.code} for {repo}@{ref}") from None
    except urllib.error.URLError as error:
        raise FetchError(f"could not reach GitHub: {error.reason}") from None


def _members_by_dir(tar) -> dict[str, list]:
    ...  # strip the single top-level segment; group file members by the
         # directory whose SKILL.md they sit under


def extract_skill(tar_bytes: bytes, entry: dict) -> list[dict]:
    # open tarball; collect candidate dirs = every directory containing SKILL.md
    # (path stripped of the top-level segment). If metadata.skill_path is
    # recorded, candidates = [skill_path]. For each candidate, extract its
    # files through the same gate as content.Unpack (_safe_relpath on the
    # dir-relative path, regular files only, MAX_FILES/MAX_UNPACKED caps),
    # then keep candidates that verify() != "mismatch". Exactly one -> return
    # its files. Zero or several -> FetchError naming the outcome.
```

Write `extract_skill` in full (no pseudo-code in the implementation): iterate `tar.getmembers()`, strip the first path segment, map dir -> members, apply the gate per candidate, run `verify` per candidate when locating by search, and return the single verified candidate's files. Reuse `content._safe_relpath` and the caps rather than re-declaring them.

```python
def verify(files: list[dict], entry: dict) -> str:
    md = entry.get("metadata") or {}
    directory_hash = md.get("directory_hash")
    if directory_hash:
        return "ok" if content.manifest_hash(files) == directory_hash else "mismatch"
    skill_md = next((f for f in files if f["path"] == "SKILL.md"), None)
    if skill_md is None:
        return "mismatch"
    try:
        text = skill_md["data"].decode()
    except UnicodeDecodeError:
        return "mismatch"
    expected = entry.get("content_hash")
    return "legacy_ok" if resolution.content_hash(text) == expected else "mismatch"
```

Locate-by-search subtlety: when searching without `skill_path`, a candidate is a match when `verify` returns `"ok"` or `"legacy_ok"`. With `skill_path` recorded, return that directory's files without verifying here — the caller verifies and owns the mismatch flow.

- [ ] **Step 3: Verify pass, commit** — `feat: add the github skill fetcher`.

---

### Task 5: Install command fetches github entries (CLI)

**Files:**
- Modify: `src/drskill/cli.py` (`install` and helpers)
- Test: `tests/test_cli_install.py`

**Interfaces:**
- Consumes: `gh_source.coordinates/fetch_tarball/extract_skill/verify`, existing install plumbing.
- Produces: install handles `github` entries end to end for the non-mismatch outcomes; `_install_one_github(entry, target, force) -> str` status; mismatches call `_remediate(...)` which this task stubs as the non-interactive failure message (Task 6 replaces it).

- [ ] **Step 1: Failing tests** (extend `tests/test_cli_install.py`; add a github entry to `MANIFEST` whose metadata carries `repo`, `skill_path`, `directory_hash` for tarball `GH_FILES`; serve the tarball from a stub `HTTPServer` fixture and set `DRSKILL_CODELOAD_URL` to it):

- install writes both the hosted and the github skill; the plan listing shows `friend/tracked @ v1`.
- a second run reports both as already installed.
- a legacy entry (no `directory_hash`, correct `content_hash`) installs and prints the bundled-files caveat.
- a mismatching `directory_hash` with `--yes` fails that entry with the verbatim message and the interactive hint, installs the others, exit 0.
- an unparseable source (`source_reference: "???"`, no metadata) is reported and skipped.
- all-entries-fail (single mismatching entry, `--yes`) exits 1.

- [ ] **Step 2: Verify failure, implement.** In `install`, replace the hosted-only filtering: partition entries into `hosted`, `github`, `other`; list github entries as `f"  {e['name']}  ({repo} @ {ref})"` or as unfetchable with the reason; keep the `other`-count note. Per github entry after the confirm:

```python
status = _install_one_github(entry, target, force, yes=yes)
```

```python
def _install_one_github(entry, target: Path, force: bool, yes: bool) -> str:
    from drskill import content, gh_source

    coords = gh_source.coordinates(entry)
    if coords is None:
        typer.echo(f"  {entry['name']}: source {entry.get('source_reference')!r} is not fetchable")
        return "failed"
    repo, ref = coords
    dest = target / entry["name"]
    try:
        tar_bytes = gh_source.fetch_tarball(repo, ref)
        files = gh_source.extract_skill(tar_bytes, entry)
    except gh_source.FetchError as error:
        typer.echo(f"  {entry['name']}: {error}")
        return "failed"
    outcome = gh_source.verify(files, entry)
    if outcome == "mismatch":
        return _remediate(entry, files, dest, force=force, yes=yes)
    if dest.exists():
        if content.manifest_hash(content.read_dir(dest)) == content.manifest_hash(files):
            typer.echo(f"  {entry['name']}: already installed")
            return "unchanged"
        if not force:
            typer.echo(f"  {entry['name']}: local copy differs; rerun with --force to replace it")
            return "held"
    if outcome == "legacy_ok":
        typer.echo(f"  {entry['name']}: bundled files are unverified "
                   "(published before directory hashes)")
    content.write_skill(files, dest)
    typer.echo(f"  {entry['name']}: installed")
    return "installed"
```

Task 5's `_remediate` stub prints the verbatim mismatch message plus "Rerun interactively to review and republish." and returns `"failed"`. Track statuses across hosted and github entries; the summary gains a failed count; exit per the spec rule:

```python
if failed and not installed and not unchanged:
    raise typer.Exit(1)
```

- [ ] **Step 3: Verify pass, full suite, commit** — `feat: install github entries with verification`.

---

### Task 6: Mismatch remediation (CLI)

**Files:**
- Modify: `src/drskill/cli.py` (`_remediate` and helpers)
- Test: `tests/test_cli_install.py`

**Interfaces:**
- Consumes: `lint` machinery (`lint_mod.classify`, `lint_mod.run_lint`, `ledger.filter_findings`, `report.print_findings`), `cli.key_source` seam, `ledger.append_ack` + `ledger.Ack`, `service.api_request`, `service.canonical_manifest`, `content.write_skill`, the fork endpoint from Task 1.
- Produces: the full interactive flow per the spec's "Mismatch remediation flow" section.

- [ ] **Step 1: Failing tests.** Monkeypatch `cli.key_source` (the pattern in `tests/test_cli_review.py`), and extend the fake `api_request` to answer `GET /api/v1/identity`, `POST .../revisions`, and `POST .../fork`. Monkeypatch the lint runner seam (`cli._review_fetched`, below) only where the test targets the publish/fork mechanics rather than the review itself. Tests:

- owner path: identity handle == loadout owner; input declines nothing; review runs (real lint over the fetched files in a temp dir — the fetched fixture is a plain valid skill so findings are empty), publish prompt answered `y`; assert the fake publish call's manifest has the entry's new `directory_hash` and that the skill installed; entry counts as installed.
- owner path, decline publish: nothing published, entry failed.
- owner path with a findings ack: fetched SKILL.md crafted to trigger a real lint finding (copy a fixture from the lint tests — see `tests/test_cli_lint.py` for one that fires deterministically); `key_source` feeds `"a"`; assert the machine ledger `home/.drskill.toml` gained an `[[ack]]` and publish proceeded.
- non-owner path: identity handle differs; confirm `y` to fork; fake fork returns 201 with the fork's owner/slug; assert publish went to the fork's path and install completed.
- non-owner slug collision: first fork response is the 422 `loadout_invalid` envelope; `input` supplies a new slug; second fork call carries it.
- non-interactive (`--yes`): remediation skipped, verbatim message + hint printed, entry failed.

- [ ] **Step 2: Verify failure, implement.** Shape:

```python
def _remediate(entry, files, dest, *, force: bool, yes: bool) -> str:
    typer.echo("The remote skill has been updated since this loadout was "
               "created and the original version was not pinned.")
    if yes or interactive.can_interact() is not None:
        # can_interact() returns a refusal string, or None when interactive
        typer.echo("Rerun interactively to review and republish.")
        return "failed"
    ...
```

Details the implementation must include (write them out, not as comments):

- Ownership: one `GET /api/v1/identity`; compare `["user"]["handle"]` with the owner parsed from the install ref. Cache on first use per run.
- Non-owner: `typer.confirm(f"Fork {owner}/{slug} to your account and review the updated skill?", default=False)`; on yes `POST /api/v1/loadouts/{owner}/{slug}/fork`; on a `loadout_invalid` ServiceError, `typer.prompt("Choose a slug for your fork")` and retry with `{"loadout": {"slug": new_slug}}`; then continue the owner path against the fork's `owner/slug`.
- Review (`_review_fetched(files, home) -> bool`, False when the user quits): write `files` to a `tempfile.TemporaryDirectory` via `content.write_skill`, `target = lint_mod.classify(tmp_skill_dir, "skill")`, config via `_load_effective_config_or_exit(Path(tmp), home, False)`, `world, findings = lint_mod.run_lint(target, config, Path(tmp), home)`, `active, acked = ledger.filter_findings(findings, config)`. Print findings with `report.print_findings(world, active, console)`. For each active non-note finding offer `a ack · s skip · q quit` via `key_source()`; `a` appends `ledger.Ack(check=f.check_id, skills=f.contributor_names, fingerprint=f.fingerprint, date=dt.date.today())` to `home / ".drskill.toml"`; `q` returns False.
- Publish: fetch the current revision manifest (already in hand from install), `copy.deepcopy`, find the entry by selector, set `metadata["directory_hash"] = content.manifest_hash(files)` and update `metadata["ref"]` when the fetched ref differs from the recorded one; `_, runtime_hash = service.canonical_manifest(manifest)`; `POST /api/v1/loadouts/{owner}/{slug}/revisions` with `{"manifest": manifest, "runtime_hash": runtime_hash}`. On success `content.write_skill(files, dest)` (respect the existing-dir/`--force` rule first) and return `"installed"`.
- Health report refresh: replace `manifest["health_report"]["findings"]` entries whose `entry_selector` equals this entry's selector with the review run's findings mapped to the report shape, and recompute the summary counts. When the manifest has no `health_report`, skip this step.

- [ ] **Step 3: Verify pass, full CLI suite, commit** — `feat: review and republish on upstream drift`.

---

### Task 7: Verification and close-out

- [ ] **Step 1: Full suites.** `uv run pytest -q` in drskill and `bin/rails test` in drskill-web: zero failures.
- [ ] **Step 2: Live check.** Start the dev server, mint a temporary token, and against a sandboxed `DRSKILL_HOME`: publish a loadout whose manifest carries a github entry for a real repo (`dbreunig/scaffold-docs-skill`, `skill_path ""`, `directory_hash` computed from a fresh clone or from `gh_source.fetch_tarball` + `extract_skill`), then `drskill loadout install` it and confirm the skill lands and verifies. Then corrupt the recorded `directory_hash` in a second revision and confirm the interactive mismatch flow triggers. Revoke the token and clean the sandbox afterward.
- [ ] **Step 3: Commit anything outstanding; report.**

## Coverage check against the spec

Publish metadata → Tasks 2–3. Fetch, locate, extract, verify → Task 4. Install behavior, exit codes, legacy caveat, unfetchable reporting → Task 5. Remediation (message, ownership, review with checks and acks, republish, fork with collision reprompt, non-interactive skip) → Task 6. Server fork endpoint + OpenAPI → Task 1. Testing section → each task's tests plus Task 7's live check. Risks section requires no code.
