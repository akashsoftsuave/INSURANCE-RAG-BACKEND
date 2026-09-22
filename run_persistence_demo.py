import sys

from app.claims_agent.data import POLICIES
from app.claims_agent.persistence import remember_policy_excess, recall_policy_excess, clear

POLICY_ID = "POL-AUTO-1001"


def phase1():
    print("[process A] starting")
    # "agent reads policy" -- this is the only place the true excess value
    # is looked up in this phase.
    excess = POLICIES[POLICY_ID]["excess"]
    print(f"[process A] read policy {POLICY_ID} -> excess={excess}")
    remember_policy_excess(POLICY_ID, excess)
    print(f"[process A] persisted excess={excess} for {POLICY_ID} to disk")
    print("[process A] terminating")


def phase2():
    print("[process B] starting (brand-new process, no memory of process A)")
    recalled = recall_policy_excess(POLICY_ID)
    if recalled is None:
        print(f"[process B] FAIL: no persisted excess found for {POLICY_ID}")
        sys.exit(1)
    print(f"[process B] retrieved persisted excess={recalled} for {POLICY_ID} "
          f"WITHOUT re-reading POLICIES")
    true_value = POLICIES[POLICY_ID]["excess"]
    if recalled == true_value:
        print(f"[process B] OK: persisted value matches the true policy excess ({true_value}) "
              f"-- survived the restart.")
    else:
        print(f"[process B] FAIL: persisted value {recalled} != true value {true_value}")
        sys.exit(1)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("clear", "phase1", "phase2"):
        print(__doc__)
        sys.exit(1)
    {"clear": clear, "phase1": phase1, "phase2": phase2}[sys.argv[1]]()


if __name__ == "__main__":
    main()
