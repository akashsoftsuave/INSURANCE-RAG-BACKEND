import json
import uuid
from pathlib import Path
from threading import Lock

from app.core.config import settings

_lock = Lock()


class TraceLogger:
    """Append-only JSONL trace log. One line per request, written once,
    never edited in place — traces must reflect what actually happened."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or settings.TRACE_LOG_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def new_trace_id() -> str:
        return str(uuid.uuid4())

    def write(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with _lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        records = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def get(self, trace_id: str) -> dict | None:
        for record in self.read_all():
            if record.get("trace_id") == trace_id:
                return record
        return None
