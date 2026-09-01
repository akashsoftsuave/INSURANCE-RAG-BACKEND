# Retrieval Evaluation Report

**Configuration under test:** current implementation (hybrid RRF + cross-encoder rerank `ms-marco-MiniLM-L-6-v2`). Dataset: `acko_bike` — corpus: `acko_bike.pdf` (25 chunks), k=3.

## Headline numbers

| Metric | Value |
|---|---|
| hit-rate@3 | 11/12 (92%) |
| mean recall@3 | 79% |
| mean MRR | 0.819 |

Failures: 0/12.

## Per-question results

| # | Question | Hit | Label |
|---|---|---|---|
| 1 | what's my policy number | ✓ | OK |
| 2 | what bike is insured under this policy | ✓ | OK |
| 3 | what's the registration number of my bike | ✓ | OK |
| 4 | what's the total premium I paid | ✓ | OK |
| 5 | who is my nominee | ✓ | OK |
| 6 | what's my nominee's relationship to me | ✓ | OK |
| 7 | what's the ACKO claims phone number | ✓ | OK |
| 8 | who was my previous insurer | ✓ | OK |
| 9 | is this a third party or comprehensive policy | ✓ | OK |
| 10 | what's the liability limit for third party property damage | ✓ | OK |
| 11 | what's the IRDAI registration number for this policy | ✓ | OK |
| 12 | when does my policy expire | ✗ | OK |

## Inspection view — failures (retrieval vs generation)

None — no failures on this question set.
