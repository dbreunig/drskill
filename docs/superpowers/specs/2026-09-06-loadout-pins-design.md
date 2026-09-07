# Loadout Pins — Design

Status: approved for implementation
Delivers the "real lockfile binding" deferred by 2026-09-02-loadout-status-update-design.md and 2026-09-02-external-source-install-design.md

## The problem

`loadout status` and `loadout update` match published entries to local skills by normalized name, with a hash tiebreak and an ambiguity note when several local skills share a name. Both specs that shipped this heuristic marked it temporary: "The lockfile phase replaces this heuristic with a real binding."

## Vocabulary

The words "lockfile", "skills-lock", and "linked" are taken: drskill already reads the third-party `skills-lock.json` (`checks/lockfile.py`, `Provenance.kind = "skills-lock"`). This feature is called **pins**. A pin binds one installed directory to the loadout entry it came from. Pins are not provenance: `Provenance` answers "where did this content come from"; a pin answers "which loadout revision does this installed copy belong to."

## Storage

One JSON document per scope at `.drskill/pins.json` (project root) and `~/.drskill/pins.json` (user scope), following the cache tier's spirit: the project file is meant to be committed so teammates and CI resolve the same bindings. Unlike the per-entity cache files, pins are one document per scope: they are written only by explicit installs, so merge pressure is low, and one readable map beats a directory of hashes.

The document maps an install path, relative to the scope base, to a pin record:

```json
{
  ".agents/skills/vector": {
    "loadout": "drew/pack",
    "revision": 3,
    "selector": "skill:vector",
    "source_type": "drskill",
    "content_hash": "sha256:...",
    "installed_at": "2026-09-06"
  }
}
```

When an install target cannot be made relative to its scope base (the bridge-retarget case), the absolute path is stored; such a pin still binds on this machine and is simply dead weight elsewhere.

## Writers

`drskill loadout install` records a pin for every hosted and github skill entry that ends the run `installed` or `unchanged` ("already installed" is still this loadout's copy — bind it). The pin's scope follows the install scope: project installs write the project file, `--user` installs write the home file. Re-installing overwrites the path's pin; installing a different loadout over the same path rebinds it (last write wins — the directory holds one skill). Each write also prunes pins whose install path no longer exists in that scope.

Out of scope for v1: pins for MCP entries (config-hash matching already binds them well), pins from `drskill skill install` (not loadout-scoped; the deferral was loadout language), and any server involvement.

## Consumers

`loadout_drift.classify_entries` gains optional `pins` and `ref` arguments. For a skill entry, when a pin exists whose `loadout` matches the ref and whose `selector` matches the entry, and a scanned contributor sits at the pinned path, that contributor is the match — compared directly (matches/changed/unreadable), no name search, no ambiguity note. Entries without a usable pin fall back to today's name-and-hash heuristic, so pre-pin installs and hand-copied skills keep working. `loadout status` and `loadout update` load pins from both scopes and pass them with the ref. The module docstring's "until the resolution phase brings a real lockfile binding" sentence comes out.

## Out of scope

- Pinning MCP entries or `skill install` results.
- A `drskill pins` management command; pruning happens on install.
- Using pins in duplicates/shadowing checks.
