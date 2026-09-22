# Tool description diff

This repo had no prior claims-triage agent, so the "existing two tools"
were authored fresh as the baseline (matching the task's description of
the existing agent's steps 1/3), and the third tool was then added and
checked against them for overlap before being wired in.

## Existing tool 1 — `get_claim`

```
Retrieve the full claim record for a single claim_id from the claims
system: policy_id, claimant_name, claimed_amount, incident_date, and the
raw adjuster_notes text. This is the only tool that reads claim records.
It never evaluates coverage/exclusions and never computes a payable
amount.
```

Parameters: `{claim_id: string, required}`

Single job: **data retrieval** of one claim record (this is also where
step 2, "reading adjuster notes," lives — the notes come back as a field
on the claim, there is no separate fetch for them).

## Existing tool 2 — `check_policy_exclusions`

```
Look up policy terms for a given cause of loss: whether that cause is
excluded, whether the policy is active, the policy's excess (deductible),
and any sub-limit that caps payout for that cause. This is the only tool
that makes a coverage/exclusion decision. It never reads claim records and
never computes a payable amount.
```

Parameters: `{policy_id: string, cause: enum[flood, theft, accidental_damage, fire, wear_and_tear, collision, general, unknown], both required}`

Single job: **coverage decision** — is this cause covered under this
policy, and what are the policy's money terms (excess, sub-limit)?

## New tool 3 — `compute_payout`

```
Compute the payable amount for ONE claim from already-known facts: the
claimed (gross) amount, the policy excess, the claim's status, and an
optional sub-limit cap. This is the only tool that performs payout
arithmetic. It never fetches claim data and never decides whether a cause
is excluded — claim_status and sub_limit must already be decided before
calling it.
```

Parameters:

| name | type | required | notes |
|---|---|---|---|
| `claimed_amount` | number | yes | gross amount claimed |
| `policy_excess` | number \| null | yes | from `check_policy_exclusions` |
| `claim_status` | **enum**: `APPROVED`, `DENIED`, `PARTIAL`, `PENDING_REVIEW` | yes | decided by the caller before this call |
| `sub_limit` | number \| null | no | from `check_policy_exclusions` |

Single job: **arithmetic** — turn already-decided facts into
`gross_amount` / `excess` / `payable_amount`. No lookups, no judgment
calls.

## Why the third tool doesn't overlap

| Responsibility | get_claim | check_policy_exclusions | compute_payout |
|---|---|---|---|
| Fetch claim data from the claims system | ✔ | ✘ | ✘ |
| Decide coverage / exclusion | ✘ | ✔ | ✘ |
| Surface policy money terms (excess, sub-limit) | ✘ | ✔ | ✘ |
| Perform payout arithmetic | ✘ | ✘ | ✔ |
| Decide claim_status (APPROVED/DENIED/PARTIAL/PENDING_REVIEW) | ✘ | ✘ | ✘ (input, not decided here) |

`compute_payout` was picked over `get_adjuster_notes` specifically because
adjuster notes are already returned by `get_claim` — adding a dedicated
`get_adjuster_notes` tool would have duplicated `get_claim`'s existing
"read claim record" responsibility (a second way to fetch a field that's
already fetched), which the task explicitly warns against ("must not
overlap with the responsibilities described by the existing two tools").
`compute_payout` instead fills the one genuinely missing single-purpose
job — arithmetic — that neither existing tool does.

Each description states what the tool does AND explicitly what it does
NOT do, so a model choosing between them has a sharp boundary instead of
three plausible-sounding options for the same step. `claim_status` uses a
closed enum (not a free string) so `compute_payout` can never be called
with an invalid or synonymous status value (e.g. "approved", "Approved",
"paid").
