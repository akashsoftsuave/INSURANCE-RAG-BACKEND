"""Builds the capstone corpus (deterministic, seeded). Run via `python -m capstone build-corpus`.

The real insurer corpus described in the brief is not in this repo, so this
module generates a synthetic stand-in with the SAME structure:

  data/wording_clauses.json   motor + household wordings (base clauses) and BOTH
                              endorsement sets (2023, 2025), one record per
                              clause-version, with effective date ranges.
  data/limits.json            deductible / sub-limit table by product x peril x version
  data/policies.json          per-policy sum insured (policy limit) + in-force periods
  data/claims.json            500 claim files: FNOL text, adjuster notes (with
                              planted PII), photo captions, final decision + reason code

The flood and theft clauses are rewritten in the 2025 set (effective
2025-03-01). The 2025 flood wording is deliberately the more keyword-rich text,
because that is what makes it win a naive similarity search for a 2024 loss.

Limits (what is NOT claimed here): wording length is ~15 clauses per product,
not 60 pages; parse quality of real PDFs is not exercised.
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta

from capstone.config import DATA_DIR, OPEN_END, OPEN_START, VERSIONS, version_for_date, ymd_int

SEED = 20261020

# --------------------------------------------------------------------------
# Wording
# --------------------------------------------------------------------------
# (clause, title, text). Base clauses are valid on every date.
BASE_CLAUSES = {
    "motor": [
        ("1.1", "Insuring clause", "We will indemnify You for accidental loss of or damage to the insured vehicle caused by an insured peril listed in clause 4, up to the Policy Limit shown in the Schedule, less the Deductible, subject to the Exclusions in clause 5."),
        ("1.2", "Definitions", "Deductible means the amount You bear first on every claim. Sub-limit means the maximum We pay for one named peril, which may be lower than the Policy Limit. Policy Limit means the Sum Insured shown in the Schedule. These are three separate amounts and are never added together or substituted for one another."),
        ("2.1", "Policy period", "Cover applies only to loss occurring on a date within a period of insurance shown in the Schedule. Loss occurring in a lapse between periods, or before inception, is outside the policy period and is referred to an adjuster."),
        ("2.2", "Wording applicable to a loss", "A claim is assessed against the policy wording and endorsements in force on the date of loss, not the wording in force on the date of notification or any later date."),
        ("4.1", "Accidental collision", "Accidental collision, overturning, or impact with another object or vehicle causing damage to the insured vehicle is an insured peril."),
        ("4.2", "Fire and explosion", "Fire, self-ignition, lightning and explosion causing damage to the insured vehicle are insured perils."),
        ("5.1", "General exclusion: wear and tear", "We do not pay for wear and tear, depreciation, mechanical or electrical breakdown, rust or corrosion, or damage that develops gradually."),
        ("5.2", "General exclusion: driver and use", "We do not pay for loss arising while the vehicle is driven by a person without a valid licence, under the influence of alcohol or drugs, or used for racing, speed testing or hire without a commercial endorsement."),
        ("5.3", "Flood and inundation", "Loss or damage caused by flood or inundation is governed by the Flood Endorsement in force on the date of loss. See the endorsement schedule."),
        ("5.4", "Theft", "Loss of or damage to the insured vehicle by theft is governed by the Theft Endorsement in force on the date of loss. See the endorsement schedule."),
        ("6.1", "Deductible and limits", "The Deductible, the Sub-limit for the peril, and the Policy Limit applicable on the date of loss are shown in the Limits Table. The Deductible is applied to the loss first; the payable amount is then capped at the Sub-limit for that peril and never exceeds the Policy Limit."),
        ("7.1", "Claims procedure", "Notify Us as soon as reasonably possible. Do not repair the vehicle before it has been inspected. Provide the first information report to the police for theft."),
        ("8.1", "Fraud", "If a claim is fraudulent or supported by false statements, all benefit under the policy is forfeited."),
    ],
    "household": [
        ("1.1", "Insuring clause", "We will indemnify You for accidental loss of or damage to the insured buildings and contents at the insured address caused by an insured peril listed in clause 4, up to the Policy Limit shown in the Schedule, less the Deductible, subject to the Exclusions in clause 6."),
        ("1.2", "Definitions", "Deductible means the amount You bear first on every claim. Sub-limit means the maximum We pay for one named peril, which may be lower than the Policy Limit. Policy Limit means the Sum Insured shown in the Schedule. These are three separate amounts and are never added together or substituted for one another."),
        ("2.1", "Policy period", "Cover applies only to loss occurring on a date within a period of insurance shown in the Schedule. Loss occurring in a lapse between periods, or before inception, is outside the policy period and is referred to an adjuster."),
        ("2.2", "Wording applicable to a loss", "A claim is assessed against the policy wording and endorsements in force on the date of loss, not the wording in force on the date of notification or any later date."),
        ("4.1", "Fire and explosion", "Fire, smoke, lightning and explosion causing damage to the insured buildings or contents are insured perils."),
        ("4.2", "Accidental damage", "Sudden and unforeseen accidental damage to the insured contents inside the home is an insured peril."),
        ("6.1", "General exclusion: wear and tear", "We do not pay for wear and tear, gradual deterioration, damp, rot, rust, corrosion, insects or vermin."),
        ("6.2", "Flood and water", "Loss or damage caused by flood, storm water or escape of water is governed by the Flood Endorsement in force on the date of loss. See the endorsement schedule."),
        ("6.3", "General exclusion: war and nuclear", "We do not pay for loss caused by war, invasion, civil commotion or nuclear risks."),
        ("6.4", "Theft and burglary", "Loss of or damage to contents by theft or burglary is governed by the Theft Endorsement in force on the date of loss. See the endorsement schedule."),
        ("7.1", "Deductible and limits", "The Deductible, the Sub-limit for the peril, and the Policy Limit applicable on the date of loss are shown in the Limits Table. The Deductible is applied to the loss first; the payable amount is then capped at the Sub-limit for that peril and never exceeds the Policy Limit."),
        ("8.1", "Claims procedure", "Notify Us promptly, preserve damaged items for inspection, and report theft or burglary to the police and obtain a copy of the report."),
        ("9.1", "Fraud", "If a claim is fraudulent or supported by false statements, all benefit under the policy is forfeited."),
    ],
}

# Endorsement sets. Each entry replaces/anchors one base clause for a date range.
ENDORSEMENTS = {
    ("motor", "2023"): [
        ("5.3", "MOT-END-FLD-2023", "Flood Endorsement (2023 set)",
         "Damage to the insured vehicle caused by flood or rising water is an insured peril and is paid up to the flood Sub-limit. EXCLUDED: damage where the driver drove the vehicle into or through standing water deeper than the wheel hubs, including engine damage from water ingress. Flood damage to a parked vehicle is not excluded."),
        ("5.4", "MOT-END-THF-2023", "Theft Endorsement (2023 set)",
         "Theft of the insured vehicle, or of its parts, is an insured peril. EXCLUDED: theft where the ignition key or key fob was left in the vehicle."),
    ],
    ("motor", "2025"): [
        ("5.3", "MOT-END-FLD-2025", "Flood Endorsement (2025 set)",
         "FLOOD EXCLUSION. We do not pay for loss or damage to the insured vehicle caused by flood, inundation, storm surge, cloudburst runoff, waterlogging or rising water of any kind, whether the vehicle was parked or driven, and we do not pay for engine damage, hydrostatic lock or water ingress, UNLESS the Flood Rider is shown in the Schedule. Where the Flood Rider is shown, the flood Sub-limit and flood Deductible in the Limits Table apply."),
        ("5.4", "MOT-END-THF-2025", "Theft Endorsement (2025 set)",
         "Theft of the insured vehicle, or of its parts, is an insured peril. EXCLUDED: theft where the ignition key or key fob was left in the vehicle, or where the vehicle was left unlocked and unattended."),
    ],
    ("household", "2023"): [
        ("6.2", "HOM-END-FLD-2023", "Flood Endorsement (2023 set)",
         "Loss or damage to insured buildings and contents at ground level or above caused by flood, storm water or escape of water is an insured peril, up to the flood Sub-limit. EXCLUDED: contents stored in a basement or below ground level."),
        ("6.4", "HOM-END-THF-2023", "Theft Endorsement (2023 set)",
         "Theft or burglary involving forcible and violent entry to or exit from the insured home is an insured peril, up to the theft Sub-limit. EXCLUDED: theft without evidence of forcible entry."),
    ],
    ("household", "2025"): [
        ("6.2", "HOM-END-FLD-2025", "Flood Endorsement (2025 set)",
         "FLOOD AND WATER EXCLUSION. We do not pay for loss or damage to insured buildings or contents caused by flood, inundation, storm surge, waterlogging, surface water or rising water of any kind, at any level of the home, UNLESS the Flood Extension is shown in the Schedule. Where the Flood Extension is shown, the flood Sub-limit and flood Deductible in the Limits Table apply."),
        ("6.4", "HOM-END-THF-2025", "Theft Endorsement (2025 set)",
         "Theft or burglary involving forcible and violent entry to or exit from the insured home is an insured peril, up to the theft Sub-limit. EXCLUDED: theft without evidence of forcible entry, and theft from a home left unoccupied for more than 30 consecutive days."),
    ],
}

# --------------------------------------------------------------------------
# Limits: deductible / sub-limit by product x peril x version. Policy limit is per policy.
# None sub-limit means "no peril cap below the policy limit".
# --------------------------------------------------------------------------
LIMITS = {
    ("motor", "flood", "2023"): (5000, 150000),
    ("motor", "flood", "2025"): (7500, 100000),
    ("motor", "theft", "2023"): (5000, 600000),
    ("motor", "theft", "2025"): (5000, 600000),
    ("motor", "collision", "ALL"): (3000, None),
    ("motor", "fire", "ALL"): (3000, 800000),
    ("motor", "wear_and_tear", "ALL"): (0, 0),
    ("household", "flood", "2023"): (2000, 120000),
    ("household", "flood", "2025"): (4000, 75000),
    ("household", "theft", "2023"): (2000, 50000),
    ("household", "theft", "2025"): (2500, 40000),
    ("household", "collision", "ALL"): (0, 0),
    ("household", "fire", "ALL"): (2000, 500000),
    ("household", "wear_and_tear", "ALL"): (0, 0),
}


def build_wording() -> list[dict]:
    out = []
    for product, clauses in BASE_CLAUSES.items():
        for clause, title, text in clauses:
            out.append({
                "chunk_id": f"{product}|{clause}|BASE", "product": product, "clause": clause,
                "title": title, "endorsement_id": None, "endorsement_version": "BASE",
                "effective_from": OPEN_START, "effective_to": OPEN_END, "text": text,
            })
    for (product, version), items in ENDORSEMENTS.items():
        lo, hi = VERSIONS[version]
        for clause, eid, title, text in items:
            out.append({
                "chunk_id": f"{product}|{clause}|{version}", "product": product, "clause": clause,
                "title": title, "endorsement_id": eid, "endorsement_version": version,
                "effective_from": lo, "effective_to": hi, "text": text,
            })
    return out


def build_limits() -> list[dict]:
    rows = []
    for (product, peril, version), (ded, sub) in LIMITS.items():
        lo, hi = VERSIONS.get(version, (OPEN_START, OPEN_END))
        rows.append({"product": product, "peril": peril, "version": version,
                     "effective_from": lo, "effective_to": hi,
                     "deductible": ded, "sub_limit": sub})
    return rows


def limits_for(product: str, peril: str, loss_date: str, rows: list[dict] | None = None) -> dict | None:
    """The limits row in force on loss_date (version-specific row wins over ALL)."""
    rows = rows if rows is not None else build_limits()
    n = ymd_int(loss_date)
    specific = [r for r in rows if r["product"] == product and r["peril"] == peril
                and r["version"] != "ALL" and r["effective_from"] <= n <= r["effective_to"]]
    if specific:
        return specific[0]
    general = [r for r in rows if r["product"] == product and r["peril"] == peril and r["version"] == "ALL"]
    return general[0] if general else None


# --------------------------------------------------------------------------
# Policies and claims
# --------------------------------------------------------------------------
FIRST = ["Aarav", "Meera", "Rohan", "Kavya", "Imran", "Sunita", "Vikram", "Ananya", "Farhan", "Lakshmi",
         "Deepak", "Nisha", "Arjun", "Pooja", "Rahul", "Divya", "Suresh", "Tara", "Manoj", "Rekha"]
LAST = ["Nair", "Iyer", "Sharma", "Menon", "Khan", "Reddy", "Patel", "Fernandes", "Das", "Joshi",
        "Gupta", "Rao", "Thomas", "Singh", "Kulkarni"]
STREETS = ["MG Road", "Lake View Lane", "Temple Street", "Gandhi Nagar 3rd Cross", "Park Avenue",
           "Residency Road", "Harbour Road", "Rose Garden Colony", "Canal Bank Road", "Hill Top Layout"]
CITIES = ["Chennai", "Kochi", "Pune", "Hyderabad", "Bengaluru", "Mumbai"]
INJURIES = ["fractured left wrist", "whiplash and neck strain", "deep laceration on the forearm",
            "bruised ribs and a concussion", "sprained ankle", "second-degree burns on the right hand"]


def build_policies(rng: random.Random, n: int = 140) -> list[dict]:
    pols = []
    for i in range(1, n + 1):
        product = "motor" if i % 2 else "household"
        yr = rng.choice([2022, 2023, 2023, 2024])
        inception = date(yr, rng.randint(1, 12), rng.randint(1, 28))
        periods = []
        start = inception
        for _ in range(rng.choice([2, 3, 3])):
            end = start + timedelta(days=364)
            periods.append({"from": start.isoformat(), "to": end.isoformat()})
            gap = rng.choice([1, 1, 1, 1, 45])  # mostly clean renewal; sometimes a 45-day lapse
            start = end + timedelta(days=gap)
        pols.append({
            "policy_id": f"POL-{'MOT' if product == 'motor' else 'HOM'}-{inception.year}-{i:06d}",
            "product": product,
            "sum_insured": rng.choice([500000, 800000, 1200000]) if product == "motor" else rng.choice([400000, 700000, 1000000]),
            "flood_cover_addon": rng.random() < 0.25,  # Flood Rider (motor) / Flood Extension (household), applies to 2025 wording
            "periods": periods,
        })
    return pols


def in_force(policy: dict, loss_date: str) -> dict:
    for p in policy["periods"]:
        if p["from"] <= loss_date <= p["to"]:
            return {"in_force": True, "period": p}
    return {"in_force": False, "period": None}


def ground_truth(product: str, peril: str, loss_date: str, facts: dict, flood_addon: bool, forced: bool = True) -> dict:
    """The correct exclusion position for a loss, per the wording in force on loss_date.
    Used to write the claim files' final decision AND as the eval oracle."""
    v = version_for_date(loss_date)
    if peril == "wear_and_tear":
        return {"excluded": True, "version": "BASE", "clause": "5.1" if product == "motor" else "6.1", "code": "EXCL-WEAR"}
    if peril in ("collision", "fire"):
        return {"excluded": False, "version": "BASE", "clause": "4.1" if (peril == "collision" or product == "household") else "4.2", "code": "ACCEPT-BASE"}
    clause = {"flood": "5.3" if product == "motor" else "6.2", "theft": "5.4" if product == "motor" else "6.4"}[peril]
    if v == "PRE_2023":
        return {"excluded": None, "version": "PRE_2023", "clause": clause, "code": "NO-WORDING-SET"}
    excluded = False
    if peril == "flood":
        if v == "2023":
            excluded = bool(facts.get("standing_water")) if product == "motor" else bool(facts.get("basement"))
        else:
            excluded = not flood_addon
    else:
        if v == "2023":
            excluded = bool(facts.get("keys_in_vehicle")) if product == "motor" else not facts.get("forced_entry", True)
        else:
            if product == "motor":
                excluded = bool(facts.get("keys_in_vehicle") or facts.get("unattended_unlocked"))
            else:
                excluded = (not facts.get("forced_entry", True)) or bool(facts.get("unoccupied_30d"))
    return {"excluded": excluded, "version": v, "clause": clause,
            "code": f"EXCL-{peril.upper()}-{v}" if excluded else f"ACCEPT-{peril.upper()}-{v}"}


