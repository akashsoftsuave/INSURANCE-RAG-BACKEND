# Retrieval Evaluation Report

**Configuration under test:** current implementation (hybrid RRF + cross-encoder rerank `ms-marco-MiniLM-L-6-v2`). Dataset: `shieldcare_basic` — corpus: `shieldcare_basic.pdf` (8 chunks), k=3.

## Headline numbers

| Metric | Value |
|---|---|
| hit-rate@3 | 12/12 (100%) |
| mean recall@3 | 100% |
| mean MRR | 0.958 |

Failures: 0/12.

## Per-question results

| # | Question | Hit | Label |
|---|---|---|---|
| 1 | what's my policy number | ✓ | OK |
| 2 | who is the beneficiary on this policy | ✓ | OK |
| 3 | what percentage does my beneficiary get | ✓ | OK |
| 4 | what's the deductible for hospitalization | ✓ | OK |
| 5 | how much was my hospitalization claim for | ✓ | OK |
| 6 | what's the coverage limit for diagnostic tests | ✓ | OK |
| 7 | what was the amount of my diagnostic claim | ✓ | OK |
| 8 | is my diagnostic claim approved yet | ✓ | OK |
| 9 | are cosmetic procedures covered | ✓ | OK |
| 10 | how much co-pay do I have for diagnostic tests | ✓ | OK |
| 11 | what's the ambulance services coverage limit | ✓ | OK |
| 12 | when does my policy expire | ✓ | OK |

## Inspection view — failures (retrieval vs generation)

None — no failures on this question set.
