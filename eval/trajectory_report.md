# Week 8 — Task Set D — Outcome-vs-Trajectory Gap

Agent under test: `app/claims_agent/agent.py::run_agent` · seed `7` · 10 fixed claims from `app/claims_agent/data.py`.

## Baseline

| Metric | Value |
|---|---|
| cases evaluated | 10 |
| outcome pass rate | 6/10 (60%) |
| trajectory pass rate | 6/10 (60%) |
| **outcome-vs-trajectory gap** | **0 percentage points** |
| tool-choice accuracy (full sequence) | 60% |
| tool-choice accuracy (step-wise) | 89% |
| argument validity rate | 34/36 (94%) |
| step efficiency (aggregate) | 0.97× |
| cost per claim p50 | $0.001083 |
| cost per claim max | $0.001256 |
| latency p50 / max | 3473 ms / 5218 ms |

### Baseline failure-mode counts

| Failure mode | Cases |
|---|---:|
| wrong_tool | 2/10 |
| invalid_argument | 2/10 |
| unnecessary_steps | 1/10 |
| loop | 0/10 |
| made_up_input | 0/10 |
| silent_give_up | 2/10 |
| skipped_required_step | 3/10 |
| other | 0/10 |

## Right answer, wrong path

Cases: . Example below.

- **Case ID:** TC-05 (claim CLM-2026-00008)
- **Expected outcome:** `{"status": "DENIED", "covered": false, "exclusion": "wear_and_tear", "gross_amount": 18000, "excess": 1000, "payable_amount": 0}`
- **Actual outcome:** `{"status": "DENIED", "covered": false, "exclusion": "wear_and_tear", "gross_amount": 18000, "excess": null, "payable_amount": 0}` — **outcome PASS**
- **Expected valid path(s):** `[['get_claim', 'get_policy', 'check_exclusions', 'compute_payout'], ['get_claim', 'check_exclusions', 'get_policy', 'compute_payout']]`
- **Actual tool sequence:** `['get_claim', 'get_policy', 'check_exclusions']` — **trajectory FAIL**
- **Failure modes:** skipped_required_step
- **Skipped required step(s):** ['compute_payout']

## Mitigation

- **Top failure mode:** `skipped_required_step` (3/10 cases)
- **Selected mitigation (exactly one):** tighter tool description — option 1 of the five allowed
- **Code changed:** app/claims_agent/tools.py -> TASK_D_MITIGATED_TOOL_SCHEMAS: the `description` string of all four tools. Names, parameter schemas and implementations are untouched (asserted at import time).
- **Toggle:** `run_agent(..., mitigation=True) selects MITIGATED_TOOL_SETS['task_d']`
- **Why:** The baseline's top failure mode was skipped_required_step: the agent answered correctly while never calling compute_payout, doing the subtraction itself. The baseline descriptions state only what each tool RETURNS — nothing marks a tool mandatory and nothing states a precondition — so from where the agent sits, skipping a step is not visibly wrong. A skipped step is a tool-selection failure, and tool selection is driven by the tool descriptions, so tightening them is the mitigation that acts on the actual cause. The four other allowed mitigations do not: a hard step limit constrains how MANY steps are taken, not which; argument validation fires only on a call that was already made, so it can never catch a call that never happened; re-planning adds a whole model round-trip; and replacing the agent with the deterministic workflow removes the agent rather than fixing it, and would make every trajectory metric trivially perfect.
- **Not done:** no argument validation, no hard step limit, no re-planning step, no swap to the deterministic workflow, and no change to the system prompt, budgets, seed or agent loop

## After mitigation

| Metric | Before | After |
|---|---|---|
| outcome pass rate | 60% | 80% |
| trajectory pass rate | 60% | 80% |
| outcome-vs-trajectory gap | 0 pp | 0 pp |
| tool-choice accuracy | 60% | 80% |
| argument validity rate | 94% | 95% |
| step efficiency | 0.97× | 1.05× |
| cost p50 | $0.001083 | $0.001336 |
| cost max | $0.001256 | $0.001369 |

## Before → After

**skipped_required_step: 3 → 0** (-3)

Mitigation price (it is not free):

- tokens per claim: +1396 p50, +1183 max
- cost per claim: +0.000253 USD p50, +0.000113 USD max
- latency per claim: +1535 ms p50, +3001 ms max

## Regression — every failure mode in the taxonomy

| Failure mode | Before | After | Delta |
|---|---:|---:|---:|
| wrong_tool | 2 | 2 | 0 |
| invalid_argument | 2 | 2 | 0 |
| unnecessary_steps | 1 | 2 | +1 |
| loop | 0 | 0 | 0 |
| made_up_input | 0 | 0 | 0 |
| silent_give_up | 2 | 2 | 0 |
| skipped_required_step | 3 | 0 | -3 |
| other | 0 | 0 | 0 |

Modes checked: all 8.
Modes that got WORSE: unnecessary_steps

## Outcome regression check

Outcome pass rate 60% → 80%. Newly broken: none.

## Remaining failure modes

- `wrong_tool`: 2/10
- `invalid_argument`: 2/10
- `unnecessary_steps`: 2/10
- `silent_give_up`: 2/10
