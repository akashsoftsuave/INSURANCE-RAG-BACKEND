"""Adds a hand-written "annotations" field (top-level JSON-RPC field ->
plain-English meaning) to each entry in wire.json, without touching the
captured raw/parsed bytes. Run once, after capture_wire.py.
"""
import json
from pathlib import Path

WIRE = Path(__file__).resolve().parents[1] / "deliverables" / "wire.json"

ANNOTATIONS = [
    {  # 0: initialize request
        "jsonrpc": "Protocol version tag for JSON-RPC itself (2.0) — fixed, not MCP-specific.",
        "id": "Client-chosen correlation id (1). The matching response echoes it back so the client can pair request<->response on a single shared stdio pipe.",
        "method": "'initialize' — the first call of every MCP session. Nothing else may be sent before this completes.",
        "params.protocolVersion": "The MCP spec date-version the client speaks. Server checks this against its own supported versions.",
        "params.capabilities": "Empty here — client is declaring it offers none of the optional client-side features (sampling, roots, elicitation).",
        "params.clientInfo": "Free-text identification of the calling application, for the server's logs — not used for auth.",
    },
    {  # 1: initialize response
        "jsonrpc": "Same fixed tag.",
        "id": "Echoes 1 — this is the response to the initialize request above.",
        "result.protocolVersion": "The version the SERVER settled on (must match or be acceptable to the client).",
        "result.capabilities": "What the server actually offers: tools (yes, listChanged=false = it won't notify on tool-list changes), resources/prompts (declared but empty — this server exposes neither).",
        "result.serverInfo": "Server's self-reported name ('claims-system' — matches the FastMCP('claims-system') constructor call) and version (the mcp SDK's own version, 1.30.0, not an app version).",
    },
    {  # 2: initialized notification
        "jsonrpc": "Same fixed tag.",
        "method": "'notifications/initialized' — a NOTIFICATION (no 'id' field), meaning no response is expected or sent. It tells the server the client has finished processing the initialize result and normal requests (tools/list, tools/call, ...) may now begin.",
    },
    {  # 3: tools/list request
        "jsonrpc": "Same fixed tag.",
        "id": "2 — a fresh id for this request, independent of the initialize exchange.",
        "method": "'tools/list' — discovery. This is the ONLY thing that produced the tool schemas the model was given; nothing about these two tools is hard-coded in the host.",
        "params": "Empty object — no pagination cursor supplied, so the server returns its full tool list in one page.",
    },
    {  # 4: tools/list response
        "jsonrpc": "Same fixed tag.",
        "id": "Echoes 2.",
        "result.tools": "Array of Tool objects. Each one's 'name' and 'description' come verbatim from the Python function name and docstring in claims_system_server.py (get_claim_status, get_adjuster_notes) — FastMCP generated this JSON from the decorated functions, nothing here was hand-written as JSON.",
        "result.tools[].inputSchema": "JSON Schema auto-derived from the Python function's type-hinted parameters (claim_number: str -> {'type':'string'}), with 'required' populated from parameters that have no default.",
    },
    {  # 5: tools/call request
        "jsonrpc": "Same fixed tag.",
        "id": "3 — a fresh id for this call.",
        "method": "'tools/call' — an actual invocation, distinct from tools/list (listing a tool never runs it).",
        "params.name": "Which discovered tool to run — must be one of the names tools/list just returned.",
        "params.arguments": "The call's arguments, validated by the server against that tool's inputSchema before the Python function runs.",
    },
    {  # 6: tools/call response
        "jsonrpc": "Same fixed tag.",
        "id": "Echoes 3.",
        "result.content": "MCP wraps every tool's return value as a list of content blocks (here one 'text' block) rather than returning arbitrary JSON directly — this is what lets a tool return images/embedded resources too, not just text. The text itself is this server's get_claim_status() return value, JSON-serialized by FastMCP.",
        "result.isError": "false — a normal, successful result. Had the Python function raised instead, this would be true and 'content' would carry the exception's str() as the recoverable error message (see error_before_after.md for that path in the OTHER server).",
    },
]

MODEL_CALL_NOTE = (
    "Where the model call happens and where it does not, in one line: "
    "every message in this file is client<->server tool plumbing that "
    "never touches an LLM; the ONE place an LLM is called in this whole "
    "system is week9_mcp/host/agent.py's _call_model() (Groq "
    "chat.completions.create), which runs in the HOST process after "
    "tools/list has already returned — this server only ever sees "
    "initialize/tools/list/tools/call and has no model client, API key, "
    "or prompt anywhere in it."
)

data = json.loads(WIRE.read_text(encoding="utf-8"))
for entry, ann in zip(data, ANNOTATIONS):
    entry["annotations"] = ann

WIRE.write_text(json.dumps({"exchange": data, "model_call_location": MODEL_CALL_NOTE}, indent=2), encoding="utf-8")
print(f"annotated {len(data)} messages")