def _fnol(rng, product, peril, loss_date, name, addr, phone, facts, injury):
    nm = name
    if product == "motor":
        base = {
            "flood": f"My car was damaged in the flood on {loss_date}. " + ("I drove through standing water on the road and the engine stopped. " if facts.get("standing_water") else "The car was parked outside and the water rose around it. "),
            "theft": f"My car was stolen on {loss_date}. " + ("I had left the key fob inside. " if facts.get("keys_in_vehicle") else "") + ("It was left unlocked outside the shop. " if facts.get("unattended_unlocked") else "It was locked and parked overnight. "),
            "collision": f"I was in a collision on {loss_date}; a truck hit the rear of my car at a junction.",
            "fire": f"There was an engine fire in my car on {loss_date} while parked.",
            "wear_and_tear": f"The gearbox failed on {loss_date}; the mechanic says the clutch plates were worn out.",
        }[peril]
    else:
        base = {
            "flood": f"Water entered our home on {loss_date} after heavy rain. " + ("The damaged items were stored in the basement. " if facts.get("basement") else "The water reached the ground floor living room. "),
            "theft": f"We had a burglary on {loss_date}. " + ("The back door lock was broken open. " if facts.get("forced_entry", True) else "There was no sign of forced entry; the door was found open. ") + ("We had been away for over a month. " if facts.get("unoccupied_30d") else ""),
            "collision": f"A delivery van reversed into our compound wall on {loss_date}, damaging the gate.",
            "fire": f"A short circuit started a fire in the kitchen on {loss_date}.",
            "wear_and_tear": f"The roof started leaking on {loss_date}; the contractor says the sheets have corroded over the years.",
        }[peril]
    pii_tail = f" This is {nm}, living at {addr}, phone {phone}."
    if injury:
        pii_tail += f" I also hurt myself: {injury}."
    return base + pii_tail


