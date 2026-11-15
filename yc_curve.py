"""The proof chart: how many YC companies does the AI actually know, by batch year?

For a fixed random sample of YC companies (data/yc_sample.json, ~30 per year
from 2016 to 2026, plus a control group from YC's own "top companies" list),
the model is asked, with no search, what each company does. A second call
grades each guess against YC's real one-liner. Result: data/yc_curve.json,
shown at http://localhost:8000/curve

Run:   python yc_curve.py
       python yc_curve.py --cutoff 2025      # draw the model's stated training cutoff year
Cost:  about 36 small LLM calls. Takes 1-2 minutes.
"""
import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

import config
import providers

ROOT = Path(__file__).parent
SAMPLE = ROOT / "data" / "yc_sample.json"
OUT = ROOT / "data" / "yc_curve.json"
CHUNK = 20

GUESS_SYSTEM = """For each company below (name and Y Combinator batch), say what it does in at most 12 words, using only what you already know.
If you do not know this specific company, answer exactly UNKNOWN. Do not guess from the name.
Each line is: id | name | batch
Return JSON only: {"answers": [{"id": "...", "guess": "..."}]}"""

GRADE_SYSTEM = """For each line, decide whether the guess describes the same business as the truth.
Wording can differ, but the product and the customer must match. UNKNOWN is never correct.
Each line is: id | guess: ... | truth: ...
Return JSON only: {"grades": [{"id": "...", "correct": true}]}"""


async def run_chunk(rows: list[dict], sem: asyncio.Semaphore) -> None:
    async with sem:
        lines = "\n".join(f"{r['id']} | {r['name']} | {r['batch']}" for r in rows)
        guesses = await providers.llm_json(GUESS_SYSTEM, lines, "curve_guess", 2000)
        by_id = {str(a.get("id")): str(a.get("guess", "")).strip()
                 for a in guesses.get("answers", []) if isinstance(a, dict)}
        for r in rows:
            r["guess"] = by_id.get(r["id"], "UNKNOWN") or "UNKNOWN"
        to_grade = [r for r in rows if r["guess"].upper() != "UNKNOWN"]
        for r in rows:
            r["correct"] = False
        if not to_grade:
            return
        lines = "\n".join(f"{r['id']} | guess: {r['guess']} | truth: {r['one_liner']}" for r in to_grade)
        grades = await providers.llm_json(GRADE_SYSTEM, lines, "curve_grade", 1500)
        ok = {str(g.get("id")) for g in grades.get("grades", [])
              if isinstance(g, dict) and g.get("correct") is True}
        for r in to_grade:
            r["correct"] = r["id"] in ok


async def main(cutoff: int | None) -> None:
    sample = json.loads(SAMPLE.read_text())
    rows = [{**s, "id": f"k{n}"} for n, s in enumerate(sample, 1)]
    sem = asyncio.Semaphore(4)
    chunks = [rows[i:i + CHUNK] for i in range(0, len(rows), CHUNK)]
    print(f"Asking about {len(rows)} YC companies in {len(chunks)} batches...")
    results = await asyncio.gather(*(run_chunk(c, sem) for c in chunks), return_exceptions=True)
    failed = 0
    for chunk, res in zip(chunks, results):
        if isinstance(res, Exception):
            failed += len(chunk)
            print(f"  a batch failed: {type(res).__name__}: {res}")
            for r in chunk:
                r["error"] = True

    good = [r for r in rows if not r.get("error")]
    per_year = defaultdict(lambda: [0, 0])
    for r in good:
        if not r["top_company"]:
            per_year[r["year"]][0] += 1
            per_year[r["year"]][1] += int(r["correct"])
    famous = [r for r in good if r["top_company"]]
    data = {
        "mode": "mock" if config.MOCK else "live",
        "model": "sample" if config.MOCK else providers.model_name(providers.available_llms()[0]),
        "measured_at": time.strftime("%Y-%m-%d %H:%M"),
        "cutoff": cutoff,
        "failed": failed,
        "per_year": [
            {"year": y, "n": n, "known": k, "pct": round(100 * k / n) if n else 0}
            for y, (n, k) in sorted(per_year.items())
        ],
        "famous": {
            "n": len(famous),
            "known": sum(r["correct"] for r in famous),
            "pct": round(100 * sum(r["correct"] for r in famous) / len(famous)) if famous else 0,
        },
        "rows": [
            {k: r.get(k) for k in ("name", "batch", "year", "top_company", "one_liner", "guess", "correct")}
            for r in good
        ],
    }
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f"\nModel: {data['model']} ({data['mode']})")
    for p in data["per_year"]:
        print(f"  {p['year']}: {p['known']:>2} of {p['n']:<2} known  {'#' * (p['pct'] // 4)} {p['pct']}%")
    f = data["famous"]
    print(f"  YC top companies (control): {f['known']} of {f['n']} known, {f['pct']}%")
    print(f"\nSaved {OUT.relative_to(ROOT)}. Open http://localhost:8000/curve")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff", type=int, default=None,
                        help="the model's training cutoff year, as its provider states it")
    asyncio.run(main(parser.parse_args().cutoff))
