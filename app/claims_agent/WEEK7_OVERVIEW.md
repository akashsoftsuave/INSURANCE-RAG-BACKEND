# Week 7 — Claims-Triage Agent: Complete Reference

This is the single file that explains everything in `app/claims_agent/`:
why each file exists, how the agent's decision loop actually works, how it
differs from the fixed workflow, how every file connects to every other
file, which scripts to run, and exactly what each run produces.

Everything here is **additive** — nothing in `app/services` or `app/api`
(the Week 4-6 RAG app) is touched. This module reuses the existing Groq
client/config (`app.core.config.settings`) so model, credentials, and
provider are identical to the rest of the app.

---

## 1. The task in one sentence

Given a `claim_id`, decide: is it covered, what's the payable amount, and
why — using two competing implementations of the *same* business logic
(an LLM **agent** that chooses its own tool calls, vs. a **fixed workflow**
that calls the same tools in a hard-coded order), then race them against
each other on cost, latency, and correctness.

---

## 2. File-by-file: what it is and why it exists

### `data.py` — the fake claims database
This is the "claims system" and "policy system" stand-in. Three module-level
dicts:
- `POLICIES` — 7 policies, each with `active`, `excess` (deductible),
  per-cause `exclusions` (with a human-readable `reason`), and per-cause
  `sub_limits` (payout caps).
- `CLAIMS` — 10 claim records (`CLM-2026-00001` … `00010`), each with
  `policy_id`, `claimant_name`, `claimed_amount`, `incident_date`, and
  free-text `adjuster_notes`. **The cause of loss is never a separate
  field** — it only exists inside the prose of `adjuster_notes`, which is
  the whole point: something has to *read* the notes and infer the cause.
- `EXPECTED` — hand-computed ground truth (status/covered/exclusion/
  gross_amount/excess/payable_amount) for all 10 claims, used to grade
  both systems identically. Never shown to the model.

Why it exists: every other file in this module either reads from `data.py`
(`tools.py`, `runner.py`) or extends it (`long_claims.py` registers 3 more
claims into the same `CLAIMS` dict). It's the single source of truth both
systems are tested against.

### `tools.py` — the three tools, shared verbatim by both systems
This is the most important file to understand because **both** the agent
and the workflow call the exact same Python functions here. The only thing
that differs between the two systems is *who decides* which tool to call,
with what arguments, in what order — never the tool implementation itself.

| Tool | Single job | Reads from |
|---|---|---|
| `get_claim(claim_id)` | Fetch one claim record (incl. raw `adjuster_notes`) | `data.CLAIMS` |
| `check_policy_exclusions(policy_id, cause)` | Decide coverage: excluded? active? excess? sub_limit? | `data.POLICIES` |
| `compute_payout(claimed_amount, policy_excess, claim_status, sub_limit)` | Pure arithmetic — turn already-decided facts into `gross_amount`/`excess`/`payable_amount` | nothing (pure function) |

Each tool has a JSON-schema description (`GET_CLAIM_SCHEMA`,
`CHECK_POLICY_EXCLUSIONS_SCHEMA`, `COMPUTE_PAYOUT_SCHEMA`) that explicitly
states what it does **and what it does not do** — this sharp-boundary
design is what lets the agent's model pick the right tool instead of
guessing between three overlapping options. See
`CLAIMS_AGENT_TOOLS.md` in this same directory for the full design
rationale (why a 3rd tool was added, why it's `compute_payout` and not
`get_adjuster_notes`, the responsibility matrix).

`ALL_TOOL_SCHEMAS` + `execute_tool(name, arguments)` is the dispatch table
the **agent** uses to actually invoke whichever tool name the model chose.
The workflow doesn't use `execute_tool` — it calls `get_claim`,
`check_policy_exclusions`, `compute_payout` directly by name, in a fixed
order (see workflow.py below).

### `contract.py` — the shared output shape + the grader
- `CONTRACT_FIELDS` — the 8 fields every final answer must have
  (`claim_id, status, covered, exclusion, gross_amount, excess,
  payable_amount, reason`).
- `make_output(...)` — builds that dict (used by the workflow; the agent
  builds it itself by returning JSON matching the same schema, enforced via
  its system prompt).
- `grade(output, expected)` — compares an output against `EXPECTED`/
  `LONG_EXPECTED` on every field **except** `reason` (free text, never
  graded), returning `(passed: bool, mismatches: list[str])`. This is the
  one grading function every run script calls, so both systems are judged
  by identical rules.

