"""Labeled offline decision benchmark. Not a measure of OCR accuracy."""
import json
from pathlib import Path
from review import decide, normalize

ROOT = Path(__file__).resolve().parent

def run():
    cases = json.loads((ROOT / "eval_cases.json").read_text())
    policy = json.loads((ROOT / "policy.json").read_text())
    rows = [{"id": c["id"], "expected": c["expected"],
             "actual": decide(normalize(c), policy)["decision"]} for c in cases]
    return {"cases": len(rows), "decision_accuracy": sum(r["actual"] == r["expected"] for r in rows) / len(rows),
            "results": rows, "scope": "Synthetic decision rules only; OCR quality not evaluated."}

if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
