"""Loads the list of MCP servers the host should connect to from a JSON
config file. This is the ONLY thing that differs between the
"server-one-only" and "server-one-plus-two" runs — see
week9_mcp/deliverables/agent_diff.txt.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    name: str
    command: str
    args: list[str]


def load_servers(config_path: str | Path) -> list[ServerConfig]:
    data = json.loads(Path(config_path).read_text(encoding="utf-8"))
    return [ServerConfig(name=s["name"], command=s["command"], args=s.get("args", []))
            for s in data["servers"]]
