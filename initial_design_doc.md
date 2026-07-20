# drskill — Design Document

**Status:** Draft v1 · July 2026
**Name:** `drskill` (binary + PyPI, verified available; register `dr-skill` defensively) · `loadout.fun` (npm shim, domain, and share destination — "loadout" survives as the concept and feature name)
**One-liner:** `brew doctor` for your AI agent's skill loadout — a read-only CLI that analyzes the *set* of Agent Skills a project or machine exposes to its coding agents, finds triggering clashes, context overloads, conflicting instructions, and injection surfaces, and suggests concrete fixes.

---

## 1. Why this exists

Agent Skills (the SKILL.md format, standardized at agentskills.io in December 2025) have been adopted across essentially every coding agent — Claude Code, Cursor, Codex, Copilot, Gemini CLI, and dozens more. The tooling ecosystem that grew around them in the first half of 2026 covers installation (`npx skills`, `gh skill`), enterprise registries (JFrog, iflytek SkillHub), per-skill linting, and — as of May 2026 — basic file hygiene (`skill-doctor` on PyPI). What nobody ships is **set-level analysis**: treating the collection of skills a given agent actually loads as a system with emergent properties.

Those properties are real and documented:

**Skill collision.** Overlapping descriptions cause the routing LLM to misroute. Microsoft hit this in production at just nine skills and coined the term; their key result is that a single automated LLM rewrite of the description, fed with false-positive/false-negative queries, recovers nearly all the routing accuracy that 120 minutes of manual tuning does — but rewrites *cannot* fix skills whose intended scopes genuinely overlap. That irreducible residual needs a human decision, and no tool today surfaces it.

**Catalog noise at scale.** An audit of a 1,234-skill community mega-repo found a 47K-token catalog, 84 skills mentioning "security," 74 mentioning "documentation," 13 groups of 85–100% identical skills, and 58.5% of descriptions that never state when the skill should trigger. No tool shows users token counts, overlap, or duplicate status for their own set.

**Context overload.** Skill names + descriptions load at every session start; MCP tool definitions stack on top. Nothing today reports the number of tokens a session burns before the user types anything.

**Injection surfaces.** Skills are instructions plus optional executable scripts. Documented attack patterns (frontloaded "mandatory" helper scripts, poisoned auxiliary files, description-level instruction smuggling) make skills a supply-chain surface; GitHub's own docs warn that installed skills are unverified. Static analysis can't *prove* a skill safe, but it can flag the known surfaces cheaply.

**Multi-tool sprawl.** Developers run 2–3 agent harnesses concurrently (multiple 2026 surveys converge on this), and installers built for that reality (`npx skills` symlinking one canonical copy into many agent directories; `gh skill` copying into each host directory with provenance frontmatter) can interact badly — including the same skill loading *twice* into one harness via two compat directories.

`drskill` occupies the analysis layer above all of this. It installs nothing, enforces nothing, and competes with nobody's package manager. It reads what the package managers and humans have accumulated and makes the user look at it.

## 2. What we are building

A single CLI, run inside a repo for project-level analysis or with `--global` for machine-level analysis, that:

1. Detects installed harnesses and resolves each one's **effective skill set** — the per-harness catalog after directory precedence, symlink resolution, and deduplication. This is the core unit of analysis; there is no such thing as "your skills," only "what Claude Code sees" and "what Cursor sees."
2. Runs a tiered battery of checks over those sets (see §5) and prints a `brew doctor`-style report in which every finding ends in a copy-pasteable fix command or an `ack` command.
3. Records human decisions in a committed **ledger** so acknowledged findings stay silent until the underlying content changes, and caches LLM verdicts in a committed **cache** so deep analysis is paid for once per content-state per team, not per run per person.
4. Exposes the same battery as a CI gate (`--ci`: nonzero exit on errors or unacknowledged warnings).
5. Extends later to MCP server/tool definitions — the same routing-by-description, same context budget, same injection concerns, different config format (see §9).

