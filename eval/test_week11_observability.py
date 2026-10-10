"""Week 11 — tests for the request-log indexes, redact-on-write and the
prompt canary. Same runner style as eval/test_deterministic_checks.py:

    python -m eval.test_week11_observability
"""

from app.core.redaction import redact_pii
from app.observability.indexers import (classify_coverage_verdict, classify_input_type,
                                        exclusion_context_ids)
from app.observability.tracing import RequestTrace
from app.services.llm_service import PROMPTS, LLMService


def test_verdict_found_failure_is_covered():
    # The exact answer the drill found (trace 26b1fd68).
    a = ("Yes. According to the policy details, theft is covered – the insurer will pay "
         "for losses “up to the total declared insurance value” in the event of theft.")
    assert classify_coverage_verdict(a, "coverage_question") == "covered"


def test_verdict_v4_answers():
    assert classify_coverage_verdict("No - theft is excluded. Listed under What’s not covered.") == "not_covered"
    assert classify_coverage_verdict("Yes - Damage to the third-party car is covered up to Rs 1 Lac.") == "covered"
    assert classify_coverage_verdict("Not stated - the context does not mention it.") == "not_stated"
    assert classify_coverage_verdict("**No.** Theft is listed among items not covered.") == "not_covered"


def test_verdict_not_applicable_for_other_inputs():
    assert classify_coverage_verdict("The deductible is Rs. 10,000.", "amount_limit") == "n/a"


def test_input_type():
    assert classify_input_type("Will ACKO pay for the theft?") == "coverage_question"
    assert classify_input_type("is AYUSH treatment covered") == "coverage_question"
    assert classify_input_type("what is the deductible for hospitalization") == "amount_limit"
    assert classify_input_type("when does the bike policy expire") == "policy_details"


def test_exclusion_context_ids():
    chunks = [{"id": "2", "document": "What's not covered\nAccident\nTheft"},
              {"id": "4", "document": "Policy Expires 2027"}]
    assert exclusion_context_ids(chunks) == ["2"]


def test_redact_pii():
    q = ("Claimant Ravi Shankar (ph 9876543210, ravi@x.test) says his Splendor TN09DB0750 "
         "on policy DBTR10541714586/01 was stolen")
    out = redact_pii(q)
    for leaked in ("Ravi", "9876543210", "ravi@x.test", "TN09DB0750", "DBTR10541714586"):
        assert leaked not in out, (leaked, out)
    # Policy terms that merely look like names must survive.
    assert "Splendor" in out and "stolen" in out
    assert redact_pii("is the owner covered for theft") == "is the owner covered for theft"


def test_spans_sum_to_stage_totals():
    t = RequestTrace("t")
    with t.span("retrieval.x", "retrieval"):
        pass
    with t.span("generation.x", "generation") as s:
        s.prompt_tokens, s.completion_tokens, s.cost_usd = 100, 20, 0.001
    tot = t.totals()
    assert tot["cost_usd"] == 0.001 and tot["tokens"] == 120
    assert tot["by_stage"]["generation"]["cost_usd"] == 0.001
    assert tot["by_stage"]["retrieval"]["cost_usd"] == 0.0


def test_prompt_registry_v4_keeps_v2_rules_and_reorders_context():
    p2 = LLMService.build_prompt("Q", "C", "insurance-qa-v2")
    p4 = LLMService.build_prompt("Q", "C", "insurance-qa-v4")
    assert p4.startswith(p2.split("\nContext:\n")[0].rstrip())
    assert "What's not covered" in p4 and "What's not covered" not in p2
    chunks = [{"id": "6", "document": "B", "metadata": {"page": 3}},
              {"id": "2", "document": "A", "metadata": {"page": 2}}]
    assert LLMService.build_context(chunks, "insurance-qa-v2") == "B\n\nA"
    assert LLMService.build_context(chunks, "insurance-qa-v4").index("A") < \
        LLMService.build_context(chunks, "insurance-qa-v4").index("B")
    assert {"insurance-qa-v2", "insurance-qa-v3", "insurance-qa-v4"} <= set(PROMPTS)


def test_canary_is_sticky_per_user(monkeypatch=None):
    from app.core.config import settings
    from app.services.llm_service import select_prompt_version
    saved = (settings.PROMPT_CANARY_VERSION, settings.PROMPT_CANARY_PCT)
    try:
        settings.PROMPT_CANARY_VERSION, settings.PROMPT_CANARY_PCT = "insurance-qa-v4", 10
        first = [select_prompt_version(f"ADJ-{i}") for i in range(200)]
        assert first == [select_prompt_version(f"ADJ-{i}") for i in range(200)]
        share = first.count("insurance-qa-v4") / len(first)
        assert 0.03 < share < 0.2, share
        settings.PROMPT_CANARY_PCT = 0  # rollback
        assert all(select_prompt_version(f"ADJ-{i}") == settings.PROMPT_STABLE_VERSION for i in range(200))
    finally:
        settings.PROMPT_CANARY_VERSION, settings.PROMPT_CANARY_PCT = saved


def main():
    tests = [v for k, v in globals().items() if k.startswith("test_")]
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
