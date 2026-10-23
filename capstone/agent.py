"""The integrated agent: the Week-7 hand-built loop, running against the Week-3/4 retriever and the Week-9
MCP host pattern, with Week-11 tracing/logging. No agent framework.

Division of labour (deliberate):
  * the MODEL chooses tools, fills arguments (peril, query, loss date) and writes a short neutral summary;
  * CODE assembles everything an adjuster will rely on (citations, in-force result, deductible/sub-limit/
    policy-limit, referral) from tool results, so a number is never "whatever the model typed";
  * CODE enforces the guard (no coverage verdict words) and the PII line.

Tools:  local  -> lookup_policy, check_coverage_in_force, refer_to_adjuster   (capstone/tools.py)
        MCP    -> whatever the server advertises in tools/list (search_policy_wording, get_limits today);
                  schemas are taken from the server at connect time, never hard-coded here.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from contextlib import AsyncExitStack
from pathlib import Path

from groq import Groq, RateLimitError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.claims_agent.pricing import call_cost  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.observability.tracing import RequestTrace  # noqa: E402
from capstone import guard, tools as local_tools  # noqa: E402
from capstone.config import LOG_DIR, version_for_date  # noqa: E402
from capstone.pii import scrub  # noqa: E402
from capstone.requestlog import write_request_log  # noqa: E402

PROMPT_VERSION = "capstone-triage-v1"
MAX_ITERATIONS = 9
MAX_RATE_RETRIES = 8
_WAIT_RE = re.compile(r"try again in (?:(\d+)m)?([\d.]+)s", re.I)

SYSTEM_PROMPT = """You triage insurance claims for HUMAN adjusters. You never decide coverage.
Turn 1: call lookup_policy with the claim or policy number.
Turn 2: in ONE turn call these three together (they are independent), all with the same LOSS date (YYYY-MM-DD; the date stated in the FNOL, else the date on file) and the product from the lookup:
  - check_coverage_in_force(policy_id, loss_date)
  - search_policy_wording(query, product, loss_date): query about the exclusion/conditions of the peril in the FNOL (flood, theft, collision, fire, wear_and_tear)
  - get_limits(product, peril, loss_date, policy_id): deductible, sub_limit and policy_limit are three different numbers