### What we are explicitly not building

No installer — `npx skills` covers ~67 agents, GitLab, local paths, and Claude plugin manifests; `gh skill` covers the GitHub-native path; suggested fixes shell out to them (`npx skills remove <name>`, `gh skill update <name>`). No materialization or enforcement — the earlier design where the tool owned a canonical store and synced harness directories was dropped once inspection of `vercel-labs/skills` showed it already *is* that materializer (canonical copies in `.agents/skills/`, symlinks per agent, a committed `skills-lock.json`, an npm-ci-style `sync`). No registry, marketplace, or hosting. No per-agent enable/disable in v1 — for harnesses that read `.agents/skills/` directly, the canonical copy is simultaneously store and live deployment, so selective disablement requires inverting the ecosystem's layout; deferred until there's demand worth that swamp.

## 3. Prior art and positioning

| Tool | Layer | What it does | What it doesn't |
|---|---|---|---|
| `npx skills` (vercel-labs) | Install | Multi-agent install, canonical store + symlinks, project lockfile (`skills-lock.json`), update/sync | No analysis of any kind; no enable/disable |
| `gh skill` (GitHub CLI) | Install | Install/pin/update/publish; provenance (repo, ref, tree SHA) written into SKILL.md frontmatter | Per-skill only; explicitly does not verify content |
| `skill-doctor` (PyPI) | Hygiene | Duplicates, drift, broken symlinks, junk, stale files across 8 runtimes | No trigger/overlap analysis, no budgets, no security, no ledger, no lockfile awareness, no LLM tier; dormant since mid-May 2026 |
| Per-skill linters (agent-skill-linter etc.) | Lint | Spec compliance, description quality for one skill | Nothing set-level |
| JFrog / SkillHub / Google SGP / GitHub Enterprise Controls | Enterprise governance | Signed registries, RBAC, load policies, allowlists | Platform-scoped, enterprise-sold; nothing for the individual developer's actual working set |
| `skillcheck` (PyPI, v1.4.1) | Quality gate | Cross-agent SKILL.md quality gate against the agentskills.io spec (discovered July 2026; autopsy pending — see §11) | Presumed per-skill; set-level scope unverified |
| **drskill** | **Set analysis** | Per-harness effective sets, collision/overlap/budget/injection checks, decision ledger, CI gate, LLM-assisted adjudication | Installing, hosting, enforcing |

The hygiene checks `skill-doctor` pioneered are table stakes we implement quickly (they fall out of our scanner nearly for free); our identity is everything above them.

## 4. Core concepts

**Effective set.** For each detected harness: enumerate its search paths (project and global), apply its precedence rules, resolve symlinks to realpaths, and deduplicate by resolved target. A skill symlinked into five agent directories is one logical skill with five deployments; a skill *copied* into two directories that one harness reads (e.g. a `gh skill` copy in `.claude/skills/` plus a `skills` symlink in `.agents/skills/`, both read by harnesses that scan compat dirs) is a double-load finding, not a dedup.

**Context contributor.** The generic unit the check engine operates on:

```python
class Contributor(BaseModel):
    id: str                    # stable identity (realpath-derived)
    kind: Literal["skill", "mcp_tool", "mcp_server"]   # mcp_* are future
    source: Provenance         # skills-lock entry | gh-skill frontmatter | unmanaged
    scope: Scope               # project | user | harness-specific dir
    deployments: list[Deployment]   # (harness, path, via_symlink)
    routing_text: str          # description — what the router sees
    body: str                  # full instruction text
    token_cost: TokenCost      # catalog_tokens (approx), body_tokens (approx)
    content_hash: str          # sha256 of normalized content (see §7)
```

Skills are provider #1. MCP tools become provider #2 later without touching check logic — only new config parsers and a handshake client.

