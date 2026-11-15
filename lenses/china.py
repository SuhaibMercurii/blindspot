"""Lens 4, China: what is big on Chinese platforms that the AI never mentions?

Built directly on Telem's platform sources (Xiaohongshu, Bilibili and Zhihu for China;
Reddit and X for the West). It is this repo's own implementation of the Arrivals idea, not
Suhayb's code; if his wrapper arrives, it replaces run_live() and the rules below stay.

1. Search the Chinese platforms with the plan's Chinese queries, recent posts only.
2. The model lists the named things the posts are excited about. Code keeps a thing only
   if its Chinese name is in a post.
3. Western check under every name variant: Reddit and X through Telem, plus one web search.
4. The status is computed from counts. A thing whose English name was not found in any
   source is "Name unverified", never "No Western posts found".
5. Engagement is shown as plain counts per side, from the top results only. No ratio.

Without a Telem key the lens says "Needs Telem" and stays out of the headline. It never
substitutes made-up data (sample mode aside, which is labelled as sample data).
"""
import asyncio
import re
from datetime import date, timedelta

import config
import mock
import providers
from lenses.base import Ctx, clean_list, domain, name_in_text, reason, safe

CHINA_PLATFORMS = ["tikhub_xhs", "tikhub_bilibili", "tikhub_zhihu"]
WEST_PLATFORMS = ["fetchlayer_reddit", "xcom"]  # xcom answers on Telem today; fetchlayer_x has no key there
MAX_THINGS = 6
MIN_POSTS = 2  # a thing named in a single post is not shown

STATUS_NONE = "No Western posts found (top results)"
STATUS_FEW = "Few Western posts"
STATUS_SEEN = "Seen in the West"
STATUS_UNVERIFIED = "Name unverified"
STATUS_FAILED = "Western check failed"

CHINA_SYSTEM = """These are recent posts from Chinese platforms about: {category}.
List at most {limit} specific named products or companies in this category that the posts recommend or are excited about. Skip generic words, and skip anything that is not in this category.
For each:
- zh: the name exactly as the posts write it (Chinese characters, or Latin letters if that is how the posts write it)
- en: its official English name only if a post gives one, else ""
- en_guess: the English brand name you believe it has, if no post gives one, else ""
- pinyin: the pinyin of the Chinese name without tone marks, or "" if zh has no Chinese characters
- kind: "product" or "company"
- does: what it is, in at most 15 plain English words, from the posts
- posts: ids of the posts that name it, like "p-4"
Return JSON only: {{"things": [...]}}"""


def west_status(posts: int, name_verified: bool, answered: bool = True) -> str:
    """answered: at least one Reddit or X search under the English name came back.
    A search that failed is not a search that found nothing."""
    if not name_verified:
        return STATUS_UNVERIFIED
    if posts <= 0:
        return STATUS_NONE if answered else STATUS_FAILED
    return STATUS_FEW if posts <= 5 else STATUS_SEEN


def has_chinese(text: str) -> bool:
    return bool(re.search(r"[一-鿿]", text or ""))


