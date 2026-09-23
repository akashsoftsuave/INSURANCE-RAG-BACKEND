# Tool count — before -> after

Numbers below are copy-pasted from `tools/list` responses (see
`discovery_before.json` / `discovery_after.json`), not from notes or code
inspection — the host's `discover()` prints exactly what each server's
`tools/list` returned and nothing else.

**1 -> 3**

| Stage | Config | Tool count | Tool names (server) |
|---|---|---|---|
| Before | `configs/server1_only.json` | **1** | `search_policy_documents` (policy-search) |
| After | `configs/server1_and_server2.json` | **3** | `search_policy_documents` (policy-search), `get_claim_status` (claims-system), `get_adjuster_notes` (claims-system) |

The two new names, `get_claim_status` and `get_adjuster_notes`, appeared
purely because `week9_mcp/configs/server1_and_server2.json` added a second
server block — `week9_mcp/host/agent.py` was not touched (see
`agent_diff.txt`). Reproduce with:

```
venv/Scripts/python.exe -m week9_mcp.host.agent --config week9_mcp/configs/server1_only.json --mode discover
venv/Scripts/python.exe -m week9_mcp.host.agent --config week9_mcp/configs/server1_and_server2.json --mode discover
```