**Finding.** `(check_id, severity, contributors, harnesses, message, fix_commands, fingerprint)`. The fingerprint is derived from the normalized content hashes of the involved contributors; it is what the ledger keys on.

**Ledger** (`drskill.toml`, committed, human-scale). Configuration plus decisions:

```toml
[budget]
catalog_tokens_max = 6000      # per-harness startup catalog
body_tokens_warn   = 20000     # per-skill body ceiling

[[ack]]
check = "trigger-clash"
skills = ["docx-report", "documentation-writer"]
fingerprint = "sha256:…"       # normalized-content pair hash
note = "docx is output-format specific; keeping both"
date = 2026-07-19
```

An ack silences its finding until any involved skill's normalized content changes, at which point it resurfaces. Decisions are durable but honest.

**Verdict cache** (`.drskill/cache/`, committed). Machine memos for the LLM tier, keyed by `sha256(pairwise normalized content)`, storing verdict, model, and date. Committing it means one person runs `--deep`, pushes, and CI plus every teammate get the verdicts free. Ledger = what humans decided; cache = what the model said. Different lifetimes, never merged into one file. The two mechanisms are also the cost-control story: deep checks run only on pairs the heuristic tier flagged, minus cache hits, under a `--max-calls N` hard budget. For a stable repo the steady-state LLM spend is zero.

## 5. Check catalog

**Tier 1 — deterministic (always on; errors and warnings).**
Name shadowing across scopes with the winner identified per harness's precedence rules. Cross-directory double-load (same logical skill, two physical instances, one harness — the `gh skill` × `npx skills` interaction). Exact duplicates (normalized content hash) and near-duplicates (MinHash/Jaccard over shingles; the mega-repo's 13 known duplicate groups are the acceptance test). Spec violations: name/folder mismatch, missing or over-length description (>1024 chars), angle brackets in frontmatter (spec-flagged injection vector). Broken symlinks. `skills-lock.json` hash drift, *attributed*: "modified outside `npx skills` — likely `gh skill update` or hand edit," not "corruption." Token accounting: per-skill body tokens, per-harness catalog totals, checked against `[budget]`.

**Tier 2 — heuristic (on by default; warnings; thresholds tunable in ledger).**
Description-overlap clusters via shingled cosine similarity plus shared trigger-phrase extraction, reported as pile-ups ("4 skills all claim 'documentation', none states an exclusive condition"). Missing activation conditions — descriptions with no "use when" semantics. Overly generic descriptions (all verbs, no distinguishing nouns). Opposing imperatives on the same noun phrase across bodies ("always X" / "never X") — cheap, low-recall, honest about it.