def quote_around(text: str, name: str, width: int = 70) -> str:
    """A piece of the post cut straight from its text around the name, so it is word for word."""
    at = text.find(name)
    if at < 0:
        return ""
    start, end = max(0, at - width // 2), min(len(text), at + len(name) + width)
    return ("…" if start else "") + text[start:end].strip() + ("…" if end < len(text) else "")


def build(n: int, zh: str, en: str, pinyin: str, kind: str, does: str, posts: list[dict],
          west_posts: list[dict], checked: list[str], verified: bool, web: dict | None = None,
          answered: bool = True, how: list[str] | None = None, name_proof: str = "") -> dict:
    """One China-lens entity. `posts` are the Chinese posts that contain zh."""
    platforms = sorted({p["platform"] for p in posts if p.get("platform")})

    def total(rows, key):
        return sum(int(r.get(key) or 0) for r in rows)

    return {
        "id": f"c-{n}",
        "name": en if verified and en else zh,
        "zh": zh,
        "aliases": [a for a in dict.fromkeys([zh, en, pinyin]) if a],
        "kind": kind if kind in ("product", "company") else "product",
        "does": does,
        "lens": "china",
        "evidence_list": [{"quote": quote_around(p["text"], zh), "url": p["url"], "domain": domain(p["url"]),
                           "title": p.get("title", ""), "date": p.get("date", ""), "source_type": "post",
                           "engine": [], "platform": p.get("platform", ""),
                           "engagement": {"likes": p.get("likes"), "comments": p.get("comments")}}
                          for p in posts[:3]],
        "found_by": platforms,
        "engines": [],  # platform sources are not search engines, so they stay out of the Engines count
        "china": {"posts": len(posts), "platforms": platforms,
                  "likes": total(posts, "likes"), "comments": total(posts, "comments"),
                  "engagement": total(posts, "likes") + total(posts, "comments")},
        "west": {"posts": len(west_posts), "checked_names": checked,
                 "likes": total(west_posts, "likes"), "comments": total(west_posts, "comments"),
                 "sources": [{"title": (p.get("title") or p.get("text", ""))[:90], "url": p["url"],
                              "platform": p.get("platform", "")} for p in west_posts[:3]],
                 "web": web, "searched_with": how or [], "answered": answered,
                 "status": west_status(len(west_posts), verified, answered)},
        "name_verified": verified,
        "name_proof": name_proof,  # where the English name was found next to the Chinese one
        "memory_count": 0,
    }


def entity(n: int, thing: dict) -> dict | None:
    """Sample-mode entity, through the same keep rule and status rule as a live one."""
    zh = str(thing.get("zh", "")).strip()
    posts = [{"text": str(p.get("text", "")), "url": p.get("url", ""), "date": p.get("date", ""),
              "platform": p.get("platform", ""), "likes": p.get("engagement"), "comments": None}
             for p in thing.get("posts", []) if zh and zh in str(p.get("text", ""))]
    if not zh or not posts:
        return None  # its Chinese name is not in any post
    en = str(thing.get("en", "")).strip()
    west = thing.get("west") or {}
    west_posts = [{"title": s.get("title", ""), "url": s.get("url", ""), "platform": s.get("platform", "")}
                  for s in west.get("sources", [])]
    west_posts += [{"title": "", "url": "", "platform": ""}] * max(0, int(west.get("posts") or 0) - len(west_posts))
    return build(n, zh, en, str(thing.get("pinyin", "")).strip(), "product", str(thing.get("does", "")).strip(),
                 posts, west_posts, list(west.get("checked_names", [])), bool(en) and bool(thing.get("en_in_source")))


def availability() -> tuple[bool, str]:
    """(can it run, what is missing)."""
    if not config.TELEM_API_KEY:
        return False, "Needs Telem: add a TELEM_API_KEY in .env."
    return True, ""


_WEST_SLOTS: dict[int, asyncio.Semaphore] = {}


def _slots() -> asyncio.Semaphore:
    """Reddit and X through Telem fail (503) when many searches arrive at once, so at most
    three run at a time."""
    return _WEST_SLOTS.setdefault(id(asyncio.get_running_loop()), asyncio.Semaphore(3))


def together(zh: str, en: str, texts: list[str]) -> str:
    """A source that has the Chinese name and the English name side by side proves they are
    the same thing. Returns that source's text (cut), or ""."""
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(en.lower()) + r"(?![a-z0-9])")
    for t in texts:
        m = pattern.search(t.lower())
        if zh in t and m:
            # show the stretch of the source that holds both names
            a, b = sorted((t.find(zh), m.start()))
            end = max(t.find(zh) + len(zh), m.end())
            if end - a > 220:  # far apart: show the part around each
                return f"…{t[max(0, t.find(zh) - 20): t.find(zh) + len(zh) + 40].strip()}… …{t[max(0, m.start() - 20): m.end() + 40].strip()}…"
            return ("…" if a > 25 else "") + t[max(0, a - 25): end + 25].strip() + ("…" if end + 25 < len(t) else "")
    return ""


async def social_search(name: str, through_telem: bool = True) -> tuple[list[dict], list[str], bool]:
    """Reddit and X for one name: through Telem, and if Telem's sources fail (or for the
    extra name variants, to stay inside the time budget), through a Tavily search limited
    to those sites. Returns (posts, how it was searched, did any answer)."""
    if not through_telem:
        return await _tavily_social(name, "Reddit and X through Tavily")
    try:
        async with _slots():
            try:
                res = await asyncio.wait_for(providers.telem_platform_search(name, WEST_PLATFORMS, 8), 30)
            except Exception:
                await asyncio.sleep(1.5)
                res = await asyncio.wait_for(providers.telem_platform_search(name, WEST_PLATFORMS, 8), 30)
        if len(getattr(res, "failed", [])) < len(WEST_PLATFORMS):
            return list(res), ["Reddit and X through Telem"], True
    except Exception:
        pass
    return await _tavily_social(name, "Reddit and X through Tavily (Telem's sources did not answer)")


