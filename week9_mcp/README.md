# Week 9 — MCP (Task Set D): bolting the claims-system server on by config

This directory is the whole Task Set D submission: two MCP servers, one
host/agent that discovers its tools at runtime instead of hard-coding
them, and the evidence files the rubric asks for. Everything below is
self-contained under `week9_mcp/` — nothing in `app/` (the Week 4-8 RAG
app) was changed to build this.

## 1. What was built, and why it's shaped this way

The brief's premise is: "your agent already discovers tools from your own
policy-document search server — prove that discovery was real by adding
server two with nothing but config." This repo's existing Week 7/8 agent
(`app/claims_agent/agent.py`) does **not** do MCP discovery — it imports
Python functions directly from `app/claims_agent/tools.py` and hands
their hand-written JSON schemas to the model. That's a fine design for
what Week 7/8 was testing (agent vs. workflow tool-choice), but it is the
opposite of what Week 9 needs to demonstrate: tools discovered at
runtime, from a server, over a protocol — not tools a Python module
happens to already know about.

So this submission is new, additive work, not a retrofit of the old
agent:

| Piece | File | Role |
|---|---|---|
| Server 1 (ours) | `servers/policy_search_server.py` | Wraps the existing `RetrievalService` (the same retrieval code the chat endpoint already uses) as one MCP tool, `search_policy_documents`. |
| Server 2 (third-party) | `servers/claims_system_server.py` | Stands in for "the claims platform team's" server. Two tools: `get_claim_status`, `get_adjuster_notes`. Its own tiny in-memory dataset — never imports from `app/`, because a real third-party server is never something we can see inside. |
| Host/agent | `host/agent.py` | The one piece that must stay unchanged when server two is added. Connects to every server named in `--config`, calls `tools/list` on each, hands the union to Groq as function-calling tools, dispatches `tools/call` back to whichever server owns the name the model picked. |
| Configs | `configs/*.json` | The only thing that changes between "server one" and "server one + two." |

Everything under `deliverables/` is real output captured by actually
running the code above — not hand-written to match the rubric. Re-running
the commands in §3 regenerates them.

## 2. The concepts, tied to what's actually in this repo

**What MCP is.** A protocol (JSON-RPC 2.0 messages, a fixed lifecycle:
initialize -> initialized -> normal requests) that lets a model-calling
application discover and invoke tools it wasn't built knowing about. The
payoff is exactly requirement 1/2 below: server two showed up in the tool
list and got called, and the host's source code is untouched.

**Host, client, server.**
- **Host** = `host/agent.py`, the process holding the Groq API key and
  the one place `_call_model()` is called. It owns the conversation.
- **Client** = one `mcp.ClientSession` per server, created inside
  `MCPHost.connect()`. The host runs one client per server it talks to.
- **Server** = `policy_search_server.py` and `claims_system_server.py`,
  each a separate OS subprocess. A server never sees the conversation,
  the API key, or the other server — only `tools/list` and `tools/call`.

**Where the AI runs.** In the host, and only in the host. See
`host/agent.py`'s module docstring and `_call_model()` — that function is
the single call site for the LLM in this entire submission. Both servers
are plain Python with no model client, no API key, and no prompt in them
(verified in `wire.json`'s `model_call_location` note, captured from a
real server process, not asserted from memory).

**Tools, resources, prompts.** This submission only uses **tools** — both
`search_policy_documents` and the claims-system pair are actions the
model chooses to invoke, which is what "tool" means in MCP. The brief's
common-mistakes list warns against the opposite error: putting something
that's really context (e.g., a policy exclusions schedule the app should
just attach) behind a tool call instead. Neither server here does that —
`search_policy_documents` is genuinely a search action with a query the
model composes, not a document the app should have handed over as
context; the claims-system tools each take a caller-supplied claim number
and return one record, again a genuine lookup rather than static context.
Neither server declares resources or prompts, because nothing in this
task needed app-attached (rather than model-invoked) content.

**Transports (stdio, HTTP).** Both servers run over **stdio**
(`mcp.run(transport="stdio")`): the host launches each as a child process
and talks JSON-RPC over its stdin/stdout, one message per line (see
`mcp/server/stdio.py`'s `stdin_reader`, and `scripts/capture_wire.py`,
which speaks that framing by hand). stdio fits this task because both
servers are local, single-client, launched-by-the-host processes; it's
the same transport `claude_desktop_config.json`-style tools normally use.
Neither server here is exposed over HTTP — that would be the shape if the
claims-system server were a real network service running centrally
rather than a subprocess we spawn, at which point auth (next point) stops
being optional.

**JSON-RPC handshake.** Captured for real (not fabricated) in
`deliverables/wire.json`: `initialize` request/response, the
`notifications/initialized` notification, `tools/list`, and one
`tools/call`, every top-level field hand-annotated in place. Produced by
`scripts/capture_wire.py` (raw stdio, no SDK client in the loop) then
`scripts/annotate_wire.py` (adds the `annotations` field without touching
the captured bytes).

