# drskill loadout commands — design

Status: Approved (design approved in discussion 2026-08-31)
Date: 2026-08-31
Scope: CLI-only (this repo). A `drskill loadout` command group consuming the
drskill-web Phase 3 API through the existing `service.py` client: list,
create, show, revisions, publish (with client-side runtime-hash
verification), and fetch (byte-stable canonical manifests by revision number
or runtime hash). The server side is already deployed on drskill-web main.

## Goal

A signed-in CLI user can manage loadouts end-to-end against a dev server:
create one, publish a manifest file as an immutable revision whose hash the
CLI computes independently (making every publish an integrity test of the
cross-implementation canonicalization contract), and fetch any revision
byte-identically.

## Out of scope

- `drskill try` / `drskill use` (install/launch — harness territory).
- Manifest *generation* from local scan/resolution state — publish takes a
  JSON file. Its own design later.
- Stars, forks, recommendations, visibility changes, archiving (web-only).
- Pagination flags (the API returns full lists at current scale).

## Decisions

1. **Sub-app**: `loadout_app = typer.Typer(...)`, registered via
   `app.add_typer(loadout_app, name="loadout")` — first sub-app in the CLI;
   plan.md's advertised `drskill loadout show owner/slug` becomes real.
2. **Client-side canonicalization is spike-proven byte-identical to Rails.**
   `canonical_manifest(manifest)` in `service.py`: drop any top-level
   `runtime_hash`, recursively sort object keys (arrays keep order), emit
   compact UTF-8 JSON via
   `json.dumps(sorted_deep, ensure_ascii=False, separators=(",", ":"))`,
   hash = `"sha256:" + sha256(canonical.encode()).hexdigest()`. Verified
   against the actual Rails `Loadouts::CanonicalizeRevision` on the basic
   fixture: identical 1090 bytes, identical
   `sha256:f6d5415881682c9cc3a911eb849b9a583d68f036a71635e8afb08be35658f6cc`.
   That input/output/hash triple ships as fixtures pinning the contract.
3. **Publish always sends the computed hash** (server rejects on mismatch —
   a free end-to-end integrity check); `--no-verify` omits it for debugging.
   On a 422 the CLI prints the server's validation messages, and for hash
   mismatches also prints the client hash so drift is visible.
4. **`ServiceError` gains `details`** (the envelope's `details` map, default
   None) so validation messages reach the user. Backward compatible.
5. **Credentials helper**: `_service_credentials()` in `cli.py` (load or
   exit 1 with the `drskill login` hint); used by all loadout commands, and
   `whoami`/`logout` are refactored onto it (removing their duplicated
   inline checks). All requests pass
   `base_url=creds.get("service_url") or service.service_url()`.
6. **Output**: rich `Table` (repo convention, see `report.py`/`cli.py` MCP
   table) for `list` and `revisions`; key-value lines for `show`; `--json`
   on list/show/revisions dumps the raw API response; `fetch` prints the raw
   canonical document (or `-o FILE` writes it); `publish` prints
   `Published revision N (sha256:...)`.
7. **`fetch` argument shapes**: `fetch owner/slug NUMBER`,
   `fetch owner/slug sha256:...` (both via the nested revision endpoint),
   and `fetch sha256:...` (global `/api/v1/revision_hashes` lookup). A
   target containing no `/` must be a `sha256:` hash; a ref without a
   revision argument is an error.

## Commands and API mapping

| Command | Endpoint | Notes |
|---|---|---|
| `loadout list [--json]` | GET /api/v1/loadouts | table: slug, name, visibility, current rev number+short hash |
| `loadout create SLUG --name NAME [--description TEXT]` | POST /api/v1/loadouts | prints created ref `handle/slug`; 422 → validation messages, exit 1 |
| `loadout show OWNER/SLUG [--json]` | GET /api/v1/loadouts/:owner/:slug | metadata, current revision, forked_from when present |
| `loadout revisions OWNER/SLUG [--json]` | GET .../revisions | table: number, runtime_hash, published_at, reproducible |
| `loadout publish OWNER/SLUG MANIFEST.json [--no-verify]` | POST .../revisions | body `{manifest, runtime_hash?}`; unreadable/invalid JSON file → exit 1 before any request |
| `loadout fetch TARGET [REVISION] [-o FILE]` | GET .../revisions/:id or /api/v1/revision_hashes/:hash | raw canonical JSON |

Errors: `ServiceError` → `typer.echo` of the message + exit 1 (matching the
existing commands' conventions); `not_found`
stays the server's uniform envelope (no client-side guessing about
private-vs-missing).

## service.py additions

- `canonical_manifest(manifest: dict) -> tuple[str, str]` — pure, exactly
  the spike-proven algorithm (Decision 2).
- `ServiceError(code, message, details=None)`; `api_request` passes the
  envelope's `details` through when present.

No other client changes; commands call `api_request` directly.

## Testing

- **Cross-implementation contract (the load-bearing test):**
  `tests/fixtures/manifests/basic.json` (copied verbatim from drskill-web's
  fixture) plus `basic.canonical.json` (the Rails canonicalizer's actual
  output). Asserts `canonical_manifest` returns byte-identical JSON and the
  exact hash constant above. Also: key-order independence (shuffled input →
  same bytes), `runtime_hash` key dropped, unicode preserved un-escaped.
- **Fake service** (extends the login tests' pattern): implements the six
  loadout/revision routes with the Rails contract — including recomputing
  the canonical hash of a posted manifest server-side and rejecting
  mismatches with the `revision_invalid` envelope — and stores published
  revisions so fetch returns the stored canonical document.
- **CLI tests** (CliRunner): each command's happy path and error UX
  (not-signed-in hint; 422 details printed; bad ref shapes; missing
  manifest file; `--json` output parses; `-o` writes the file). Publish →
  fetch round trip against the fake asserts fetched bytes re-hash to the
  published hash.
- `whoami`/`logout` refactor covered by their existing tests (must stay
  green unchanged).

## Risks

- **Float/number formatting divergence** between Ruby and Python JSON could
  break hash equality for manifests containing floats. Current manifests
  are strings/ints/bools; the server recomputes authoritatively and rejects
  mismatches loudly, and `--no-verify` is the escape hatch. Accepted; noted
  for the future manifest-generation design.
- **Fake-service drift**: the fake uses `canonical_manifest` itself, so the
  publish-hash check in tests is self-consistent rather than independent —
  the independence comes from the Rails-generated fixture pair, which is why
  that test is load-bearing.
