"""Runs the agent once per case and stores the full run. The suite, the judge and the trajectory eval all read
THESE runs, so the three evals describe the same executions (and the Groq token budget is spent once)."""
from __future__ import annotations

import asyncio
import json
import time
import traceback

from capstone.agent import CapstoneAgent
from capstone.config import RUNS_DIR


def run_dir(mode: str):
    d = RUNS_DIR / mode
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_run(mode: str, case_id: str) -> dict | None:
    p = run_dir(mode) / f"{case_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


async def _run(cases: list[dict], mode: str, fresh: bool, label: str) -> None:
    agent = CapstoneAgent(wording_mode=mode)
    await agent.connect()
    try:
        for i, c in enumerate(cases, 1):
            path = run_dir(mode) / f"{c['case_id']}.json"
            if path.exists() and not fresh:
                continue
            t0 = time.time()
            try:
                out = await agent.triage(c["fnol"], claim_id=c.get("claim_id"), case_id=c["case_id"])
            except Exception as e:   # a crashed run is data: it is scored as a failure, never skipped
                out = {"case_id": c["case_id"], "crashed": True, "error": f"{type(e).__name__}: {e}",
                       "traceback": traceback.format_exc()[-1500:]}
            path.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
            print(f"[{label} {i}/{len(cases)}] {c['case_id']}  {round(time.time() - t0, 1)}s  "
                  f"{'CRASH ' + out['error'] if out.get('crashed') else out['tool_sequence']}", flush=True)
    finally:
        await agent.aclose()


def run_cases(cases: list[dict], mode: str = "in_force", fresh: bool = False, label: str = "run") -> None:
    asyncio.run(_run(cases, mode, fresh, label))
