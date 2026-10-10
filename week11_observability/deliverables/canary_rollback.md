# Prompt version bump + canary / rollback

**Bump:** `insurance-qa-v2` → **`insurance-qa-v4`** (`app/services/llm_service.py → PROMPTS`).
`insurance-qa-v3` was a candidate that ran the suite and was **rejected** (27/32: verdicts fixed, but two Week-6 cosmetic cases and three must_mention checks regressed because answers collapsed to "No - Exclusions (page 5)"). It stays registered, unshipped, so that run can be reproduced.

| Version | Suite | Tokens/query | Cost/query | Mean latency |
|---|---|---|---|---|
| insurance-qa-v2 (stable) | 28/32 | 998 | $0.000230 | 1,529 ms |
| insurance-qa-v3 (rejected) | 27/32 | — | $0.000309 (suite) | 1,641 ms (suite) |
| insurance-qa-v4 (canary) | **32/32** | 1,413 | $0.000335 | 2,235 ms |

(v2/v4 tokens, cost and latency come from the request log sliced by `prompt_version`; v3 numbers come from its suite run.)

**Canary:** ship v4 to 10% of adjusters (`PROMPT_CANARY_VERSION=insurance-qa-v4`, `PROMPT_CANARY_PCT=10`, sticky per `user_id` hash) for 24 h. Promote (`PROMPT_STABLE_VERSION=insurance-qa-v4`) only if `logq find --prompt-version insurance-qa-v4 --contradictions` shows no real contradictions, the suite stays 32/32, and v4 p95 cost/latency stay within +50% of v2.
**Rollback:** set `PROMPT_CANARY_PCT=0` and restart. It is config only, with no deploy and no code change (v2 stays in the registry), and `logq --prompt-version` confirms v4 traffic has dropped to zero.
