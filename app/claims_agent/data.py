from __future__ import annotations

# ---------------------------------------------------------------------------
# Policies: excess (deductible), active flag, per-cause exclusion rules, and
# optional per-cause sub-limits (payout caps).
# ---------------------------------------------------------------------------

POLICIES = {
    "POL-AUTO-1001": {
        "policy_id": "POL-AUTO-1001",
        "product": "motor_comprehensive",
        "active": True,
        "excess": 3000,
        "exclusions": {
            "flood": {"excluded": True, "reason": "Flood/inundation damage is excluded under the standard motor policy (no flood rider purchased)."},
            "theft": {"excluded": True, "reason": "Theft is excluded when the vehicle was left unattended, unlocked, with keys inside (owner negligence clause 4.2)."},
        },
        "sub_limits": {},
    },
    "POL-AUTO-1002": {
        "policy_id": "POL-AUTO-1002",
        "product": "motor_comprehensive_flood_rider",
        "active": True,
        "excess": 5000,
        "exclusions": {
            # flood rider purchased -> flood NOT excluded on this policy
        },
        "sub_limits": {},
    },
    "POL-HOME-2001": {
        "policy_id": "POL-HOME-2001",
        "product": "home_contents",
        "active": True,
        "excess": 2000,
        "exclusions": {
            "flood": {"excluded": True, "reason": "Flood/rising water is excluded under the base home-contents policy (no flood add-on)."},
        },
        "sub_limits": {},
    },
    "POL-HOME-2002": {
        "policy_id": "POL-HOME-2002",
        "product": "home_contents_burglary",
        "active": True,
        "excess": 2000,
        "exclusions": {},
        "sub_limits": {"theft": 50000},
    },
    "POL-GADGET-3001": {
        "policy_id": "POL-GADGET-3001",
        "product": "gadget_protection",
        "active": True,
        "excess": 1000,
        "exclusions": {
            "wear_and_tear": {"excluded": True, "reason": "Gradual wear, rust, and corrosion are excluded under the gadget protection policy — only sudden accidental damage is covered."},
            "general": {"excluded": True, "reason": "An unspecified/undetermined cause is excluded under the gadget protection policy — cover requires a confirmed accidental event."},
        },
        "sub_limits": {},
    },
    "POL-HOME-2003": {
        "policy_id": "POL-HOME-2003",
        "product": "home_fire_theft",
        "active": False,  # lapsed
        "excess": 2500,
        "exclusions": {
            "policy_lapsed": {"excluded": True, "reason": "Policy had lapsed (premium unpaid) at the time of loss; no cover was in force."},
        },
        "sub_limits": {},
    },
    "POL-AUTO-1003": {
        "policy_id": "POL-AUTO-1003",
        "product": "motor_comprehensive",
        "active": True,
        "excess": 3000,
        "exclusions": {},
        "sub_limits": {},
    },
}

# ---------------------------------------------------------------------------
# Claims: raw claim record as the "claims system" would return it.
# adjuster_notes is free text — the cause of loss must be read out of it,
# it is never a separate structured field the caller already has.
# ---------------------------------------------------------------------------

