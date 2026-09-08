import csv
from pathlib import Path

from app.claims_agent.agent import run_agent
from app.claims_agent.workflow import run_workflow
from app.claims_agent.runner import run_all, summarize, save_detail_json, RESULTS_DIR

ROOT = Path(__file__).resolve().parent


def write_race_csv(agent_summary: dict, workflow_summary: dict, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["System", "Pass Rate", "P50 Latency", "Total Tokens", "Cost / Claim"])
        writer.writerow([
            "Agent",
            f"{agent_summary['pass_rate']:.2%}",
            f"{agent_summary['p50_latency_ms']} ms",
            agent_summary["total_tokens"],
            f"${agent_summary['cost_per_claim']:.6f}",
        ])
        writer.writerow([
            "Fixed Workflow",
            f"{workflow_summary['pass_rate']:.2%}",
            f"{workflow_summary['p50_latency_ms']} ms",
            workflow_summary["total_tokens"],
            f"${workflow_summary['cost_per_claim']:.6f}",
        ])


def write_detail_csv(agent_rows: list[dict], workflow_rows: list[dict], path: Path) -> None:
    fieldnames = ["claim_id", "system", "passed", "latency_ms", "tokens", "cost", "termination_reason"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in agent_rows + workflow_rows:
            writer.writerow({k: row[k] for k in fieldnames})


def main():
    print("=== running agent over 10 claims ===")
    agent_rows = run_all(run_agent, "agent")
    agent_summary = summarize(agent_rows)

    print("\n=== running fixed workflow over 10 claims ===")
    workflow_rows = run_all(run_workflow, "workflow")
    workflow_summary = summarize(workflow_rows)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    save_detail_json(agent_rows, "agent_results.json")
    save_detail_json(workflow_rows, "workflow_results.json")

    race_csv_path = ROOT / "race.csv"
    detail_csv_path = RESULTS_DIR / "race_detail.csv"
    write_race_csv(agent_summary, workflow_summary, race_csv_path)
    write_detail_csv(agent_rows, workflow_rows, detail_csv_path)

    print("\n=== RACE RESULT ===")
    print(f"{'System':<16} {'Pass Rate':>10} {'P50 Latency':>13} {'Total Tokens':>13} {'Cost/Claim':>12}")
    print(f"{'Agent':<16} {agent_summary['pass_rate']:>10.2%} {agent_summary['p50_latency_ms']:>11} ms "
          f"{agent_summary['total_tokens']:>13} {agent_summary['cost_per_claim']:>12.6f}")
    print(f"{'Fixed Workflow':<16} {workflow_summary['pass_rate']:>10.2%} {workflow_summary['p50_latency_ms']:>11} ms "
          f"{workflow_summary['total_tokens']:>13} {workflow_summary['cost_per_claim']:>12.6f}")
    print(f"\nrace.csv written to {race_csv_path}")
    print(f"per-claim detail written to {detail_csv_path}")


if __name__ == "__main__":
    main()
