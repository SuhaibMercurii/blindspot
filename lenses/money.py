"""Lens 2, Money: who wrote the pages the answer is built on?

A plain "best <category>" search, the way AI search grounds an answer. The model lists the
companies the results recommend; code keeps only names that are in the results. Every page
is opened and flagged in code: a vendor's own page, a page that says it ranks its own
product, an affiliate link or disclosure sentence, or over a year old.

Filter test: the model is asked for its single best pick twice, once from all the pages and
once from only the unflagged ones. Code compares the two names and the page reports
whether filtering changed the pick, whichever way it comes out.
"""
import asyncio
import re
from datetime import date

import config
import providers
from lenses.base import (Ctx, clean_list, find_stake, is_vendor_site, name_in_text, name_tokens,
                         norm_text, on_topic, page_date, public, safe, same_company,
                         sources_text, topic_words)

NORMAL_SYSTEM = """These are web search results for the query: "{query}".
The person asking wants: {need}
List every company the results recommend or rank as an option for that query. Use only these results.
Skip publishers and investors unless they are ranked as an option.
Some results are about a neighbouring topic. Skip a company that is only recommended for something else than what the person wants.
For each company:
- name: the company's name as written in the results
- does: what it does in at most 15 plain words, without its name, product names or praise words
- sources: ids of the results that mention it, like "n-2"
Return JSON only: {{"companies": [...]}}"""

PICK_SYSTEM = """These are web search results for the query: "{query}".
The person asking wants: {need}
Using only these results, name the single company you would recommend first.
Return JSON only: {{"pick": "the company's name as written in the results", "why": "at most 12 words"}}"""


async def filter_test(ctx: Ctx, query: str, sources: list[dict]) -> dict:
    """Does removing the flagged pages change the model's top pick? Two calls, compared in code."""
    clean = [s for s in sources if not s.get("flagged")]
    out = {"all_pages": len(sources), "clean_pages": len(clean), "all_pick": "", "clean_pick": "",
           "changed": None, "status": ""}
    if len(clean) == len(sources):
        out["status"] = "no_flagged"   # nothing to filter
        return out
    if not clean:
        out["status"] = "no_clean"     # every page is flagged
        return out

    async def pick(pages: list[dict], label: str) -> str:
        raw = await safe(providers.llm_json(PICK_SYSTEM.format(query=query, need=ctx.need),
                                            sources_text(pages), "pick", 300, temperature=0.2),
                         ctx.errors, f"filter test ({label})", {}, 40)
        name = re.sub(r"\s+", " ", str(raw.get("pick", ""))).strip()
        text = " ".join(f"{s['title']} {s['content']}" for s in pages)
        # A pick that is not named in the pages it was given does not count.
        return name if name and name_in_text(name, text) else ""

    out["all_pick"], out["clean_pick"] = await asyncio.gather(
        pick(sources, "all pages"), pick(clean, "unflagged pages"))
    if out["all_pick"] and out["clean_pick"]:
        out["changed"] = not same_company(out["all_pick"], out["clean_pick"])
        out["status"] = "done"
    else:
        out["status"] = "no_pick"
    return out


