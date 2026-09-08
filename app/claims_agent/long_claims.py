from __future__ import annotations

from app.claims_agent.data import CLAIMS, POLICIES

_L1_TURNS = [
    "Turn 1 (claimant): Filing a claim, our street flooded badly last night.",
    "Turn 2 (adjuster): Understood, logging this as a flood-related water damage claim.",
    "Turn 3 (claimant): The monsoon rain caused the whole street to flood, water came into the ground floor.",
    "Turn 4 (adjuster): Can you confirm the water entered from outside, not from any internal plumbing?",
    "Turn 5 (claimant): Yes, definitely from outside -- the street was like a river, water rose to knee height.",
    "Turn 6 (adjuster): Noted -- external flooding, not a plumbing issue. Scheduling a site visit.",
    "Turn 7 (claimant): Thank you, when can someone come by?",
    "Turn 8 (adjuster): Site visit scheduled for Thursday morning.",
    "Turn 9 (claimant): Ok, I'll be home.",
    "Turn 10 (adjuster): Please have photos of the flood damage ready.",
    "Turn 11 (claimant): Already took photos of the flooded living room.",
    "Turn 12 (adjuster): Great, that'll help process the flood damage claim faster.",
    "Turn 13 (claimant): Neighbours also flooded, whole street affected by the monsoon.",
    "Turn 14 (adjuster): Yes, we've had several flood claims from that area this week.",
    "Turn 15 (claimant): How long will the flood claim assessment take?",
    "Turn 16 (adjuster): Typically 5-7 business days after the site visit.",
    "Turn 17 (claimant): Ok, following up on flood damage timeline.",
    "Turn 18 (adjuster): Site visit completed, confirming flood damage to ground floor as described.",
    "Turn 19 (claimant): Great, what's next?",
    "Turn 20 (adjuster): We'll check your policy terms for flood coverage.",
    "Turn 21 (claimant): Please let me know soon, still cleaning up after the flood.",
    "Turn 22 (adjuster): Understood, will update you shortly.",
    "Turn 23 (claimant): Any update on the flood claim?",
    "Turn 24 (adjuster): Still reviewing, thank you for your patience.",
    "Turn 25 (claimant): It has been a week since the flooding, please expedite.",
    "Turn 26 (adjuster): Escalating your flood claim for faster review.",
    "Turn 27 (claimant): Appreciated, this flood really damaged a lot of furniture.",
    "Turn 28 (adjuster): Noted the furniture damage from the flood water.",
    "Turn 29 (claimant): Let me know the final decision on the flood claim soon.",
    "Turn 30 (adjuster): Will do -- confirming again this is being processed as a flood-cause claim.",
]

# ---------------------------------------------------------------------------
# L2 -- the trap: an early correction reverses the cause from "flood" to
# "burst indoor pipe", but 27 turns of generic small talk (which keeps
# mentioning rain/flood in passing) follow it, and the correction itself
# falls outside the kept-recent window.
# ---------------------------------------------------------------------------

_L2_TURNS = [
    "Turn 1 (claimant): Water damage in the kitchen, we think it flooded overnight during the storm.",
    "Turn 2 (adjuster): Initial report logged as suspected flood damage, pending site visit.",
    "Turn 3 (adjuster): Site visit complete. CORRECTION: this was NOT external flooding. The supply pipe under the kitchen sink burst and leaked overnight -- no water entered from outside at all.",
    "Turn 4 (claimant): Oh I see, the plumber confirmed the pipe fitting had corroded and gave way.",
    "Turn 5 (adjuster): Understood, updating the file to reflect an indoor plumbing failure, not flood.",
    "Turn 6 (claimant): Ok, by the way it has been raining a lot this week, is that related?",
    "Turn 7 (adjuster): No, the rain outside is unrelated -- your case is the burst pipe, separate from the rain.",
    "Turn 8 (claimant): Got it. When will the plumber's invoice be reimbursed?",
    "Turn 9 (adjuster): We'll include that once the claim is approved.",
    "Turn 10 (claimant): The weather forecast says more rain and possible flooding this weekend.",
    "Turn 11 (adjuster): Noted, though that's not relevant to your claim.",
    "Turn 12 (claimant): Just mentioning it because everyone on the street is worried about flooding again.",
    "Turn 13 (adjuster): Understandable. Continuing to process your claim.",
    "Turn 14 (claimant): Do you need more photos of the kitchen?",
    "Turn 15 (adjuster): A few more of the cabinet base would help.",
    "Turn 16 (claimant): Sending them now.",
    "Turn 17 (adjuster): Received, thank you.",
    "Turn 18 (claimant): Any news on the flooding elsewhere in the city, is that affecting processing times?",
    "Turn 19 (adjuster): There is a backlog from flood claims citywide, but yours is being handled separately.",
    "Turn 20 (claimant): Ok, just checking. The news says this is the worst flooding season in years.",
    "Turn 21 (adjuster): Noted. Continuing with your file.",
    "Turn 22 (claimant): Following up again.",
    "Turn 23 (adjuster): Still in review, thank you for your patience.",
    "Turn 24 (claimant): Neighbours are also filing flood claims, hope mine isn't stuck in that queue.",
    "Turn 25 (adjuster): It is not -- yours is tracked separately.",
    "Turn 26 (claimant): Good to know. Any update?",
    "Turn 27 (adjuster): Reviewing the plumber's invoice now.",
    "Turn 28 (claimant): Thanks, let me know soon.",
    "Turn 29 (adjuster): Will update you within two business days.",
    "Turn 30 (claimant): Appreciate it, hoping this gets resolved before the next round of rain.",
]

