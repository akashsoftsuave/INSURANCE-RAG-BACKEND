# Drill — "it told a claimant something was covered when the policy excludes it"

| | Drill 1 (required) | Drill 2 (bonus) |
|---|---|---|
| **Time-to-find** | **00:18** | **00:19** |
| **Slice that found it** | **time** (`find --since 24h`, then read all 29 lines) | **output verdict** (`find --since 24h --contradictions`) |
| Found trace | `26b1fd68-3b77-4220-b363-4a6fdbd4e8b1` | `4c81eb88-57f0-4ef2-87c2-57e57f8c5209` |
| Log schema | `week11-log-v1` (as first built) | `week11-log-v2` (+ output index) |
| Lines in the window | 29 | 67 (29 v1 + 38 v2) |
| Lines the slice returned | 29 (no usable narrowing slice) | **5** |
| Planted by | Claude main session acting as squadmate (plant spec kept outside the repo) | same |
| Timed by | `logq drill start/stop` (machine clock, `week11_observability/logs/drill_runs.jsonl`) | same |
| Finder | a **blind** sub-agent with no knowledge of the plant, limited to `logq` commands only | a fresh blind sub-agent, same rules |

> **Honesty note.** Nobody on the squad was available in this session, so the "squadmate" was the main session and the finder was a blind sub-agent. Before submitting, re-run the drill with a named human squadmate: `logq drill start` → find → `logq drill stop --found <id> --slice <slice>`.

## Drill 1 — what the finder did (from its report)

```
logq drill start --label drill-1                              17:21:25Z
logq find --since 24h --contradictions     -> 0 lines  "!! field 'contradiction_flag' is ABSENT on 29 line(s) (schema week11-log-v1)"
logq find --since 24h --verdict covered    -> 0 lines  "!! field 'coverage_verdict' is ABSENT on 29 line(s)"
logq find --since 24h --width 200          -> 29 lines (read every answer by eye)
logq show 26b1fd68                          -> full record
logq drill stop --found 26b1fd68-... --slice time   -> time-to-find 00:18
```

Found: `17:19:11Z · ADJ-0106 · insurance-qa-v2 · coverage_question`
Q: *"The claimant's bike was stolen last night. Will ACKO pay for the theft?"*
A: *"Yes. According to the policy details, theft is covered – the insurer will pay for losses 'up to the total declared insurance value' in the event of theft of your vehicle."*

**The missing log field:** `coverage_verdict`, the output-side index. The v1 log indexed only the input (`input_type`), but the complaint describes the output ("said it was covered"). The `input_type=coverage_question` slice still left every coverage answer to read. Drill 1 came in under 5 minutes only because the window held 29 lines. At production volume the same "read everything" step is what blows the budget.

**One-hop diagnosis from the logged context ids:** the found request retrieved chunks `6, 5, 17, 2, 7`. Chunk 2 is the ACKO "What's not covered → Accident, Theft, Fire" list. **Retrieval did find the exclusions schedule. The model ignored it.** That points the fix at the prompt, not at retrieval.

## Drill 2 (bonus) — the field that was added, and the result

Added in schema `week11-log-v2` (`app/observability/request_log.py`, `app/observability/indexers.py`):

* `coverage_verdict`: covered / not_covered / partial / not_stated / n/a, a deterministic classifier over the answer.
* `exclusion_context_ids`: retrieved chunk ids that contain exclusion language.
* `contradiction_flag`: verdict is covered or partial **and** an exclusion chunk was in the context.

```
logq drill start --label drill-2                              17:42:59Z
logq find --since 24h --contradictions     -> 5 lines (warning: field absent on the 29 older v1 lines)
logq find --since 24h --verdict covered    -> 5 lines
logq stats / find --since 24h              -> 67 lines (to check the v1 lines the index can't see)
logq show 4c81eb88
logq drill stop --found 4c81eb88-... --slice output_verdict  -> time-to-find 00:19
```

Found: `17:42:10Z · ADJ-0103 · insurance-qa-v2` · *"Are flood damages to my bike covered?"* → *"Yes, flood damages to your bike are covered. The policy's 'Calamities' coverage includes…"* (`coverage_verdict=covered`, `contradiction_flag=true`, `exclusion_context_ids=[2,6,3]`).

### Did we beat our own time? **No: 00:18 → 00:19.** Why:

1. The finder is an LLM. It reads 29 lines about as fast as it reads 5, so on a log this small the index can't show up as seconds saved. What the field changed is **how many lines had to be judged: 29 (every line) → 5 (flagged)**, while the window more than doubled (67 lines). That ratio is what scales with volume.
2. The 29 old v1 lines have no index, and the finder rightly spent time checking them by eye. Indexes only cover lines written after they are added.
3. `contradiction_flag` precision was **1/5** on the v2 lines. Dental, mental health, AYUSH (ShieldCare) and third-party car damage (ACKO) were all flagged because their contexts also contain exclusion text. Next tightening: flag only when the question's subject term appears **inside** an exclusion chunk.

The first plant attempt for drill 2 (*"…Splendor caught fire… is the fire damage covered?"*, trace `aee2ce7e`) did **not** produce a bad answer: v2 said "not covered" that time. Since a plant has to be a bad answer, the squadmate re-planted with the flood question. Both lines are still in the log.

## PII

Questions in the traffic carried a claimant name, phone, email and vehicle registration. They appear in the log only as `[NAME]`, `[PHONE]`, `[EMAIL]`, `[VEHICLE_REG]` (redacted on write by `redact_pii`). A scan of `requests.jsonl` for the raw values returns nothing. Chunk text is never written to the request log, only chunk ids, pages and sections.
