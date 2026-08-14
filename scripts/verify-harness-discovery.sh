#!/usr/bin/env bash
# Empirical verification of harness skill-discovery facts, for maintaining
# src/drskill/data/harnesses.toml (method borrowed from
# prime-radiant-inc/everyharness's container install checks).
#
# For each harness CLI found on PATH that has an offline skill-enumeration
# verb, this script builds a throwaway fixture tree -- one uniquely-named
# skill per candidate discovery directory, skills nested two levels below
# each root, and same-name collision pairs across scopes and directories --
# then runs the enumeration under a SANDBOXED $HOME inside the fixture tree
# and prints the raw output. A skill's presence proves its directory is
# read; a nested skill's presence proves recursion; which collision copy
# survives (or whether both stay visible, as on codex) proves precedence.
#
# The script never touches the real $HOME and never writes outside its
# mktemp workspace. It only PRINTS evidence: interpreting it and editing
# harnesses.toml (with a dated provenance comment naming the CLI version)
# stays a human step. CLIs it knows how to probe: copilot (`copilot skill
# list`), opencode (`opencode debug skill`), hermes (`hermes skills list`;
# NOTE hermes's list omits plugin-registered skills, so a path ABSENT there
# is not proven unread -- only presence is evidence).
#
# First run recorded 2026-08-13 (Copilot CLI 1.0.80, opencode-ai 1.18.18,
# Hermes Agent v0.20.0, macOS); findings live in harnesses.toml comments
# and docs/superpowers/specs/2026-08-13-harness-empirical-verification.md.
set -u

WORKROOT="$(mktemp -d "${TMPDIR:-/tmp}/drskill-harness-probe.XXXXXX")"
trap 'rm -rf "$WORKROOT"' EXIT
PROJ="$WORKROOT/proj"
SANDBOX_HOME="$WORKROOT/home"
mkdir -p "$PROJ" "$SANDBOX_HOME"

# mkskill <dir> <name> <description> -- descriptions must stay colon-free:
# a bare `key: value` colon inside an unquoted YAML scalar breaks strict
# frontmatter parsers (copilot rejects the whole skill).
mkskill() {
  mkdir -p "$1/$2"
  printf -- '---\nname: %s\ndescription: %s\n---\n\n%s body.\n' "$2" "$3" "$2" > "$1/$2/SKILL.md"
}

section() { printf '\n===== %s =====\n' "$1"; }

# --- fixture tree: unique per-directory markers ---------------------------
mkskill "$PROJ/.github/skills"   probe-gh-proj      "marker - project .github/skills"
mkskill "$PROJ/.claude/skills"   probe-claude-proj  "marker - project .claude/skills"
mkskill "$PROJ/.agents/skills"   probe-agents-proj  "marker - project .agents/skills"
mkskill "$PROJ/skills"           probe-bare-proj    "marker - project ./skills"
mkskill "$PROJ/.copilot/skills"  probe-copilot-proj "marker - project .copilot/skills"
mkskill "$PROJ/.opencode/skills" probe-oc-proj      "marker - project .opencode/skills"
mkskill "$PROJ/.opencode/skill"  probe-oc-singular  "marker - project .opencode/skill (singular)"
mkskill "$PROJ/.hermes/skills"   probe-hermes-proj  "marker - project .hermes/skills"

mkskill "$SANDBOX_HOME/.copilot/skills"         probe-copilot-global     "marker - global copilot skills"
mkskill "$SANDBOX_HOME/.agents/skills"          probe-agents-global      "marker - global agents skills"
mkskill "$SANDBOX_HOME/.claude/skills"          probe-claude-global      "marker - global claude skills"
mkskill "$SANDBOX_HOME/.config/opencode/skills" probe-oc-global          "marker - global opencode skills"
mkskill "$SANDBOX_HOME/.config/opencode/skill"  probe-oc-global-singular "marker - global opencode skill (singular)"
mkskill "$SANDBOX_HOME/.hermes/skills"          probe-hermes-global      "marker - global hermes skills"

