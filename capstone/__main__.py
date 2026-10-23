"""Single entry point.   python -m capstone <command>

  setup                      build corpus + ingest wording (idempotent; run once)
  triage "<FNOL text>"       triage one FNOL end to end (agent -> MCP -> referral), prints the structured result
        [--claim CLM-...] [--mode in_force|naive]
  eval [--all]               run the whole eval suite on the INTEGRATED system (see capstone/evals/run_all.py)
  serve-mcp                  run our MCP server on stdio (for an external client)
  replay <trace_id>          re-derive the evidence for a logged triage and verify it is byte-identical
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


async def _triage(fnol: str, claim: str | None, mode: str) -> dict:
    from capstone.agent import CapstoneAgent
    agent = CapstoneAgent(wording_mode=mode)
    await agent.connect()
    try:
        return await agent.triage(fnol, claim_id=claim)
    finally:
        await agent.aclose()


def main() -> None:
    ap = argparse.ArgumentParser(prog="capstone")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    t = sub.add_parser("triage")
    t.add_argument("fnol")
    t.add_argument("--claim")
    t.add_argument("--mode", default="in_force", choices=["in_force", "naive"])
    t.add_argument("--full", action="store_true", help="print the full trajectory too")
    e = sub.add_parser("eval")
    e.add_argument("--stage", default="all", choices=["all", "run", "score"])
    e.add_argument("--limit", type=int, default=None)
    sub.add_parser("serve-mcp")
    r = sub.add_parser("replay")
    r.add_argument("trace_id")
    args = ap.parse_args()

    if args.cmd == "setup":
        from capstone import corpus
        from capstone.retrieval import ingest
        print(json.dumps({"corpus": corpus.build_all(), "ingest": ingest()}, indent=2))
    elif args.cmd == "triage":
        out = asyncio.run(_triage(args.fnol, args.claim, args.mode))
        if not args.full:
            out = {k: v for k, v in out.items() if k not in ("trajectory", "spans")}
        print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    elif args.cmd == "serve-mcp":
        from capstone import mcp_server
        mcp_server.mcp.run(transport="stdio")
    elif args.cmd == "eval":
        from capstone.evals.run_all import main as run_all
        run_all(stage=args.stage, limit=args.limit)
    elif args.cmd == "replay":
        from capstone.replay import replay
        print(json.dumps(replay(args.trace_id), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
