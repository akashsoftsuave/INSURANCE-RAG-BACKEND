from app.claims_agent.agent import run_agent
from app.claims_agent.budgets import BudgetConfig

TIGHT_BUDGET = BudgetConfig(max_iters=6, max_tokens=2200, max_cost=1.0, max_wall_clock_ms=30_000)


def main():
    claim_id = "CLM-2026-00002"  # needs get_claim + check_policy_exclusions + compute_payout + final answer
    print(f"Running agent on {claim_id} with a deliberately tight max_tokens={TIGHT_BUDGET.max_tokens} "
          f"to force BUDGET_TERMINATED...\n")

    result = run_agent(claim_id, budget_config=TIGHT_BUDGET, verbose=True)

    assert result["termination_reason"] in ("max_tokens", "max_iterations", "max_cost", "wall_clock"), \
        "expected a budget termination for this demo run"
    assert result["output"] is None, "expected no final structured output — the loop must stop before completing"
    print("\nOK: agent terminated cleanly on a budget limit, never continued past it.")


if __name__ == "__main__":
    main()
