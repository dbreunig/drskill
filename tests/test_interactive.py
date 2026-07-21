import io

from drskill import interactive


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


def test_can_interact_requires_ttys():
    assert interactive.can_interact(FakeTTY(), FakeTTY(), {}) is None
    assert interactive.can_interact(io.StringIO(), FakeTTY(), {}) is not None
    assert interactive.can_interact(FakeTTY(), io.StringIO(), {}) is not None


def test_can_interact_honors_env():
    assert interactive.can_interact(FakeTTY(), FakeTTY(), {"CI": "true"}) is not None
    assert interactive.can_interact(
        FakeTTY(), FakeTTY(), {"DRSKILL_NO_INTERACTIVE": "1"}
    ) is not None


def test_read_key_restores_termios():
    import os
    import pty
    import termios
    import threading

    master, slave = pty.openpty()
    # the keypress arrives after raw mode is entered, as in real use; a byte
    # queued under canonical mode beforehand is not readable on macOS ptys
    threading.Timer(0.05, os.write, args=(master, b"a")).start()
    with os.fdopen(slave, "r") as stream:
        before = termios.tcgetattr(stream.fileno())
        assert interactive.read_key(stream) == "a"
        after = termios.tcgetattr(stream.fileno())
    os.close(master)
    # macOS: cfmakeraw sets NOKERNINFO and the pty driver keeps it; echo and
    # canonical mode are what matter for the user's terminal
    NOKERNINFO = 0x20000000
    before[3] &= ~NOKERNINFO
    after[3] &= ~NOKERNINFO
    assert before == after
