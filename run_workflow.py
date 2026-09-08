from app.claims_agent.workflow import run_workflow
from app.claims_agent.runner import run_all, summarize, save_detail_json


def main():
    rows = run_all(run_workflow, "workflow")
    summary = summarize(rows)
    path = save_detail_json(rows, "workflow_results.json")

    print("\n=== workflow summary ===")
    print(f"pass_rate={summary['pass_rate']:.2%} ({sum(r['passed'] for r in rows)}/{summary['n']})")
    print(f"p50_latency_ms={summary['p50_latency_ms']}")
    print(f"total_tokens={summary['total_tokens']}")
    print(f"cost_per_claim=${summary['cost_per_claim']:.6f}")
    print(f"total_cost=${summary['total_cost']:.6f}")
    print(f"detail written to {path}")


if __name__ == "__main__":
    main()
