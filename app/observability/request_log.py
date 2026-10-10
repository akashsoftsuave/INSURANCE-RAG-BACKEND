"""The Week-11 structured request log — one JSON line per request.

Design rules (and the drill mistake each one prevents):

* Redact on write. Every free-text field goes through redact_pii() before
  the line exists. Chunk TEXT is never logged here, only chunk ids/pages/
  sections — the policy schedule itself holds owner name, address and
  vehicle number.
* Log the retrieved context ids. Without them a wrong coverage call cannot
  be split into "retrieval missed the exclusion" vs "model ignored it".
* Index the output as well as the input (schema v2). The complaint describes
  the output; input-only indexes cost the finder most of the five minutes.
* Spans carry their own stage, latency, tokens and cost, so cost-by-stage is
  a sum, not an estimate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from app.core.config import settings
from app.core.redaction import redact_pii
from app.observability.indexers import (classify_coverage_verdict, classify_input_type,
                                        exclusion_context_ids)

_lock = Lock()

SCHEMA_V1 = "week11-log-v1"
SCHEMA_V2 = "week11-log-v2"


class RequestLog:
    def __init__(self, path: str | Path | None = None, schema: str | None = None):
        self.path = Path(path or settings.REQUEST_LOG_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = schema or settings.REQUEST_LOG_SCHEMA
        if self.schema not in (SCHEMA_V1, SCHEMA_V2):
            raise ValueError(f"unknown request-log schema {self.schema!r}")

    def build_record(self, *, trace_id: str, user_id: str | None, channel: str,
                     collection: str | None, question: str, answer: str,
                     prompt_version: str | None, model: str | None,
                     context_chunks: list[dict], spans: list[dict], totals: dict,
                     guardrail: dict, rate_limit: dict | None,
                     timestamp: str | None = None) -> dict:
        input_type = classify_input_type(question)
        record = {
            "schema": self.schema,
            "ts": timestamp or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "trace_id": trace_id,
            "user_id": user_id or "anonymous",
            "channel": channel,
            "collection": collection,
            "prompt_version": prompt_version,
            "model": model,
            "input_type": input_type,
            "question_redacted": redact_pii(question),
            "answer_redacted": redact_pii(answer),
            "context_ids": [
                {
                    "chunk_id": str(c.get("id")),
                    "page": (c.get("metadata") or {}).get("page"),
                    "section": (c.get("metadata") or {}).get("section"),
                    "rerank_score": round(c["rerank_score"], 4) if c.get("rerank_score") is not None else None,
                }
                for c in context_chunks
            ],
            "guardrail": {"allowed": guardrail.get("allowed"), "reason": guardrail.get("reason")},
            "spans": spans,
            "totals": totals,
            "rate_limit": rate_limit,
            "redaction": {"applied_on_write": True, "method": "app.core.redaction.redact_pii",
                          "chunk_text_logged": False},
        }
        if self.schema == SCHEMA_V2:
            verdict = classify_coverage_verdict(answer, input_type)
            excl_ids = exclusion_context_ids(context_chunks)
            record["coverage_verdict"] = verdict
            record["exclusion_context_ids"] = excl_ids
            # The drill's one-hop index: the answer said "covered" while at
            # least one chunk the model was shown carries exclusion language.
            record["contradiction_flag"] = bool(verdict in ("covered", "partial") and excl_ids)
        return record

    def write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with _lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        with self.path.open(encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
