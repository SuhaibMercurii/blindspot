"""The Blind Spot Report: plan, run the lenses, merge what they found, and count coverage.

    "The AI's answer named K of the N things we found."

"The AI's answer" is every company named in any of the memory answers (lens 1) or listed
by the normal search (lens 2). "The things we found" are the verified entities from the
Time and China lenses. Merging, named-or-not, coverage and the order of the missed list
are plain code. Nothing here says which company is better.
"""
import asyncio
import re
import time

import config
import providers
from lenses import china, engines, memory, money
from lenses import time as time_lens
from lenses.base import COMMON_WORDS, STOP, Ctx, find_match, named_on_pages, reason, safe, same_company

PLAN_SYSTEM = """You turn a buyer's or investor's question (and an optional specific need) into search terms.
Return JSON only:
{"category": "what a buyer would search for, 2-5 words, e.g. AI chip design tools",
 "area": "the same thing as a market name for news searches, 2-4 words, without the words tools, software, platform or companies, e.g. AI chip design",
 "need": "one sentence: what the person needs a company to do",
 "keywords": ["8-12 lowercase words or short phrases a company in this category would use to describe its product"],
 "zh_queries": ["2-3 search queries in Chinese for the same category, as a Chinese user would type them on Xiaohongshu or Bilibili"]}"""

LENS_TIMEOUT = {"memory": 70, "money": 80, "time": 80, "china": 60}


async def make_plan(ctx: Ctx) -> dict:
    asked = ctx.question + (f"\nSpecific need: {ctx.specific}" if ctx.specific else "")
    plan = await safe(providers.llm_json(PLAN_SYSTEM, asked, "plan", 900, temperature=0.2),
                      ctx.errors, "plan", {}, 60)

    def strings(value, limit):
        return [str(v).strip() for v in (value if isinstance(value, list) else []) if str(v).strip()][:limit]

    ctx.category = str(plan.get("category") or ctx.question).strip()
    ctx.area = str(plan.get("area") or ctx.category).strip()
    ctx.need = ctx.specific or str(plan.get("need") or ctx.question).strip()
    ctx.keywords = strings(plan.get("keywords"), 12)
    ctx.zh_queries = strings(plan.get("zh_queries"), 3)
    return {"stage": "plan", "question": ctx.question, "specific": ctx.specific,
            "category": ctx.category, "area": ctx.area, "need": ctx.need,
            "keywords": ctx.keywords, "zh_queries": ctx.zh_queries}


def brand(name: str) -> str:
    """The first word of a name when it can stand for a brand: Latin letters, three or more
    long, and not a generic word. "Even G2" and "Even Realities" share the brand "even"."""
    m = re.match(r"\s*([A-Za-z][A-Za-z0-9]{2,})", name or "")
    word = m.group(1).lower() if m else ""
    return "" if word in STOP or word in COMMON_WORDS else word


def fold_products(out: list[dict]) -> list[dict]:
    """A product goes into its company when both names start with the same brand word.
    Only a product whose name is verified is folded, and only into a company we also found."""
    for p in [e for e in out if e["kind"] == "product" and e["name_verified"]]:
        mine = {brand(n) for n in [p["name"]] + p["aliases"]} - {""}
        home = next((e for e in out if e is not p and e["kind"] == "company" and e["name_verified"]
                     and brand(e["name"]) in mine), None)
        if home is None:
            continue
        home["lenses"] += [x for x in p["lenses"] if x not in home["lenses"]]
        home["aliases"] += [n for n in [p["name"]] + p["aliases"] if n != home["name"] and n not in home["aliases"]]
        home.setdefault("products", []).append(p["name"])
        home["evidence"] += [x for x in p["evidence"] if x["url"] not in [y["url"] for y in home["evidence"]]]
        for key in ("found_by", "engines"):
            home[key] += [x for x in p[key] if x not in home[key]]
        if p.get("china") and home.get("china") is None:  # the Chinese posts and the Western check are the product's
            home["china"], home["west"], home["name_proof"] = p["china"], p["west"], p.get("name_proof", "")
            home["zh"] = home["zh"] or (p["zh"] if p["zh"] != p["name"] else "")
        out.remove(p)
    return out