async def _tavily_social(name: str, label: str) -> tuple[list[dict], list[str], bool]:
    if not config.TAVILY_API_KEY:
        return [], [], False
    sites = [("Reddit", ["reddit.com"]), ("X", ["x.com", "twitter.com"])]
    got = await asyncio.gather(*(asyncio.wait_for(providers.tavily_search(f'"{name}"', 10, domains=d), 25)
                                 for _, d in sites), return_exceptions=True)
    posts = [{"title": r["title"], "url": r["url"], "text": r["content"], "platform": label, "likes": None, "comments": None}
             for (label, _), rows in zip(sites, got) if not isinstance(rows, BaseException) for r in rows]
    answered = any(not isinstance(rows, BaseException) for rows in got)
    return posts, ([label] if answered else []), answered


async def web_search(query: str) -> list[dict]:
    if config.TAVILY_API_KEY:
        return list(await asyncio.wait_for(providers.tavily_search(query, 10), 25))
    return list(await asyncio.wait_for(providers.telem_search(query, 10, engines=providers.telem_engines()[:2]), 40))


async def check_thing(thing: dict, all_posts: list[dict]) -> dict:
    """Verify the English name, then look for the thing in the West under every name."""
    zh, pinyin = thing["zh"], thing["pinyin"]
    en = thing["en"] or thing["en_guess"]
    proof = ""
    if not has_chinese(zh):
        en, proof = zh, "The posts write the name in Latin letters."
    elif en:
        # The English name counts only if a source shows it next to the Chinese name.
        hit = together(zh, en, [p["text"] for p in all_posts])
        if hit:
            proof = f"A Chinese post has both names: {hit}"
        else:
            pair = await safe(web_search(f"{zh} {en}"), [], "name check", [], 30)
            hit = together(zh, en, [f"{r['title']} {r['content']}" for r in pair])
            if hit:
                proof = f"A web page has both names: {hit}"
    verified = bool(en) and bool(proof)
    names = [n for n in dict.fromkeys([en if verified else "", zh, "" if verified else pinyin]) if n][:3]

    def naming(rows) -> list[dict]:
        return [r for r in rows if any(name_in_text(n, f"{r.get('title', '')} {r.get('text', r.get('content', ''))}") for n in names)]

    jobs = [social_search(n, through_telem=(verified and i == 0)) for i, n in enumerate(names)]
    jobs.append(safe(web_search(f'"{names[0]}"'), [], "web search", None, 30))
    *social, web_rows = await asyncio.gather(*jobs, return_exceptions=True)
    west_posts, seen, how, answered = [], set(), [], False
    for i, res in enumerate(social):
        if isinstance(res, BaseException):
            continue
        posts, method, ok = res
        if i == 0:
            answered = ok  # the first name is the English one when it is verified
        how += [m for m in method if m not in how]
        for r in naming(posts):
            if r["url"] not in seen:
                seen.add(r["url"])
                west_posts.append(r)
    web = None
    if isinstance(web_rows, list):
        hits = naming(web_rows)
        web = {"searched_as": names[0], "count": len(hits), "returned": len(web_rows),
               "links": [{"title": r["title"][:90], "url": r["url"], "domain": domain(r["url"])} for r in hits[:3]]}
    return {"thing": thing, "en": en if verified else "", "names": names, "west_posts": west_posts, "web": web,
            "verified": verified, "answered": answered, "how": how, "proof": proof}


