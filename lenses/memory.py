"""Lens 1, Memory: how much does the AI repeat the same names from training?

The same no-search question is asked HIVEMIND_SAMPLES times, each time worded differently,
at the normal temperature, spread across every LLM family with a key. Code merges name
variants, counts how many answers named each company, and splits the count per model, so
the page never says "all AIs agree" when one family did the talking.
"""
import asyncio
from collections import Counter

import config
import providers
from lenses.base import Ctx, clean_list, reason, same_company

MEMORY_SYSTEM = """Answer only from what you already know. Do not search and do not mention browsing.
List the companies you would recommend for this request, best first, up to 10.
For each, give the company's own name (not the name of one of its products) and say what it does in at most 15 plain words. Do not use its name, its product names, or praise words (leading, best, top) in that description.
Return JSON only: {"companies": [{"name": "...", "does": "..."}]}"""

# Realistic ways different people ask the same question. If eight different phrasings (and
# several AI models) still return the same names, the repetition is not one prompt's doing.
PHRASINGS = [
    "{q}",
    "Which companies would you recommend for this? {q}",
    "I'm an investor mapping this market. Which companies lead here? {q}",
    "I'm a CTO choosing a vendor. What are the top companies for this? {q}",
    "What are the most innovative companies working on this? {q}",
    "List the best options available right now for this: {q}",
    "Which startups and established companies stand out here? {q}",
    "Who should I talk to first if I need this? {q}",
]


def cluster_answers(answers: list[list[dict]], labels: list[str] | None = None) -> list[dict]:
    """Merge name variants across answers ("Intercom" / "Intercom Fin") and count how many
    answers named each company, in total and per model (labels[i] is answer i's model).

    same_company() is not transitive ("Synopsys DSO.ai" and "Synopsys Fusion Compiler" both
    match "Synopsys" but not each other), so variants are linked through a union-find and
    the result does not depend on the order the answers came back in.
    Returns [{name, does, count, by_model, variants}] with the most-used spelling as the name."""
    labels = labels or [""] * len(answers)
    names: list[str] = []
    does: dict[str, str] = {}
    for answer in answers:
        for c in answer:
            if c["name"] not in does:
                names.append(c["name"])
                does[c["name"]] = c["does"]
    parent = {n: n for n in names}

    def root(n: str) -> str:
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    for i, a in enumerate(names):
        for b in names[:i]:
            if same_company(a, b):
                ra, rb = root(a), root(b)
                if ra != rb:
                    parent[max(ra, rb, key=names.index)] = min(ra, rb, key=names.index)

    groups: dict[str, dict] = {}
    for answer, label in zip(answers, labels):
        counted = set()
        for c in answer:
            r = root(c["name"])
            g = groups.setdefault(r, {"spellings": Counter(), "count": 0, "by": Counter(),
                                      "first": names.index(r)})
            g["spellings"][c["name"]] += 1
            if r not in counted:  # count once per answer
                counted.add(r)
                g["count"] += 1
                g["by"][label] += 1
    out = []
    for g in groups.values():
        spellings = g["spellings"]
        best = min(spellings, key=lambda n: (-spellings[n], len(n), names.index(n)))
        out.append({"name": best, "does": does[best], "count": g["count"], "first": g["first"],
                    "by_model": {k: v for k, v in g["by"].items() if k},
                    "variants": sorted(spellings, key=names.index)})
    out.sort(key=lambda t: (-t["count"], t["first"]))
    return out


async def run(ctx: Ctx):
    families = ctx.families
    n = config.HIVEMIND_SAMPLES
    sample_families = [families[i % len(families)] for i in range(n)]
    prompts = [PHRASINGS[i % len(PHRASINGS)].format(q=ctx.question)
               + (f"\nSpecific need: {ctx.specific}" if ctx.specific else "") for i in range(n)]
    samples = await asyncio.gather(
        *(asyncio.wait_for(
            providers.llm_json(MEMORY_SYSTEM, prompt, "memory", 900, fam, config.HIVEMIND_TEMPERATURE), 60)
          for fam, prompt in zip(sample_families, prompts)),
        return_exceptions=True)
    answers, labels = [], []
    for fam, sample in zip(sample_families, samples):
        if isinstance(sample, BaseException) or not isinstance(sample, dict):
            ctx.errors.append(f"memory sample ({fam}): {reason(sample) if isinstance(sample, BaseException) else 'no answer'}"[:300])
            continue
        answers.append(clean_list(sample.get("companies"), 10))
        labels.append(providers.model_name(fam))
    if not answers:
        raise RuntimeError("every memory call failed: " + " | ".join(ctx.errors[-2:]))
    tally = cluster_answers(answers, labels)  # every distinct company, most-repeated first
    ok = len(answers)
    ctx.memory_ok = True
    ctx.memory_samples = ok
    ctx.memory_spellings = {v: t["name"] for t in tally for v in t["variants"]}
    ctx.memory_counts = {t["name"]: t["count"] for t in tally}

    mentions = sum(t["count"] for t in tally)
    top5 = sum(t["count"] for t in tally[:5])
    asked = Counter(labels)
    per_model = []
    for model, k in asked.items():
        named = [t for t in tally if t["by_model"].get(model)]
        named.sort(key=lambda t: (-t["by_model"][model], t["first"]))
        per_model.append({"model": model, "samples": k, "distinct": len(named),
                          "top": [{"name": t["name"], "count": t["by_model"][model]} for t in named[:5]]})
    shown = tally[:10]
    yield {
        "stage": "memory",
        "question": ctx.question,
        "specific": ctx.specific,
        "category": ctx.category,
        "need": ctx.need,
        "companies": [{"name": c["name"], "does": c["does"], "count": c["count"],
                       "by_model": c["by_model"],
                       "variants": [v for v in c["variants"] if v != c["name"]]} for c in shown],
        "hivemind": {
            "samples": ok,
            "asked": n,
            "temperature": config.HIVEMIND_TEMPERATURE,
            "phrasings": [PHRASINGS[i % len(PHRASINGS)].format(q="…") for i in range(min(n, len(PHRASINGS)))],
            "models": [f"{m} x{k}" for m, k in asked.items()],
            "per_model": per_model,
            "distinct": len(tally),
            "top5_share": round(100 * top5 / mentions) if mentions else 0,
            "others": [{"name": t["name"], "count": t["count"]} for t in tally[10:]],
        },
    }
