"""The MCP host/agent. THIS FILE IS THE "agent module" referred to in
week9_mcp/deliverables/agent_diff.txt — it is never edited between the
server-one-only run and the server-one-plus-two run. Everything about
which servers exist comes from the JSON file passed as --config.

Architecture (Where the AI runs):
  - This process is the HOST. It owns the one LLM call site
    (`_call_model` below) and the tool-execution loop.
  - Each configured MCP server is a separate subprocess (a CLIENT
    connection per server, stdio transport). Servers never call an LLM —
    they only implement tools and answer tools/call. See
    week9_mcp/README.md #4 for the one-line statement this satisfies.
  - Discovery is real: tool schemas handed to the model come from each
    server's tools/list response, not from anything hard-coded here.

Usage:
    python -m week9_mcp.host.agent --config <path> --mode discover
    python -m week9_mcp.host.agent --config <path> --mode query --query "..."
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

# Windows consoles default to cp1252, which cannot print characters models
# routinely emit (curly quotes, en-dashes). Reconfigure rather than crash
# mid-loop on a print() of otherwise-successful output.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from groq import Groq
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import settings  # noqa: E402
from week9_mcp.host.config_loader import load_servers, ServerConfig  # noqa: E402

MAX_ITERATIONS = 8

SYSTEM_PROMPT = (
    "You are a claims-triage assistant for an insurance company. You have "
    "access to whatever tools are listed below — you were not told in "
    "advance what they are or which servers they come from; use their "
    "names and descriptions to decide when each applies. Answer the "
    "user's question using the tools as needed, then give a concise final "
    "answer in plain English. If a tool call fails, read the error "
    "message — it will usually tell you what to fix — and try again "
    "before giving up."
)


class MCPHost:
    """Connects to every server in the config, aggregates their tools into
    one flat registry, and dispatches tool calls back to the right server.
    """

    def __init__(self):
        self._stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}          # server name -> session
        self.tool_owner: dict[str, str] = {}                  # tool name -> server name
        self.tools: list[dict] = []                            # raw mcp Tool info, for reporting
        self.tool_schemas: list[dict] = []                     # OpenAI/Groq function-calling schemas

    async def connect(self, servers: list[ServerConfig]) -> None:
        for server in servers:
            params = StdioServerParameters(command=server.command, args=server.args)
            read, write = await self._stack.enter_async_context(stdio_client(params))
            session = await self._stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            self.sessions[server.name] = session

            listed = await session.list_tools()
            for tool in listed.tools:
                if tool.name in self.tool_owner:
                    raise RuntimeError(
                        f"tool name collision: '{tool.name}' offered by both "
                        f"'{self.tool_owner[tool.name]}' and '{server.name}'"
                    )
                self.tool_owner[tool.name] = server.name
                self.tools.append({
                    "server": server.name,
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.inputSchema,
                })
                self.tool_schemas.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description or "",
                        "parameters": tool.inputSchema,
                    },
                })

    async def call_tool(self, name: str, arguments: dict) -> dict:
        owner = self.tool_owner.get(name)
        if owner is None:
            return {"error": f"Unknown tool: {name!r}"}
        session = self.sessions[owner]
        result = await session.call_tool(name, arguments)
        text_parts = [block.text for block in result.content if getattr(block, "type", None) == "text"]
        payload = "\n".join(text_parts)
        if result.isError:
            return {"error": payload}
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"result": payload}

    async def aclose(self) -> None:
        await self._stack.aclose()


def _call_model(client: Groq, messages: list[dict], tool_schemas: list[dict]):
    """The one and only LLM call site in this whole module. Servers never
    reach this — they only ever see tools/call requests and return data."""
    return client.chat.completions.create(
        model=settings.MODEL_NAME,
        temperature=0,
        messages=messages,
        tools=tool_schemas,
        tool_choice="auto",
    )


async def discover(config_path: str) -> dict:
    host = MCPHost()
    try:
        await host.connect(load_servers(config_path))
        return {
            "config": config_path,
            "tool_count": len(host.tools),
            "tools": [{"server": t["server"], "name": t["name"]} for t in host.tools],
        }
    finally:
        await host.aclose()


async def run_query(config_path: str, user_query: str, verbose: bool = True) -> dict:
    host = MCPHost()
    trajectory: list[dict] = []
    log: list[str] = []

    def log_line(line: str) -> None:
        log.append(line)
        if verbose:
            print(line)

    try:
        await host.connect(load_servers(config_path))
        log_line(f"[host] connected servers={list(host.sessions)}")
        log_line(f"[host] discovered {len(host.tools)} tools: "
                  f"{[t['name'] for t in host.tools]}")

        client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_query},
        ]

        final_text = None
        for _ in range(MAX_ITERATIONS):
            response = _call_model(client, messages, host.tool_schemas)
            msg = response.choices[0].message

            if msg.tool_calls:
                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {"id": tc.id, "type": "function",
                         "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                        for tc in msg.tool_calls
                    ],
                })
                for tc in msg.tool_calls:
                    args = json.loads(tc.function.arguments or "{}")
                    result = await host.call_tool(tc.function.name, args)
                    log_line(f"[host] tool_call server={host.tool_owner.get(tc.function.name)} "
                              f"tool={tc.function.name} args={args} -> {result}")
                    trajectory.append({
                        "order": len(trajectory) + 1,
                        "server": host.tool_owner.get(tc.function.name),
                        "tool": tc.function.name,
                        "arguments": args,
                        "result": result,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    })
                continue

            final_text = (msg.content or "").strip()
            log_line(f"[host] final_answer={final_text}")
            break

        return {
            "config": config_path,
            "query": user_query,
            "tool_count_discovered": len(host.tools),
            "tools_discovered": [t["name"] for t in host.tools],
            "trajectory": trajectory,
            "tool_sequence": [step["tool"] for step in trajectory],
            "final_answer": final_text,
            "log": log,
        }
    finally:
        await host.aclose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--mode", choices=["discover", "query"], default="query")
    parser.add_argument("--query", default="")
    parser.add_argument("--out", default=None, help="optional path to write JSON result")
    args = parser.parse_args()

    if args.mode == "discover":
        result = asyncio.run(discover(args.config))
    else:
        if not args.query:
            parser.error("--query is required in --mode query")
        result = asyncio.run(run_query(args.config, args.query))

    print(json.dumps(result, indent=2, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
