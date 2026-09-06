import json
from pathlib import Path

import pytest

from drskill import mcp_write

STDIO_METADATA = {
    "server_name": "Notion", "transport": "stdio",
    "command": "npx", "args": ["-y", "notion-mcp"],
    "url": None, "env_names": ["NOTION_TOKEN"], "tools": ["search"],
}

HTTP_METADATA = {
    "server_name": "Linear", "transport": "http",
    "command": None, "args": [], "url": "https://mcp.linear.app/sse",
    "env_names": [], "tools": [],
}


def test_server_block_stdio():
    assert mcp_write.server_block(STDIO_METADATA) == {
        "command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": ""},
    }


def test_server_block_http():
    assert mcp_write.server_block(HTTP_METADATA) == {"url": "https://mcp.linear.app/sse"}


def test_server_block_http_with_env_names():
    metadata = dict(HTTP_METADATA, env_names=["LINEAR_TOKEN"])
    assert mcp_write.server_block(metadata) == {
        "url": "https://mcp.linear.app/sse", "env": {"LINEAR_TOKEN": ""},
    }


def test_write_server_creates_the_file(tmp_path):
    path = tmp_path / ".mcp.json"
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA))
    data = json.loads(path.read_text())
    assert data == {"mcpServers": {"Notion": {
        "command": "npx", "args": ["-y", "notion-mcp"], "env": {"NOTION_TOKEN": ""}}}}


def test_write_server_preserves_other_keys(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "unrelated": 1}))
    mcp_write.write_server(path, "Linear", mcp_write.server_block(HTTP_METADATA))
    data = json.loads(path.read_text())
    assert data["unrelated"] == 1
    assert set(data["mcpServers"]) == {"other", "Linear"}


def test_written_server_round_trips_through_the_parser(tmp_path):
    path = tmp_path / ".mcp.json"
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA))
    servers = mcp_write.read_servers(path, "mcp-json")
    assert len(servers) == 1
    assert servers[0].name == "Notion"
    assert servers[0].env_names == ["NOTION_TOKEN"]


def test_read_servers_missing_file_is_empty(tmp_path):
    assert mcp_write.read_servers(tmp_path / ".mcp.json", "mcp-json") == []


def test_write_server_rejects_other_formats(tmp_path):
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(tmp_path / "config.toml", "x", {"command": "x"},
                               fmt="claude-user-json")


def test_write_server_rejects_a_corrupt_config(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("not json")
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "Notion", {"command": "x"})


def test_validate_metadata_accepts_the_real_shapes():
    assert mcp_write.validate_metadata(STDIO_METADATA) is None
    assert mcp_write.validate_metadata(HTTP_METADATA) is None


def test_validate_metadata_rejects_malformed_input():
    assert mcp_write.validate_metadata("not a dict") == "metadata is not an object"
    assert "transport" in mcp_write.validate_metadata({"transport": "carrier-pigeon"})
    assert "args" in mcp_write.validate_metadata(
        {"transport": "stdio", "command": "npx", "args": "not-a-list"})
    assert "env_names" in mcp_write.validate_metadata(
        {"transport": "stdio", "command": "npx", "args": [], "env_names": [1, 2]})
    assert "command" in mcp_write.validate_metadata(
        {"transport": "stdio", "command": ["npx"], "args": []})


def test_write_server_refuses_an_unparsed_same_name(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"Notion": "just-a-string"}}))
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA))


def test_write_server_replace_overwrites_a_parsed_name(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"Notion": {"command": "old"}}}))
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA),
                           replace=True)
    data = json.loads(path.read_text())
    assert data["mcpServers"]["Notion"]["command"] == "npx"


def test_codex_write_appends_a_stdio_server(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.6-luna"\n')
    mcp_write.write_server(path, "Notion", mcp_write.server_block(STDIO_METADATA),
                           fmt="codex-toml")
    text = path.read_text()
    assert text.startswith('model = "gpt-5.6-luna"\n')
    servers = mcp_write.read_servers(path, "codex-toml")
    assert len(servers) == 1
    assert servers[0].name == "Notion"
    assert servers[0].command == "npx"
    assert servers[0].args == ["-y", "notion-mcp"]
    assert servers[0].env_names == ["NOTION_TOKEN"]


def test_codex_write_creates_the_file(tmp_path):
    path = tmp_path / "config.toml"
    mcp_write.write_server(path, "papers", {"command": "uvx", "args": ["papers-mcp"]},
                           fmt="codex-toml")
    servers = mcp_write.read_servers(path, "codex-toml")
    assert [s.name for s in servers] == ["papers"]


def test_codex_write_quotes_awkward_names_and_values(tmp_path):
    path = tmp_path / "config.toml"
    mcp_write.write_server(path, "my server", {"command": 'echo "hi"', "args": []},
                           fmt="codex-toml")
    servers = mcp_write.read_servers(path, "codex-toml")
    assert servers[0].name == "my server"
    assert servers[0].command == 'echo "hi"'


def test_codex_write_refuses_an_existing_name(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[mcp_servers.Notion]\ncommand = "old"\n')
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "Notion", {"command": "npx", "args": []},
                               fmt="codex-toml", replace=True)


def test_codex_write_refuses_http_blocks(tmp_path):
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(tmp_path / "config.toml", "Linear",
                               mcp_write.server_block(HTTP_METADATA), fmt="codex-toml")


def test_codex_write_refuses_a_corrupt_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("not = valid = toml")
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "x", {"command": "x", "args": []}, fmt="codex-toml")


def test_codex_write_rejects_a_surrogate_and_leaves_the_file_unchanged(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.6-luna"\n')
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "x", {"command": "\ud800", "args": []},
                               fmt="codex-toml")
    assert path.read_text() == 'model = "gpt-5.6-luna"\n'


def test_codex_write_rejects_a_non_table_mcp_servers(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("mcp_servers = 5\n")
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "x", {"command": "x", "args": []}, fmt="codex-toml")


def test_codex_write_rejects_an_inline_table_mcp_servers(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("mcp_servers = {}\n")
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "x", {"command": "x", "args": []}, fmt="codex-toml")


def test_codex_write_collapses_trailing_blank_lines_to_one(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('model = "gpt-5.6-luna"\n\n\n')
    mcp_write.write_server(path, "papers", {"command": "uvx", "args": ["papers-mcp"]},
                           fmt="codex-toml")
    text = path.read_text()
    assert text == 'model = "gpt-5.6-luna"\n\n[mcp_servers.papers]\ncommand = "uvx"\nargs = ["papers-mcp"]\n'


def test_validate_metadata_rejects_stdio_without_a_command():
    assert mcp_write.validate_metadata(
        {"transport": "stdio", "command": None, "args": []}) == "stdio entry has no command"


def test_validate_metadata_rejects_http_without_a_url():
    assert mcp_write.validate_metadata(
        {"transport": "http", "url": None}) == "http entry has no url"
