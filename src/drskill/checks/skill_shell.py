"""Invocation-time shell commands embedded in skill files.

Claude Code runs `` !`command` `` placeholders and ```! fenced blocks in a
skill file before the model sees the content (dynamic context injection).
This module extracts those commands and checks them: an approval baseline
with a rug-pull diff, and an immediate scan against the dangerous-content
lexicons in checks/injection.py."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import shlex
from collections import Counter
from pathlib import Path

from drskill.checks import check, fingerprint, injection, make_finding
from drskill.ledger import Config
from drskill.models import Contributor, Finding, ShellBaseline
from drskill.resolution import World

# The harness only recognizes `!` at line start or immediately after
# whitespace; KEY=!`cmd` stays literal text and never runs.
_INLINE = re.compile(r"(?:^|(?<=\s))!`([^`\n]+)`")


def extract_commands(text: str) -> list[tuple[int, str]]:
    """Ordered (1-based line number, command) pairs for every embedded
    shell command: inline !`cmd` and lines inside ```! fenced blocks."""
    out: list[tuple[int, str]] = []
    in_fence = False
    for i, line in enumerate(injection._split_lines(text), start=1):
        stripped = line.strip()
        if in_fence:
            if stripped == "```":
                in_fence = False
            elif stripped:
                out.append((i, stripped))
        elif stripped == "```!":
            in_fence = True
        else:
            out.extend((i, m.group(1)) for m in _INLINE.finditer(line))
    return out


def _skillmd(c: Contributor) -> injection.Source | None:
    """The SKILL.md source from the shared scan-view cache; None for MCP
    tools and unreadable skills."""
    return next((s for s in injection.scan_view(c) if s.kind == "skillmd"), None)


def shell_dir(project_root: Path, home: Path, global_mode: bool) -> Path:
    base = home if global_mode else project_root
    return base / ".drskill" / "cache" / "skill-shell"


