"""Week 8 — Task Set D: the 10 trajectory-evaluation cases.

These reuse the existing fixed claim/policy fixtures in
`app/claims_agent/data.py` (CLAIMS, POLICIES, EXPECTED). Nothing here
invents new claim data — the whole point is that the outcome evaluation
(EXPECTED) and the trajectory evaluation (VALID_PATHS) grade the *same*
ten claims, so outcome correctness and trajectory correctness are directly
comparable.

Valid paths
-----------
`valid_paths` is a list of tool sequences; a trajectory passes the sequence
check if the actual sequence equals one of them. Paths are deliberately NOT
over-constrained: where two orderings are both legitimate, both are listed.

The agent runs on the Task-D tool set (`app/claims_agent/tools.py`):

    get_claim        -> the claim record (policy_id, amount, adjuster_notes)
    get_policy       -> the policy's terms: active flag, excess, sub-limits
    check_exclusions -> the coverage decision for one cause
    compute_payout   -> the payout arithmetic

`get_claim` must come first, because the policy_id only exists in the claim
record, and `compute_payout` must come last, because it consumes the excess,
the sub-limit and the status. But `get_policy` and `check_exclusions` each
need nothing but the policy_id, so their relative order is a free choice and
BOTH orderings are accepted on every full-pipeline case:

    ["get_claim", "get_policy", "check_exclusions", "compute_payout"]
    ["get_claim", "check_exclusions", "get_policy", "compute_payout"]

TC-03 (empty adjuster notes) has its own two-path set — see the note there.
"""

from __future__ import annotations

from app.claims_agent.data import CLAIMS, POLICIES, EXPECTED

# The full pipeline. get_claim is pinned first and compute_payout last by the
# data dependencies; the two middle lookups are order-free, so both orderings
# are valid.
FULL_PATHS = [
    ["get_claim", "get_policy", "check_exclusions", "compute_payout"],
    ["get_claim", "check_exclusions", "get_policy", "compute_payout"],
]


class TrajectoryCase:
    def __init__(self, case_id: str, claim_id: str, variation: str, notes: str,
                 valid_paths: list[list[str]], allow_alternate_paths: bool,
                 acceptable_causes: list[str] | None):
        self.case_id = case_id
        self.claim_id = claim_id
        self.variation = variation
        self.notes = notes
        self.valid_paths = valid_paths
        self.allow_alternate_paths = allow_alternate_paths
        # Cause readings a careful adjuster could defensibly take from the
        # free-text notes. Used by argument validation, so that a legitimate
        # alternate reading is not scored as an invalid argument.
        self.acceptable_causes = acceptable_causes or []

    # --- derived -----------------------------------------------------
    @property
    def claim(self) -> dict:
        return CLAIMS[self.claim_id]

    @property
    def policy(self) -> dict:
        return POLICIES[self.claim["policy_id"]]

    @property
    def expected_outcome(self) -> dict:
        return EXPECTED[self.claim_id]

    @property
    def min_required_steps(self) -> int:
        """Shortest legitimate path = the denominator of step efficiency."""
        return min(len(p) for p in self.valid_paths)

    @property
    def max_valid_steps(self) -> int:
        return max(len(p) for p in self.valid_paths)

    @property
    def required_tools(self) -> set[str]:
        """Tools that appear in *every* valid path. Missing one of these is
        a skipped required step no matter which path the agent intended."""
        sets = [set(p) for p in self.valid_paths]
        required = sets[0]
        for s in sets[1:]:
            required &= s
        return required

    @property
    def allowed_tools(self) -> set[str]:
        """Tools that appear in at least one valid path. Anything outside
        this set is a wrong-tool selection for this case."""
        allowed: set[str] = set()
        for p in self.valid_paths:
            allowed |= set(p)
        return allowed

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "claim_id": self.claim_id,
            "variation": self.variation,
            "notes": self.notes,
            "claim_input": {
                "claim_id": self.claim_id,
                "policy_id": self.claim["policy_id"],
                "claimed_amount": self.claim["claimed_amount"],
                "adjuster_notes": self.claim["adjuster_notes"],
            },
            "expected_outcome": self.expected_outcome,
            "expected_tool_sequence": self.valid_paths[0],
            "valid_paths": self.valid_paths,
            "allow_alternate_paths": self.allow_alternate_paths,
            "acceptable_causes": self.acceptable_causes,
            "min_required_steps": self.min_required_steps,
        }