# --- recursion probes: two levels below a root ----------------------------
mkskill "$PROJ/.github/skills/nest1/nest2"                probe-gh-nested            "marker - nested project github"
mkskill "$PROJ/.agents/skills/nest1/nest2"                probe-agents-nested        "marker - nested project agents"
mkskill "$PROJ/.opencode/skills/nest1/nest2"              probe-oc-nested            "marker - nested project opencode"
mkskill "$SANDBOX_HOME/.copilot/skills/nest1/nest2"       probe-copilot-global-nested "marker - nested global copilot"
mkskill "$SANDBOX_HOME/.hermes/skills/nest1/nest2"        probe-hermes-global-nested "marker - nested global hermes"

# --- collision probes: same name, distinguishable descriptions ------------
# cross-scope
mkskill "$PROJ/.github/skills"                  collide-scope-gh  "PROJECT copy"
mkskill "$SANDBOX_HOME/.copilot/skills"         collide-scope-gh  "GLOBAL copy"
mkskill "$PROJ/.agents/skills"                  collide-scope-ag  "PROJECT copy"
mkskill "$SANDBOX_HOME/.agents/skills"          collide-scope-ag  "GLOBAL copy"
mkskill "$PROJ/.opencode/skills"                collide-scope-oc  "PROJECT copy"
mkskill "$SANDBOX_HOME/.config/opencode/skills" collide-scope-oc  "GLOBAL copy"
# intra-project, pairwise
mkskill "$PROJ/.github/skills"   collide-proj-gh-ag "GITHUB copy"
mkskill "$PROJ/.agents/skills"   collide-proj-gh-ag "AGENTS copy"
mkskill "$PROJ/.github/skills"   collide-proj-gh-cl "GITHUB copy"
mkskill "$PROJ/.claude/skills"   collide-proj-gh-cl "CLAUDE copy"
mkskill "$PROJ/.agents/skills"   collide-proj-ag-cl "AGENTS copy"
mkskill "$PROJ/.claude/skills"   collide-proj-ag-cl "CLAUDE copy"
mkskill "$PROJ/.opencode/skills" collide-proj-oc-ag "OPENCODE copy"
mkskill "$PROJ/.agents/skills"   collide-proj-oc-ag "AGENTS copy"
# intra-global, pairwise
mkskill "$SANDBOX_HOME/.copilot/skills"         collide-glob-cp-ag "COPILOT copy"
mkskill "$SANDBOX_HOME/.agents/skills"          collide-glob-cp-ag "AGENTS copy"
mkskill "$SANDBOX_HOME/.agents/skills"          collide-glob-ag-cl "AGENTS copy"
mkskill "$SANDBOX_HOME/.claude/skills"          collide-glob-ag-cl "CLAUDE copy"
mkskill "$SANDBOX_HOME/.config/opencode/skills" collide-glob-oc-ag "OPENCODE copy"
mkskill "$SANDBOX_HOME/.agents/skills"          collide-glob-oc-ag "AGENTS copy"

# --- probes ---------------------------------------------------------------
cd "$PROJ"

section "copilot skill list (cwd=fixture project)"
if command -v copilot >/dev/null 2>&1; then
  copilot --version 2>&1 | head -1
  HOME="$SANDBOX_HOME" copilot skill list 2>&1
else
  echo "skip: copilot not on PATH"
fi

section "opencode debug skill (cwd=fixture project; name -> location -> description)"
if command -v opencode >/dev/null 2>&1; then
  echo "opencode $(opencode --version 2>&1 | head -1)"
  HOME="$SANDBOX_HOME" opencode debug skill 2>&1 |
    jq -r '.[] | select(.location != "<built-in>") |
           .name + "  ->  " + .location + "  [" + .description + "]"' 2>/dev/null |
    sort
else
  echo "skip: opencode not on PATH"
fi

section "hermes skills list (cwd=fixture project; presence-only evidence)"
if command -v hermes >/dev/null 2>&1; then
  HOME="$SANDBOX_HOME" hermes --version 2>&1 | head -1
  HOME="$SANDBOX_HOME" hermes skills list 2>&1
else
  echo "skip: hermes not on PATH"
fi

section "done"
echo "Interpret: presence = directory read; nested presence = recursive;"
echo "surviving collision copy = precedence. Run the script SEVERAL times"
echo "before trusting a collision winner: a winner that flips between runs"
echo "means the harness dedupes nondeterministically (opencode 1.18.18 does"
echo "-- see its harnesses.toml comment), and precedence_verified must stay"
echo "false for it. Update harnesses.toml comments with the CLI versions"
echo "printed above and today's date."
