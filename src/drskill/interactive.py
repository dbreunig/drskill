"""TTY guard and raw keypress input for the review command. scan never
prompts; review refuses to start without a real terminal."""

from __future__ import annotations

import os
import sys

REFUSAL = (
    "review is interactive; no TTY detected. Use `drskill scan` for the "
    "report or `drskill ack <id>` to record decisions."
)


def can_interact(stdin=None, stdout=None, env=None) -> str | None:
    """None when interactive input is allowed, else the refusal message."""
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    env = os.environ if env is None else env
    if env.get("CI") or env.get("DRSKILL_NO_INTERACTIVE"):
        return REFUSAL
    if not (hasattr(stdin, "isatty") and stdin.isatty()):
        return REFUSAL
    if not (hasattr(stdout, "isatty") and stdout.isatty()):
        return REFUSAL
    if sys.platform == "win32":  # raw termios input is posix-only for now
        return REFUSAL
    return None


def read_key(stream=None) -> str:
    """One raw keypress, terminal settings restored even on error.

    Reads through os.read to bypass Python's stream buffering, which can
    block past the first available byte on a raw terminal."""
    import termios
    import tty

    stream = sys.stdin if stream is None else stream
    fd = stream.fileno()
    old = termios.tcgetattr(fd)
    try:
        # TCSANOW, not setraw's default TCSAFLUSH: flushing would discard
        # a keypress typed just before the switch to raw mode.
        tty.setraw(fd, termios.TCSANOW)
        return os.read(fd, 1).decode(errors="replace")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