def build_claims(policies: list[dict], rng: random.Random, n: int = 500) -> list[dict]:
    limit_rows = build_limits()
    pol_by_id = {p["policy_id"]: p for p in policies}
    claims = []

    def make(claim_id, pol, peril, loss_date, facts, name=None, addr=None, force_in_force=None):
        name = name or f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        addr = addr or f"{rng.randint(2, 98)}, {rng.choice(STREETS)}, {rng.choice(CITIES)} {rng.randint(560001, 682999)}"
        phone = f"9{rng.randint(100000000, 999999999)}"
        injury = rng.choice(INJURIES) if rng.random() < 0.18 else None
        product = pol["product"]
        status = in_force(pol, loss_date)
        gt = ground_truth(product, peril, loss_date, facts, pol["flood_cover_addon"])
        lim = limits_for(product, peril, loss_date, limit_rows)
        if not status["in_force"]:
            decision = {"decision": "DECLINED", "reason_code": "NOT-IN-FORCE", "basis": "loss date outside any period of insurance"}
        elif gt["excluded"]:
            decision = {"decision": "DECLINED", "reason_code": gt["code"], "basis": f"clause {gt['clause']} ({gt['version']} wording)"}
        else:
            decision = {"decision": "ACCEPTED", "reason_code": gt["code"], "basis": f"clause {gt['clause']} ({gt['version']} wording)"}
        notes = (f"Spoke with insured {name} at {addr} (ph {phone}). "
                 f"Facts recorded: {json.dumps(facts, sort_keys=True)}. " + (f"Insured reports injury: {injury}. " if injury else "")
                 + "Photos reviewed; no inconsistency noted.")
        return {
            "claim_id": claim_id, "policy_id": pol["policy_id"], "product": product, "peril": peril,
            "loss_date": loss_date, "notified_date": loss_date,
            "fnol_text": _fnol(rng, product, peril, loss_date, name, addr, phone, facts, injury),
            "adjuster_notes": notes,
            "photo_captions": [f"Photo 1: {peril.replace('_', ' ')} damage, {product} claim", "Photo 2: wide shot of insured location"],
            "facts": facts,
            "pii": {"name": name, "address": addr, "phone": phone, "injury": injury},
            "final_decision": decision,
            "limits_applied": {"deductible": lim["deductible"] if lim else None, "sub_limit": lim["sub_limit"] if lim else None,
                               "policy_limit": pol["sum_insured"]},
        }

    # Hand-placed claims: the straddle cases, including the headline 4471-A and the day-before case.
    motor_nof = next(p for p in policies if p["product"] == "motor" and not p["flood_cover_addon"] and len(p["periods"]) >= 2)
    house_nof = next(p for p in policies if p["product"] == "household" and not p["flood_cover_addon"] and len(p["periods"]) >= 2)
    # Make the pinned policies cover the dates the pinned claims need.
    motor_nof["periods"] = [{"from": "2023-06-01", "to": "2024-05-31"}, {"from": "2024-06-01", "to": "2025-05-31"}, {"from": "2025-06-01", "to": "2026-05-31"}]
    house_nof["periods"] = [{"from": "2023-03-01", "to": "2024-02-29"}, {"from": "2024-03-01", "to": "2025-02-28"}, {"from": "2025-03-01", "to": "2026-02-28"}]
    pinned = [
        ("CLM-2024-4471A", motor_nof, "flood", "2024-08-11", {"standing_water": False}, "Ritu Varghese", "14, Lake View Lane, Kochi 682011"),
        ("CLM-2025-0002A", motor_nof, "flood", "2025-02-28", {"standing_water": False}, None, None),   # day before 2025 took effect
        ("CLM-2025-0003A", motor_nof, "flood", "2025-03-01", {"standing_water": False}, None, None),   # first day of 2025
        ("CLM-2024-0004A", house_nof, "flood", "2024-11-15", {"basement": False}, None, None),
        ("CLM-2025-0005A", house_nof, "flood", "2025-02-28", {"basement": False}, None, None),
        ("CLM-2024-0006A", motor_nof, "theft", "2024-12-20", {"keys_in_vehicle": False, "unattended_unlocked": True}, None, None),
        ("CLM-2025-0007A", house_nof, "flood", "2025-08-05", {"basement": False}, None, None),          # non-straddle control, 2025 loss
        ("CLM-2023-0008A", motor_nof, "flood", "2023-09-14", {"standing_water": True}, None, None),    # non-straddle, 2023 loss, excluded under 2023
        ("CLM-2025-0009A", motor_nof, "flood", "2025-01-31", {"standing_water": False}, None, None),
        ("CLM-2025-0010A", motor_nof, "flood", "2025-02-27", {"standing_water": False}, None, None),
    ]
    for cid, pol, peril, d, facts, nm, ad in pinned:
        claims.append(make(cid, pol, peril, d, facts, nm, ad))

    perils = ["flood"] * 30 + ["theft"] * 25 + ["collision"] * 25 + ["fire"] * 10 + ["wear_and_tear"] * 10
    for i in range(len(claims), n):
        pol = rng.choice(policies)
        peril = rng.choice(perils)
        if pol["product"] == "household" and peril == "collision":
            peril = "fire"
        # loss date: mostly inside a period, ~10% outside
        if rng.random() < 0.9:
            per = rng.choice(pol["periods"])
            lo, hi = date.fromisoformat(per["from"]), date.fromisoformat(per["to"])
            d = lo + timedelta(days=rng.randint(0, (hi - lo).days))
        else:
            d = date(2023, 1, 1) + timedelta(days=rng.randint(0, 1100))
        if peril == "flood" and rng.random() < 0.55:
            # push a share of flood losses to the months around the 2025 boundary
            d = date(2024, 9, 1) + timedelta(days=rng.randint(0, 330))
        facts = {}
        if pol["product"] == "motor":
            facts = {"standing_water": rng.random() < 0.4, "keys_in_vehicle": rng.random() < 0.2, "unattended_unlocked": rng.random() < 0.25}
        else:
            facts = {"basement": rng.random() < 0.35, "forced_entry": rng.random() < 0.8, "unoccupied_30d": rng.random() < 0.1}
        claims.append(make(f"CLM-{d.year}-{i + 1:05d}", pol, peril, d.isoformat(), facts))
    return claims


def build_all() -> dict:
    rng = random.Random(SEED)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    wording = build_wording()
    limits = build_limits()
    policies = build_policies(rng)
    claims = build_claims(policies, rng)
    for name, obj in (("wording_clauses", wording), ("limits", limits), ("policies", policies), ("claims", claims)):
        (DATA_DIR / f"{name}.json").write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"wording_chunks": len(wording), "limits_rows": len(limits), "policies": len(policies), "claims": len(claims)}


def load(name: str):
    return json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
