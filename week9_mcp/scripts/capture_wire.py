"""Requirement 4 — capture the RAW JSON-RPC exchange against the new
(claims-system) server, with no MCP SDK client in the way, so the bytes
saved are literally what went over stdio.

MCP's stdio transport is newline-delimited JSON-RPC 2.0 (no Content-Length
framing, unlike LSP) — see mcp/server/stdio.py's stdin_reader, which does
`async for line in stdin: types.JSONRPCMessage.model_validate_json(line)`.
This script exploits that directly: it starts the server subprocess and
writes/reads whole JSON lines by hand.

Produces week9_mcp/deliverables/wire.json: a list of {direction, raw,
parsed} entries, one per JSON-RPC message actually sent or received.
Hand annotation of each message's top-level fields lives in
week9_mcp/README.md #4 (kept there rather than inline so it's easy to read
prose next to structured data).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON = str(REPO_ROOT / "venv" / "Scripts" / "python.exe")
SERVER = str(REPO_ROOT / "week9_mcp" / "servers" / "claims_system_server.py")

entries: list[dict] = []


def send(proc: subprocess.Popen, message: dict) -> None:
    raw = json.dumps(message)
    entries.append({"direction": "client -> server", "raw": raw, "parsed": message})
    proc.stdin.write(raw + "\n")
    proc.stdin.flush()


def recv(proc: subprocess.Popen) -> dict:
    line = proc.stdout.readline()
    while line.strip() == "":
        line = proc.stdout.readline()
    parsed = json.loads(line)
    entries.append({"direction": "server -> client", "raw": line.rstrip("\n"), "parsed": parsed})
    return parsed


def main() -> None:
    proc = subprocess.Popen(
        [PYTHON, SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        bufsize=1,
    )
    try:
        # 1. initialize
        send(proc, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "wire-capture-script", "version": "0.1.0"},
            },
        })
        recv(proc)

        # 2. initialized notification (no id -> no response expected)
        send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})

        # 3. tools/list
        send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        recv(proc)

        # 4. tools/call — a real, successful call against the new server
        send(proc, {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_claim_status", "arguments": {"claim_number": "CLM-2024-88120"}},
        })
        recv(proc)
    finally:
        proc.stdin.close()
        proc.terminate()
        stderr = proc.stderr.read()
        proc.wait(timeout=5)

    out_path = REPO_ROOT / "week9_mcp" / "deliverables" / "wire.json"
    out_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    print(f"wrote {len(entries)} messages to {out_path}")
    if stderr.strip():
        print("server stderr (log noise, not part of the wire protocol):", file=sys.stderr)
        print(stderr, file=sys.stderr)


if __name__ == "__main__":
    main()