Turn 3: refer_to_adjuster with neutral facts and the clause + endorsement ids you were shown.
Then reply with a 2-3 sentence neutral summary of facts for the adjuster.
Rules: never write the words covered, covers, approved, declined, payable, and never say what the insurer will pay or refuse; state which wording version was applied; if a tool returns an error, read it and correct the call; always end with refer_to_adjuster."""


def _wait_seconds(err: Exception) -> float:
    m = _WAIT_RE.search(str(err))
    if not m:
        return 20.0
    return int(m.group(1) or 0) * 60 + float(m.group(2)) + 1.0


class CapstoneAgent:
    def __init__(self, wording_mode: str = "in_force", model: str | None = None):
        self.wording_mode = wording_mode
        self.model = model or settings.MODEL_NAME
        self._stack = AsyncExitStack()
        self.session: ClientSession | None = None
        self.mcp_tools: dict[str, dict] = {}   # discovered at runtime
        self.client = Groq(api_key=settings.GROQ_API_KEY, max_retries=0)

    # ---- MCP -----------------------------------------------------------
    async def connect(self) -> None:
        import os
        env = {**os.environ, "WORDING_MODE": self.wording_mode, "PYTHONPATH": str(REPO_ROOT),
               "TOKENIZERS_PARALLELISM": "false", "PYTHONIOENCODING": "utf-8"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "capstone.mcp_server"],
                                       env=env, cwd=str(REPO_ROOT))
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self.session = await self._stack.enter_async_context(ClientSession(read, write))
        await self.session.initialize()
        listed = await self.session.list_tools()
        for t in listed.tools:
            schema = json.loads(json.dumps(t.inputSchema))
            schema.get("properties", {}).pop("trace_id", None)   # the host owns trace propagation, not the model
            self.mcp_tools[t.name] = {"description": t.description or "", "schema": schema}

    async def aclose(self) -> None:
        await self._stack.aclose()

    def tool_schemas(self) -> list[dict]:
        out = [s for _, s in local_tools.LOCAL_TOOLS.values()]
        for name, t in self.mcp_tools.items():
            out.append({"type": "function", "function": {
                "name": name, "description": t["description"].strip().split("\n\n")[0] + " " + _args_hint(t["description"]),
                "parameters": t["schema"]}})
        return out

    async def _call_mcp(self, name: str, args: dict, trace_id: str) -> dict:
        res = await self.session.call_tool(name, {**args, "trace_id": trace_id})
        text = "\n".join(b.text for b in res.content if getattr(b, "type", None) == "text")
        if res.isError:
            return {"error": text}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"result": text}

    # ---- LLM -----------------------------------------------------------
    def _llm(self, messages: list[dict], schemas: list[dict]):
        waited = 0.0
        for _ in range(MAX_RATE_RETRIES):
            try:
                return self.client.chat.completions.create(
                    model=self.model, temperature=0, messages=messages, tools=schemas, tool_choice="auto",
                    max_tokens=700), waited
            except RateLimitError as e:
                w = min(_wait_seconds(e), 70.0)
                time.sleep(w)
                waited += w
        raise RuntimeError("rate-limit retries exhausted")

    # ---- the loop ------------------------------------------------------
    async def triage(self, fnol_text: str, claim_id: str | None = None, case_id: str | None = None,
                     write_log: bool = True) -> dict:
        trace_id = uuid.uuid4().hex[:16]
        tr = RequestTrace(trace_id)
        ids = local_tools.extract_ids(fnol_text)
        claim_id = (claim_id or ids["claim_id"])
        rec = local_tools.find_claim(claim_id) if claim_id else None
        pii = rec["pii"] if rec else None

        with tr.span("input.sanitize", "tools", hop="agent") as sp:
            safe_fnol = scrub(fnol_text, pii)
            sp.attrs["pii_known_from_record"] = bool(pii)

        schemas = self.tool_schemas()
        user_msg = f"FNOL (redacted): {safe_fnol}\nClaim number: {claim_id or 'not given'}  Policy number in text: {ids['policy_id'] or 'none'}"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]
        trajectory: list[dict] = []
        final_text, stop = None, "max_iterations"
        referral_called = False

        for it in range(MAX_ITERATIONS):
            with tr.span(f"llm.call.{it + 1}", "generation", hop="agent", model=self.model) as sp:
                resp, waited = self._llm(messages, schemas)
                u = resp.usage
                sp.prompt_tokens, sp.completion_tokens = u.prompt_tokens, u.completion_tokens
                sp.cost_usd = call_cost(self.model, u.prompt_tokens, u.completion_tokens)
                sp.attrs["rate_limit_wait_s"] = round(waited, 1)
            msg = resp.choices[0].message
            if not msg.tool_calls:
                final_text, stop = (msg.content or "").strip(), "final_answer"
                break
            messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls]})
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name in local_tools.LOCAL_TOOLS:
                    with tr.span(f"tool.{name}", "tools", hop="agent", tool=name) as sp:
                        try:
                            call_args = dict(args)
                            if name == "refer_to_adjuster":
                                call_args["trace_id"] = trace_id
                                call_args.setdefault("claim_id", claim_id or "UNKNOWN")
                            result = local_tools.LOCAL_TOOLS[name][0](**call_args)
                            if name == "refer_to_adjuster":
                                referral_called = True
                        except Exception as e:  # tool errors go back to the model, as in Week 7/9
                            result = {"error": str(e)}
                    origin = "local"
                elif name in self.mcp_tools:
                    stage = "retrieval" if name == "search_policy_wording" else "tools"
                    with tr.span(f"mcp.{name}", stage, hop="mcp", tool=name) as sp:
                        result = await self._call_mcp(name, args, trace_id)
                        srv = result.get("_span") or {}
                        sp.attrs["server_latency_ms"] = srv.get("latency_ms")
                        sp.attrs["server_trace_id"] = result.get("trace_id")
                    origin = "mcp"
                else:
                    result, origin = {"error": f"unknown tool {name!r}"}, "none"
                trajectory.append({"order": len(trajectory) + 1, "tool": name, "origin": origin, "arguments": args,
                                   "result": result})
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(_compact(name, result))})
            if referral_called and stop != "final_answer":
                stop = "referred"

        out = self._assemble(tr, trace_id, claim_id, rec, trajectory, final_text, stop, referral_called, case_id, safe_fnol)
        if write_log:
            write_request_log(out)
        return out

    # ---- assemble everything the adjuster relies on from TOOL RESULTS ---
    def _assemble(self, tr, trace_id, claim_id, rec, trajectory, final_text, stop, referral_called, case_id, fnol_redacted=None):
        def first(tool):
            return next((s for s in trajectory if s["tool"] == tool and "error" not in s["result"]), None)

        lk, ck, sr, lm = first("lookup_policy"), first("check_coverage_in_force"), first("search_policy_wording"), first("get_limits")
        searches = [s for s in trajectory if s["tool"] == "search_policy_wording" and "error" not in s["result"]]
        loss_date = None
        for s in (searches + [x for x in [ck, lm] if x]):
            loss_date = s["arguments"].get("loss_date") or loss_date
            if loss_date:
                break
        expected_set = version_for_date(loss_date) if loss_date and re.fullmatch(r"\d{4}-\d{2}-\d{2}", loss_date) else None

        citations, seen = [], set()
        for s in searches:
            for c in s["result"].get("citations", []):
                if c["chunk_id"] not in seen:
                    seen.add(c["chunk_id"])
                    citations.append({k: c[k] for k in ("citation", "url", "product", "clause", "title", "endorsement_id",
                                                        "endorsement_version", "effective_from", "effective_to", "chunk_id")}
                                     | {"excerpt": c["text"]})
        wrong = [c["chunk_id"] for c in citations if expected_set and c["endorsement_version"] not in ("BASE", expected_set)]

        lres = lm["result"] if lm else {}
        limits = {"deductible": lres.get("deductible"), "sub_limit": lres.get("sub_limit"), "policy_limit": lres.get("policy_limit")} if lm else None

        if not referral_called:   # fail closed: a triage always ends in a referral, even when the model forgot
            r = local_tools.refer_to_adjuster(claim_id or "UNKNOWN", "Automatic referral: agent ended without calling refer_to_adjuster.",
                                              [c["chunk_id"] for c in citations], trace_id)
            trajectory.append({"order": len(trajectory) + 1, "tool": "refer_to_adjuster", "origin": "forced_by_code",
                               "arguments": {"claim_id": claim_id}, "result": r})
            referral = {**r, "forced_by_code": True}
        else:
            ref_step = [s for s in trajectory if s["tool"] == "refer_to_adjuster" and "error" not in s["result"]][-1]
            referral = {**ref_step["result"], "forced_by_code": False}

        summary, guard_info = guard.enforce_summary(final_text)
        pii = rec["pii"] if rec else None
        totals = tr.totals()
        return {
            "trace_id": trace_id, "case_id": case_id, "claim_id": claim_id,
            "policy_id": lk["result"]["policy_id"] if lk else (rec or {}).get("policy_id"),
            "product": lk["result"]["product"] if lk else (rec or {}).get("product"),
            "loss_date_used": loss_date, "loss_date_on_file": (lk or {}).get("result", {}).get("loss_date_on_file"),
            "wording_set_expected_for_loss_date": expected_set,
            "in_force": None if not ck else {"in_force_on_loss_date": ck["result"]["in_force_on_loss_date"],
                                             "period_of_insurance": ck["result"]["period_of_insurance"]},
            "citations": citations, "wrong_version_citations": wrong,
            "limits": limits, "referral": referral,
            "fnol_redacted": fnol_redacted, "summary": summary, "guard": guard_info, "raw_model_summary_redacted": scrub(final_text, pii),
            "stop_reason": stop, "tool_sequence": [s["tool"] for s in trajectory],
            "trajectory": _redact_trajectory(trajectory, pii),
            "tools_discovered_over_mcp": sorted(self.mcp_tools),
            "wording_mode": self.wording_mode, "prompt_version": PROMPT_VERSION, "model": self.model,
            "spans": tr.to_list(), "totals": totals,
        }


def _args_hint(desc: str) -> str:
    return ""


def _compact(name: str, result: dict) -> dict:
    """Trim what goes back into the model's context (tokens are the scarce resource on this tier)."""
    if name == "search_policy_wording" and "citations" in result:
        return {"wording_in_force": [{"citation": c["citation"], "text": c["text"]} for c in result["citations"]]}
    return {k: v for k, v in result.items() if k not in ("_span", "trace_id", "reading_note")}


def _redact_trajectory(traj: list[dict], pii: dict | None) -> list[dict]:
    """The stored trajectory is the reproducibility record, so it is scrubbed like everything else."""
    out = []
    for s in traj:
        blob = scrub(json.dumps(s, default=str), pii, keep_ids=True)
        try:
            out.append(json.loads(blob))
        except json.JSONDecodeError:   # scrub can never be allowed to corrupt the record into silence
            out.append({"order": s["order"], "tool": s["tool"], "redaction_error": True})
    return out
