import argparse
import json
import re
import time
from pathlib import Path

from groq import Groq, RateLimitError

from app.core.config import settings

TRACES_PATH = Path("traces/traces.jsonl")
LABELS_PATH = Path("eval/labels_27.json")

TEMPERATURE = 0
MAX_RETRIES = 3
PACING_SECONDS = 15.0
_RETRY_SECONDS_RE = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)


def load_traces():
    with TRACES_PATH.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def load_labels():
    return json.loads(LABELS_PATH.read_text(encoding="utf-8"))["labels"]


def build_context(trace: dict) -> str:
    candidates = trace["retrieval"]["candidates"]
    return "\n\n".join(c["text_redacted"] for c in candidates)


def build_user_message(trace: dict) -> str:
    question = trace["question_redacted"]
    context = build_context(trace)
    answer = trace.get("final_answer_redacted") or trace.get("raw_output_redacted") or ""
    return f"QUESTION:\n{question}\n\nCONTEXT:\n{context}\n\nANSWER:\n{answer}"


def parse_verdict(raw_text: str) -> dict:
    match = re.search(r"\{.*\}", raw_text, re.DOTALL)
    if not match:
        return {"verdict": "PARSE_ERROR", "mode": "OTHER", "reason": f"no JSON object in: {raw_text!r}"}
    try:
        obj = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        return {"verdict": "PARSE_ERROR", "mode": "OTHER", "reason": f"JSON decode failed ({e}): {raw_text!r}"}
    obj.setdefault("verdict", "PARSE_ERROR")
    obj.setdefault("mode", "OTHER")
    obj.setdefault("reason", "")
    return obj


def judge_trace(client: Groq, system_prompt: str, trace: dict) -> dict:
    user_message = build_user_message(trace)
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=settings.MODEL_NAME,
                temperature=TEMPERATURE,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
            )
            raw = response.choices[0].message.content
            return parse_verdict(raw)
        except RateLimitError as e:
            match = _RETRY_SECONDS_RE.search(str(e))
            wait = float(match.group(1)) + 1.0 if match else 20.0 * (attempt + 1)
            print(f"  [rate limit] waiting {wait:.1f}s (attempt {attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
    raise RuntimeError("giving up after repeated rate-limit errors")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True, help="path to judge prompt txt file")
    parser.add_argument("--out", required=True, help="path to write results json")
    args = parser.parse_args()

    system_prompt = Path(args.prompt).read_text(encoding="utf-8")
    traces = load_traces()
    labels = load_labels()

    client = Groq(api_key=settings.GROQ_API_KEY)

    rows = []
    first = True
    for trace in traces:
        reference = labels.get(trace["trace_id"])
        if reference is None:
            continue
        if not first:
            time.sleep(PACING_SECONDS)
        first = False
        judged = judge_trace(client, system_prompt, trace)
        agree = judged["verdict"] == reference["label"]
        rows.append({
            "trace_id": trace["trace_id"],
            "question_index": reference["question_index"],
            "dataset": reference["dataset"],
            "question": reference["question"],
            "reference_label": reference["label"],
            "reference_mode": reference["mode"],
            "reference_reason": reference["reason"],
            "judge_verdict": judged["verdict"],
            "judge_mode": judged.get("mode"),
            "judge_reason": judged.get("reason"),
            "agree": agree,
        })
        print(f"{'AGREE' if agree else 'DISAGREE'}  Q{reference['question_index']:>2} "
              f"{reference['dataset']:18s} ref={reference['label']:5s} judge={judged['verdict']:5s}")

    n = len(rows)
    agreement = sum(1 for r in rows if r["agree"]) / n if n else None

    out = {
        "prompt_file": args.prompt,
        "population": n,
        "agreement": round(agreement, 4) if agreement is not None else None,
        "disagreements": [r for r in rows if not r["agree"]],
        "rows": rows,
    }
    Path(args.out).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nAgreement with eval/labels_27.json: {sum(1 for r in rows if r['agree'])}/{n} ({agreement:.2%})")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
