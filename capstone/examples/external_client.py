"""A deliberately independent MCP client for our policy-wording server. It imports NOTHING from this repo,
only the `mcp` package, so it behaves like another squad's agent would.

    python capstone/examples/external_client.py --repo D:/INSURANCE_RAG/INSURANCE-RAG-BACKEND [--python path/to/python]

It (1) lists the server's tools, (2) asks for the flood exclusion for a motor loss dated 2024-08-11 and for
2025-03-01, (3) tries a malformed date, (4) prints exactly what came back so the reviewer can check:
  - which endorsement version was returned for each date
  - that the error for the bad date says what to fix
  - that the trace id you passed is echoed back
  - that nothing in the payload is claimant data
"""
import argparse
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main(repo: str, python: str) -> None:
    params = StdioServerParameters(command=python, args=["-m", "capstone.mcp_server"], cwd=repo,
                                   env={"PYTHONPATH": repo, "SYSTEMROOT": "C:/Windows", "PATH": ""})
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("TOOLS:", [t.name for t in tools.tools])
            for d in ("2024-08-11", "2025-03-01"):
                res = await s.call_tool("search_policy_wording", {"query": "flood exclusion", "product": "motor",
                                                                   "loss_date": d, "trace_id": "reviewer-trace-001"})
                body = json.loads(res.content[0].text)
                print(f"\nloss_date={d} trace_id echoed={body.get('trace_id')}")
                for c in body["citations"]:
                    print("  ", c["citation"])
            bad = await s.call_tool("search_policy_wording", {"query": "flood", "product": "motor", "loss_date": "11 August 2024"})
            print("\nMALFORMED DATE ->", bad.isError, bad.content[0].text[:200])
            lim = await s.call_tool("get_limits", {"product": "motor", "peril": "flood", "loss_date": "2024-08-11",
                                                    "policy_id": "POL-MOT-2024-000001"})
            print("\nLIMITS:", json.loads(lim.content[0].text))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    asyncio.run(main(a.repo, a.python))
