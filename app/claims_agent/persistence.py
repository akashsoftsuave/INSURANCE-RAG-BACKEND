from __future__ import annotations

import json
from pathlib import Path

STORE_PATH = Path(__file__).resolve().parents[2] / "results" / "policy_excess_store.json"


def _load() -> dict:
    if not STORE_PATH.exists():
        return {}
    with STORE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save(data: dict) -> None:
    STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STORE_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def remember_policy_excess(policy_id: str, excess: float) -> None:
    data = _load()
    data[policy_id] = excess
    _save(data)


def recall_policy_excess(policy_id: str) -> float | None:
    return _load().get(policy_id)


def clear() -> None:
    if STORE_PATH.exists():
        STORE_PATH.unlink()