def _norm_path(p: Path, project_root: Path, home: Path) -> str:
    """Portable form of a skill path: project checkouts and home dirs move
    between machines, so the key must not bake in either prefix."""
    try:
        return "./" + p.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        pass
    try:
        return "~/" + p.relative_to(home.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def baseline_key(c: Contributor, project_root: Path, home: Path) -> str:
    """Identity hash for the baseline file: survives command changes (it is
    content-free) and machine moves (the path is normalized)."""
    ident = c.name + "\n" + _norm_path(Path(c.id), project_root, home)
    return hashlib.sha256(ident.encode()).hexdigest()


def load_baselines(bdir: Path) -> dict[str, ShellBaseline]:
    out: dict[str, ShellBaseline] = {}
    if not bdir.is_dir():
        return out
    for p in sorted(bdir.glob("*.json")):
        try:
            out[p.stem] = ShellBaseline.model_validate_json(p.read_text())
        except (OSError, ValueError):
            continue  # corrupt entries are prune's job
    return out


_CMD_MAX = 100


def _cmd_line(cmd: str) -> str:
    rendered = injection._printable(cmd)
    if len(rendered) > _CMD_MAX:
        rendered = rendered[: _CMD_MAX - 1].rstrip() + "…"
    return rendered


def unreviewed_fingerprint(c: Contributor, cmds: list[tuple[int, str]]) -> str:
    """Fingerprint of the approval surface: the command multiset only, so
    an ack survives prose edits and reformatting. Public because the ack
    path verifies it before saving a baseline."""
    return fingerprint(
        "injection-shell-unreviewed", [c], c.name, sorted(cmd for _ln, cmd in cmds)
    )


def _diff_lines(approved: list[str], current: list[str]) -> str:
    old, new = Counter(approved), Counter(current)
    lines = [f"\n        - {_cmd_line(cmd)}" for cmd in sorted((old - new).elements())]
    lines += [f"\n        + {_cmd_line(cmd)}" for cmd in sorted((new - old).elements())]
    return "".join(lines)


@check("injection-shell-unreviewed")
def shell_unreviewed(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        src = _skillmd(c)
        if src is None:
            continue
        cmds = extract_commands(src.text)
        if not cmds:
            continue
        fp = unreviewed_fingerprint(c, cmds)
        prior = [
            a for a in config.ack
            if a.check == "injection-shell-unreviewed" and c.name in a.skills
        ]
        changed = bool(prior) and fp not in {a.fingerprint for a in prior}
        n = len(cmds)
        # The approval surface: every command, no cap. You cannot approve
        # what the report does not show.
        listing = "".join(
            f"\n        {src.relpath}:{ln}: {_cmd_line(cmd)}" for ln, cmd in cmds
        )
        if changed:
            when = next((str(a.date) for a in prior if a.date), "earlier")
            head = (
                f"'{injection._printable(c.name)}' CHANGED its invocation-time "
                f"shell commands since you approved them ({when}). A skill that "
                f"swaps a command after you trusted it is worth a look. Re-ack "
                f"once you have reviewed the current set:"
            )
            severity = "warning"
            approved = world.shell_approved.get(c.id)
            if approved is not None:
                diff = _diff_lines(approved.commands, [cmd for _ln, cmd in cmds])
                listing = diff or listing
        else:
            head = (
                f"'{injection._printable(c.name)}' runs {n} shell "
                f"command{'s' if n != 1 else ''} at invocation, before the model "
                f"sees the content (Claude Code dynamic context injection). "
                f"drskill has not recorded this set yet. Acking saves it as your "
                f"approved baseline, so drskill can flag it if a command later "
                f"changes:"
            )
            severity = "note"
        out.append(make_finding(
            "injection-shell-unreviewed", severity, [c], head + listing,
            fix_commands=[
                f"drskill ack injection-shell-unreviewed {shlex.quote(c.name)}"
            ],
            extra_key=c.name,
            fingerprint_texts=sorted(cmd for _ln, cmd in cmds),
        ))
    return out


def save_approved(world, f, project_root: Path, home: Path, global_mode: bool) -> None:
    """Acking the note approves the exact command set the finding showed;
    keep a copy so a later rug-pull warning can name what changed."""
    for cid in f.contributors:
        c = world.contributors.get(cid)
        if c is None:
            continue
        src = _skillmd(c)
        if src is None:
            continue
        cmds = extract_commands(src.text)
        if unreviewed_fingerprint(c, cmds) != f.fingerprint:
            continue  # the file changed since the scan; never approve unseen text
        bdir = shell_dir(project_root, home, global_mode)
        bdir.mkdir(parents=True, exist_ok=True)
        baseline = ShellBaseline(
            name=c.name,
            path=_norm_path(Path(c.id), project_root, home),
            commands=[cmd for _ln, cmd in cmds],
            date=dt.date.today().isoformat(),
        )
        (bdir / f"{baseline_key(c, project_root, home)}.json").write_text(
            baseline.model_dump_json(indent=2) + "\n"
        )


# Categories over extracted command text, reusing the injection lexicons.
# pipe-to-shell wins over egress for the same command: it is the stronger
# claim, and one command should not produce two findings.
_SUMMARIES = {
    "credential-read": "embeds invocation-time shell commands that reference credential paths",
    "pipe-to-shell": "embeds an invocation-time shell command that pipes remote content to a shell",
    "egress": "embeds invocation-time shell commands that talk to the network",
    "encoded-blob": "embeds invocation-time shell commands containing long encoded blobs",
}


@check("injection-shell-dangerous")
def shell_dangerous(world: World, config: Config) -> list[Finding]:
    out = []
    for c in world.contributors.values():
        src = _skillmd(c)
        if src is None:
            continue
        cmds = extract_commands(src.text)
        if not cmds:
            continue
        store_hits: list[injection.Hit] = []
        env_hits: list[injection.Hit] = []
        cats: dict[str, list[injection.Hit]] = {
            "pipe-to-shell": [], "egress": [], "encoded-blob": []
        }
        for ln, cmd in cmds:
            hit = (src, ln, cmd)
            if any(p.search(cmd) for p in injection._CRED_STORE):
                store_hits.append(hit)
            elif injection._ENV_FILE.search(cmd):
                env_hits.append(hit)
            if injection._pipe_to_shell(cmd):
                cats["pipe-to-shell"].append(hit)
            else:
                cleaned = injection._LOCAL_URL.sub("", cmd)
                all_urls_local = (
                    injection._URLISH.search(cmd)
                    and not injection._URLISH.search(cleaned)
                )
                if (
                    any(p.search(cleaned) for p in injection._EGRESS)
                    and not all_urls_local
                ):
                    cats["egress"].append(hit)
            stripped = injection._URL.sub("", cmd)
            if injection._B64_RUN.search(stripped) or injection._HEX_RUN.search(stripped):
                cats["encoded-blob"].append(hit)

        def emit(category: str, hits: list[injection.Hit], severity: str,
                 fixes: list[str]) -> None:
            out.append(make_finding(
                "injection-shell-dangerous", severity, [c],
                injection.evidence_message(c, _SUMMARIES[category], hits),
                fix_commands=fixes,
                extra_key=f"{c.name}|{category}",
                fingerprint_texts=sorted({cmd for _s, _ln, cmd in hits}),
            ))

        if store_hits or env_hits:
            severity = "error" if store_hits else "warning"
            fixes = (
                injection.removal_commands(c)
                if severity == "error"
                else ["Check what the command does with the values it reads"]
            )
            emit("credential-read", store_hits + env_hits, severity, fixes)
        if cats["pipe-to-shell"]:
            emit("pipe-to-shell", cats["pipe-to-shell"], "error",
                 injection.removal_commands(c))
        if cats["egress"]:
            emit("egress", cats["egress"], "warning", [
                "Check each command's destination; its output is inlined"
                " into the prompt at invocation"
            ])
        if cats["encoded-blob"]:
            emit("encoded-blob", cats["encoded-blob"], "warning", [
                "Decode the blob yourself before trusting the skill, or remove it"
            ])
    return out
