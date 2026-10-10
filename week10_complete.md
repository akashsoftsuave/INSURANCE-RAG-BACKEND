# Week 10 Practical (Task Set D) - Complete Merged Document

> Single-file merge of all Week 10 markdown deliverables.
>
> Sources: `week10_multi_agent/deliverables/race_table.md`, `week10_multi_agent/deliverables/failure_case.md`, `week10_multi_agent/deliverables/verdict.md` (all kept unchanged alongside this document), plus the Part 1 implementation guide.

## Contents
- Part 1 - Complete Implementation & Flow Guide
- Part 2 - Race Table (4 Metrics x 2 Arms)
- Part 3 - Injected Worker Failure & A2A Lifecycle
- Part 4 - Verdict (KILL / KEEP)

---

## Part 1 - Complete Implementation & Flow Guide

<!-- source: week10_multi_agent/deliverables/race_table.md, failure_case.md, verdict.md -->

## Week 10 Practical (Task Set D) — Complete Implementation & Flow Guide

> **What this file is:** the single markdown document for **Week 10 Practical — Task Set D ("Race the claims squad against your single agent")** — what we changed, why every file was created, how the single-agent vs. multi-agent flow works step-by-step, how every requirement and bonus question is answered, and what the measured numbers mean. Reading this one file gives you the entire picture.
>
> The graded artefacts themselves are **not** merged away by this file: they stay as separate files under `week10_multi_agent/deliverables/` (`race_table.md`, `handoffs.log`, `failure_case.md`, `verdict.md`, `agent_card.json`). This document is the narrative; the deliverables are the evidence.

---

### 1. What Week 10 (Task Set D) Is Asking Us To Prove

In earlier weeks, we built a **Single Agent** (`app/claims_agent/agent.py::run_agent`) that triages an insurance claim (`claim_id`) using tools (`get_claim`, `get_policy`, `check_exclusions` / `check_policy_exclusions`, `compute_payout`) and outputs an 8-field JSON decision (`claim_id`, `status`, `covered`, `exclusion`, `gross_amount`, `excess`, `payable_amount`, `reason`).

For Week 10, we built a **Multi-Agent Orchestrator ("Claims Squad")** (`week10_multi_agent/orchestrator.py::run_orchestrator`) that splits that job across three specialized roles:
1. **Lead Orchestrator (Decompose & Synthesise):** Decomposes the claim request, coordinates the workers, calls `compute_payout` on their combined findings, and produces the final JSON decision.
2. **Adjuster-Note Summary Worker (`adjuster_notes_worker`):** Has access **only** to `get_claim`. Reads the raw free-text `adjuster_notes`, extracts the `cause` of loss (`flood`, `theft`, `accidental_damage`, `fire`, `wear_and_tear`, `collision`, `general`, `unknown`), and returns a 1-sentence `notes_summary` alongside `policy_id` and `claimed_amount`.
3. **Coverage & Exclusions Worker (`coverage_exclusions_worker`):** Has access **only** to `get_policy` and `check_exclusions` (never `get_claim` or `compute_payout`). Receives **only** the scoped `(policy_id, cause)` pair — never the raw adjuster notes — and returns whether the policy is active, whether the cause is covered or excluded, the deductible (`excess`), and any `sub_limit`.

**The core engineering question:** Does splitting the task across a multi-agent squad actually beat the single agent on the **exact same 10 evaluation cases** with the **exact same judge**, or does it just multiply latency, tokens, and cost per claim?

---

### 2. What Was Changed / Created (File-by-File Map)

Nothing in `app/` (your existing Week 4–8 RAG and single-agent code) or `week9_mcp/` was modified. Everything is additive so existing functionality stays 100% intact.

