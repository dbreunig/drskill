from __future__ import annotations

import hashlib
from collections.abc import Callable

from drskill.ledger import Config
from drskill.models import Contributor, Finding
from drskill.resolution import World

CheckFn = Callable[[World, Config], list[Finding]]
REGISTRY: dict[str, CheckFn] = {}


def check(check_id: str):
    def deco(fn: CheckFn) -> CheckFn:
        REGISTRY[check_id] = fn
        return fn

    return deco


def fingerprint(
    check_id: str,
    contributors: list[Contributor],
    extra: str = "",
    texts: list[str] | None = None,
) -> str:
    """Fingerprint over the material the check judged. By default that is
    each contributor's whole normalized content; a check that only judged a
    slice (e.g. descriptions) passes `texts` so acks survive unrelated edits."""
    if texts is None:
        parts = sorted(c.content_hash for c in contributors)
    else:
        parts = sorted(hashlib.sha256(t.encode()).hexdigest() for t in texts)
    payload = "|".join([check_id, *parts, extra])
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def make_finding(
    check_id: str,
    severity: str,
    contributors: list[Contributor],
    message: str,
    *,
    harnesses: list[str] | None = None,
    fix_commands: list[str] | None = None,
    extra_key: str = "",
    fingerprint_texts: list[str] | None = None,
) -> Finding:
    if harnesses is None:
        harnesses = sorted({d.harness for c in contributors for d in c.deployments})
    return Finding(
        check_id=check_id,
        severity=severity,
        contributors=[c.id for c in contributors],
        contributor_names=sorted({c.name for c in contributors}),
        harnesses=harnesses,
        message=message,
        fix_commands=fix_commands or [],
        fingerprint=fingerprint(check_id, contributors, extra_key, fingerprint_texts),
    )


def run_all(world: World, config: Config) -> list[Finding]:
    # Import registers every check module exactly once.
    from drskill.checks import budget, duplicates, filesystem, heuristics, lockfile, shadowing, spec  # noqa: F401

    findings: list[Finding] = []
    for fn in REGISTRY.values():
        findings.extend(fn(world, config))
    merged: dict[str, Finding] = {}
    for f in findings:
        if f.fingerprint in merged:
            prior = merged[f.fingerprint]
            merged[f.fingerprint] = prior.model_copy(
                update={"harnesses": sorted(set(prior.harnesses) | set(f.harnesses))}
            )
        else:
            merged[f.fingerprint] = f
    return sorted(
        merged.values(), key=lambda f: (f.severity, f.check_id, f.message)
    )