async def run(ctx: Ctx):
    query = f"best {ctx.category}"
    results = await asyncio.wait_for(providers.search(query, "normal", config.NORMAL_RESULTS), 60)
    retried = ""
    words = topic_words(ctx.category)

    def about(rs) -> int:
        return sum(on_topic(f"{r['title']} {r['content']}", words) for r in rs)

    # A "best X" search can come back about the word "best" (dictionary pages). That is a
    # failed search, not a finding. Run the same query once more at Tavily's advanced depth
    # and keep whichever answer is more about the category. The page says so.
    if words and about(results) < max(2, len(results) // 3) \
            and not config.MOCK and config.SEARCH_PROVIDER == "tavily":
        deeper = await safe(providers.search(query, "normal", config.NORMAL_RESULTS, deep=True),
                            ctx.errors, "normal search (second try)", None, 45)
        if deeper is not None and about(deeper) > about(results):
            results, retried = deeper, "advanced"
    ctx.saw(results)
    sources: list[dict] = [{**r, "id": f"n-{i}"} for i, r in enumerate(results, 1)]
    by_id = {s["id"]: s for s in sources}
    routing = list(getattr(results, "routing", []) or [])

    async def nothing():
        return {}

    extract_task = asyncio.create_task(safe(
        providers.llm_json(NORMAL_SYSTEM.format(query=query, need=ctx.need),
                           sources_text(sources), "normal", 2500, temperature=0.2),
        ctx.errors, "normal extract", {}) if sources else nothing())
    # Open the pages behind the search while the model reads the results.
    pages_task = asyncio.create_task(safe(
        providers.extract([s["url"] for s in sources[:config.EXTRACT_PAGES]]),
        ctx.errors, "opening pages", providers.Pages(), 50))

    raw = await extract_task
    companies = []
    for c in clean_list(raw.get("companies"), 15):
        cited = c.get("sources") if isinstance(c.get("sources"), list) else []
        ids = [i for i in cited if isinstance(i, str) and i in by_id]
        ids = [i for i in ids if name_in_text(c["name"], f"{by_id[i]['title']} {by_id[i]['content']}")]
        if not ids:
            ids = [s["id"] for s in sources if name_in_text(c["name"], f"{s['title']} {s['content']}")]
        if not ids:
            continue  # the model named a company that is not in the results
        c["sources"] = ids
        c["in_memory"] = ctx.in_memory(c["name"])
        companies.append(c)
    names = [c["name"] for c in companies]

    today = date.today()
    pages = await pages_task
    for s in sources:
        key = s["url"].rstrip("/")
        page = pages.get(key, "")
        s["opened"] = bool(page)
        s["opened_by"] = getattr(pages, "by", {}).get(key, "")
        s["open_error"] = "" if page else getattr(pages, "failed", {}).get(key, "")
        seen_text = f"{s['title']} {s['content']} {page}"
        low = norm_text(seen_text)

        # Vendor: the page is on the site of a company that this search (or the AI's own
        # list) recommends, and the page names that company.
        for name in names + list(ctx.memory_spellings):
            brand = [t for t in name_tokens(name) if len(t) >= 3]
            if is_vendor_site(s["url"], name) and (
                    name_in_text(name, seen_text)
                    or any(re.search(rf"\b{re.escape(t)}\b", low) for t in brand)):
                s["vendor"] = name
                break

        # Age. Only the page's own date line can call a page old. A search engine's date is
        # often the day an evergreen page was first published, so it is shown but never flags.
        shown, line = page_date(page)
        if shown:
            s["date"], s["date_from"], s["date_note"] = shown, "page", line
        elif s.get("date"):
            s["date_from"] = "search"
        try:
            s["stale"] = s.get("date_from") == "page" and \
                (today - date.fromisoformat(str(s.get("date", ""))[:10])).days > 365
        except ValueError:
            s["stale"] = False

        s["paid_kind"], s["paid"] = find_stake(page or s["content"])
        s["flagged"] = bool(s.get("vendor") or s["paid"] or s["stale"])
    for c in companies:
        c["flagged_only"] = all(by_id[i].get("flagged") for i in c["sources"])
    overlap = {c["in_memory"] for c in companies if c["in_memory"]}
    ctx.normal_names = names
    # Everything normal search put in front of the AI, so "it was not there" can be checked in code.
    ctx.normal_text = "\n".join(f"{s['title']} {s['content']} {pages.get(s['url'].rstrip('/'), '')}" for s in sources)
    ctx.money_ok = True

    event = {
        "stage": "money",
        "query": query,
        "searched_with": providers.search_label("normal"),
        "routing": routing,
        "retried": retried,
        "companies": [
            {"name": c["name"], "does": c["does"], "sources": c["sources"],
             "in_memory": bool(c["in_memory"]), "flagged_only": c["flagged_only"]}
            for c in companies
        ],
        "flagged_only": sum(1 for c in companies if c["flagged_only"]),
        "memory_overlap": sorted(overlap),
        "echo": {"repeated": sum(1 for c in companies if c["in_memory"]), "total": len(companies)},
        "stake": {
            "vendor": sum(1 for s in sources if s.get("vendor") or s.get("paid_kind") == "self"),
            "paid": sum(1 for s in sources if s.get("paid_kind") in ("link", "wording")),
            "stale": sum(1 for s in sources if s.get("stale")),
            "flagged": sum(1 for s in sources if s.get("flagged")),
            "opened": sum(1 for s in sources if s.get("opened")),
            "tried": min(len(sources), config.EXTRACT_PAGES),
            "total": len(sources),
        },
        "sources": [{**public(s), "flagged": bool(s.get("flagged"))} for s in sources],
        "filter_test": {"status": "running", "all_pages": len(sources),
                        "clean_pages": sum(1 for s in sources if not s.get("flagged"))},
    }
    yield event  # the flags are ready; the filter test follows
    event = {**event, "filter_test": await filter_test(ctx, query, sources)}
    yield event