# ---------------------------------------------------------------------------
# L3 -- the cause-of-loss detail (accidental drop) only appears once, early,
# and the remaining turns are pure admin chatter with no cause information
# at all, so if it's summarized away there's nothing left to reconstruct it.
# ---------------------------------------------------------------------------

_L3_TURNS = [
    "Turn 1 (claimant): Need to file a claim for my laptop.",
    "Turn 2 (claimant): It was accidentally dropped into the pool during a family event over the weekend, screen and motherboard are damaged.",
    "Turn 3 (adjuster): Sorry to hear that, logging the claim now.",
    "Turn 4 (adjuster): Can you send the purchase receipt for the laptop?",
    "Turn 5 (claimant): Sending it now.",
    "Turn 6 (adjuster): Received, thank you.",
    "Turn 7 (claimant): How long does processing usually take?",
    "Turn 8 (adjuster): Around 5-7 business days.",
    "Turn 9 (claimant): Ok, thanks.",
    "Turn 10 (adjuster): Do you have the original box or warranty card?",
    "Turn 11 (claimant): I can look for it.",
    "Turn 12 (adjuster): No rush, whenever you find it.",
    "Turn 13 (claimant): Found the warranty card, sending a photo.",
    "Turn 14 (adjuster): Got it, thanks.",
    "Turn 15 (claimant): Any update on my claim status?",
    "Turn 16 (adjuster): Still in the queue, will update soon.",
    "Turn 17 (claimant): Ok, following up again.",
    "Turn 18 (adjuster): Thanks for your patience, reviewing now.",
    "Turn 19 (claimant): Let me know if you need anything else from me.",
    "Turn 20 (adjuster): Will do.",
    "Turn 21 (claimant): Checking in again on the status.",
    "Turn 22 (adjuster): Still reviewing the documents you sent.",
    "Turn 23 (claimant): No problem, take your time.",
    "Turn 24 (adjuster): Appreciate your patience.",
    "Turn 25 (claimant): Any timeline update?",
    "Turn 26 (adjuster): Expecting a decision within two more business days.",
    "Turn 27 (claimant): Sounds good, thank you.",
    "Turn 28 (adjuster): You're welcome.",
    "Turn 29 (claimant): One more follow-up, please let me know as soon as there's news.",
    "Turn 30 (adjuster): Absolutely, will update you as soon as the review is complete.",
]

LONG_CLAIMS = {
    "CLM-2026-L001": {
        "claim_id": "CLM-2026-L001",
        "policy_id": "POL-HOME-2001",  # flood excluded on this policy
        "claimant_name": "R. Das",
        "claimed_amount": 70000,
        "incident_date": "2026-07-10",
        "adjuster_notes": "\n".join(_L1_TURNS),
    },
    "CLM-2026-L002": {
        "claim_id": "CLM-2026-L002",
        "policy_id": "POL-HOME-2001",  # flood excluded, but true cause is burst pipe (covered)
        "claimant_name": "H. Verma",
        "claimed_amount": 40000,
        "incident_date": "2026-08-05",
        "adjuster_notes": "\n".join(_L2_TURNS),
    },
    "CLM-2026-L003": {
        "claim_id": "CLM-2026-L003",
        "policy_id": "POL-GADGET-3001",  # accidental_damage not excluded (only wear_and_tear is)
        "claimant_name": "S. Ghosh",
        "claimed_amount": 22000,
        "incident_date": "2026-09-01",
        "adjuster_notes": "\n".join(_L3_TURNS),
    },
}

LONG_CLAIM_IDS = list(LONG_CLAIMS.keys())

# get_claim (app.claims_agent.tools) reads from the single CLAIMS dict --
# register the long claims into it so both systems can fetch them by id
# exactly like any of the 10 race claims. Importing this module is what
# makes CLM-2026-L00x resolvable.
CLAIMS.update(LONG_CLAIMS)

# Ground truth computed from the FULL notes (what a system with no context
# limits would correctly conclude).
LONG_EXPECTED = {
    "CLM-2026-L001": {  # true cause: flood -> excluded on POL-HOME-2001
        "status": "DENIED", "covered": False, "exclusion": "flood",
        "gross_amount": 70000, "excess": 2000, "payable_amount": 0,
    },
    "CLM-2026-L002": {  # true cause: burst pipe ("general", no exclusion) -> covered
        "status": "APPROVED", "covered": True, "exclusion": None,
        "gross_amount": 40000, "excess": 2000, "payable_amount": 38000,
    },
    "CLM-2026-L003": {  # true cause: accidental_damage -> covered
        "status": "APPROVED", "covered": True, "exclusion": None,
        "gross_amount": 22000, "excess": 1000, "payable_amount": 21000,
    },
}