CLAIMS = {
    "CLM-2026-00001": {
        "claim_id": "CLM-2026-00001",
        "policy_id": "POL-AUTO-1002",
        "claimant_name": "R. Iyer",
        "claimed_amount": 80000,
        "incident_date": "2026-07-14",
        "adjuster_notes": (
            "Insured reports heavy monsoon rain caused waterlogging on the "
            "street; engine bay was flooded and the engine seized. Policy "
            "confirmed to include the flood rider add-on."
        ),
    },
    "CLM-2026-00002": {
        "claim_id": "CLM-2026-00002",
        "policy_id": "POL-HOME-2001",
        "claimant_name": "S. Menon",
        "claimed_amount": 60000,
        "incident_date": "2026-06-02",
        "adjuster_notes": (
            "Claimant states basement flooded after three days of continuous "
            "rain; water entered through the foundation and damaged stored "
            "furniture and electronics."
        ),
    },
    "CLM-2026-00003": {
        "claim_id": "CLM-2026-00003",
        "policy_id": "POL-HOME-2002",
        "claimant_name": "A. Fernandes",
        "claimed_amount": 45000,
        "incident_date": "2026-05-20",
        "adjuster_notes": (
            "Claimant reports garage was broken into overnight; a bicycle "
            "and a set of power tools were stolen. Police complaint filed, "
            "FIR number on record. Forced-entry marks confirmed by adjuster."
        ),
    },
    "CLM-2026-00004": {
        "claim_id": "CLM-2026-00004",
        "policy_id": "POL-AUTO-1001",
        "claimant_name": "D. Kapoor",
        "claimed_amount": 350000,
        "incident_date": "2026-04-11",
        "adjuster_notes": (
            "Vehicle stolen from the driveway overnight. Claimant admits the "
            "car was left unlocked with the keys inside while running to "
            "the store. No signs of forced entry."
        ),
    },
    "CLM-2026-00005": {
        "claim_id": "CLM-2026-00005",
        "policy_id": "POL-GADGET-3001",
        "claimant_name": "P. Nair",
        "claimed_amount": 15000,
        "incident_date": "2026-08-01",
        "adjuster_notes": (
            "Claimant dropped laptop while moving it off a table; screen "
            "cracked on impact. Damage confirmed accidental, single "
            "incident, no prior faults reported."
        ),
    },
    "CLM-2026-00006": {
        "claim_id": "CLM-2026-00006",
        "policy_id": "POL-HOME-2003",
        "claimant_name": "V. Rao",
        "claimed_amount": 120000,
        "incident_date": "2026-03-09",
        "adjuster_notes": (
            "Kitchen fire caused by an electrical short circuit in the "
            "wiring; extensive smoke and heat damage to cabinetry and "
            "appliances. Fire department report attached."
        ),
    },
    "CLM-2026-00007": {
        "claim_id": "CLM-2026-00007",
        "policy_id": "POL-AUTO-1003",
        "claimant_name": "N. Bhat",
        "claimed_amount": 25000,
        "incident_date": "2026-07-22",
        "adjuster_notes": (
            "Minor collision with another vehicle at a traffic signal; "
            "front bumper and headlight damaged. Straightforward own-damage "
            "claim, no disputes, other party's insurer not involved."
        ),
    },
    "CLM-2026-00008": {
        "claim_id": "CLM-2026-00008",
        "policy_id": "POL-GADGET-3001",
        "claimant_name": "K. Joseph",
        "claimed_amount": 18000,
        "incident_date": "2026-02-15",
        "adjuster_notes": (
            "Claimant reports washing machine motor stopped working. "
            "Adjuster inspection found significant rust and corrosion on "
            "internal components consistent with gradual wear over time, "
            "not a sudden accidental event."
        ),
    },
    "CLM-2026-00009": {
        "claim_id": "CLM-2026-00009",
        "policy_id": "POL-HOME-2002",
        "claimant_name": "T. Sharma",
        "claimed_amount": 100000,
        "incident_date": "2026-01-30",
        "adjuster_notes": (
            "Home burglary reported; claimant states jewelry was stolen "
            "from a bedroom safe. Police report filed. Forced entry into "
            "the house confirmed by responding officer."
        ),
    },
    "CLM-2026-00010": {
        "claim_id": "CLM-2026-00010",
        "policy_id": "POL-HOME-2003",
        "claimant_name": "M. Pillai",
        "claimed_amount": 90000,
        "incident_date": "2026-06-18",
        "adjuster_notes": "",  # missing / not yet recorded
    },
}

CLAIM_IDS = list(CLAIMS.keys())

# ---------------------------------------------------------------------------
# Ground truth used for grading BOTH systems identically. Not shown to the
# model; computed independently from the same POLICIES/CLAIMS data above by
# applying the stated business rules by hand.
# ---------------------------------------------------------------------------

EXPECTED = {
    "CLM-2026-00001": {"status": "APPROVED", "covered": True, "exclusion": None, "gross_amount": 80000, "excess": 5000, "payable_amount": 75000},
    "CLM-2026-00002": {"status": "DENIED", "covered": False, "exclusion": "flood", "gross_amount": 60000, "excess": 2000, "payable_amount": 0},
    "CLM-2026-00003": {"status": "APPROVED", "covered": True, "exclusion": None, "gross_amount": 45000, "excess": 2000, "payable_amount": 43000},
    "CLM-2026-00004": {"status": "DENIED", "covered": False, "exclusion": "theft", "gross_amount": 350000, "excess": 3000, "payable_amount": 0},
    "CLM-2026-00005": {"status": "APPROVED", "covered": True, "exclusion": None, "gross_amount": 15000, "excess": 1000, "payable_amount": 14000},
    "CLM-2026-00006": {"status": "DENIED", "covered": False, "exclusion": "policy_lapsed", "gross_amount": 120000, "excess": 2500, "payable_amount": 0},
    "CLM-2026-00007": {"status": "APPROVED", "covered": True, "exclusion": None, "gross_amount": 25000, "excess": 3000, "payable_amount": 22000},
    "CLM-2026-00008": {"status": "DENIED", "covered": False, "exclusion": "wear_and_tear", "gross_amount": 18000, "excess": 1000, "payable_amount": 0},
    "CLM-2026-00009": {"status": "PARTIAL", "covered": True, "exclusion": None, "gross_amount": 100000, "excess": 2000, "payable_amount": 48000},
    "CLM-2026-00010": {"status": "PENDING_REVIEW", "covered": False, "exclusion": None, "gross_amount": 90000, "excess": None, "payable_amount": 0},
}