### `budgets.py` — stops a runaway agent loop
`BudgetConfig` (max_iters=6, max_tokens=6000, max_cost=$0.02,
max_wall_clock_ms=45000 by default) + `BudgetTracker`, which accumulates
iterations/tokens/cost/elapsed-time and exposes `.check()` → returns a
termination-reason string (`"max_iterations"`, `"max_tokens"`,
`"max_cost"`, `"wall_clock"`) the instant any ceiling is crossed, or `None`
if still under budget. Only the **agent** loop uses this (the workflow is
always exactly 1 model call, so it can't run away). `run_budget_demo.py`
exists specifically to prove this fires.

### `pricing.py` — one place cost-per-call is computed
`MODEL_PRICING` table (currently only `openai/gpt-oss-120b` — Groq's
published $0.15/1M input, $0.60/1M output) + `call_cost(model,
prompt_tokens, completion_tokens)`. Both `agent.py` and `workflow.py` call
this after every model response so cost numbers are computed the same way
everywhere instead of being hand-calculated in multiple files.

### `context_window.py` — sliding-window summarization for long notes
`apply_sliding_window(notes, client, model)`: if `adjuster_notes` has more
than `TRIGGER_TURNS=12` lines, it summarizes everything except the last
`KEEP_RECENT_TURNS=6` lines with one fixed LLM call, and returns
`"[SUMMARY OF N EARLIER ENTRIES]: ...\n[LAST 6 ENTRIES, VERBATIM]: ..."`
instead of the raw notes. Returns `(notes, None)` unchanged if the notes
are already short. This is **opt-in** — only triggers when a caller passes
`use_context_window=True` to `run_agent`/`run_workflow`. It exists so long
(~30-turn) adjuster-notes conversations don't blow the token budget, and
`run_long_claims_demo.py` exists to check whether summarizing away detail
ever flips the correct answer.

### `long_claims.py` — 3 extra claims designed to stress the context window
`LONG_CLAIMS` (`CLM-2026-L001/L002/L003`), each with ~30 turns of
back-and-forth adjuster/claimant dialogue instead of one short note. Each
is a deliberate trap:
- **L001**: straightforward flood, but 30 turns of repeated "flood" chatter
  (tests that summarization doesn't lose an easy signal).
- **L002**: the *true* cause (burst indoor pipe, not flood — covered
  instead of excluded) is stated once, early, then corrected; 27 turns of
  small talk keep mentioning rain/flood in passing. If summarization
  swallows the correction, the wrong (excluded) cause gets extracted.
- **L003**: the cause (dropped laptop, accidental) is stated once at turn
  2; everything else is pure logistics chatter with zero cause information
  — nothing to reconstruct it from if it's summarized away.

**Important side effect**: importing this module runs
`CLAIMS.update(LONG_CLAIMS)` at import time (`long_claims.py:150`) — this
is what makes `CLM-2026-L00x` resolvable via the normal `get_claim` tool,
exactly like any of the 10 race claims. `LONG_EXPECTED` is ground truth
computed from the **full** notes (what a system with no context limit
would conclude), used to check if the windowed run's answer diverges from
the control run's answer.

### `persistence.py` — proves state survives a process restart
Tiny JSON-file-backed key/value store (`results/policy_excess_store.json`)
with `remember_policy_excess`, `recall_policy_excess`, `clear`. Not used by
the agent or workflow at all — it exists purely to demonstrate (via
`run_persistence_demo.py`) that a value written by one process is still
readable by a completely separate process that starts with no memory.

### `runner.py` — the shared test harness
`run_all(run_one, system_name, verbose=False)`: loops over all 10
`CLAIM_IDS`, calls either `run_agent` or `run_workflow` on each, grades
the output against `EXPECTED`, and prints one line per claim
(`[agent] CLM-2026-00001: PASS | latency_ms=... iterations=... tokens=...
cost=... termination=...`). Returns a list of per-claim result rows.
`summarize(rows)` reduces that list to pass-rate / p50 latency / total
tokens / cost-per-claim. `save_detail_json(rows, filename)` dumps the full
row list (including every log line) to `results/<filename>`. This is the
one harness both `run_agent.py` and `run_workflow.py` (and `run_race.py`,
which runs both) call — it's why the two systems are graded identically.

### `agent.py` — the LLM agent (dynamic tool choice)
See §3 below — this is the file that actually implements "the agent loop."

### `workflow.py` — the fixed workflow (hard-coded tool order)
See §4 below — the deterministic counterpart to `agent.py`.

---

## 3. How the agent loop actually works, step by step

`run_agent(claim_id, budget_config=None, verbose=False,
use_context_window=False)` in `agent.py` is the entire decision loop.
Here is exactly what happens, in order:

1. **Setup.** A `Groq` client is created from `settings.GROQ_API_KEY`. A
   `BudgetTracker` is created (default budgets from `BudgetConfig()` unless
   overridden). The message list is seeded with:
   - a `system` message — `SYSTEM_PROMPT` (see below), which is the *only*
     place the agent's business rules live.
   - a `user` message — just `"claim_id: <id>"`. The agent is given
     nothing else; everything else it must fetch itself via tools.

2. **The loop** (`for _safety in range(50)` — a hard backstop; the real
   limit is the budget check, not this number):

   a. **Budget check #1.** `tracker.check()` — if any ceiling
      (`max_iters`/`max_tokens`/`max_cost`/`max_wall_clock_ms`) is already
      exceeded, log `BUDGET_TERMINATED` and break immediately, before
      spending on another model call.

   b. **Call the model.** `client.chat.completions.create(model=...,
      temperature=0, messages=messages, tools=ALL_TOOL_SCHEMAS,
      tool_choice="auto")`. `temperature=0` for determinism.
      `tool_choice="auto"` is the crux of "agent" vs "workflow" — the
      *model itself* decides whether to call a tool, which one, and with
      what arguments, based only on the system prompt and what's in
      `messages` so far.

   c. **Record cost.** Token usage from the response is priced via
      `pricing.call_cost` and recorded into the tracker
      (`record_iteration`).

   d. **Budget check #2.** Same check as (a), run again after paying for
      this call, in case this iteration itself blew a ceiling.

   e. **Branch on whether the model asked for tool calls:**
      - **If `msg.tool_calls` is non-empty:** the assistant's message
        (including the raw tool_calls) is appended to `messages`. Then,
        for **each** tool call the model requested:
        - parse its JSON arguments,
        - dispatch through `tools.execute_tool(name, args)`,
        - **special case:** if `use_context_window=True` and the tool was
          `get_claim` and the result has `adjuster_notes`, immediately run
          `apply_sliding_window` on the notes and substitute the windowed
          text into the result before the model ever sees it — this is
          the only point where context management touches the agent path.
        - on `ToolError`, the result becomes `{"error": "..."}` instead of
          crashing the loop — the model sees the error and can react to
          it in its next turn.
        - append a `role: "tool"` message with the JSON result, keyed to
          that `tool_call_id`.
        Then `continue` — loop back to step (a) for another model call,
        now with the tool results in context.
      - **If there are no tool calls:** this is the model's final answer.
        Its `content` is stripped of markdown fences if present, then
        `json.loads`'d. If it parses **and** contains every field in
        `CONTRACT_FIELDS`, it's accepted (`termination_reason =
        "completed"`). Otherwise `termination_reason` is
        `"invalid_output_json"` or `"invalid_output_schema"`. Either way,
        `break` — the loop is over.

3. **Return.** A single dict: `claim_id, output (or None),
   termination_reason, iterations, tokens_total, cost_total,
   wall_clock_ms, log` (the full list of `[agent] ...` log lines — this is
   what ends up in `results/agent_results.json`'s `"log"` field per row).

### How the model actually decides (the system prompt)

`SYSTEM_PROMPT` (`agent.py:15`) is where every business rule lives — the
model is never given code, only these instructions:
- Read `adjuster_notes` yourself and determine the cause of loss (one of
  the fixed `CAUSE_ENUM` values); if empty/unclear, cause = `"unknown"`.
- If cause is `"unknown"`: **do not call any more tools** — go straight to
  `status=PENDING_REVIEW, covered=false, exclusion=null,
  payable_amount=0, excess=null`.
- Otherwise: call `check_policy_exclusions(policy_id, cause)`.
- Not covered → `DENIED`. Covered + `claimed_amount` over `sub_limit` →
  `PARTIAL`. Covered + within (or no) `sub_limit` → `APPROVED`.
- Always call `compute_payout` to get the final numbers — never compute
  gross/excess/payable by hand.
- Never call a tool outside its stated single job.

The model is trusted to follow this without any code-level enforcement
(no `if` statements route it) — that's exactly the "agent" side of the
race: the *loop* (agent.py) is generic/fixed, but the *decision of which
tool to call, with what arguments, in what order* is 100% delegated to the
model, guided only by tool descriptions + this prompt.

---

## 4. How the fixed workflow works (the deterministic counterpart)

`run_workflow(claim_id, verbose=False, use_context_window=False)` in
`workflow.py` runs the **same three tools** but the order and arguments
are hard-coded Python control flow — nothing is decided by an LLM except
one thing (step 6). Steps, always in this order:

1. **`get_claim(claim_id)`** — same tool as the agent uses, called
   directly (not through `execute_tool`).
2. **Read `adjuster_notes`** — if `use_context_window=True`, apply the
   same `apply_sliding_window` as the agent path.
3. **`extract_cause(notes)`** (`workflow.py:23`) — a **hard-coded keyword
   matcher** (`CAUSE_KEYWORDS`, e.g. `"stolen"/"theft"/"burglar"` →
   `"theft"`), not a model call. This is the fixed-workflow's replacement
   for "the model reads the notes and decides the cause" — same
   fixed-workflow with `KEYWORDS -> cause` and no branching intelligence.
   If notes are empty → `"unknown"`; if no keyword matches → `"general"`.
4. **If cause is `"unknown"`**: same fixed shortcut as the agent —
   `PENDING_REVIEW`, skip exclusion/payout entirely. **Else**:
   `check_policy_exclusions(policy_id, cause)` — same tool as the agent.
5. **`decide_status(covered, claimed_amount, sub_limit)`**
   (`workflow.py:36`) — the exact same rule the agent's system prompt
   states in English, but here it's a real Python `if`/`else`. Then
   **`compute_payout(...)`** — same tool, same arithmetic.
6. **One fixed model call** to render `reason` — a plain-English
   explanation of the *already-decided* facts (`REASON_PROMPT_TEMPLATE`).
   No tools are bound to this call and nothing branches on its output; it
   fills exactly one text field. This is the *only* place workflow.py
   calls the LLM at all.
7. **Return** `make_output(...)` wrapped in the same result-dict shape as
   `run_agent` (`claim_id, output, termination_reason="completed",
   iterations=1, tokens_total, cost_total, wall_clock_ms, log`) — same
   shape so `runner.py` can grade/summarize both identically.

**The core comparison the whole module is built around:** same tools,
same business rules, same output contract, same grader — the only
variable is *whether an LLM or hard-coded Python chooses the tool
sequence and arguments*. Agent = 1 model call per step (dynamic,
flexible, costs more tokens/latency, can go off-script). Workflow = 1
model call total, only for prose (fixed, cheap, fast, cannot misinterpret
which tool to call — but its `extract_cause` keyword matcher is dumber
than an LLM reading the same text, which is exactly what
`run_long_claims_demo.py` stress-tests).

---

## 5. File interconnection map

```
data.py  (CLAIMS, POLICIES, EXPECTED)
   |
   |  imported by
   v
tools.py  (get_claim, check_policy_exclusions, compute_payout,
           ALL_TOOL_SCHEMAS, execute_tool)
   |                                   ^
   | imported by                      | imported directly (not execute_tool)
   v                                   |
agent.py  --------------------->  workflow.py
   |  uses: budgets.py                |  uses: contract.make_output
   |         pricing.py               |         pricing.py
   |         contract.py (CONTRACT_    |         context_window.py
   |            FIELDS)                |
   |         context_window.py         |
   v                                   v
        both return the same result-dict shape
                     |
                     v
                runner.py (run_all, summarize, save_detail_json)
                     |            uses contract.grade + data.EXPECTED
                     v
   run_agent.py / run_workflow.py / run_race.py   (entry points)

long_claims.py  --(CLAIMS.update on import)-->  data.CLAIMS
   used by: run_long_claims_demo.py (imports agent.py + workflow.py directly,
   its own run_condition() loop instead of runner.run_all(), grades against
   LONG_EXPECTED instead of EXPECTED)

persistence.py  -- standalone, only used by run_persistence_demo.py
budgets.py      -- only used by agent.py (workflow is always 1 call, no budget needed)
```

---

## 6. Which file to run, and exactly what you get

All commands below run from the repo root (`INSURANCE-RAG-BACKEND/`) with
your normal Python env (needs `GROQ_API_KEY`/`MODEL_NAME` set — same
`.env` the rest of the app uses).

| Run this | What it does | What you get |
|---|---|---|
| `python run_agent.py` | Runs **only the agent** over all 10 claims via `runner.run_all` | Console: one PASS/FAIL line per claim. File: `results/agent_results.json` (full per-claim detail incl. every `[agent] ...` log line, tool calls, tokens, cost, termination reason). |
| `python run_workflow.py` | Runs **only the fixed workflow** over all 10 claims | Console: per-claim lines + a `=== workflow summary ===` block (pass_rate, p50 latency, total tokens, cost/claim). File: `results/workflow_results.json`. |
| `python run_race.py` | Runs **both** systems over all 10 claims and compares them | Console: both systems' per-claim lines, then a `=== RACE RESULT ===` table (pass rate / p50 latency / total tokens / cost per claim, side by side). Files: `results/agent_results.json`, `results/workflow_results.json`, `race.csv` (repo root, the summary table), `results/race_detail.csv` (per-claim row for both systems). **This is the main "week 7 answer" script** — run this first if you just want the headline comparison. |
| `python run_budget_demo.py` | Deliberately starves the agent (`max_tokens=2200`, a run that normally needs ~4 model calls) to prove budget enforcement fires | Console: full verbose `[agent]` log ending in `BUDGET_TERMINATED` / `reason=max_tokens`, then `OK: agent terminated cleanly on a budget limit`. Proves the loop stops itself instead of running forever — no file output. |
| `python run_long_claims_demo.py` | Runs agent + workflow, each twice (control = raw ~30-turn notes, windowed = summarized via `context_window.py`), over the 3 `long_claims.py` trap claims | Console: PASS/FAIL per claim per condition, a summary block per condition (pass_rate/tokens/cost/latency), and a `=== did summarization change correctness vs control? ===` section that flags any claim where windowed disagrees with control. File: `results/long_claims_results.json`. This is the file to check if you want to know **whether summarization ever breaks correctness** — currently `L001`/`L003` survive summarization but the cause it extracts can still land on `"flood"` due to repeated keyword noise even when windowed (see `results/summary_inspect.txt` for a saved example of the actual summarized text produced). |
| `python run_persistence_demo.py clear` then `phase1` then `phase2` (3 separate invocations — it's meant to simulate 2 different processes) | `phase1` reads `POLICIES["POL-AUTO-1001"]["excess"]` and persists it to `results/policy_excess_store.json`; `phase2` is a **fresh process** that reads only the file, never `POLICIES` again | Console: `[process A] ...` then, in the second invocation, `[process B] ... OK: persisted value matches the true policy excess (...) -- survived the restart.` Proves state survives across process boundaries. No relation to the agent/workflow claim logic — this is a standalone persistence check. |

Run order if you want the whole story once: `run_race.py` (headline
comparison) → `run_budget_demo.py` (proves the safety net works) →
`run_long_claims_demo.py` (proves/stress-tests context management) →
`run_persistence_demo.py clear/phase1/phase2` (proves state survives a
restart, unrelated to claims logic).

---

## 7. Where to look for a specific answer

- **"What did the agent actually say/do for claim X?"** →
  `results/agent_results.json`, find the row with that `claim_id`, read
  its `"log"` array (every `[agent] tool_call=... args=... -> result`
  line, in order) and its `"output"`.
- **"Did the agent or workflow get claim X wrong, and how?"** → same file,
  `"passed"` + `"mismatches"` fields (`contract.grade` output).
- **"How much did the whole race cost / how fast was it?"** →
  `race.csv` (repo root) for the headline numbers, `results/race_detail.csv`
  for per-claim numbers.
- **"Does summarizing long notes ever change the answer?"** →
  `results/long_claims_results.json` + the "did summarization change
  correctness" console section from `run_long_claims_demo.py`; a saved
  example of what the summarized text actually looks like is in
  `results/summary_inspect.txt`.
- **"Why does tool X exist / not overlap with tool Y?"** →
  `CLAIMS_AGENT_TOOLS.md` in this directory.
- **"What are the ground-truth expected answers?"** → `data.py`'s
  `EXPECTED` dict (10 short claims) / `long_claims.py`'s `LONG_EXPECTED`
  (3 long claims).
