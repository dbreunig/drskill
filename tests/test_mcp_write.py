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
                               fmt="codex-toml")


def test_write_server_rejects_a_corrupt_config(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text("not json")
    with pytest.raises(mcp_write.WriteUnsupportedError):
        mcp_write.write_server(path, "Notion", {"command": "x"})
