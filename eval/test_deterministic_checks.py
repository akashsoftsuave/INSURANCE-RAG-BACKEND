"""
Verifies eval/deterministic_checks.py (Task Set D) against real strings —
either straight from the 30-trace baseline (traces/traces.jsonl) or from
the underlying source PDFs (eval/data/*.pdf, via the claim numbers /
figures already listed as markers in eval/questions/*.json). No invented
claim numbers, dates, or amounts.

Plain asserts, no pytest dependency (none is installed in this project).

Usage: python -m eval.test_deterministic_checks
"""

from eval.deterministic_checks import (
    PASS, FAIL, NOT_APPLICABLE,
    check_claim_number_format,
    check_date_of_loss,
    check_deductible_numeric,
    check_denial_requires_exclusion,
)


def test_claim_number_valid_ascii():
    # eval/questions/shieldcare_full.json marker
    r = check_claim_number_format("Your claim CLM-2026-00421 was approved.")
    assert r["status"] == PASS, r


def test_claim_number_valid_typographic_hyphen():
    # Verbatim from trace 6e6434b6 final_answer_redacted (traces/traces.jsonl)
    # before the app/core/redaction.py fix, this exact string is also what
    # leaked past redaction — same shape, so the check must accept it.
    r = check_claim_number_format(
        "Your diagnostic claim (reference CLM‑2026‑00437, dated 15 Aug 2026) "
        "is listed with a status of “Under Review,”"
    )
    assert r["status"] == PASS, r


def test_claim_number_malformed():
    r = check_claim_number_format("Your claim CLM-26-421 was approved.")
    assert r["status"] == FAIL, r


def test_claim_number_absent():
    r = check_claim_number_format("Your policy number is DBTR10541714586/01.")
    assert r["status"] == NOT_APPLICABLE, r


def test_date_of_loss_absent_no_label():
    # Synthetic sentence (not from a real trace) purely to exercise the
    # NOT_APPLICABLE path: no date-of-loss/claim-date label present at all.
    # Separately verified against the real 30-trace population: none of
    # the 30 recorded answers contain "date of loss"/"loss date"/"claim
    # date"/"incident date" (grepped traces/traces.jsonl directly) — see
    # eval/regression_runner.py's report, which shows NOT_APPLICABLE on
    # all 30 for this check.
    r = check_date_of_loss("Your hospitalization claim was I 48,750.")
    assert r["status"] == NOT_APPLICABLE, r


def test_date_of_loss_parseable():
    # Source ground truth: eval/data/shieldcare_full.pdf claims table
    r = check_date_of_loss("Claim date: 12 Aug 2026, status Approved.")
    assert r["status"] == PASS, r


def test_date_of_loss_unparseable():
    r = check_date_of_loss("Claim date: sometime last August, status Approved.")
    assert r["status"] == FAIL, r


def test_deductible_numeric_real_trace():
    # Verbatim from trace a4d8dcdb final_answer_redacted
    r = check_deductible_numeric("Your hospitalization deductible is ₹10,000 per policy year.")
    assert r["status"] == PASS, r


def test_deductible_explicit_absence_real_trace():
    # Verbatim from trace a70efd3e final_answer_redacted
    r = check_deductible_numeric(
        "The policy provides Ambulance Services coverage of Rs. 10,000 per policy year, with no deductible."
    )
    assert r["status"] == PASS, r


def test_deductible_absent_field():
    r = check_deductible_numeric("Your hospitalization claim was Rs. 48,750.")
    assert r["status"] == NOT_APPLICABLE, r


def test_deductible_numeric_value_later_in_sentence():
    # Verbatim from trace 69db754e final_answer_redacted (traces/traces.jsonl,
    # Q18). Regression test: an earlier version of check_deductible_numeric
    # only inspected a fixed +/-60 char window around the FIRST "deductible"
    # occurrence, which sat before the explanatory clause and missed the
    # numeric value stated later in the same sentence -- a false FAIL caught
    # by running eval/regression_runner.py against eval/labels_30.json
    # (reference label for this trace is PASS).
    r = check_deductible_numeric(
        "A co‑pay is a percentage of the cost of the service that you must pay, "
        "whereas a deductible is a fixed amount you must pay out‑of‑pocket "
        "before the insurer begins to cover expenses (e.g., the hospitalization "
        "deductible is ₹10,000). Thus, the diagnostic‑test co‑pay is a 10 % "
        "share of the test cost, while the deductible is a set monetary amount "
        "that applies to other types of claims."
    )
    assert r["status"] == PASS, r


def test_deductible_numeric_backreference_without_digit():
    # Verbatim from trace a4d8dcdb final_answer_redacted (Q13). Regression
    # test for a second bug the sentence-split fix introduced: requiring
    # EVERY sentence mentioning "deductible" to carry its own digit is too
    # strict -- a trailing comparison sentence ("...deductible is not the
    # same as a co-pay") legitimately has no number of its own. Only one
    # label-bearing sentence needs to carry the value.
    r = check_deductible_numeric(
        "Your hospitalization deductible is ₹10,000 per policy year.\n\n"
        "No co‑pay is specified for hospitalization; the only co‑pay mentioned "
        "in the policy is a 10 % co‑pay for Diagnostic Tests. Therefore, the "
        "hospitalization deductible is not the same as a co‑pay."
    )
    assert r["status"] == PASS, r


def test_deductible_numeric_rs_abbreviation_not_a_sentence_break():
    # Verbatim from trace 983ef060 final_answer_redacted (Q23). Regression
    # test for a bug in an earlier sentence-split implementation: "Rs."
    # ends in a period, so naive ". "-splitting cut the sentence right
    # between "Rs." and the number that followed it, separating the
    # deductible label from its own value and producing a false FAIL.
    r = check_deductible_numeric(
        "The hospitalization deductible is **Rs. 10,000**.\n\n"
        "A deductible is **the amount the policyholder must pay "
        "out‑of‑pocket before the insurance coverage applies**. In other "
        "words, for a hospitalization claim the first Rs. 10,000 of the "
        "expenses must be paid by the policyholder, and only the amount "
        "exceeding that will be covered by the insurer."
    )
    assert r["status"] == PASS, r


def test_denial_with_exclusion_real_trace():
    # Verbatim excerpt from trace 26ee3af3 final_answer_redacted, itself
    # sourced from eval/data/shieldcare_full.pdf's claims table entry for
    # CLM-2026-00502.
    r = check_denial_requires_exclusion(
        "Claim CLM-2026-00502 (Dental) was rejected because the treatment "
        "(cosmetic dental veneers) falls under this exclusion."
    )
    assert r["status"] == PASS, r


def test_denial_without_exclusion():
    r = check_denial_requires_exclusion("Your claim was rejected.")
    assert r["status"] == FAIL, r


def test_denial_absent():
    r = check_denial_requires_exclusion("Your diagnostic claim is Under Review.")
    assert r["status"] == NOT_APPLICABLE, r


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = []
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed.append(t.__name__)
            print(f"FAIL  {t.__name__}: {e}")
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