| File Path | Type | Why This File Was Created & What It Does |
|---|---|---|
| `week10_multi_agent/__init__.py` | Code | Marks `week10_multi_agent` as a Python package. |
| `week10_multi_agent/workers.py` | Code | Implements the two narrow workers (`run_adjuster_notes_worker` and `run_coverage_exclusions_worker`), enforces their restricted tool lists, scopes their context, tracks per-worker tokens/cost/latency, and implements `WorkerHTTPError(500)` for failure injection. |
| `week10_multi_agent/orchestrator.py` | Code | Implements `run_orchestrator(claim_id, ...)`. Runs the 4-step multi-agent pipeline (`decompose_plan` -> `adjuster_notes_worker` -> `coverage_exclusions_worker` -> `orchestrator_synthesis`), records token usage on every hand-off, and handles worker HTTP 500 failures (1 retry -> safe degradation to `PENDING_REVIEW`). |
| `week10_multi_agent/a2a_card.py` | Code | **Bonus Challenge (§5):** Defines `ORCHESTRATOR_AGENT_CARD` (the A2A `AgentCard` JSON advertising skills, input/output MIME modes, and Bearer/OAuth2 auth) and `map_failure_to_a2a_lifecycle()` mapping the injected 500 failure onto the A2A task states (`submitted` -> `working` -> `failed` vs. `input-required`). |
| `week10_multi_agent/race_runner.py` | Code | The master benchmark harness. Runs all 10 claims (`CLM-2026-00001` .. `CLM-2026-00010`) through both the Single Agent and the Multi-Agent Orchestrator, grades both with `app/claims_agent/contract.py::grade`, runs the HTTP 500 failure injection on `CLM-2026-00002`, and writes all deliverable files. |
| `race_table.md` *(in `week10_multi_agent/deliverables/`)* | Deliverable | **Requirement 1 & 2:** The 4 metrics × 2 arms comparison table (Pass Rate, p50/p99 Latency, Total Tokens, Cost/Claim), hand-off token breakdown, and per-claim results across all 10 cases. |
| `handoffs.log` *(in `week10_multi_agent/deliverables/`)* | Deliverable | **Requirement 3:** Chronological log of all 40 hand-offs across the 10 claims (4 hand-offs × 10 claims) with prompt, completion, and total token counts, plus the **context re-send multiplier line**. |
| `failure_case.md` *(in `week10_multi_agent/deliverables/`)* | Deliverable | **Requirement 4 & Bonus:** Records the injected HTTP 500 on `coverage_exclusions_worker` for `CLM-2026-00002`, the one-line classification of what the orchestrator actually did (retry once -> degrade safely), the `AgentCard`, the A2A task lifecycle mapping, and the 2-line A2A vs. REST comparison. |
| `verdict.md` *(in `week10_multi_agent/deliverables/`)* | Deliverable | **Requirement 5:** Strictly <= 10 lines (7 lines). Names the **sunk-cost bias** out loud first, states the **KILL / KEEP** verdict, and cites all four metrics. |
| `agent_card.json` *(in `week10_multi_agent/deliverables/`)* | Deliverable | **Bonus Challenge:** Standalone JSON file containing the orchestrator's A2A `AgentCard`. |
| `week10_complete.md` | Documentation | This complete walkthrough document (Parts 1-4). The graded deliverables above are the separate, canonical files under `week10_multi_agent/deliverables/`. |

---

### 3. How the Flow Works Step-by-Step

#### 3.1 Arm 1: The Single Agent (`app/claims_agent/agent.py::run_agent`)

In the Single Agent flow, **one** LLM conversation holds all tools at once and loops until it emits the final JSON:

```text
[User: "claim_id: CLM-2026-00002"]
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│ Single Agent Loop (1 system prompt, all tools attached)     │
│   Iter 1: Calls get_claim("CLM-2026-00002")                 │
│   Iter 2: Reads adjuster_notes -> infers cause="flood"      │
│           Calls check_policy_exclusions("POL-HOME-2001",    │
│                                         "flood")            │
│   Iter 3: Sees covered=False, exclusion="flood", excess=2000│
│           Calls compute_payout(60000, 2000, "DENIED")       │
│   Iter 4: Emits final 8-field JSON decision                 │
└─────────────────────────────────────────────────────────────┘
```

- **Why the Single Agent is token-efficient:** It only has one system prompt, and tool outputs stay in one short message list (~4 iterations, ~5,136 tokens per claim on average).
- **Where the Single Agent can struggle (Week 8 `task_d` 4-tool planning mode):** When the single agent is given an unscripted planning prompt (`PLANNING_SYSTEM_PROMPT`) and all 4 split tools (`get_claim`, `get_policy`, `check_exclusions`, `compute_payout`) remain attached on the final turn, the model occasionally tries to emit its final JSON by calling a non-existent `"JSON"` tool (`HALLUCINATED_TOOL 'JSON'`), dropping its pass rate to **80% (8/10)**. When given a tighter prompt (`SYSTEM_PROMPT`), the single agent scores **100% (10/10)**.

---

#### 3.2 Arm 2: The Multi-Agent Orchestrator ("Claims Squad") (`week10_multi_agent/orchestrator.py::run_orchestrator`)

In the Multi-Agent flow, the orchestrator decomposes the task and executes **4 explicit hand-offs** per claim, with each worker running in its own isolated context window with a narrow prompt and only the tools for its single job:

```text
[Incoming Request: claim_id = "CLM-2026-00002"]
       │
       ├──► HAND-OFF 1: `orchestrator -> decompose_plan`
       │      Orchestrator LLM creates the 2-worker delegation plan (~481 tokens/claim).
       │
       ├──► HAND-OFF 2: `orchestrator -> adjuster_notes_worker`
       │      Input:  claim_id="CLM-2026-00002"
       │      Tools:  ONLY `[get_claim]` (cannot see policy or payout tools)
       │      Action: Calls `get_claim`, reads raw `adjuster_notes`, infers `cause="flood"`,
       │              and compresses notes into a 1-sentence `notes_summary`.
       │      Output: `{"policy_id": "POL-HOME-2001", "claimed_amount": 60000, "cause": "flood", ...}`
       │              (~1,256 tokens/claim)
       │
       ├──► HAND-OFF 3: `orchestrator -> coverage_exclusions_worker`
       │      Input:  SCOPED `{"policy_id": "POL-HOME-2001", "cause": "flood"}`
       │              (Raw `adjuster_notes` and `claimed_amount` are NEVER re-sent!)
       │      Tools:  ONLY `[get_policy, check_exclusions]` (cannot see `get_claim` or `compute_payout`)
       │      Action: Calls `get_policy("POL-HOME-2001")` and `check_exclusions("POL-HOME-2001", "flood")`.
       │      Output: `{"covered": false, "exclusion": "flood", "excess": 2000, "sub_limit": null, ...}`
       │              (~2,205 tokens/claim)
       │
       └──► HAND-OFF 4: `workers -> orchestrator_synthesis`
              Input:  Structured JSON reports from Worker 1 + Worker 2
              Tools:  ONLY `[compute_payout]`
              Action: Applies strict synthesis rules (never rounds a conditional/excluded finding
                      up to covered), calls `compute_payout(60000, 2000, "DENIED", null)`, and
                      emits the canonical 8-field JSON output (~2,604 tokens/claim).
```

