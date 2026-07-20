# Granular harness verification

Date: 2026-07-20
Status: approved
Parent documents: `docs/superpowers/specs/2026-07-19-drskill-v0.1-design.md` (harness definitions), `docs/superpowers/specs/2026-07-20-tier2-heuristics-design.md`

## Why

Real-machine use showed "(best effort)" stamped on nearly every finding, and the label never said what was uncertain. The single `verified` flag conflates two facts with different blast radii: which directories a harness reads (paths), and which copy wins a name collision (precedence). Most findings depend only on paths, which are verified for every harness a typical machine has installed. The blanket label punished solid findings for an uncertainty they did not depend on.

## What changes

### Two flags instead of one

`HarnessDef.verified` splits into `paths_verified` and `precedence_verified`, both defaulting to false. `verified = true` in the old data maps to both true. The data file migrates in place; there is no legacy key.

### `search_order = "none"`

The `search_order` literal gains `"none"`: the harness does not shadow on name collisions, every copy stays visible. Discovery enumerates paths in project-first order for determinism; resolution skips shadow marking for these harnesses. Codex behaves this way (confirmed from source in the v0.1 research) and becomes fully verified once the schema can express it. A same-name collision inside a "none" harness is itself a routing hazard; flagging it is a recorded follow-up, not part of this change.

### Findings carry facet-scoped uncertainty

Checks are classed by what they depend on. `name-shadow` and `double-load` depend on precedence; every other check depends only on paths. A finding marks a harness as uncertain only when that harness lacks the facet the check depends on. The old per-finding "(best effort)" tag is removed.

### Marker rendering

Uncertain harnesses get a `?` suffix on their name in the finding's harness line. When any `?` was printed, one legend line follows the summary: "? = drskill has not verified this harness's skill-loading rules". `list` table titles say "(paths unverified)" when paths are unverified, or "(collision rules unverified)" when only precedence is.

### Verification status updates

- codex: paths and precedence verified (source-confirmed; "none" now expressible).
- cursor: paths verified from official docs (already cited in the data file); precedence stays unverified (closed source, docs silent).
- copilot: paths verified only if the data file's citations actually support the directories; otherwise unchanged. The implementer judges from the recorded evidence.
- cline: researched from its public source this cycle; flags set to what the evidence supports, with citations recorded like the other verified harnesses.
- The vendored table stays both-false.

## Testing

- `search_order = "none"`: same-name different-content copies in one "none" harness produce no `shadowed_by` marks and no `name-shadow` finding; same-content copies still produce `double-load`.
- Facet marking: a paths-verified, precedence-unverified harness shows no `?` on a diverged-copies finding and shows `?` on a name-shadow finding.
- Legend renders once when any `?` appears, never otherwise.
- Migration: every entry in the shipped data file has both new flags; no entry has the old key; the three previously verified harnesses are both-true.
- Real-machine check: the current findings on this machine render with no markers.