def merge(ctx: Ctx, found: list[dict]) -> list[dict]:
    """One entity per company or product, whichever lens found it. Two finds are the same
    thing when their names match (same_company) or their Chinese names are identical."""
    out: list[dict] = []
    for c in found:
        names = [c["name"]] + [a for a in c.get("aliases", []) if a]
        twin = next((e for e in out
                     if (c.get("zh") and c.get("zh") == e.get("zh"))
                     or any(same_company(n, m) for n in names for m in [e["name"]] + e["aliases"])), None)
        if twin is None:
            twin = {
                "id": c["id"], "name": c["name"], "zh": c.get("zh", ""), "aliases": list(c.get("aliases", [])),
                "kind": c.get("kind", "company"), "does": c["does"], "lens": c["lens"], "lenses": [c["lens"]],
                "evidence": [], "found_by": [], "engines": [],
                "funding": None, "yc_batch": "", "west": None, "china": None,
                "name_verified": bool(c.get("name_verified", True)),
            }
            out.append(twin)
        elif c["lens"] not in twin["lenses"]:
            twin["lenses"].append(c["lens"])
            twin["aliases"] += [n for n in names if n != twin["name"] and n not in twin["aliases"]]
            twin["name_verified"] = twin["name_verified"] or bool(c.get("name_verified", True))
        else:
            twin["aliases"] += [n for n in names if n != twin["name"] and n not in twin["aliases"]]
        # A second Chinese find for a thing that already has one (two posts, two spellings of
        # one product line). The name, the verified flag and the Western check must all come
        # from the same find, so the row never shows one find's name with the other's status.
        second = c["lens"] == "china" and twin["china"] is not None
        takes_over = second and bool(c.get("name_verified")) and not twin["name_verified"]
        if takes_over:
            twin["name"], twin["zh"], twin["name_verified"] = c["name"], c.get("zh", ""), True
            twin["aliases"] = [a for a in twin["aliases"] if a != twin["name"]]
            mine = c.get("evidence_list", [])
            twin["evidence"] = mine + [x for x in twin["evidence"] if x["url"] not in [m["url"] for m in mine]]
        for item in c.get("evidence_list", []):
            if item["url"] not in [x["url"] for x in twin["evidence"]]:
                twin["evidence"].append(item)
        for key in ("found_by", "engines"):
            twin[key] += [x for x in c.get(key, []) if x not in twin[key]]
        if c["lens"] == "time":
            twin["yc_batch"] = c.get("yc_batch", "")
            twin["funding"] = {"round": c.get("round", ""), "amount": c.get("amount", ""),
                               "when": c.get("when", ""), "investors": c.get("investors", []),
                               "verified": bool(c.get("evidence_ok")) and bool(c.get("round") or c.get("amount"))}
        if c["lens"] == "china" and (not second or takes_over):
            twin["west"], twin["china"] = c.get("west"), c.get("china")
            twin["name_proof"] = c.get("name_proof", "")

    out = fold_products(out)
    for e in out:
        names = [e["name"]] + e["aliases"]
        shown = next((m for m in (ctx.in_memory(n) for n in names) if m), None)
        e["ai"] = {"memory_count": ctx.memory_counts.get(shown, 0) if shown else 0,
                   "memory_samples": ctx.memory_samples,
                   "memory_name": shown or "",
                   # in the model's list of companies from normal search, or (checked in code)
                   # named anywhere in the results and pages behind that search
                   "in_normal_list": any(find_match(n, ctx.normal_names) for n in names),
                   "on_normal_pages": bool(ctx.normal_text) and any(named_on_pages(n, ctx.normal_text) for n in names)}
        e["ai"]["in_normal_search"] = e["ai"]["in_normal_list"] or e["ai"]["on_normal_pages"]
        e["named_by_ai"] = e["ai"]["memory_count"] >= 1 or e["ai"]["in_normal_search"]
        e["independent_sources"] = sum(1 for x in e["evidence"] if x["source_type"] == "independent")
        e["newest"] = max((x["date"] for x in e["evidence"] if x.get("date")), default="")
    return out


