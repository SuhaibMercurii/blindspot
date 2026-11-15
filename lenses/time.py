"""Lens 3, Time: which recently funded or launched companies did the AI leave out?

Four recent-news searches (seed, Series A, out of stealth, and the plain "new startups"
search a person would type) plus YC's public directory. Results that never mention the
category are dropped in code before the model sees them. The model picks young companies
that fit; code keeps a company only if its name is in the source it is cited from, and
shows funding only if the quoted phrase is in that source word for word.

Companies the AI already named are kept and marked, because the headline needs both groups.
"""
import asyncio
from datetime import date

import config
import providers
from lenses.base import (Ctx, amount_in, clean_list, domain, evidence_found, find_match,
                         name_in_text, on_topic, public, reason, safe, source_type, sources_text,
                         topic_words, yc_candidates)

NEW_SYSTEM = """You find young companies in news results and a startup directory.
Category: {category}
Need: {need}
List at most 14 companies from the sources that (1) sell their own product that fits this category and need and (2) look like startups: recently founded, funded, launched, out of stealth, or in a recent YC batch.
Skip investors, publishers, customers, partners, acquirers, big established companies, and anything whose product does not fit the category. If a source does not say what the company's product does, leave the company out.
For each company:
- name: exactly as written in the source
- does: what it does in at most 15 plain words, taken from the source, without its name, product names or praise words
- round: e.g. "Seed", "Series A", "Pre-seed", or ""
- amount: e.g. "$24.5M", or ""
- when: month and year if the source says it, or ""
- investors: up to 3 investor names from the source, or []
- evidence: one short phrase (6-25 words) copied character for character from the source that shows the funding, launch or batch
- sources: ids of the sources that mention it, like "d-3" or "y-1"
Return JSON only: {{"companies": [...]}}"""

PLAIN_LABEL = 'Plain "new startups" search'


