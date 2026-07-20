from __future__ import annotations

from drskill.checks import check, make_finding
from drskill.ledger import Config
from drskill.models import Finding
from drskill.resolution import World


@check("broken-symlink")
def broken_symlink(world: World, config: Config) -> list[Finding]:
    return [
        make_finding(
            "broken-symlink", "error", [],
            f"broken symlink: {b.path} points at nothing",
            harnesses=[b.harness],
            extra_key=str(b.path),
            fix_commands=[f"rm '{b.path}'"],
        )
        for b in world.broken_symlinks
    ]