def why_missed(e: dict, eng: dict, ctx: Ctx) -> list[str]:
    """Plain reasons, each computed from the run."""
    chips = [f"Not in any of the {ctx.memory_samples} AI answers, and not on any page behind normal search"]
    if e.get("west"):
        chips.append(e["west"]["status"])
        if e.get("china"):
            chips.append("Found on " + ", ".join(e["china"]["platforms"]))
    if eng.get("enough") and len(e["engines"]) == 1:
        chips.append(f"Found by one engine: {e['engines'][0].title()}")
    if not e["engines"] and e.get("yc_batch"):
        chips.append("Only in YC's directory, not in any news search")
    return chips


async def run(question: str, specific: str = ""):
    """Async generator of events: plan, memory, money, time, china, engines, report, done."""
    t0 = time.time()
    ctx = Ctx(question, specific)
    ctx.t0 = t0
    providers.take_notes()
    ctx.families = providers.available_llms()
    if not ctx.families:
        raise RuntimeError("No LLM key in .env")
    status: dict[str, str] = {}

    def stamp(event: dict) -> dict:
        event["seconds"] = round(time.time() - t0, 1)
        return event

    async def drive(name: str, lens, queue: asyncio.Queue) -> None:
        """Run one lens. Whatever happens to it, the other lenses and the report carry on."""
        try:
            async with asyncio.timeout(LENS_TIMEOUT[name]):
                async for event in lens.run(ctx):
                    if event.get("status") == "needs":
                        status[name] = "needs: " + event.get("reason", "")
                    await queue.put(event)
            status.setdefault(name, "ok")
        except Exception as exc:
            why = reason(exc)
            status[name] = "failed: " + why
            ctx.errors.append(f"{name} lens: {why}"[:300])
            await queue.put({"stage": name, "failed": why})
        finally:
            await queue.put(None)

    # The plan and the memory lens need nothing from each other, so they go out together.
    first: asyncio.Queue = asyncio.Queue()
    plan_event, _ = await asyncio.gather(make_plan(ctx), drive("memory", memory, first))
    yield stamp(plan_event)
    while (event := await first.get()) is not None:
        if event["stage"] == "memory" and "failed" not in event:
            event["category"], event["need"] = ctx.category, ctx.need
        yield stamp(event)

    # The other lenses run side by side; each section appears as soon as its lens is done.
    queue: asyncio.Queue = asyncio.Queue()
    switched = {"money": config.LENS_MONEY, "time": config.LENS_TIME, "china": config.LENS_CHINA}
    lenses = [("money", money), ("time", time_lens), ("china", china)]
    latest: dict[str, dict] = {}
    for name, _ in lenses:
        if not switched[name]:
            status[name] = "off"
            latest[name] = {"stage": name, "status": "off", "reason": f"Switched off in .env (LENS_{name.upper()}=0)."}
            yield stamp(latest[name])
    tasks = [asyncio.create_task(drive(name, lens, queue)) for name, lens in lenses if switched[name]]
    running = len(tasks)
    while running:
        event = await queue.get()
        if event is None:
            running -= 1
            continue
        latest[event["stage"]] = event
        yield stamp(event)
    await asyncio.gather(*tasks)

    found = [c for name in ("time", "china") for c in latest.get(name, {}).get("companies", [])]
    entities = merge(ctx, found)
    eng = engines.summarise(entities, ctx.engines_seen)
    yield stamp(eng)

    # What counts. A recently funded or launched company always counts. A thing from Chinese
    # platforms counts only when the West has posted little or nothing about it.
    def little_known(e: dict) -> bool:
        return (e.get("west") or {}).get("status") in (china.STATUS_NONE, china.STATUS_FEW)

    for e in entities:
        e["counted"] = bool(e["name_verified"]) and ("time" in e["lenses"] or little_known(e))
    counted = [e for e in entities if e["counted"]]
    by_lens = {}
    for name in ("time", "china"):
        if status.get(name) == "ok":
            mine = [e for e in counted if name in e["lenses"] and (name == "time" or little_known(e))]
            by_lens[name] = {"found": len(mine), "named": sum(e["named_by_ai"] for e in mine),
                             "missed": sum(not e["named_by_ai"] for e in mine)}
    # Seen in the West, so not counted, but still in none of the AI's answers.
    known = [e for e in entities if e["name_verified"] and not e["counted"] and not e["named_by_ai"]
             and (e.get("west") or {}).get("status") == china.STATUS_SEEN]
    ready = ctx.memory_ok and ctx.money_ok and bool(by_lens)
    why_not = ""
    if not ctx.memory_ok:
        why_not = "The memory lens failed, so the AI's answer is incomplete and no total is shown."
    elif not ctx.money_ok:
        why_not = ("The normal-search lens is switched off" if status.get("money") == "off" else "The normal-search lens failed") \
            + ", so the AI's answer is incomplete and no total is shown."
    elif not by_lens:
        why_not = "No lens that looks for missed things finished, so there is nothing to count."
    missed = sorted((e for e in counted if not e["named_by_ai"]),
                    key=lambda e: (-e["independent_sources"], -len(e["engines"]),
                                   -int((e["newest"] or "0000-00-00").replace("-", "")[:8] or 0)))
    for e in entities:
        e["why_missed"] = [] if e["named_by_ai"] else why_missed(e, eng, ctx)

    yield stamp({
        "stage": "report",
        "coverage": {"ready": ready, "why_not": why_not,
                     "named": sum(e["named_by_ai"] for e in counted) if ready else None,
                     "found": len(counted) if ready else None,
                     "by_lens": by_lens,
                     "unverified": sum(not e["name_verified"] for e in entities)},
        "entities": entities,
        "missed": [e["id"] for e in missed],
        "known_in_west": [e["id"] for e in known],
        "china_rule": f"A thing from Chinese platforms is counted only with {china.MIN_POSTS} or more posts there and the "
                      f"status \"{china.STATUS_NONE}\" or \"{china.STATUS_FEW}\".",
        "lenses": {name: status.get(name, "not run") for name in ("memory", "money", "time", "china")},
        "sort": "independent sources, then engines, then newest date",
    })

    yield stamp({
        "stage": "done",
        "mode": "mock" if config.MOCK else "live",
        "provider": "sample" if config.MOCK else config.SEARCH_PROVIDER,
        "models": [providers.model_name(f) for f in ctx.families],
        "errors": ctx.errors,
        "notes": providers.take_notes(),
    })


