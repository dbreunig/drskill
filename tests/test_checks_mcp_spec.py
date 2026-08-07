import json
from pathlib import Path

from drskill.checks import run_checks
from drskill.ledger import Config
from drskill.lint import MCP_SPEC_CHECKS, build_lint_world, classify

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"


def run(tmp_path: Path, servers: dict, schema: str | None = SCHEMA,
        make_files: list[str] | None = None):
    data: dict = {"mcpServers": servers}
    if schema:
        data["$schema"] = schema
    f = tmp_path / "mcp.json"
    f.write_text(json.dumps(data))
    for rel in make_files or []:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("#!/bin/sh\n")
    w = build_lint_world(classify(f))
    return run_checks(w, Config(), MCP_SPEC_CHECKS)


def ids(findings):
    return sorted(f.check_id for f in findings)


def test_clean_stdio_and_http(tmp_path):
    got = run(tmp_path, {
        "local": {"type": "stdio", "command": "./bin/run",
                  "args": ["--data", "${PLUGIN_DATA}"], "cwd": "${PLUGIN_ROOT}/srv"},
        "remote": {"type": "streamable-http", "url": "https://api.example.com/mcp"},
    }, make_files=["bin/run"])
    assert got == []


def test_missing_schema_and_bad_transport(tmp_path):
    (tmp_path / "plugin.json").write_text(json.dumps({
        "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
        "name": "fixture-plugin",
    }))
    got = run(tmp_path, {"s": {"type": "websocket", "url": "wss://x"}}, schema=None)
    msgs = " ".join(f.message for f in got)
    assert all(f.check_id == "mcp-spec-invalid" for f in got)
    assert "$schema" in msgs and "websocket" in msgs


def test_stdio_command_rules(tmp_path):
    got = run(tmp_path, {
        "multi": {"type": "stdio", "command": "python -m srv"},
        "abs": {"type": "stdio", "command": "/usr/bin/srv"},
        "missing-rel": {"type": "stdio", "command": "./bin/gone"},
    })
    assert len(got) == 3 and all(f.severity == "error" for f in got)


def test_url_rules(tmp_path):
    got = run(tmp_path, {
        "userinfo": {"type": "sse", "url": "https://u:p@example.com/mcp"},
        "fragment": {"type": "streamable-http", "url": "https://example.com/mcp#frag"},
        "plain-http": {"type": "streamable-http", "url": "http://example.com/mcp"},
        "loopback-ok": {"type": "streamable-http", "url": "http://127.0.0.1:8000/mcp"},
    })
    assert len(got) == 3


def test_placeholder_rules(tmp_path):
    got = run(tmp_path, {
        "in-command": {"type": "stdio", "command": "${PLUGIN_ROOT}/bin/run"},
        "env-key": {"type": "stdio", "command": "srv",
                    "env": {"PLUGIN_ROOT": "/x", "${PLUGIN_DATA}": "y"}},
        "bad-cwd": {"type": "stdio", "command": "srv", "cwd": "/absolute"},
    })
    assert "mcp-spec-placeholder" in ids(got)
    assert sum(1 for f in got if f.check_id == "mcp-spec-placeholder") >= 3


def test_header_placeholder_warns(tmp_path):
    got = run(tmp_path, {
        "h": {"type": "streamable-http", "url": "https://example.com/mcp",
              "headers": {"X-Root": "${PLUGIN_ROOT}"}},
    })
    assert [f.severity for f in got] == ["warning"]


def test_standalone_notes_provisional_root(tmp_path):
    got = run(tmp_path, {"missing-rel": {"type": "stdio", "command": "./gone"}})
    assert "assuming" in got[0].message


def test_fingerprints_are_path_independent(tmp_path):
    # Same mcp.json content written under two different directories must
    # fingerprint identically, or a committed ack goes red on CI (a fresh
    # checkout lands at a different absolute path).
    servers = {
        "s": {"type": "websocket", "url": "wss://x"},
        "bad-cmd": {"type": "stdio", "command": "python -m srv"},
    }
    dir_a = tmp_path / "checkout-a" / "nested"
    dir_b = tmp_path / "somewhere-else" / "deeper" / "still"
    dir_a.mkdir(parents=True)
    dir_b.mkdir(parents=True)
    got_a = run(dir_a, servers)
    got_b = run(dir_b, servers)
    assert got_a and got_b
    assert {f.fingerprint for f in got_a} == {f.fingerprint for f in got_b}
    # Sanity: the absolute paths genuinely differ, so this isn't vacuous.
    assert str(dir_a) != str(dir_b)


def test_fingerprints_unique_per_violation_of_same_entry(tmp_path):
    # Two distinct violations on the very same entry (a bad env key that is
    # both reserved AND a placeholder key would collide) must still produce
    # distinct fingerprints. Use an entry with both a reserved env name and
    # a cwd-form violation, which share the same entry-content hash input.
    got = run(tmp_path, {
        "multi": {"type": "stdio", "command": "srv", "cwd": "/absolute",
                  "env": {"PLUGIN_ROOT": "/x"}},
    })
    assert len(got) == 2
    assert len({f.fingerprint for f in got}) == 2


def test_args_env_headers_type_validation(tmp_path):
    got = run(tmp_path, {
        "bad-args": {"type": "stdio", "command": "srv", "args": "not-a-list"},
        "bad-env": {"type": "stdio", "command": "srv", "env": {"HOME": 5}},
        "bad-headers": {"type": "streamable-http",
                         "url": "https://example.com/mcp", "headers": {"X": 5}},
    })
    assert all(f.check_id == "mcp-spec-invalid" for f in got)
    assert len(got) == 3
    msgs = " ".join(f.message for f in got)
    assert "'args'" in msgs and "'env'" in msgs and "'headers'" in msgs