**Tool discovery.** `MCPHost.connect()` builds `self.tool_schemas` purely
from each server's `tools/list` response — nothing in `host/agent.py`
names `search_policy_documents`, `get_claim_status`, or
`get_adjuster_notes` anywhere. `deliverables/tool_count_before_after.md`
is the 1 -> 3 proof, read straight from two real `tools/list` calls.

**Building a server (fastmcp).** Both servers use `mcp.server.fastmcp
.FastMCP` — `@mcp.tool()` on a type-hinted, docstring'd Python function is
the entire server definition; FastMCP derives the JSON Schema
(`inputSchema`) from the type hints and the `name`/`description` sent
over `tools/list` from the function's name and docstring, which is
visible directly in `wire.json`'s captured `tools/list` response.

**Recoverable errors.** `deliverables/error_before_after.md` — rewrote
`search_policy_documents`'s docstring and its errors from "what's wrong"
to "why it's wrong and what to call instead" and measured the effect on
the model's own tool-call trajectory: 4 calls (3 of them blind guesses at
the input string) before, 1 call after, for the literal same failing
input, same model, same other tools available. `policy_search_server_
before.py` is kept only so that comparison can be regenerated; it is not
part of any live config.

**Remote MCP & auth.** Not exercised by the graded requirements (both
servers here are local subprocesses with no network exposure, so there's
no token to steal in this submission — see `risk_note.md`'s "ship or
don't" line, which ships this specific shape). If the claims-system
server were the platform team's actual remote server instead of a local
stand-in, the transport would become Streamable HTTP and the host would
need to hold a bearer/OAuth token scoped to that server, which is exactly
the "scope a token so the adjuster-note tool is denied" bonus challenge —
this submission covers the required six sections, not that bonus.

## 3. How to reproduce every deliverable

All commands run from the repo root with the project venv:

```
# Requirement 1 & bonus discovery proof — real tool call, tool name in the trace
venv/Scripts/python.exe -m week9_mcp.host.agent \
  --config week9_mcp/configs/server1_and_server2.json --mode query \
  --query "What is the current status of claim CLM-2024-88120, and who is the claimant?"

# Requirement 3 — tool counts before/after (discover-only, no LLM call)
venv/Scripts/python.exe -m week9_mcp.host.agent --config week9_mcp/configs/server1_only.json --mode discover
venv/Scripts/python.exe -m week9_mcp.host.agent --config week9_mcp/configs/server1_and_server2.json --mode discover

# Requirement 4 — raw JSON-RPC wire capture + hand annotation
venv/Scripts/python.exe week9_mcp/scripts/capture_wire.py
venv/Scripts/python.exe week9_mcp/scripts/annotate_wire.py

# Requirement 5 — before/after recoverable-error transcript (same failing call)
venv/Scripts/python.exe -m week9_mcp.host.agent \
  --config week9_mcp/configs/policy_before_and_claims.json --mode query \
  --query "Call the search_policy_documents tool with query set to exactly 'CLM-2024-88120', then tell me what happened and what you'll do next."
venv/Scripts/python.exe -m week9_mcp.host.agent \
  --config week9_mcp/configs/server1_and_server2.json --mode query \
  --query "Call the search_policy_documents tool with query set to exactly 'CLM-2024-88120', then tell me what happened and what you'll do next."
```

Server smoke tests (each just sits waiting for stdio input — that's
correct, they're meant to be launched by the host, not run interactively):
```
venv/Scripts/python.exe week9_mcp/servers/policy_search_server.py
venv/Scripts/python.exe week9_mcp/servers/claims_system_server.py
```

## 4. Deliverables map (submission checklist)

| Checklist item | File |
|---|---|
| `agent_diff.txt` — 0 changed lines in the agent module | `deliverables/agent_diff.txt` |
| config diff adding the second server | `deliverables/config_diff.txt` |
| `wire.json` — raw initialize/tools-list/tools-call, annotated | `deliverables/wire.json` |
| Tool count line: N before -> M after, with names | `deliverables/tool_count_before_after.md` |
| `error_before_after.md` | `deliverables/error_before_after.md` |
| `risk_note.md` — exactly 5 lines | `deliverables/risk_note.md` |

Supporting raw traces (not required by the checklist, kept for
verification — each is a full JSON result including the `log` array,
produced by the commands in §3): `deliverables/discovery_before.json`,
`discovery_after.json`, `trace_query_server1_and_2.json`,
`error_demo_before.json`, `error_demo_after.json`.

## 5. What's deliberately NOT here

- The Week 7/8 agent (`app/claims_agent/`) is untouched — this is
  additive, not a migration of that agent onto MCP.
- The bonus (single gateway process fanning out to both servers, one
  audit line per call, a scoped-down token denying `get_adjuster_notes`)
  is not implemented. §2's "Remote MCP & auth" note above sketches what
  it would take; building it wasn't asked for.
- No HTTP/remote transport, since both servers here are local
  subprocesses the host itself launches.

## Run comments
venv\Scripts\python.exe -m week9_mcp.host.agent --config week9_mcp\configs\server1_only.json --mode discover
venv\Scripts\python.exe -m week9_mcp.host.agent --config week9_mcp\configs\server1_and_server2.json --mode discover
venv\Scripts\python.exe -m week9_mcp.host.agent --config week9_mcp\configs\server1_and_server2.json