# ---------- Check anything ----------

CHECK_SYSTEM = """Answer only from what you already know. Do not search and do not guess from the name.
Return JSON only: {"answer": "what it is, in at most 12 words, or exactly UNKNOWN if you do not know it"}"""

WEST = [("Reddit", ["reddit.com"]), ("X", ["x.com", "twitter.com"]), ("Web", None)]


async def check(name: str) -> dict:
    """One name, four questions, side by side: does the AI know it from memory, is it on the
    Western internet, is it on the Chinese internet, and which engines return it.
    Counts are of the top results only, and say so."""
    from lenses.base import domain, name_in_text
    t0 = time.time()
    families = providers.available_llms()
    out = {"name": name, "mode": "mock" if config.MOCK else "live"}

    async def memory_panel() -> dict:
        if not families:
            return {"status": "needs", "reason": "No LLM key in .env."}
        picked = [families[i % len(families)] for i in range(3)]
        replies = await asyncio.gather(
            *(asyncio.wait_for(providers.llm_json(CHECK_SYSTEM, f"What is {name}?", "check", 200, fam), 30)
              for fam in picked), return_exceptions=True)
        answers = []
        for fam, r in zip(picked, replies):
            if isinstance(r, BaseException):
                answers.append({"model": providers.model_name(fam), "answer": "", "failed": reason(r)})
                continue
            text = str(r.get("answer", "")).strip()
            knew = bool(text) and text.strip(" .").upper() != "UNKNOWN"
            answers.append({"model": providers.model_name(fam), "answer": text, "knew": knew})
        ok = [a for a in answers if "failed" not in a]
        return {"status": "ok" if ok else "failed", "asked": len(ok),
                "knew": sum(1 for a in ok if a["knew"]), "answers": answers}

    async def west_panel() -> dict:
        if config.MOCK:
            return {"status": "ok", "top": 10, "searched_with": "sample data",
                    "sources": [{"label": label, "count": 0, "links": []} for label, _ in WEST]}
        if not config.TAVILY_API_KEY:
            return await west_panel_telem()
        results = await asyncio.gather(
            *(asyncio.wait_for(providers.tavily_search(f'"{name}"', 10, domains=d), 25) for _, d in WEST),
            return_exceptions=True)
        sources = []
        for (label, _), res in zip(WEST, results):
            if isinstance(res, BaseException):
                sources.append({"label": label, "failed": reason(res)})
                continue
            hits = [r for r in res if name_in_text(name, f"{r['title']} {r['content']}")]
            sources.append({"label": label, "count": len(hits), "returned": len(res),
                            "links": [{"title": r["title"][:90], "url": r["url"], "domain": domain(r["url"])} for r in hits[:3]]})
        return {"status": "ok", "top": 10, "searched_with": "Tavily", "searched_as": f'"{name}"', "sources": sources}

    async def west_panel_telem() -> dict:
        """The same panel with no Tavily key: Reddit and X posts and web results through Telem."""
        social, web = await asyncio.gather(
            asyncio.wait_for(providers.telem_platform_search(name, china.WEST_PLATFORMS, 10), 45),
            asyncio.wait_for(providers.telem_search(f'"{name}"', 10, engines=providers.telem_engines()[:2]), 45),
            return_exceptions=True)
        def panel(label, rows, keep):
            if isinstance(rows, BaseException):
                return {"label": label, "failed": reason(rows)}
            rows = [r for r in rows if keep(str(r.get("url", "")))]
            hits = [r for r in rows if name_in_text(name, f"{r.get('title', '')} {r.get('text', '') or r.get('content', '')}")]
            return {"label": label, "count": len(hits), "returned": len(rows),
                    "links": [{"title": str(r.get("title", ""))[:90], "url": r["url"], "domain": domain(r["url"])} for r in hits[:3]]}
        sources = [panel("Reddit", social, lambda u: "reddit.com" in u),
                   panel("X", social, lambda u: "x.com" in u or "twitter.com" in u),
                   panel("Web", web, lambda u: True)]
        return {"status": "ok", "top": 10, "searched_with": "Telem", "searched_as": name, "sources": sources}

    async def china_panel() -> dict:
        if config.MOCK:
            return {"status": "needs", "reason": "Sample mode searches nothing."}
        ok, why = china.availability()
        if not ok:
            return {"status": "needs", "reason": why}
        res = await asyncio.wait_for(providers.telem_platform_search(name, china.CHINA_PLATFORMS, 8), 40)
        by: dict[str, dict] = {providers.PLATFORMS[p]: {"label": providers.PLATFORMS[p], "count": 0, "returned": 0, "links": []}
                               for p in china.CHINA_PLATFORMS}
        for r in res:
            row = by.setdefault(r["platform"], {"label": r["platform"], "count": 0, "returned": 0, "links": []})
            row["returned"] += 1
            if name_in_text(name, r["text"]):
                row["count"] += 1
                if len(row["links"]) < 2:
                    row["links"].append({"title": (r["title"] or r["text"])[:90], "url": r["url"], "domain": domain(r["url"])})
        for miss in getattr(res, "failed", []):
            if miss in by:
                by[miss]["failed"] = "did not answer"
        return {"status": "ok", "searched_as": name, "searched_with": "Telem", "sources": list(by.values())}

    async def engines_panel() -> dict:
        if config.MOCK:
            return {"status": "needs", "reason": "Sample mode searches nothing."}
        if not config.TELEM_API_KEY:
            return {"status": "needs", "reason": "Needs Telem: add a TELEM_API_KEY in .env to ask several engines."}
        res = await asyncio.wait_for(providers.telem_search(name, 20, engines=providers.telem_engines()), 40)
        by: dict[str, int] = {e: 0 for e in providers.telem_engines()}
        for r in res:
            if name_in_text(name, f"{r['title']} {r['content']}"):
                for e in r.get("engines", []):
                    by[e] = by.get(e, 0) + 1
        return {"status": "ok", "engines": [{"engine": e, "count": n} for e, n in by.items()],
                "failed": list(getattr(res, "failed", []))}

    panels = await asyncio.gather(memory_panel(), west_panel(), china_panel(), engines_panel(),
                                  return_exceptions=True)
    for key, panel in zip(("memory", "west", "china", "engines"), panels):
        out[key] = {"status": "failed", "reason": reason(panel)} if isinstance(panel, BaseException) else panel
    out["seconds"] = round(time.time() - t0, 1)
    providers.take_notes()
    return out
