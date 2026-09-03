# Skill Registry CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `drskill skill publish/list/log/show/diff/install` against the registry API, with the check-gated publish and the wizard's registry offer.

**Architecture:** A new `skill_app` typer group in cli.py plus a `skill_pub.py` module holding the pure parts: directory collection, frontmatter extraction, the gate's decision rule, and ref parsing (`owner/slug[@N]`). Publish reuses `content.upload`; install reuses `content.download`/`write_skill` and the loadout-install target rules; the gate reuses the lint machinery the way `_review_fetched` does.

**Tech Stack:** drskill CLI only. Server endpoints from the merged registry server work.

**Spec:** `~/Development/drskill-web/docs/superpowers/specs/2026-09-03-skill-registry-design.md` (chunk 1, CLI half)

## Global Constraints

- Publish gate: active error or warning findings block; acked findings and notes do not; interactive a/s/q review may ack; no bypass flag; nothing uploads before the gate passes.
- Slug from frontmatter `name` via `manifest_build.normalize_name`; claimed name/description sent from frontmatter.
- `owner/slug[@N]` ref syntax everywhere; unpinned means current.
- Install verifies the downloaded archive's manifest hash (content.download already does).
- TDD; commits carry the session trailer.

---

### Task 1: skill_pub module (pure helpers)

**Files:** Create `src/drskill/skill_pub.py`; Test `tests/test_skill_pub.py`.

**Interfaces:**
- `collect_dir(path: Path) -> list[dict]` — every regular file under path (skip symlinks and `.git`), content.py file shape, error (ValueError) when no SKILL.md at the root.
- `frontmatter_meta(files) -> tuple[str, str | None]` — (normalized name, description) from SKILL.md via `resolution.split_frontmatter`; falls back to the directory name when the frontmatter lacks a name (caller passes a fallback).
- `parse_skill_ref(ref: str) -> tuple[str, str, int | None]` — ("drew", "citation-style", 3) for `drew/citation-style@3`, None number when unpinned; ValueError on junk.
- `blocking_findings(findings) -> list` — active error/warning findings (the gate's rule; ledger filtering happens at the caller with the effective config).

- [ ] Failing tests: collect_dir shapes and exec bits from a tmp dir, `.git` skipped, missing SKILL.md raises; frontmatter name normalization and absent description; ref parsing incl. `@0`/`@x` rejection; blocking_findings keeps error+warning, drops note. Implement, full suite, commit — `feat: add skill publish helpers`.

---

### Task 2: skill publish with the gate

**Files:** Modify `src/drskill/cli.py` (new `skill_app`); Test `tests/test_cli_skill.py`.

**Interfaces:** `drskill skill publish [path] [-m NOTE]`. Gate: run lint over the directory (classify/run_lint/filter as `_review_fetched` does), block per rule, interactive review may ack (reuse the `_review_fetched`-style loop factored so both share it, or accept small duplication — prefer factoring a `_review_findings(world, active, home) -> bool`).

- [ ] Failing tests (fake api pattern from tests/test_cli_install.py; monkeypatch `content.upload`; real lint over tmp skill dirs):
  - clean skill publishes: upload called, POST /api/v1/skills body carries slug/name/description/note/content_hash, prints `Published drew/x@1`.
  - idempotent response (`existed: true`) prints "already version".
  - a warning-bearing skill blocks non-interactively: exit 1, findings listed, no upload, no POST.
  - interactive ack unblocks: key_source "a" (+ can_interact allowed), publish proceeds, machine ledger gains the ack.
  - an error finding blocks even after interactive review (errors cannot be acked past? drskill acks work for errors too — decision: acks unblock errors as well, consistent with ack semantics; test ack-on-error unblocks).
  - `-m` note lands in the POST body.
- [ ] Implement; the ack loop and blocking re-check run until no blocking findings remain or the user quits. Commit — `feat: add gated skill publish`.

---

### Task 3: read commands (list, log, show, diff)

**Files:** cli.py; tests in `tests/test_cli_skill.py`.

**Interfaces:**
- `skill list` → table of my skills (GET /api/v1/skills).
- `skill log owner/slug` → versions with notes (GET .../versions), `@N  note  date` lines.
- `skill show owner/slug[@N]` → prints SKILL.md (GET files/SKILL.md, pinned or alias); `--files` prints the listing; `--file PATH` prints that file (binary-safe: write raw to stdout buffer).
- `skill diff owner/slug @A @B` → added/removed/changed lines from the diff endpoint (`GET .../versions/A/diff?against=B` — A is the newer, B the base).

- [ ] Failing tests against the fake api (rendering assertions on output; show pinned vs alias path selection; --file fetches the right endpoint; diff line rendering). Implement, commit — `feat: add skill read commands`.

---

### Task 4: skill install

**Files:** cli.py; tests.

**Interfaces:** `skill install owner/slug[@N] [--harness ID] [--project/--user] [--yes] [--force]` — resolves the version's content hash (named show for unpinned, versions log filtered for pinned — or GET named + versions; use `GET .../versions` and pick), then target via `_install_target`, confirm, `content.download` + collision rule + `write_skill`, exactly the loadout-install behavior for one skill.

- [ ] Failing tests: unpinned installs current into the shared store; `@1` installs the old version; identical reinstall is a no-op; differing local copy needs `--force`; decline writes nothing; 404 skill errors cleanly. Implement (factor the per-skill install steps shared with `_install_one_github`'s tail if convenient — small helper `_install_files(files, dest, name, force)`). Commit — `feat: add skill install`.

---

### Task 5: wizard registry offer

**Files:** `src/drskill/loadout_wizard.py`; tests in `tests/test_loadout_wizard.py`.

**Interfaces:** `_offer_hosting` becomes `_offer_registry`: same one batched confirmation, new text ("Publish N skills to your registry and include them?"). On yes, per skill: the publish gate (reuse the Task 2 flow against the collected files — factor `skill_publish_flow(files, name, description, note, creds, base, home) -> dict | None` in cli or skill_pub so wizard and command share it), then the entry gains `source_reference: "owner/slug@N"` (hosted map value becomes `(content_hash, reference)`).
`manifest_build.contributors_to_manifest`'s hosted map value extends to carry the reference: `hosted={id: {"content_hash": ..., "source_reference": ...}}` (update the two existing hosted tests).

- [ ] Failing tests: accepted offer publishes each skill (fake publish flow), entries carry `drew/name@1` references and hashes, declined offer leaves local_only, gate failure for one skill aborts the wizard before any loadout is created. Implement, commit — `feat: publish wizard skills to the registry`.

---

### Task 6: close-out

- [ ] Full suite; live e2e against the dev server with a sandboxed DRSKILL_HOME: gated publish of a clean scratch skill (and a blocked publish of a vague one), log/show/diff, install into a sandbox store, wizard offer end to end if practical (tty caveat: use the non-interactive parts). Clean up. Report; branch menu.

## Coverage check against the spec (CLI half)

Publish + gate → Tasks 1–2. list/log/show/diff → Task 3. Install → Task 4. Wizard offer + source_reference → Task 5. Live verification → Task 6.
