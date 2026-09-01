"""
Registry of predefined eval datasets.

Each dataset pairs one PDF with its own golden-question set and gets its
own Chroma collection (so ingesting one dataset never clobbers another)
and its own report file.

To add a new dataset: drop the PDF in eval/data/, write its golden
questions in eval/questions/<key>.json, and add an entry below.
"""

from pathlib import Path

DATA_DIR = Path("eval/data")
QUESTIONS_DIR = Path("eval/questions")
REPORTS_DIR = Path("eval/reports")

DATASETS = {
    "shieldcare_full": {
        "pdf": DATA_DIR / "shieldcare_full.pdf",
        "questions": QUESTIONS_DIR / "shieldcare_full.json",
        "report": REPORTS_DIR / "shieldcare_full.md",
        "collection": "eval_shieldcare_full",
    },
    "shieldcare_basic": {
        "pdf": DATA_DIR / "shieldcare_basic.pdf",
        "questions": QUESTIONS_DIR / "shieldcare_basic.json",
        "report": REPORTS_DIR / "shieldcare_basic.md",
        "collection": "eval_shieldcare_basic",
    },
    "acko_bike": {
        "pdf": DATA_DIR / "acko_bike.pdf",
        "questions": QUESTIONS_DIR / "acko_bike.json",
        "report": REPORTS_DIR / "acko_bike.md",
        "collection": "eval_acko_bike",
    },
}