**Tier 3 — injection surfaces (on by default; severity-scored).**
Static *flagging*, never verification (the runtime-audit literature is unambiguous that static analysis can't prove a skill benign). Surfaces: instruction-override phrasing; frontloaded mandatory script execution ("you must first run scripts/…" — the documented SkillJect pattern); network egress in bundled scripts; reads of `.env`, key files, cloud credential paths; base64/hex blobs; zero-width and bidi Unicode; mid-task remote-fetch instructions. High-severity findings recommend removal; all are ack-able.

**Tier 4 — LLM-assisted (`--deep`, opt-in, extras-gated).**
Runs only on Tier-2-flagged pairs not in the verdict cache. Two DSPy programs (§6): `ConflictJudge` classifies a pair as *distinct* / *description collision (rewrite fixes it)* / *scope overlap (human must choose)*; `DescriptionRewrite` proposes concrete description diffs for the middle class, following the Microsoft single-rewrite-with-confusion-cases recipe. Proposals are printed as diffs; nothing is ever auto-applied.

**Simulated routing (`explain`).**
`drskill explain "<query>"` scores the query against every description in each harness's effective set and shows the contested top-k — turning abstract collision into something felt in five seconds. A saved query set replayed under `check --ci` becomes a routing-regression test.

## 6. Technical decisions (locked)

**Language: Python, ≥3.11, no hybrid.** The workload is filesystem walking, parsing, hashing, and shingling over at most a few MB of text; a full scan is sub-second and doctor tools aren't perf-bound. 3.11 floor because `tomllib` is stdlib there and Python 3.10 hits EOL in October 2026, right at ship time; nothing we need requires 3.12. The one CPU-flavored dependency, tokenization, already ships as tiktoken's Rust-backed wheel, so the sensible hybrid exists for free. If org-scale scans (10K+ skills) ever profile badly, the escape hatch is a PyO3/maturin extension module for the shingling loop inside the same package — never a second binary shelling between languages. CLI startup is managed by lazy imports; `dspy` must not load unless `--deep` is passed.

**LLM layer: DSPy, in an extras group.** The deep checks are signature-shaped tasks, and `DescriptionRewrite` is literally the published production recipe formalized. We compile the programs with **GEPA** (dspy.GEPA, reflective prompt evolution) against labeled corpora — the mega-repo's known duplicate groups, planted synthetic collisions, a held-out set — and **ship the compiled prompt programs as JSON artifacts in the package**. GEPA over MIPROv2 for two reasons that fit this project: it is markedly more rollout-efficient, which matters when the labeled corpus is small, and it optimizes on *textual* feedback rather than scalar scores — so our eval metrics must return explanations, not just grades (for `ConflictJudge`: which pair was misjudged and what distinguisher was missed; for `DescriptionRewrite`: which confusion queries still misroute). That feedback-rich metric design is a build requirement, not an option. Shipping the compiled artifacts means users get optimized prompts without ever running optimization themselves. Provider-agnostic via LiteLLM; the user brings their own key. DSPy's internal request cache is disabled/ignored — our pair-content-keyed committable cache is the source of truth because its invalidation semantics (content changed → re-judge) are the correct ones for a team artifact. Install: `pip install 'drskill[deep]'`.

**Dependencies (default path, deliberately light):** typer + rich (CLI/report), pydantic (schemas), pyyaml (frontmatter), stdlib `tomllib` + `tomli-w`, tiktoken behind a lazy import with counts labeled approximate, datasketch or ~40 hand-rolled lines for MinHash. Everything LLM lives in `[deep]`.

**Interop rules (from source inspection of `vercel-labs/skills` and `gh skill` docs):**
Read `skills-lock.json` as the install manifest of record; never write to it (their reader wipes unrecognized schema versions). Read `gh skill` provenance from SKILL.md frontmatter as the secondary source; classify everything else "unmanaged." All hashing and fingerprinting operates on **normalized content**: symlinks resolved to realpath, `gh skill` provenance frontmatter keys stripped, so the identical skill installed via different tools hashes identically and a metadata-only update never falsely resurfaces an ack. Vendor the agent→directory mapping table from `vercel-labs/skills` (MIT) as the harness-resolution seed data, and track it.

**Distribution and naming (decided):**
PyPI package `drskill`, console script `drskill` (both verified available July 2026; `dr-skill` and npm `drskill` are also free — register all three, applying our own one-separator-neighbor rule defensively rather than being on the wrong end of it). Primary install: `uv tool install drskill`. npm name `loadout.fun` (owned, matches the domain) ships a thin shim exec-ing `uvx --from drskill drskill` so the npx crowd reaches the same tool, and the domain remains the share destination (§12). Naming history, for the record: `loadout` was the working name but is occupied on PyPI by a dormant unrelated installer; `skillmd`/`contextmd` are taken; `skill-issue` is squatted by a joke package; `skilldoctor` was rejected as one hyphen from the existing `skill-doctor` hygiene tool. Adjacent-name awareness: `skill-doctor` and `skillcheck` on PyPI, `loadout-cli` on npm.

## 7. CLI surface

```
drskill scan [--global] [--deep] [--ci] [--max-calls N] [--json]
drskill ack <check-id> <skill>... [--note "..."]
drskill explain "<query>" [--harness claude-code]
drskill list [--tokens] [--harness <h>] [--global]
drskill init                 # write starter drskill.toml with budgets
drskill cache [prune|stats]
drskill share [preview]      # publish loadout manifest to loadout.fun (v0.3+, §12)
drskill telemetry [on|off|preview]   # opt-in ecosystem stats (v0.6+, §12)
```

Report style: findings grouped by severity, every entry scoped to the harnesses it affects, ending in either fix commands (delegating to `npx skills` / `gh skill` where they own the operation) or the exact `drskill ack` line. Exit codes: 0 clean or everything acknowledged; 1 errors; 2 unacknowledged warnings under `--ci`. `--json` emits the findings model for tooling.

## 8. Validation plan

Free eval corpora exist and are the pre-release gate: the 1,234-skill mega-repo (known pile-ups, 13 ground-truth duplicate groups, 58.5% missing-activation baseline), `anthropics/skills`, `vercel-labs/agent-skills`, plus synthetic planted collisions. Tier-2 thresholds get tuned against these before any release — the difference between a doctor and a noise generator is the false-positive rate on catalogs the community already knows. The same corpora provide DSPy compilation and held-out sets. `explain` query sets double as routing-regression fixtures for our own CI.

## 9. MCP extension (post-v1)

Same problem, different config format: MCP servers inject tool definitions (name + description + schema) into the same context window, routed by the same description matching. Static half: parse `.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`, `~/.claude.json` etc. with the same per-harness resolution; findings include duplicate servers across scopes, secrets in config env blocks, unpinned `npx -y pkg@latest` servers. Handshake half (gated behind `--mcp-connect` because it executes third-party server processes): enumerate tools, measure real token cost, detect cross-server tool-name collisions, and **rug-pull detection** — the ledger stores approved tool-description hashes at first sight and flags any later change, reusing the fingerprint mechanism verbatim on a threat class skills don't have. Headline capability once both providers exist: the unified pre-typing context bill — "this Claude Code session starts at 14.3K tokens: 6.1K skill catalog, 8.2K MCP tool definitions."

## 10. Milestones

**v0.1 — the useful skeleton.** Harness detection + effective-set resolution, Tier-1 checks, token accounting, report with fix commands, `drskill.toml` ledger with acks, `--ci`, `list --tokens`. Ships value with zero LLM calls and zero config.
**v0.2 — the analysis identity.** Tier-2 overlap clusters and activation-condition checks tuned against corpora, Tier-3 injection surfaces, `explain`.
**v0.3 — the deep tier.** `[deep]` extra, DSPy programs compiled and shipped, verdict cache, `--max-calls`, description-rewrite diffs. Plus `drskill share` — cheap, client-side, and the growth loop (§12).
**v0.4 — MCP static.** Config parsers as provider #2 under the contributor interface.
**v0.5 — MCP handshake.** `--mcp-connect`, tool enumeration, unified budget, rug-pull ledger.
**v0.6 — ecosystem telemetry.** Opt-in stats shipped in the same release as the public dataset and the ecosystem report it powers (§12) — the ask and the give arrive together.

## 11. Risks and open questions

**Spec absorption.** The agentskills spec discussion on precedence/`specializes` may ship a structured arbitration field. Response: treat it as an output format, not a threat — the triage flow's "A specializes B" resolution should *write* the spec-native field the moment it exists.
**Model routing improves.** Better routers shrink the collision pain (this is a bet we're aware of). Budgets, injection surfaces, duplicate/drift hygiene, and the MCP rug-pull ledger are orthogonal to routing quality and carry the tool regardless.
**Heuristic noise.** A doctor that cries wolf gets uninstalled. Mitigation is the corpus-tuned thresholds plus the ack ledger; success metric is repeat-run silence on a triaged repo.
**`skill-doctor` wakes up** or `npx skills` grows analysis features. Mitigation is speed on v0.1–v0.2 and the ledger/cache/deep design, which is the hard-to-copy part.
**`skillcheck` (PyPI v1.4.1)** bills itself as a cross-agent SKILL.md quality gate and is more mature than `skill-doctor`; wheel autopsy required before v0.1 positioning is finalized — if it is per-skill linting, no change; if it has set-level checks, sharpen the boundary.
**Per-agent enablement** remains deliberately unsolved (the `.agents/skills` universal-directory inversion problem); revisit only on demand.

## 12. Sharing and ecosystem telemetry

Two features with opposite consent models, kept deliberately separate. The purpose of both is the same: learning and improving the ecosystem — turning drskill's vantage point over real skill sets into public knowledge that skill authors, harness developers, and spec maintainers can act on.

### 12.1 `drskill share` — explicit publishing (v0.3)

Not telemetry: a deliberate act. `drskill share` generates a manifest of the current loadout — public skills with provenance and versions, plus set-level stats (count, token bill, scan status) — and publishes it to `loadout.fun/l/<id>`. Anyone can apply a shared loadout via generated `npx skills add` commands. The rendered "loadout card" (the set, its startup token cost, a clean-scan badge) is designed to be posted; shared loadouts are content that markets the tool, and the feature is what the domain was bought for. Implementation is client-side generation plus static hosting — cheap enough to ship alongside v0.3. `drskill share preview` prints exactly what will be published before anything leaves the machine.

### 12.2 Opt-in ecosystem telemetry (v0.6)

**Why it's worth doing.** The research that motivated this project ranked real-world skill co-occurrence and the size of the irreducible collision residual as the top unanswered empirical questions in the ecosystem — nobody has field data on which skills actually co-install or which pairs actually clash. Aggregated loadout data answers both. It makes drskill the source of record for skill-ecosystem statistics, lets Tier-2 thresholds be tuned on real distributions rather than one community mega-repo, and gives skill authors feedback no registry provides ("trigger-clashes with X in 62% of co-installs; most-confused query themes: …"). Findings feed back into the public dataset, the spec discussions (precedence, activation-condition conventions), and the compiled DSPy programs.

**Trust constraints (non-negotiable).** drskill is a security and audit tool; a doctor that exfiltrates is a contradiction, so the design is the trust surface:

- **Opt-in, never opt-out** — the deliberate contrast to `npx skills` (opt-out `DISABLE_TELEMETRY`). First-run prompt, `drskill telemetry on|off`, honored `DO_NOT_TRACK`, auto-disabled in CI.
- **Go-toolchain model:** local-first counters, a published and versioned payload schema committed to the repo, scheduled (not per-run) uploads, and the aggregated dataset released *publicly* — converting telemetry from liability into community asset.
- **Public-provenance-only rule.** A skill is reported by identity only if its provenance (skills-lock entry or gh-skill frontmatter) resolves to a public repo. Private and unmanaged skills never leave the machine as names, hashes, or descriptions — only as anonymous aggregates ("+4 private skills, 3.1K tokens").
- **Conflict pairs** are reported only when *both* skills are public, and only as `(skill_a, skill_b, check_id, verdict_class)` — never description text, never body excerpts, never rewrite diffs.
- **`drskill telemetry preview`** prints the exact pending payload at any time.

**Dataset framing.** Opt-in data will over-represent public/OSS usage; that is the point, not a flaw — the dataset documents and improves the public skill ecosystem, which is exactly the commons where collisions, duplicates, and description quality are a shared problem. Enterprise-internal catalogs are out of scope for this dataset by design (and by the provenance rule above).

**Sequencing rationale.** Telemetry is the tool's first server dependency and first trust surface, so it ships only after credibility is banked (v0.1–v0.5), and in the same release as the public dataset and first ecosystem report — the ask and the give arrive together.