CASES: list[TrajectoryCase] = [
    TrajectoryCase(
        case_id="TC-01",
        claim_id="CLM-2026-00007",
        variation="clean_claim",
        notes=(
            "Straightforward collision on an active motor policy with no "
            "exclusions and no sub-limits. Nothing about the claim is "
            "contested; the only work is the four-step pipeline. Because it "
            "is clean, the correct payout is also reachable without the "
            "coverage decision — get_policy alone supplies the excess."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["collision", "accidental_damage"],
    ),
    TrajectoryCase(
        case_id="TC-02",
        claim_id="CLM-2026-00002",
        variation="exclusion_applies",
        notes=(
            "Basement flood on a home-contents policy with no flood add-on. "
            "Flood is excluded, so the outcome is DENIED / payable 0. The "
            "payable figure (0) is reachable WITHOUT calling compute_payout "
            "at all, which makes this a right-answer-wrong-path candidate."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["flood"],
    ),
    TrajectoryCase(
        case_id="TC-03",
        claim_id="CLM-2026-00010",
        variation="missing_information",
        notes=(
            "adjuster_notes is empty, so no cause of loss can be read. The "
            "contract says: status PENDING_REVIEW, payable 0, excess null, "
            "and stop — the policy terms are not needed, because the expected "
            "excess is null. Two paths are accepted: stopping at the claim "
            "record, or additionally confirming the undetermined cause "
            "against the policy, since check_exclusions ships a first-class "
            "'unknown' branch that returns covered=False with a reason."
        ),
        valid_paths=[
            ["get_claim"],
            ["get_claim", "check_exclusions"],
        ],
        allow_alternate_paths=True,
        acceptable_causes=["unknown"],
    ),
    TrajectoryCase(
        case_id="TC-04",
        claim_id="CLM-2026-00006",
        variation="requires_policy_lookup",
        notes=(
            "Kitchen fire — the notes read as a clearly covered peril. The "
            "only thing that denies the claim is that the POLICY had lapsed, "
            "a fact that exists nowhere in the claim record. The agent CANNOT "
            "get this right without the policy lookup."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["fire"],
    ),
    TrajectoryCase(
        case_id="TC-05",
        claim_id="CLM-2026-00008",
        variation="requires_exclusion_lookup",
        notes=(
            "Washing-machine failure from rust/corrosion. wear_and_tear is "
            "excluded on the gadget policy — discoverable only through the "
            "exclusion lookup."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["wear_and_tear"],
    ),
    TrajectoryCase(
        case_id="TC-06",
        claim_id="CLM-2026-00009",
        variation="requires_multiple_tools",
        notes=(
            "Burglary claim of 100,000 against a 50,000 theft sub-limit, so "
            "the outcome is PARTIAL (48,000 after the 2,000 excess). Every "
            "number in the answer comes from a different tool: gross from "
            "get_claim, sub-limit and excess from check_policy_exclusions, "
            "the capped payable from compute_payout."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["theft"],
    ),
    TrajectoryCase(
        case_id="TC-07",
        claim_id="CLM-2026-00001",
        variation="misleading_adjuster_notes",
        notes=(
            "Engine flooded in a monsoon. The adjuster notes end with "
            "'Policy confirmed to include the flood rider add-on' — a "
            "coverage CONCLUSION planted in free text. An agent that trusts "
            "it can skip the exclusion lookup and still be right about "
            "coverage, but it would then have to invent the 5,000 excess."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["flood"],
    ),
    TrajectoryCase(
        case_id="TC-08",
        claim_id="CLM-2026-00004",
        variation="exclusion_applies_high_value",
        notes=(
            "350,000 vehicle theft where the owner left the keys in the car. "
            "The owner-negligence theft exclusion applies: DENIED, payable 0. "
            "Like TC-02, the correct payable (0) is reachable without the "
            "payout tool."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["theft"],
    ),
    TrajectoryCase(
        case_id="TC-09",
        claim_id="CLM-2026-00003",
        variation="correct_payout_reachable_without_exclusion_check",
        notes=(
            "Garage break-in, 45,000 claimed against a 50,000 theft "
            "sub-limit — so the cap does NOT bite and the answer is a plain "
            "APPROVED 43,000. The notes are padded with irrelevant detail "
            "(FIR number, forced-entry marks). An agent that assumes 'theft "
            "on a burglary policy is obviously covered' and goes straight "
            "from get_policy to compute_payout lands on the right payout by "
            "accident: it already holds the real excess and the real "
            "sub-limit from get_policy, and the coverage decision it never "
            "made happens to have gone its way. This is the archetypal "
            "right-answer-wrong-path case."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["theft"],
    ),
    TrajectoryCase(
        case_id="TC-10",
        claim_id="CLM-2026-00005",
        variation="clean_claim_gadget",
        notes=(
            "Dropped laptop, cracked screen — sudden accidental damage on a "
            "gadget policy. Clean claim, but the same policy excludes "
            "wear_and_tear and 'general', so the exclusion lookup is what "
            "separates this from TC-05 rather than the notes alone."
        ),
        valid_paths=FULL_PATHS,
        allow_alternate_paths=True,
        acceptable_causes=["accidental_damage"],
    ),
]

CASES_BY_ID = {c.case_id: c for c in CASES}

assert len(CASES) == 10, "Task Set D requires exactly 10 trajectory cases"
assert len({c.claim_id for c in CASES}) == 10, "each case must grade a distinct claim"
