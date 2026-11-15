"""Sealed room card: predict what a hackathon room will build, before the demos.

The same "what would you build?" question is put to the AI about 100 times, each call
separate, at temperature 1.0, spread across every LLM family with a key. One more call
groups the ideas into at most 15 specific project types. The counting is plain code.
The result goes to sealed/card.md and its SHA-256 is printed. Post the hash publicly
with the time; after the demos, publish the file and match the room by hand.

Needs:  data/event.txt       the public event description (paste it in, unedited)
Option: data/our_ideas.txt   our own early ideas, one per line. They are counted in the
                             same table, marked as ours, so the card never reads as
                             mocking other teams.

Run:    python sealed_card.py                # 100 samples
        python sealed_card.py --samples 40
Do not re-run to get a "better" card. One card, one hash, posted before the demos.
"""
import argparse
import asyncio
import hashlib
import time
from collections import Counter
from pathlib import Path

import config
import providers

ROOT = Path(__file__).parent
EVENT = ROOT / "data" / "event.txt"
OURS = ROOT / "data" / "our_ideas.txt"
OUT = ROOT / "sealed" / "card.md"

IDEA_SYSTEM = """You are taking part in the hackathon described below. Propose the ONE project you would build today.
Return JSON only: {"title": "3-8 words", "idea": "one sentence, at most 30 words, saying what it does and for whom"}"""

GROUP_SYSTEM = """Below are project ideas for one hackathon, one per line as: id | title | idea.
Group them into at most 15 project types. Make each type specific enough that a person could look at a finished project and say whether it belongs (not "AI tools").
Every id must appear in exactly one type.
Return JSON only: {"types": [{"name": "3-8 words", "describes": "one sentence", "ids": ["i1", "i7"]}]}"""


async def one_idea(event: str, family: str) -> dict | Exception:
    try:
        raw = await asyncio.wait_for(
            providers.llm_json(IDEA_SYSTEM, event, "sealed_idea", 300, family, 1.0), 90)
    except Exception as exc:
        return exc
    title, idea = str(raw.get("title", "")).strip(), str(raw.get("idea", "")).strip()
    if not title and not idea:
        return ValueError("empty idea")
    return {"title": title[:120], "idea": idea[:300], "by": providers.model_name(family)}


async def main(samples: int) -> None:
    if not EVENT.exists() or not EVENT.read_text().strip():
        raise SystemExit(f"Put the public event description in {EVENT.relative_to(ROOT)} first.")
    event = EVENT.read_text().strip()
    families = providers.available_llms()
    if not families:
        raise SystemExit("No LLM key in .env.")
    print(f"Asking {samples} times ({', '.join(providers.model_name(f) for f in families)})...")
    replies = await asyncio.gather(*(one_idea(event, families[i % len(families)]) for i in range(samples)))
    ideas = [r for r in replies if isinstance(r, dict)]
    failed = [r for r in replies if not isinstance(r, dict)]
    if failed:
        print(f"  {len(failed)} calls failed, for example: {type(failed[0]).__name__}: {failed[0]}")
    if len(ideas) < max(5, samples // 2):
        raise SystemExit("Too few answers to make a card. Nothing was written.")
    ours = [l.strip() for l in OURS.read_text().splitlines() if l.strip()] if OURS.exists() else []
    for line in ours:
        ideas.append({"title": line[:120], "idea": "", "by": "our team"})
    for i, idea in enumerate(ideas, 1):
        idea["id"] = f"i{i}"

    lines = "\n".join(f"{x['id']} | {x['title']} | {x['idea']}" for x in ideas)
    grouped = await providers.llm_json(GROUP_SYSTEM, lines, "sealed_group", 6000, temperature=0.2)
    by_id = {x["id"]: x for x in ideas}
    types, placed = [], set()
    for t in grouped.get("types", []) if isinstance(grouped.get("types"), list) else []:
        if not isinstance(t, dict):
            continue
        ids = [i for i in dict.fromkeys(str(i).strip() for i in t.get("ids", []) if isinstance(t.get("ids"), list))
               if i in by_id and i not in placed]  # each idea counted once
        if ids:
            placed.update(ids)
            types.append({"name": str(t.get("name", "")).strip() or "Unnamed type",
                          "describes": str(t.get("describes", "")).strip(), "ids": ids})
    types = sorted(types, key=lambda t: -len(t["ids"]))[:15]
    kept = {i for t in types for i in t["ids"]}
    rest = [i for i in by_id if i not in kept]
    if rest:  # ideas the grouping call left out are still counted, in their own row
        types.append({"name": "Not grouped", "describes": "Ideas the grouping step did not place.", "ids": rest})

    total = len(ideas)
    models = Counter(x["by"] for x in ideas if x["by"] != "our team")
    now = time.strftime("%Y-%m-%d %H:%M:%S %Z")
    out = [
        "# Sealed room card" + (" (SAMPLE DATA, made without a model: not a real card)" if config.MOCK else ""),
        "",
        f"Made {now} ({time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC), before the demos.",
        "",
        f"The event description was given to an AI {len(ideas) - len(ours)} separate times with the question "
        f"\"what ONE project would you build today?\", at temperature 1.0 ("
        + ", ".join(f"{m} x{n}" for m, n in models.items()) + "). "
        + (f"Our own {len(ours)} early ideas are counted in the same table. " if ours else "")
        + "One more AI call grouped the ideas into types; the counts and shares below are computed by code.",
        "",
        "| # | Project type | Share of ideas | Ideas" + (" | Ours |" if ours else " |"),
        "|---|---|---|---|" + ("---|" if ours else ""),
    ]
    for n, t in enumerate(types, 1):
        mine = sum(1 for i in t["ids"] if by_id[i]["by"] == "our team")
        out.append(f"| {n} | **{t['name']}**. {t['describes']} | {round(100 * len(t['ids']) / total)}% | {len(t['ids'])} of {total}"
                   + (f" | {mine} |" if ours else " |"))
    out += ["", "## How to check it", "",
            "After the demos, put each team's project in the type it fits, or in \"none of these\". "
            "The card was right about a team if its project fits one of the types above.", "",
            "## Every idea, by type", ""]
    for t in types:
        out.append(f"### {t['name']} ({len(t['ids'])})")
        out += [f"- {by_id[i]['title']}" + (f": {by_id[i]['idea']}" if by_id[i]["idea"] else "")
                + f" ({by_id[i]['by']})" for i in t["ids"]]
        out.append("")
    text = "\n".join(out) + "\n"
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_bytes(text.encode())
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    print(f"\nWrote {OUT.relative_to(ROOT)}: {len(ideas)} ideas in {len(types)} types"
          + (" (SAMPLE DATA, not a real card)" if config.MOCK else ""))
    for t in types[:5]:
        print(f"  {round(100 * len(t['ids']) / total):>3}%  {t['name']}")
    print(f"\nSHA-256  {digest}\nPost the hash and the time now. Keep the file private until after the demos.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--samples", type=int, default=100)
    asyncio.run(main(max(5, parser.parse_args().samples)))