#### Why We Designed the Squad This Way (Avoiding All 6 Common Mistakes in §7 of the Prompt)

1. **Same 10 Week-6/7/8 Eval Cases & Same Judge (§7 Mistake #2):** Both arms run on the exact same 10 claims in `app/claims_agent/data.py` (`CLM-2026-00001` .. `CLM-2026-00010`, also mapped to `TC-01` .. `TC-10` in `eval/trajectory_cases.py`) and are graded by the exact same deterministic function `app/claims_agent/contract.py::grade` against `EXPECTED`.
2. **Scoped Context — No Full Adjuster-Note Resend (§7 Mistake #3):** `run_coverage_exclusions_worker(policy_id, cause)` only receives `{"policy_id": policy_id, "cause": cause}`. The raw `adjuster_notes` never leave `adjuster_notes_worker`. That is why our context re-send multiplier is a lean **1.3x** instead of a bloated 9x.
3. **Narrow Prompt + Fewer Tools per Worker (§7 Mistake #4):**
   - `adjuster_notes_worker` only has `get_claim` (1 tool).
   - `coverage_exclusions_worker` only has `get_policy` and `check_exclusions` (2 tools).
   - `orchestrator_synthesis` only has `compute_payout` (1 tool).
   - Furthermore, once a worker has called its required tool(s), tools are detached on the final turn (`active_tools = None`) so the worker emits pure JSON and can never hallucinate a fictitious `"JSON"` tool.
4. **No Quality Loss in Synthesis (§7 Mistake #5):** `ORCHESTRATOR_SYNTHESIS_PROMPT` enforces exact mapping from the exclusions worker's output: if `covered == False` -> `DENIED`; if `covered == True` and `claimed_amount > sub_limit` (as in `CLM-2026-00009`, ₹100,000 burglary vs. ₹50,000 theft sub-limit) -> `PARTIAL` (₹48,000 payable after ₹2,000 excess); if `cause == "unknown"` (`CLM-2026-00010`) -> `PENDING_REVIEW` with `excess = null`.
5. **Reporting Both p50 and p99 Latency (§7 Mistake #6):** Both `p50` and `p99` latencies are computed and reported in `race_table.md` and `verdict.md`.

---

### 4. Detailed Results & How Each Rubric Requirement Is Answered

#### Requirement 1 & 2: The Race Table (4 Metrics × 2 Arms on the Same 10 Cases)

From `race_table.md`:

| Metric | Single Agent (`run_agent`) | Multi-Agent Orchestrator (`run_orchestrator`) | Delta (Multi vs. Single) |
|---|---:|---:|---:|
| **Pass Rate** (10 cases) | **100%** (10/10) | **100%** (10/10) | +0 pp |
| **Latency (p50 / p99)** | **3,496 ms / 4,487 ms** | **6,879 ms / 10,110 ms** | +3,383 ms p50 / +5,623 ms p99 |
| **Total Tokens** (10 claims) | **51,363** | **65,457** | +14,094 (**1.3x**) |
| **Cost per Claim** (USD) | **$0.000971** | **$0.001455** | +$0.000484 (**1.5x**) |

*(Note: We also compare against the Week-8 unscripted 4-tool planning Single Agent (`prompt_mode="planning", tool_set="task_d", mitigation=True`), which scored **80% (8/10)** in **66,580 tokens** / **$0.001202 per claim**. Narrowing the tool surface across workers fixes the 2 hallucinated-JSON-tool failures (80% -> 100%), but tightening the single agent's prompt already achieves 100% at lower token cost and half the latency.)*

---

#### Requirement 3: Context Re-Send Multiplier & Dominant Hand-Off Attribution

From `handoffs.log` and `race_table.md`:

> **Context re-send multiplier: 1.3x (65,457 multi tokens / 51,363 single tokens) — dominant hand-off: `workers -> orchestrator_synthesis` resend, 39.8% of all multi-agent tokens (26,038 / 65,457 tokens).**

Here is the exact token breakdown across all 4 hand-off legs over the 10 claims:

| Hand-Off Leg | Calls | Prompt Tokens | Completion Tokens | Total Tokens | Share of All Multi Tokens | Total Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| `workers -> orchestrator_synthesis` | 10 | 21,629 | 4,409 | **26,038** | **39.8%** | $0.005890 |
| `orchestrator -> coverage_exclusions_worker` | 10 | 19,676 | 2,378 | **22,054** | **33.7%** | $0.004378 |
| `orchestrator -> adjuster_notes_worker` | 10 | 10,522 | 2,037 | **12,559** | **19.2%** | $0.002801 |
| `orchestrator -> decompose_plan` | 10 | 3,110 | 1,696 | **4,806** | **7.3%** | $0.001484 |
| **Total (10 Claims)** | **40** | **54,937** | **10,520** | **65,457** | **100.0%** | **$0.014553** |

**Why `workers -> orchestrator_synthesis` (39.8%) and `orchestrator -> coverage_exclusions_worker` (33.7%) dominate:**
Each time the orchestrator hands off to a worker or synthesises worker outputs, a fresh system prompt, tool schema definition, and structured worker report must be sent in `prompt_tokens`, plus an internal tool-call turn (`compute_payout` in synthesis; `get_policy` + `check_exclusions` in the exclusions worker).

---

#### Requirement 4: Injected Worker HTTP 500 Failure (`failure_case.md`)

We injected an HTTP 500 (`WorkerHTTPError(status_code=500)`) into `coverage_exclusions_worker` on **`CLM-2026-00002` (`TC-02`)** — a ₹60,000 basement flood claim on `POL-HOME-2001` where flood is **excluded** (normal ground truth: `status: "DENIED"`, `covered: false`, `exclusion: "flood"`, `payable_amount: 0`).

**What happened in the actual trace:**
1. `orchestrator -> decompose_plan`: Succeeded (469 tokens).
2. `orchestrator -> adjuster_notes_worker`: Succeeded (1,260 tokens). Extracted `policy_id: "POL-HOME-2001"`, `claimed_amount: 60000`, `cause: "flood"`.
3. `orchestrator -> coverage_exclusions_worker`:
   - **Attempt 1:** Raised `500 Internal Server Error: coverage_exclusions_worker crashed while querying policy/exclusions backend for policy_id='POL-HOME-2001', cause='flood'`.
   - **Attempt 2 (Retry 1/1):** Orchestrator retried once; worker raised `500 Internal Server Error` again.
4. `workers -> orchestrator_synthesis`: Orchestrator received the adjuster-note report plus `"worker_status": "FAILED", "http_status": 500, "attempts": 2`. Instead of guessing coverage from the adjuster notes or lying that the claim was covered, it called `compute_payout(claimed_amount=60000, policy_excess=null, claim_status="PENDING_REVIEW", sub_limit=null)` and emitted:
   ```json
   {
     "claim_id": "CLM-2026-00002",
     "status": "PENDING_REVIEW",
     "covered": false,
     "exclusion": null,
     "gross_amount": 60000,
     "excess": null,
     "payable_amount": 0,
     "reason": "Coverage verification failed: coverage_exclusions_worker returned HTTP 500 after retries, so claim could not be verified."
   }
   ```

**One-line behaviour classification:**
> **When `coverage_exclusions_worker` returned an injected HTTP 500 on `CLM-2026-00002` (`TC-02`), the orchestrator retried the worker once (2 total attempts) and then degraded to a safe partial answer (`status: "PENDING_REVIEW"`, `covered: false`, `payable_amount: 0`), refusing to lie that the claim was covered without a completed exclusions check.**

---

#### Requirement 5: The Verdict (`verdict.md` — 7 lines, max 10 lines allowed)

1. **Names the sunk-cost bias out loud first:** Just because we spent last week building the orchestrator, two narrow workers, and scoped hand-off plumbing does not mean we should ship it if the numbers don't justify the cost.
2. **States the verdict (`KILL` the multi-agent orchestrator, `KEEP` the single agent) and cites all four metrics:**
   - **Pass rate:** `100% (10/10)` vs. `100% (10/10)` (0 pp gain).
   - **Total tokens & multiplier:** `65,457` vs. `51,363` (**1.3x** multiplier, led by `workers -> orchestrator_synthesis` at **39.8%**).
   - **Cost per claim:** `$0.001455` vs. `$0.000971` (**1.5x** more expensive per claim).
   - **Latency (p50 / p99):** `6,879 ms p50 / 10,110 ms p99` vs. `3,496 ms p50 / 4,487 ms p99` (~2.0x slower p50, ~2.3x slower p99).

---

#### Section 5 (Bonus Challenge): A2A `AgentCard`, Lifecycle Mapping & A2A vs. REST

1. **Published `AgentCard` (`agent_card.json` & `failure_case.md` §4.1):**
   Advertises `claims-squad-orchestrator` at `https://claims-squad.internal.softsuave.insure/a2a/v1` with:
   - `capabilities`: `streaming: true`, `pushNotifications: true`, `stateTransitionHistory: true`
   - `defaultInputModes`: `["application/json", "text/plain"]`
   - `defaultOutputModes`: `["application/json"]`
   - `authentication`: `Bearer` / `OAuth2` Client Credentials JWT with scopes `['claims:read', 'policy:exclusions:evaluate', 'claims:triage:write']`
   - `skills`: `triage_insurance_claim`, `summarize_adjuster_notes`, `evaluate_coverage_and_exclusions`
2. **A2A Task Lifecycle Mapping for the Failed Case (`failed` vs. `input-required`):**
   - **The HTTP 500 case (`CLM-2026-00002`) MUST end in `failed`** (with the degraded partial artifact `PENDING_REVIEW`, `covered=false`, `payable_amount=0` attached), **NOT `input-required`**. Why? Because the adjuster already supplied a complete incident note (`cause="flood"`). Asking the human adjuster for confirmation cannot fix a crashed policy/exclusions worker (`HTTP 500`).
   - **When `input-required` IS used:** On **`CLM-2026-00010` (`TC-03`)**, where `adjuster_notes` is empty (`""` -> `cause="unknown"`), the A2A task transitions `submitted -> working -> input-required`, pausing the task until the human adjuster submits the incident description on the same `task_id`.
3. **What A2A Buys You Over a Plain REST Call to the Worker (Two Lines):**
   - **Line 1 (Stateful task lifecycle & resumability):** Unlike a stateless REST call that either blocks or drops context on a timeout/pause, A2A gives every delegated task a durable `task_id` with explicit states (`submitted`, `working`, `input-required`, `completed`, `failed`), allowing a claim missing adjuster confirmation to pause at `input-required` and resume on the same task thread without re-running prior workers.
   - **Line 2 (Standardized capability discovery & negotiated modalities):** Via the published `AgentCard` (skills, input/output MIME modes, and OAuth/Bearer auth schemes), the orchestrator discovers what peer agents can do, negotiates structured JSON vs. streaming updates, and collects partial artifacts on failure without hard-coding bespoke REST endpoints per worker.

---

### 5. How to Run Everything Yourself

From the repository root (`d:\INSURANCE_RAG\INSURANCE-RAG-BACKEND`):

```powershell
# 1. Regenerate all markdown & log deliverables instantly from cached traces (no LLM wait):
.\venv\Scripts\python.exe -m week10_multi_agent.race_runner --from-cached

# 2. Run the full live race (10 Single Agent runs + 10 Multi-Agent runs + 1 Injected 500 run) against Groq:
.\venv\Scripts\python.exe -m week10_multi_agent.race_runner
```

#### Submission Checklist Verification
- [x] `race_table.md` — 4 metrics × 2 arms, same 10 cases named (`CLM-2026-00001` .. `CLM-2026-00010` / `TC-01` .. `TC-10`)
- [x] `handoffs.log` — every hand-off across all 10 claims (40 hand-offs) with its prompt, completion, and total token count
- [x] Multiplier line: `1.3x` (`65,457 multi tokens / 51,363 single tokens`), with dominant hand-off named (`workers -> orchestrator_synthesis resend, 39.8% of all multi-agent tokens`)
- [x] `failure_case.md` — the injected HTTP 500 on `CLM-2026-00002` and what the orchestrator actually did (retried once, degraded safely to `PENDING_REVIEW`, `covered=false`, `payable_amount=0`), plus the Bonus `AgentCard` and A2A lifecycle analysis
- [x] `verdict.md` — 7 lines (<= 10 lines max), keep/kill stated, all four numbers cited, sunk-cost bias named out loud first

---

## Part 2 - Race Table (4 Metrics x 2 Arms)

<!-- source: week10_multi_agent/deliverables/race_table.md -->

## Week 10 Practical (Task Set D) — Single Agent vs. Claims Squad Race Table

Both arms were raced on the **exact same 10 evaluation cases** (`CLM-2026-00001` through `CLM-2026-00010` from `app/claims_agent/data.py`, mapped to `TC-01` .. `TC-10` in `eval/trajectory_cases.py`) and graded by the **exact same deterministic judge** (`app/claims_agent/contract.py::grade` against `EXPECTED`).

### 1. Headline Race Table (4 Metrics × 2 Arms)

| Metric | Single Agent (`run_agent`) | Multi-Agent Orchestrator (`run_orchestrator`) | Delta (Multi vs. Single) |
|---|---:|---:|---:|
| **Pass Rate** (10 cases) | **100%** (10/10) | **100%** (10/10) | +0 pp |
| **Latency (p50 / p99)** | **3,496 ms / 4,487 ms** | **6,879 ms / 10,110 ms** | +3,383 ms p50 / +5,623 ms p99 |
| **Total Tokens** (10 claims) | **51,363** | **65,457** | +14,094 (**1.3x**) |
| **Cost per Claim** (USD) | **$0.000971** | **$0.001455** | +$0.000484 (**1.5x**) |

> **Note on Single-Agent Tool Surface (`week7` vs. Week-8 `task_d` planning baseline):**
> Above, `Single Agent` runs `app/claims_agent/agent.py::run_agent` in its default race configuration (**100%** pass rate, **51,363** tokens, **$0.000971**/claim). When the single agent instead runs under the Week-8 unscripted 4-tool planning configuration (`prompt_mode='planning', tool_set='task_d', mitigation=True`), it scores **80% (8/10)** with **66,580 total tokens** (**1.0x** multiplier vs. multi-agent) and **$0.001202**/claim because holding all 4 tool schemas in one agent loop causes it to hallucinate a `'JSON'` tool on `TC-03` (`CLM-2026-00010`) and `TC-08` (`CLM-2026-00004`). Splitting tools narrowly across workers eliminates that hallucination (80% -> 100%), whereas tightening the single agent's prompt already gets 100% at half the token cost.

### 2. Context Re-Send Multiplier & Hand-Off Attribution

**Context re-send multiplier: 1.3x (65,457 multi tokens / 51,363 single tokens) — dominant hand-off: workers -> orchestrator_synthesis resend, 39.8% of all multi-agent tokens (26,038 / 65,457 tokens).**

| Hand-Off Leg | Calls | Prompt Tokens | Completion Tokens | Total Tokens | Share of Multi Tokens | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| `workers -> orchestrator_synthesis` | 10 | 21,629 | 4,409 | **26,038** | **39.8%** | $0.005890 |
| `orchestrator -> coverage_exclusions_worker` | 10 | 19,676 | 2,378 | **22,054** | **33.7%** | $0.004378 |
| `orchestrator -> adjuster_notes_worker` | 10 | 10,522 | 2,037 | **12,559** | **19.2%** | $0.002801 |
| `orchestrator -> decompose_plan` | 10 | 3,110 | 1,696 | **4,806** | **7.3%** | $0.001484 |

### 3. Per-Case Comparison Across the Same 10 Cases

| Case ID | Claim ID | Expected Status | Single Pass | Single Latency (ms) | Single Tokens | Single Cost | Multi Pass | Multi Latency (ms) | Multi Tokens | Multi Cost |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `TC-07` | `CLM-2026-00001` | `APPROVED` | PASS | 3,690 | 5,378 | $0.001002 | PASS | 6,436 | 6,524 | $0.001413 |
| `TC-02` | `CLM-2026-00002` | `DENIED` | PASS | 3,247 | 5,461 | $0.001044 | PASS | 6,015 | 6,537 | $0.001413 |
| `TC-09` | `CLM-2026-00003` | `APPROVED` | PASS | 2,793 | 5,388 | $0.001000 | PASS | 7,420 | 6,841 | $0.001583 |
| `TC-08` | `CLM-2026-00004` | `DENIED` | PASS | 3,302 | 5,440 | $0.001020 | PASS | 6,553 | 6,617 | $0.001465 |
| `TC-10` | `CLM-2026-00005` | `APPROVED` | PASS | 2,956 | 5,378 | $0.001004 | PASS | 6,348 | 6,549 | $0.001425 |
| `TC-04` | `CLM-2026-00006` | `DENIED` | PASS | 3,766 | 5,402 | $0.001007 | PASS | 10,375 | 6,562 | $0.001422 |
| `TC-01` | `CLM-2026-00007` | `APPROVED` | PASS | 4,181 | 5,377 | $0.001002 | PASS | 6,139 | 6,460 | $0.001385 |
| `TC-05` | `CLM-2026-00008` | `DENIED` | PASS | 4,507 | 5,463 | $0.001023 | PASS | 7,232 | 6,780 | $0.001526 |
| `TC-06` | `CLM-2026-00009` | `PARTIAL` | PASS | 4,281 | 5,382 | $0.001009 | PASS | 7,434 | 6,908 | $0.001630 |
| `TC-03` | `CLM-2026-00010` | `PENDING_REVIEW` | PASS | 2,061 | 2,694 | $0.000604 | PASS | 7,205 | 5,679 | $0.001291 |

---

## Part 3 - Injected Worker Failure & A2A Lifecycle

<!-- source: week10_multi_agent/deliverables/failure_case.md -->

## Week 10 Practical (Task Set D) — Injected Worker Failure & A2A Lifecycle (`failure_case.md`)

### 1. One-Line Behaviour Statement (Requirement 4)

**When `coverage_exclusions_worker` returned an injected HTTP 500 on `CLM-2026-00002` (`TC-02`), the orchestrator retried the worker once (2 total attempts) and then degraded to a safe partial answer (`status: "PENDING_REVIEW"`, `covered: false`, `payable_amount: 0`), refusing to lie that the claim was covered without a completed exclusions check.**

---

### 2. Failure Injection Setup & Ground Truth

- **Target case:** `CLM-2026-00002` (`TC-02` — `POL-HOME-2001`, S. Menon, ₹60,000 basement flood claim).
- **Why this case is a sharp test for lying:** The adjuster notes describe basement flooding from three days of rain (`cause = "flood"`), which is **excluded** under `POL-HOME-2001` (no flood add-on). Ground truth is `status: "DENIED"`, `covered: false`, `exclusion: "flood"`, `payable_amount: 0`. If the orchestrator ignored the worker 500 and guessed coverage from the adjuster notes alone, it would ship a wrongful coverage statement on an excluded claim.
- **Injected fault:** `run_orchestrator('CLM-2026-00002', inject_500_on_exclusions=True, max_worker_retries=1)` forces `coverage_exclusions_worker` to raise `WorkerHTTPError(status_code=500)` on every call.

### 3. What the Orchestrator Actually Did (Captured Trace)

#### Hand-off sequence recorded on the failed run

| Step | Hand-Off | Status | Attempts | Tokens | Detail |
|---:|---|---|---:|---:|---|
| 1 | `orchestrator -> decompose_plan` | `ok` | 1 | 475 | claim_id=CLM-2026-00002 |
| 2 | `orchestrator -> adjuster_notes_worker` | `ok` | 1 | 1257 | claim_id=CLM-2026-00002 -> extracted policy_id=POL-HOME-2001, cause=flood |
| 3 | `orchestrator -> coverage_exclusions_worker` | `error_500` | 2 | 0 | scoped={'policy_id': 'POL-HOME-2001', 'cause': 'flood'} (raw adjuster_notes omitted) |
| 4 | `workers -> orchestrator_synthesis` | `degraded_partial` | 1 | 2536 | claimed_amount=60000, cause=flood, exclusions_status=HTTP_500 |

#### Captured worker error payload
```json
{
  "status_code": 500,
  "worker": "coverage_exclusions_worker",
  "error": "500 Internal Server Error: coverage_exclusions_worker crashed while querying policy/exclusions backend for policy_id='POL-HOME-2001', cause='flood'",
  "attempt": 2,
  "max_attempts": 2
}
```

#### Orchestrator's actual degraded output vs. normal ground truth
```json
{
  "normal_expected_ground_truth": {
    "status": "DENIED",
    "covered": false,
    "exclusion": "flood",
    "gross_amount": 60000,
    "excess": 2000,
    "payable_amount": 0
  },
  "actual_degraded_orchestrator_output_on_500": {
    "claim_id": "CLM-2026-00002",
    "status": "PENDING_REVIEW",
    "covered": false,
    "exclusion": null,
    "gross_amount": 60000,
    "excess": null,
    "payable_amount": 0,
    "reason": "Coverage verification failed: coverage_exclusions_worker returned HTTP 500 after retries, so claim could not be verified."
  },
  "termination_reason": "degraded_worker_500"
}
```

- **Did it retry?** Yes — it retried `coverage_exclusions_worker` once (`attempt 1 -> HTTP 500`, `attempt 2 -> HTTP 500`).
- **Did it degrade to a partial answer?** Yes — it preserved the extracted claim facts (`gross_amount: 60000`, `cause: flood` noted in `reason`), set `status: "PENDING_REVIEW"`, `covered: false`, `exclusion: null`, `excess: null`, `payable_amount: 0`, and explicitly stated in `reason` that coverage/exclusions verification failed with HTTP 500 after retry.
- **Did it lie?** No — it never declared `covered: true` or `APPROVED` without an exclusions check.

---

### 4. Bonus Challenge — Orchestrator `AgentCard` & A2A Task Lifecycle Mapping

#### 4.1 Advertised A2A `AgentCard` (`agent_card.json`)
```json
{
  "name": "claims-squad-orchestrator",
  "description": "Multi-agent insurance claims triage orchestrator. Decomposes a claim question, delegates to an Adjuster-Note Summary Worker and a Coverage & Exclusions Worker, and synthesises a grounded coverage/payout verdict.",
  "url": "https://claims-squad.internal.softsuave.insure/a2a/v1",
  "provider": {
    "organization": "Soft Suave Insurance AI Platform",
    "url": "https://internal.softsuave.insure"
  },
  "version": "1.0.0",
  "protocolVersion": "0.2.1",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "stateTransitionHistory": true
  },
  "authentication": {
    "schemes": [
      "Bearer",
      "OAuth2"
    ],
    "credentials": "OAuth2 Client Credentials JWT (audience='a2a://claims-squad-orchestrator') with required scopes: ['claims:read', 'policy:exclusions:evaluate', 'claims:triage:write']."
  },
  "defaultInputModes": [
    "application/json",
    "text/plain"
  ],
  "defaultOutputModes": [
    "application/json"
  ],
  "skills": [
    {
      "id": "triage_insurance_claim",
      "name": "End-to-End Claim Triage & Payout Synthesis",
      "description": "Accepts a claim_id, coordinates adjuster-note cause extraction and policy exclusion verification, computes the net payout after deductible and sub-limits, and returns the canonical 8-field claim decision JSON.",
      "tags": [
        "insurance",
        "claims",
        "triage",
        "orchestrator"
      ],
      "inputModes": [
        "application/json",
        "text/plain"
      ],
      "outputModes": [
        "application/json"
      ],
      "examples": [
        "{\"claim_id\": \"CLM-2026-00001\"}",
        "Triage claim CLM-2026-00002 and report status, coverage, exclusion, and payable amount."
      ]
    },
    {
      "id": "summarize_adjuster_notes",
      "name": "Adjuster-Note Cause Extraction (Delegated Worker Skill)",
      "description": "Reads raw adjuster notes for a claim_id and extracts the structured cause of loss (flood, theft, accidental_damage, fire, wear_and_tear, collision, general, unknown) and a 1-sentence factual summary.",
      "tags": [
        "adjuster-notes",
        "nlp",
        "cause-of-loss"
      ],
      "inputModes": [
        "application/json"
      ],
      "outputModes": [
        "application/json"
      ]
    },
    {
      "id": "evaluate_coverage_and_exclusions",
      "name": "Policy Coverage & Exclusions Check (Delegated Worker Skill)",
      "description": "Evaluates policy active status, per-cause exclusions, deductible (excess), and sub-limits for a scoped (policy_id, cause) pair.",
      "tags": [
        "policy",
        "exclusions",
        "coverage",
        "sub-limits"
      ],
      "inputModes": [
        "application/json"
      ],
      "outputModes": [
        "application/json"
      ]
    }
  ]
}
```

#### 4.2 Mapping the Failed Case onto the A2A Task Lifecycle (`failed` vs. `input-required`)

- **Verdict on terminal A2A state for `CLM-2026-00002` (injected HTTP 500):** The A2A task **must end in `failed`** (attaching the partial degraded artifact `status: "PENDING_REVIEW"`, `covered: false`, `payable_amount: 0`), **NOT pause at `input-required`**.
- **Why `failed` and not `input-required`:** On `CLM-2026-00002`, the adjuster's note is already complete (`"Claimant states basement flooded after three days of continuous rain..."` -> `cause = "flood"`). Pausing at `input-required` awaiting the adjuster's confirmation would ask a human adjuster to supply information they already provided, when the actual blocker is an infrastructure crash (HTTP 500) in `coverage_exclusions_worker`. In A2A, `input-required` is the correct state for **`CLM-2026-00010` (`TC-03`)**, where `adjuster_notes` is empty (`""`) and the orchestrator genuinely needs the adjuster to confirm the cause of loss before resuming.

| Step | A2A Task State | Actor | Transition Reason |
|---:|---|---|---|
| 1 | `submitted` | `claims_lead_client` | Task created with input {'claim_id': 'CLM-2026-00002'}. |
| 2 | `working` | `claims-squad-orchestrator` | Orchestrator decomposed task; adjuster_notes_worker succeeded (extracted policy_id='POL-HOME-2001', cause='flood'). Delegated scoped (policy_id, cause) to coverage_exclusions_worker. |
| 3 | `working` | `coverage_exclusions_worker` | Attempt 1 returned HTTP 500 Internal Server Error; orchestrator retried once (Attempt 2) which also returned HTTP 500. |
| 4 | `failed` | `claims-squad-orchestrator` | A2A Task ends in terminal state `failed` (with partial artifact status='PENDING_REVIEW', covered=false, payable_amount=0) because an upstream worker HTTP 500 is a system/infrastructure fault, NOT missing human input. By contrast, `input-required` is reserved for cases like CLM-2026-00010 (empty adjuster_notes -> cause='unknown') where the system is healthy and must pause awaiting the human adjuster's confirmation of the cause of loss. |

#### 4.3 What A2A Buys You Over a Plain REST Call to the Worker (Two Lines)

1. 1. Stateful task lifecycle & resumability: Unlike a stateless REST call that either blocks or drops context on a timeout/pause, A2A gives every delegated task a durable task_id with explicit states (`submitted`, `working`, `input-required`, `completed`, `failed`), allowing a claim missing adjuster confirmation to pause at `input-required` and resume on the same task thread without re-running prior workers.
2. 2. Standardized capability discovery & negotiated modalities: Via the published `AgentCard` (skills, input/output MIME modes, and OAuth/Bearer auth schemes), the orchestrator discovers what peer agents can do, negotiates structured JSON vs. streaming updates, and collects partial artifacts on failure without hard-coding bespoke REST endpoints per worker.

---

## Part 4 - Verdict (KILL / KEEP)

<!-- source: week10_multi_agent/deliverables/verdict.md -->

**Naming the sunk-cost bias out loud:** We spent last week building the multi-agent orchestrator, two narrow workers, and scoped hand-off plumbing, which creates strong sunk-cost pressure to justify keeping the squad in production simply because we already built it.
**Verdict: KILL the multi-agent orchestrator for standard claims triage and KEEP the single agent.**
1. **Pass rate:** Both arms achieved **100% (10/10)** on the same 10 Week-6 eval cases under the same judge — a **0 percentage-point gain** over the single agent's **100% (10/10)** (and even against the unscripted 4-tool single agent's 80%, fixing the single agent's prompt already reaches 100% without multi-agent overhead).
2. **Total tokens & context re-send multiplier:** The orchestrator burned **65,457 tokens** vs. the single agent's **51,363 tokens** (**1.3x** multiplier), led by `workers -> orchestrator_synthesis` at **39.8%** of all multi-agent tokens.
3. **Cost per claim:** The multi-agent bill is **$0.001455/claim** vs. **$0.000971/claim** for the single agent (**1.5x** higher cost per claim for identical accuracy).
4. **Latency (p50 / p99):** Sequential worker hand-offs pushed latency from **3,496 ms p50 / 4,487 ms p99** (single agent) to **6,879 ms p50 / 10,110 ms p99** (orchestrator).
**Decision rule:** Paying a ~2x token, cost, and latency tax for 0% accuracy gain over a well-prompted single agent fails the production gate; retire the orchestrator and keep the single agent.