async def run_live(ctx: Ctx):
    queries = ctx.zh_queries[:3]
    if not queries:
        raise RuntimeError("the plan gave no Chinese queries")
    searches = await asyncio.gather(
        *(asyncio.wait_for(providers.telem_platform_search(q, CHINA_PLATFORMS, 8), 45) for q in queries),
        return_exceptions=True)
    cutoff = (date.today() - timedelta(days=providers.LOOKBACK_DAYS.get(config.LOOKBACK, 365))).isoformat()
    posts, seen, old, down = [], set(), 0, set()
    for q, res in zip(queries, searches):
        if isinstance(res, BaseException):
            ctx.errors.append(f"China search '{q}': {reason(res)}"[:300])
            continue
        down.update(getattr(res, "failed", []))
        for r in res:
            if r["url"] in seen:
                continue
            seen.add(r["url"])
            if r["date"] and r["date"] < cutoff:  # recent posts only
                old += 1
                continue
            posts.append({**r, "id": f"p-{len(posts) + 1}"})
    if not posts:
        raise RuntimeError("the Chinese platforms returned no recent posts")
    if down:
        providers.note("Chinese platforms that did not answer on at least one search: " + ", ".join(sorted(down)) + ".")

    listing = "\n\n".join(f"[{p['id']}] ({p['platform']}, {p['date'] or 'no date'}) {p['text'][:420]}" for p in posts)
    raw = await safe(providers.llm_json(CHINA_SYSTEM.format(category=ctx.category, limit=MAX_THINGS + 2),
                                        listing, "china", 2500, temperature=0.2),
                     ctx.errors, "China lens extract", {})
    by_id = {p["id"]: p for p in posts}
    things, dropped, thin = [], 0, 0
    for t in raw.get("things", []) if isinstance(raw.get("things"), list) else []:
        if not isinstance(t, dict):
            continue
        zh = re.sub(r"\s+", " ", str(t.get("zh", ""))).strip()
        if not zh or len(zh) > 40 or any(x["zh"] == zh for x in things):
            continue
        # Keep rule: the name as written must be in a post. Every post that contains it counts,
        # whether or not the model cited it.
        mine = [p for p in posts if zh in p["text"]]
        if not mine:
            dropped += 1
            continue
        if len(mine) < MIN_POSTS:
            thin += 1
            continue
        things.append({"zh": zh, "en": re.sub(r"\s+", " ", str(t.get("en", ""))).strip(),
                       "en_guess": re.sub(r"\s+", " ", str(t.get("en_guess", ""))).strip(),
                       "pinyin": re.sub(r"\s+", " ", str(t.get("pinyin", ""))).strip(),
                       "kind": str(t.get("kind", "")).strip(), "does": str(t.get("does", "")).strip(), "posts": mine})
        if len(things) >= MAX_THINGS:
            break

    checked = await asyncio.gather(*(check_thing(t, posts) for t in things), return_exceptions=True)
    found, unanswered = [], 0
    for item in checked:
        if isinstance(item, BaseException):
            ctx.errors.append(f"China lens Western check: {reason(item)}"[:300])
            continue
        t = item["thing"]
        unanswered += item["verified"] and not item["answered"]
        found.append(build(len(found) + 1, t["zh"], item["en"], t["pinyin"], t["kind"], t["does"], t["posts"],
                           item["west_posts"], item["names"], item["verified"], item["web"],
                           item["answered"], item["how"], item["proof"]))
    if unanswered:
        providers.note(f"Reddit and X did not answer for {unanswered} thing{'s' if unanswered > 1 else ''}; "
                       f"those say \"{STATUS_FAILED}\", not \"{STATUS_NONE}\".")

    yield {
        "stage": "china", "status": "ok", "queries": queries,
        "platforms": [providers.PLATFORMS[p] for p in CHINA_PLATFORMS],
        "west_platforms": [providers.PLATFORMS[p] for p in WEST_PLATFORMS] + ["web"],
        "posts_read": len(posts), "old_posts_dropped": old, "not_in_post": dropped, "one_post_dropped": thin,
        "companies": found, "note": "Counts are from the top results only.",
    }


async def run(ctx: Ctx):
    if config.MOCK:
        things = [t for t in (entity(i, t) for i, t in enumerate(mock.china(), 1)) if t]
        kept = [t for t in things if t["china"]["posts"] >= MIN_POSTS]
        yield {"stage": "china", "status": "ok", "queries": ["(sample data)"], "one_post_dropped": len(things) - len(kept),
               "companies": kept, "note": "Counts are from the top results only."}
        return
    ok, why = availability()
    if not ok:
        yield {"stage": "china", "status": "needs", "reason": why, "queries": ctx.zh_queries, "companies": []}
        return
    async for event in run_live(ctx):
        yield event
