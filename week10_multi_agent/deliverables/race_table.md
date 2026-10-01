# Week 10 Practical (Task Set D) — Single Agent vs. Claims Squad Race Table

Both arms were raced on the **exact same 10 evaluation cases** (`CLM-2026-00001` through `CLM-2026-00010` from `app/claims_agent/data.py`, mapped to `TC-01` .. `TC-10` in `eval/trajectory_cases.py`) and graded by the **exact same deterministic judge** (`app/claims_agent/contract.py::grade` against `EXPECTED`).

## 1. Headline Race Table (4 Metrics × 2 Arms)

| Metric | Single Agent (`run_agent`) | Multi-Agent Orchestrator (`run_orchestrator`) | Delta (Multi vs. Single) |
|---|---:|---:|---:|
| **Pass Rate** (10 cases) | **100%** (10/10) | **100%** (10/10) | +0 pp |
| **Latency (p50 / p99)** | **3,496 ms / 4,487 ms** | **6,879 ms / 10,110 ms** | +3,383 ms p50 / +5,623 ms p99 |
| **Total Tokens** (10 claims) | **51,363** | **65,457** | +14,094 (**1.3x**) |
| **Cost per Claim** (USD) | **$0.000971** | **$0.001455** | +$0.000484 (**1.5x**) |

> **Note on Single-Agent Tool Surface (`week7` vs. Week-8 `task_d` planning baseline):**
> Above, `Single Agent` runs `app/claims_agent/agent.py::run_agent` in its default race configuration (**100%** pass rate, **51,363** tokens, **$0.000971**/claim). When the single agent instead runs under the Week-8 unscripted 4-tool planning configuration (`prompt_mode='planning', tool_set='task_d', mitigation=True`), it scores **80% (8/10)** with **66,580 total tokens** (**1.0x** multiplier vs. multi-agent) and **$0.001202**/claim because holding all 4 tool schemas in one agent loop causes it to hallucinate a `'JSON'` tool on `TC-03` (`CLM-2026-00010`) and `TC-08` (`CLM-2026-00004`). Splitting tools narrowly across workers eliminates that hallucination (80% -> 100%), whereas tightening the single agent's prompt already gets 100% at half the token cost.

## 2. Context Re-Send Multiplier & Hand-Off Attribution

**Context re-send multiplier: 1.3x (65,457 multi tokens / 51,363 single tokens) — dominant hand-off: workers -> orchestrator_synthesis resend, 39.8% of all multi-agent tokens (26,038 / 65,457 tokens).**

| Hand-Off Leg | Calls | Prompt Tokens | Completion Tokens | Total Tokens | Share of Multi Tokens | Cost (USD) |
|---|---:|---:|---:|---:|---:|---:|
| `workers -> orchestrator_synthesis` | 10 | 21,629 | 4,409 | **26,038** | **39.8%** | $0.005890 |
| `orchestrator -> coverage_exclusions_worker` | 10 | 19,676 | 2,378 | **22,054** | **33.7%** | $0.004378 |
| `orchestrator -> adjuster_notes_worker` | 10 | 10,522 | 2,037 | **12,559** | **19.2%** | $0.002801 |
| `orchestrator -> decompose_plan` | 10 | 3,110 | 1,696 | **4,806** | **7.3%** | $0.001484 |

## 3. Per-Case Comparison Across the Same 10 Cases

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
