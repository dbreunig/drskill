"""A minimal stdio MCP server for tests: answers initialize and tools/list
over newline-delimited JSON-RPC, then exits. With arg 'hang' it sleeps
forever after initialize, to exercise the timeout."""
import json
import sys
import time


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main():
    hang = len(sys.argv) > 1 and sys.argv[1] == "hang"
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "0"},
            }})
        elif method == "notifications/initialized":
            if hang:
                time.sleep(60)
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": "echo", "description": "Echo text back.",
                 "inputSchema": {"type": "object", "properties": {
                     "text": {"type": "string", "description": "The text to echo."}}}},
                {"name": "ping", "description": "Ping the server.",
                 "inputSchema": {"type": "object"}},
            ]}})
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main()
