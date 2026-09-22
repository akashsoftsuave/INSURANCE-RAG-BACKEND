from app.claims_agent.agent import run_agent
from app.claims_agent.runner import run_all, summarize, save_detail_json


def main():
    rows = run_all(run_agent, "agent")
    summary = summarize(rows)
    path = save_detail_json(rows, "agent_results.json")


if __name__ == "__main__":
    main()