async def run(ctx: Ctx):
    year = date.today().year
    discovery = [
        ("Seed round news", f"{ctx.area} startup raises seed round"),
        ("Series A news", f"{ctx.area} startup raises Series A"),
        ("Out of stealth news", f"{ctx.area} startup comes out of stealth"),
        (PLAIN_LABEL, f"new {ctx.area} startups {year}"),  # what a person would type
    ]
    results = await asyncio.gather(
        *(asyncio.wait_for(providers.search(q, "discovery", config.DISCOVERY_RESULTS, recent=True), 60)
          for _, q in discovery), return_exceptions=True)

    relevant = topic_words(ctx.category, ctx.area, *ctx.keywords)
    sources: list[dict] = []
    seen: dict[str, dict] = {}
    off_topic: set[str] = set()
    failed = 0
    for (label, q), res in zip(discovery, results):
        if isinstance(res, BaseException):
            failed += 1
            ctx.errors.append(f"search '{q}': {reason(res)}"[:300])
            continue
        ctx.saw(res)
        for r in res:
            if r["url"] in seen:  # found by more than one search
                if label not in seen[r["url"]]["found_by"]:
                    seen[r["url"]]["found_by"].append(label)
                continue
            # A result that never mentions the category is noise. Drop it before the model.
            if relevant and not on_topic(f"{r['title']} {r['content']}", relevant, 1):
                off_topic.add(r["url"])
                continue
            src = {**r, "id": f"d-{len(sources) + 1}", "found_by": [label]}
            seen[r["url"]] = src
            sources.append(src)
    if failed == len(discovery) and not config.USE_YC:
        raise RuntimeError("every discovery search failed")

    yc_sources = []
    if config.USE_YC:
        for i, c in enumerate(yc_candidates(ctx.area, ctx.keywords), 1):
            yc_sources.append({
                "id": f"y-{i}",
                "title": f"{c['name']} (YC {c['batch']})",
                "url": c["yc_url"] or c["website"],
                "date": "",
                "content": f"{c['name']}: {c['one_liner']}. {c['description']}",
                "provider": "YC directory",
                "engines": [],
                "found_by": ["YC directory"],
                "batch": c["batch"],
                "website": c.get("website") or "",
            })
    all_sources = sources + yc_sources
    by_id = {s["id"]: s for s in all_sources}

    raw = await safe(
        providers.llm_json(NEW_SYSTEM.format(category=ctx.category, need=ctx.need),
                           sources_text(all_sources, 700), "new", 3500, temperature=0.2),
        ctx.errors, "time lens extract", {}) if all_sources else {}

    found, not_in_source = [], 0
    for c in clean_list(raw.get("companies"), 25):
        cited = c.get("sources") if isinstance(c.get("sources"), list) else []
        ids = [i for i in cited if isinstance(i, str) and i in by_id]
        ids = [i for i in ids if name_in_text(c["name"], f"{by_id[i]['title']} {by_id[i]['content']}")]
        if not ids:
            not_in_source += 1  # the name is not in any source the model cited for it
            continue
        if find_match(c["name"], [f["name"] for f in found]):
            continue
        quote = str(c.get("evidence", "")).strip()
        amount = str(c.get("amount", "")).strip()
        # A quote must come from the source's own words. For a directory entry that is its
        # description, not the "Name (YC batch)" label this code wrote for it.
        texts = {i: by_id[i]["content"] if i.startswith("y-") else f"{by_id[i]['title']} {by_id[i]['content']}"
                 for i in ids}
        quote_ok = evidence_found(quote, list(texts.values()))
        funding_ok = quote_ok and amount_in(amount, quote)
        yc = next((by_id[i] for i in ids if i.startswith("y-")), None)
        website = yc["website"] if yc else ""
        found_by, engines, items = [], [], []
        for i in ids:
            s = by_id[i]
            for label in s.get("found_by", []):
                if label not in found_by:
                    found_by.append(label)
            for e in s.get("engines", []):
                if e not in engines:
                    engines.append(e)
            items.append({
                # the quote is attached only to a source that contains it word for word
                "quote": quote if quote_ok and evidence_found(quote, [texts[i]]) else "",
                "url": s["url"], "domain": domain(s["url"]), "title": s["title"], "date": s.get("date", ""),
                "source_type": source_type(s["url"], c["name"], directory=i.startswith("y-"), website=website),
                "engine": list(s.get("engines", [])), "platform": "", "engagement": None,
            })
        investors = c.get("investors") if isinstance(c.get("investors"), list) else []
        memory_name = ctx.in_memory(c["name"])
        found.append({
            "id": f"t-{len(found) + 1}",
            "name": c["name"],
            "aliases": [],
            "kind": "company",
            "does": c["does"],
            "lens": "time",
            "round": str(c.get("round", "")).strip() if funding_ok else "",
            "amount": amount if funding_ok else "",
            "when": str(c.get("when", "")).strip(),
            "investors": [str(x) for x in investors][:3] if funding_ok else [],
            "evidence": quote if quote_ok else "",
            "evidence_ok": quote_ok,
            "evidence_list": items,
            "yc_batch": yc["batch"] if yc else "",
            "found_by": found_by,
            "engines": engines,
            "sources": ids,
            "name_verified": True,
            "memory_count": ctx.memory_counts.get(memory_name, 0) if memory_name else 0,
        })
        if len(found) >= 12:
            break

    yield {
        "stage": "time",
        "queries": [q for _, q in discovery],
        "searched_with": providers.search_label("discovery"),
        "companies": found,
        "plain_label": PLAIN_LABEL,
        "plain_found": sum(1 for f in found if PLAIN_LABEL in f["found_by"]),
        "already_known": sorted(f["name"] for f in found if f["memory_count"]),
        "not_in_source": not_in_source,
        "off_topic_results": len(off_topic),
        "sources": [public(s) for s in all_sources],
    }
