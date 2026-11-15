"""Search and LLM calls. Everything that touches an outside API lives here.

Owner: the person doing the search pipeline.

Search goes to Tavily directly or through Telem (a router over Exa, Brave, Tavily,
Parallel and others). Telem's request and response shapes below follow
https://docs.telem.ai (checked 2026-11-14):
  POST /v1/search  {"user_input": {"query"}, "search": {...}}
                   -> preprocessor_runs[].output_payload.results[]  (one run per engine)
  POST /v1/fetch   {"urls": [up to 5], "options": {...}}
                   -> preprocessor_runs[].output_payload.fetched_results[]
"""
import asyncio
import json
import math
import random
import re
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime

import httpx

import config
import mock

TAVILY_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
TELEM_URL = f"{config.TELEM_BASE_URL}/v1/search"
TELEM_FETCH_URL = f"{config.TELEM_BASE_URL}/v1/fetch"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS_URL = "https://generativelanguage.googleapis.com/v1beta/models"

DEFAULT_ANTHROPIC_MODEL = "claude-haiku-5-5"
# Check this name is still offered before the demo, or set OPENAI_MODEL in .env.
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
# If Google no longer serves this name, the first call lists the models your key can use
# and switches to the newest Flash model (see _gemini). Or set GEMINI_MODEL in .env.
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"

# Things a run should tell the user that are not failures of a whole step, for example
# "Telem's Brave engine failed, the others answered". pipeline.run() drains this list
# into the run's notes. One run at a time (a demo app), so a module list is enough.
NOTES: list[str] = []


def note(text: str) -> None:
    if text not in NOTES:
        NOTES.append(text)


def take_notes() -> list[str]:
    out = list(NOTES)
    NOTES.clear()
    return out


# ---------- HTTP with retry ----------

RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

_CLIENT: httpx.AsyncClient | None = None
_CLIENT_LOOP = None


def _client() -> httpx.AsyncClient:
    """One shared connection pool per event loop (a new TLS handshake for each of the
    ~40 calls in a run costs seconds)."""
    global _CLIENT, _CLIENT_LOOP
    loop = asyncio.get_running_loop()
    if _CLIENT is None or _CLIENT.is_closed or _CLIENT_LOOP is not loop:
        _CLIENT = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=80, max_keepalive_connections=40))
        _CLIENT_LOOP = loop
    return _CLIENT


async def request_json(method: str, url: str, headers: dict, payload: dict | None, timeout: float,
                       label: str, attempts: int = 3) -> httpx.Response:
    """HTTP call with retries on rate limits and server hiccups (honours Retry-After)."""
    last = None
    for i in range(attempts):
        try:
            resp = await _client().request(method, url, headers=headers, json=payload, timeout=timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        else:
            if resp.status_code not in RETRY_STATUS or i == attempts - 1:
                return resp
            last = f"{label} returned {resp.status_code}"
            wait = resp.headers.get("retry-after")
            if wait and wait.replace(".", "", 1).isdigit():
                await asyncio.sleep(min(float(wait), 20))
                continue
        if i < attempts - 1:
            await asyncio.sleep(1.2 * (2 ** i) + random.random())
    raise RuntimeError(f"{label} failed after {attempts} tries: {last}")


async def post_json(url: str, headers: dict, payload: dict, timeout: float, label: str,
                    attempts: int = 3) -> httpx.Response:
    return await request_json("POST", url, headers, payload, timeout, label, attempts)


def error_text(resp: httpx.Response) -> str:
    """The provider's own reason, short enough to show on the page."""
    try:
        data = resp.json()
    except ValueError:
        return resp.text[:240]
    for key in ("error", "detail", "message"):
        value = data.get(key) if isinstance(data, dict) else None
        if isinstance(value, dict):
            value = value.get("message") or value.get("code") or json.dumps(value)
        if isinstance(value, list):
            value = "; ".join(str(v.get("msg", v)) if isinstance(v, dict) else str(v) for v in value)
        if value:
            return str(value)[:240]
    return resp.text[:240]


# ---------- dates ----------

def iso_date(value) -> str:
    """Any date a search engine returns -> "YYYY-MM-DD", or "" when it is missing or unreadable.
    Tavily news gives "Thu, 18 Jun 2026 09:00:00 GMT"; Telem's engines give ISO dates."""
    if not isinstance(value, str) or not value.strip():
        return ""
    value = value.strip()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", value)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return ""
    try:
        return parsedate_to_datetime(value).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return ""


LOOKBACK_DAYS = {"day": 1, "week": 7, "month": 31, "year": 365}


# ---------- search ----------

class Results(list):
    """Search results [{title, url, content, date, provider, engines, via}], plus what the
    router reported about the call."""
    routing: list[str] = []   # engines Telem's auto-routing chose, best first
    failed: list[str] = []    # engines that errored inside Telem


def telem_engines() -> list[str]:
    return [e for e in (x.strip().lower() for x in config.TELEM_ENGINES.split(",")) if e]


def search_label(kind: str) -> str:
    """Who answers a search of this kind, for the page."""
    if config.MOCK:
        return "sample data"
    if config.SEARCH_PROVIDER != "telem":
        return "Tavily"
    if kind == "profile":
        return "Tavily" if config.TAVILY_API_KEY else f"{telem_engines()[0].title()} through Telem"
    if kind == "normal" and config.TELEM_AUTO_ROUTING == "accuracy":
        return "Telem auto-routing"
    return "Telem (" + ", ".join(e.title() for e in telem_engines()) + ")"


async def search(query: str, kind: str, max_results: int, recent: bool = False,
                 deep: bool = False) -> Results:
    """Return results for one query.

    kind: "normal" (a plain "best X" search), "discovery" (recent news) or "profile"
          (the one product search each ranked candidate gets).
    recent: only ask for recently published results.
    deep: Tavily's advanced depth (used once, when the basic search comes back off topic).
    """
    if config.MOCK:
        return Results(mock.search(kind, query))
    if config.SEARCH_PROVIDER == "telem":
        if kind == "profile":
            # One engine for every candidate, so the comparison is like for like.
            if config.TAVILY_API_KEY:
                return await tavily_search(query, max_results, recent)
            return await telem_search(query, max_results, recent, engines=telem_engines()[:1])
        if kind == "normal":
            # Let Telem's router pick the engines, as it would for any agent, and ask for
            # the publish date so old pages can be flagged.
            return await telem_search(query, max_results, recent, engines=telem_engines(),
                                      auto=config.TELEM_AUTO_ROUTING == "accuracy", dated=True)
        # Discovery: pinned engines, so every source can credit the engine that found it.
        return await telem_search(query, max_results, recent, engines=telem_engines(), keep_all=True)
    return await tavily_search(query, max_results, recent, deep)


async def tavily_search(query: str, max_results: int, recent: bool = False, deep: bool = False,
                        domains: list[str] | None = None) -> Results:
    payload = {
        "query": query,
        "max_results": min(max_results, 20),
        "search_depth": "advanced" if deep else "basic",  # 1 credit basic, 2 advanced
    }
    if domains:
        payload["include_domains"] = domains  # only results from these sites
    if recent:
        payload["topic"] = "news"
        payload["time_range"] = config.LOOKBACK
    headers = {"Authorization": f"Bearer {config.TAVILY_API_KEY}"}
    resp = await post_json(TAVILY_URL, headers, payload, 25, "Tavily search")
    if resp.status_code == 400 and recent:
        # Tavily rejected a recency option. Try again with the plain search, and say so.
        payload.pop("topic", None)
        payload.pop("time_range", None)
        resp = await post_json(TAVILY_URL, headers, payload, 25, "Tavily search")
        if resp.status_code < 400:
            note(f'Tavily refused the recent-news filter for "{query}", so that search was not limited by date.')
    if resp.status_code >= 400:
        raise RuntimeError(f"Tavily search returned {resp.status_code}: {error_text(resp)}")
    data = resp.json()
    return Results(
        {
            "title": item.get("title") or item.get("url", ""),
            "url": item.get("url", ""),
            "content": (item.get("content") or "")[:900],
            # Only news results carry a date; a plain Tavily search has none.
            "date": iso_date(item.get("published_date")),
            "provider": "Tavily",
            "engines": ["tavily"],
            "via": "",
        }
        for item in data.get("results", [])
        if item.get("url")
    )


def _telem_text(row: dict) -> str:
    """Telem calls the snippet `summary` whichever engine answered; `excerpt` (extended tier)
    is longer text, as a string or a list of strings."""
    parts = []
    for key in ("summary", "excerpt"):
        value = row.get(key)
        if isinstance(value, list):
            value = " ".join(v for v in value if isinstance(v, str))
        if isinstance(value, str) and value.strip() and value.strip() not in " ".join(parts):
            parts.append(value.strip())
    return " ".join(parts)


def telem_rows(data: dict, max_results: int) -> Results:
    """Flatten Telem's one-run-per-engine response into one list, each row tagged with every
    engine that found it. Order follows Telem's own merge advice: pages that more engines
    found come first, then the best rank any engine gave them."""
    pages: dict[str, dict] = {}
    failed = []
    for run in data.get("preprocessor_runs") or []:
        if not isinstance(run, dict):
            continue
        engine = str(run.get("preprocessor_name") or "telem").lower()
        payload = run.get("output_payload")
        if not isinstance(payload, dict):
            if engine not in failed:
                failed.append(engine)
            continue
        for row in payload.get("results") or []:
            url = row.get("url") if isinstance(row, dict) else None
            if not isinstance(url, str) or not url.startswith("http"):
                continue
            key = url.split("#")[0].rstrip("/")
            rank = row.get("rank") if isinstance(row.get("rank"), int) else 99
            page = pages.get(key)
            if page is None:
                page = pages[key] = {"title": "", "url": url, "content": "", "date": "",
                                     "engines": [], "rank": 99}
            if engine not in page["engines"]:
                page["engines"].append(engine)
            page["rank"] = min(page["rank"], rank)
            page["title"] = page["title"] or str(row.get("title") or "")
            text = _telem_text(row)
            if len(text) > len(page["content"]):
                page["content"] = text
            page["date"] = max(page["date"], iso_date(row.get("publish_date")))  # newest any engine gives
    ordered = sorted(pages.values(), key=lambda p: (-len(p["engines"]), p["rank"]))
    out = Results()
    for p in ordered[:max_results]:
        out.append({
            "title": p["title"] or p["url"],
            "url": p["url"],
            "content": p["content"][:900],
            "date": p["date"],
            "provider": " + ".join(e.title() for e in p["engines"]) + " through Telem",
            "engines": p["engines"],
            "via": "telem",
        })
    routing = data.get("routing")
    if isinstance(routing, list) and routing and isinstance(routing[0], dict):
        out.routing = [str(e).lower() for e in routing[0].get("providers") or []]
    out.failed = failed
    return out


async def telem_search(query: str, max_results: int, recent: bool = False,
                       engines: list[str] | None = None, auto: bool = False,
                       dated: bool = False, keep_all: bool = False) -> Results:
    """One query through Telem.

    engines: the engines to run (`search.providers.include`). With auto=True they are the
             set Telem's auto-routing chooses from, and the response says which it chose.
    recent:  `published_after` keeps results from the lookback window (and makes every
             result carry `publish_date`).
    dated:   ask for the `extended` tier, which adds `publish_date` and a longer excerpt.
    """
    engines = engines or telem_engines()
    spread = 2 if auto else max(1, len(engines))
    options: dict = {
        # num_results is per engine. Ask each for enough to fill the list after merging.
        "num_results": max(3, min(20, math.ceil(max_results / spread) + 1)),
        "providers": {"include": engines},
    }
    if auto:
        options["auto_routing"] = "accuracy"
    if recent:
        days = LOOKBACK_DAYS.get(config.LOOKBACK, 365)
        options["published_after"] = (date.today() - timedelta(days=days)).isoformat()
    if dated:
        options["tier"] = "extended"
    headers = {"Authorization": f"Bearer {config.TELEM_API_KEY}"}
    payload = {"user_input": {"query": query}, "search": options}
    # A search waits for its slowest engine, so give it room.
    resp = await post_json(TELEM_URL, headers, payload, 45, "Telem search")
    # 400/422 name the option Telem did not accept. Drop that one option, retry, and say so,
    # rather than failing the whole step.
    for _ in range(3):
        if resp.status_code not in (400, 422):
            break
        reason = error_text(resp).lower()
        dropped = next((k for k in ("tier", "published_after", "auto_routing")
                        if k in options and k in reason), None)
        if dropped is None:
            break
        options.pop(dropped)
        note(f"Telem did not accept the {dropped} option ({error_text(resp)[:120]}), so it was left out.")
        resp = await post_json(TELEM_URL, headers, payload, 45, "Telem search")
    if resp.status_code >= 400:
        raise RuntimeError(f"Telem search returned {resp.status_code}: {error_text(resp)}")
    # keep_all: every page any engine returned stays in, so a page that only one engine
    # found is not cut off by the cap (the Engines count depends on it).
    out = telem_rows(resp.json(), options["num_results"] * len(engines) if keep_all else max_results)
    if out.failed:
        note("Telem engines that failed on at least one search: " + ", ".join(sorted(out.failed)) + ".")
    if recent and "published_after" not in options:
        note(f'The search "{query}" was not limited by date.')
    return out


# ---------- platform sources through Telem (Chinese platforms, Reddit, X) ----------

PLATFORMS = {
    "tikhub_xhs": "Xiaohongshu", "tikhub_bilibili": "Bilibili", "tikhub_zhihu": "Zhihu", "tikhub_weibo": "Weibo",
    "fetchlayer_reddit": "Reddit", "fetchlayer_x": "X",
}
_LIKES = ("like", "liked_count", "voteup_count", "like_count", "score")
_COMMENTS = ("review", "comments_count", "comment_count", "reply_count")


async def telem_platform_search(query: str, platforms: list[str], per_platform: int = 8) -> Results:
    """Posts from named platform sources, one run per platform. Each post:
    {title, url, text, date, platform, likes, comments}. Counts are what the platform
    reports for that post. Platforms that fail are listed in .failed."""
    headers = {"Authorization": f"Bearer {config.TELEM_API_KEY}"}
    payload = {"user_input": {"query": query},
               "search": {"num_results": per_platform, "providers": {"include": platforms}, "tier": "max"}}
    resp = await post_json(TELEM_URL, headers, payload, 45, "Telem platform search", attempts=2)
    if resp.status_code in (400, 422) and "tier" in error_text(resp).lower():
        payload["search"].pop("tier")
        resp = await post_json(TELEM_URL, headers, payload, 45, "Telem platform search", attempts=2)
    if resp.status_code >= 400:
        raise RuntimeError(f"Telem platform search returned {resp.status_code}: {error_text(resp)}")
    out, failed, seen = Results(), [], set()
    for run in resp.json().get("preprocessor_runs") or []:
        if not isinstance(run, dict):
            continue
        source = str(run.get("preprocessor_name") or "")
        payload_out = run.get("output_payload")
        if not isinstance(payload_out, dict):
            failed.append(PLATFORMS.get(source, source))
            continue
        for row in payload_out.get("results") or []:
            url = row.get("url") if isinstance(row, dict) else None
            if not isinstance(url, str) or not url.startswith("http") or url in seen:
                continue
            seen.add(url)
            extra = row.get("enrichments") if isinstance(row.get("enrichments"), dict) else {}

            def count(keys):
                for k in keys:
                    if isinstance(extra.get(k), (int, float)):
                        return int(extra[k])
                return None

            when = iso_date(row.get("publish_date"))
            updated = extra.get("updated_time")
            if isinstance(updated, (int, float)) and updated > 0:  # an edited post carries its latest edit
                when = max(when, datetime.fromtimestamp(updated).date().isoformat())
            title = str(row.get("title") or "")
            text = _telem_text(row)
            out.append({"title": title, "url": url, "text": (title + " " + text if title and title not in text else text)[:1200],
                        "date": when, "platform": PLATFORMS.get(source, source),
                        "likes": count(_LIKES), "comments": count(_COMMENTS)})
    out.failed = failed
    return out


# ---------- opening pages ----------

class Pages(dict):
    """{url without trailing slash: page text}, plus who opened each page and why any failed."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.by: dict[str, str] = {}
        self.failed: dict[str, str] = {}


def _key(url: str) -> str:
    return url.rstrip("/")


async def extract(urls: list[str]) -> Pages:
    """Open pages and return their text as markdown (markdown keeps links, so affiliate
    links can be detected). Tavily Extract opens them; with SEARCH_PROVIDER=telem, Telem's
    fetch endpoint then tries whatever Tavily could not open."""
    urls = list(dict.fromkeys(u for u in urls if u))[:20]
    pages = Pages()
    if not urls:
        return pages
    if config.MOCK:
        pages.update(mock.extract(urls))
        pages.by = {k: "sample data" for k in pages}
        return pages
    telem = config.SEARCH_PROVIDER == "telem" and bool(config.TELEM_API_KEY)
    if config.TAVILY_API_KEY:
        try:
            await tavily_extract(urls, pages)
        except Exception as exc:
            if not telem:
                raise
            note(f"Tavily Extract failed ({str(exc)[:140]}), so Telem fetch opened the pages.")
    rest = [u for u in urls if _key(u) not in pages]
    if rest and telem:
        try:
            await telem_fetch(rest, pages)
        except Exception as exc:
            note(f"Telem page fetch failed ({str(exc)[:140]}).")
    return pages


async def tavily_extract(urls: list[str], pages: Pages) -> None:
    headers = {"Authorization": f"Bearer {config.TAVILY_API_KEY}"}
    payload = {"urls": urls, "extract_depth": "basic", "format": "markdown"}
    resp = await post_json(TAVILY_EXTRACT_URL, headers, payload, 30, "Tavily extract", attempts=2)
    if resp.status_code == 400:
        payload.pop("format", None)
        resp = await post_json(TAVILY_EXTRACT_URL, headers, payload, 30, "Tavily extract", attempts=2)
    if resp.status_code >= 400:
        raise RuntimeError(f"Tavily extract returned {resp.status_code}: {error_text(resp)}")
    data = resp.json()
    for item in data.get("results", []):
        url, text = item.get("url"), item.get("raw_content") or ""
        if url and text:
            pages[_key(url)] = text[:80000]
            pages.by[_key(url)] = "Tavily Extract"
            pages.failed.pop(_key(url), None)
    for item in data.get("failed_results", []):
        if isinstance(item, dict) and item.get("url"):
            pages.failed.setdefault(_key(item["url"]), str(item.get("error") or "could not be opened")[:120])


async def telem_fetch(urls: list[str], pages: Pages) -> None:
    """Telem reads up to 5 URLs per call and tries its fetch providers in turn."""
    headers = {"Authorization": f"Bearer {config.TELEM_API_KEY}"}

    async def one(chunk: list[str]) -> None:
        payload = {"urls": chunk, "options": {"inline_max_chars": 80000}}
        resp = await post_json(TELEM_FETCH_URL, headers, payload, 40, "Telem fetch", attempts=2)
        if resp.status_code >= 400:
            raise RuntimeError(f"Telem fetch returned {resp.status_code}: {error_text(resp)}")
        for run in resp.json().get("preprocessor_runs") or []:
            payload_out = run.get("output_payload") if isinstance(run, dict) else None
            for row in (payload_out or {}).get("fetched_results") or []:
                url = row.get("url") if isinstance(row, dict) else None
                if not isinstance(url, str):
                    continue
                text = row.get("content") or ""
                status = row.get("http_status")
                ok = row.get("status") == "succeeded" and text and not (isinstance(status, int) and status >= 400)
                if ok:
                    pages[_key(url)] = text[:80000]
                    pages.by[_key(url)] = f"{str(row.get('provider') or 'a fetch provider').title()} through Telem"
                else:
                    err = row.get("error")
                    reason = (err.get("message") or err.get("code")) if isinstance(err, dict) else err
                    pages.failed[_key(url)] = str(reason or f"HTTP {status}" if status else reason or "could not be opened")[:120]

    chunks = [urls[i:i + 5] for i in range(0, len(urls), 5)]
    results = await asyncio.gather(*(one(c) for c in chunks), return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors and len(errors) == len(chunks):
        raise errors[0]
    for e in errors:
        note(f"One Telem page-fetch batch failed ({str(e)[:120]}).")


# ---------- LLM ----------

def available_llms() -> list[str]:
    """Every LLM family we have a key for, in preference order."""
    if config.MOCK:
        return ["sample"]
    out = []
    if config.ANTHROPIC_API_KEY:
        out.append("anthropic")
    if config.OPENAI_API_KEY:
        out.append("openai")
    if config.GEMINI_API_KEY:
        out.append("gemini")
    if not out and config.LLM_CLI == "claude":
        out.append("claude-cli")
    return out


_MODEL_SWITCH: dict[str, str] = {}  # family -> model picked at run time (Gemini fallback)


def model_name(family: str) -> str:
    """The model a family uses: its own setting (ANTHROPIC_MODEL, OPENAI_MODEL, GEMINI_MODEL),
    else LLM_MODEL if it is the first available family, else the default."""
    if family in _MODEL_SWITCH:
        return _MODEL_SWITCH[family]
    own = {"anthropic": config.ANTHROPIC_MODEL, "openai": config.OPENAI_MODEL,
           "gemini": config.GEMINI_MODEL}.get(family, "")
    if own:
        return own
    first = (available_llms() or [""])[0]
    if config.LLM_MODEL and family == first:
        return config.LLM_MODEL
    if family == "claude-cli":
        return f"claude-{config.LLM_CLI_MODEL} (local CLI)"
    return {"anthropic": DEFAULT_ANTHROPIC_MODEL, "openai": DEFAULT_OPENAI_MODEL,
            "gemini": DEFAULT_GEMINI_MODEL}.get(family, family)


_SEMAPHORES: dict[tuple, asyncio.Semaphore] = {}
# Request options a model refused with a 400, so later calls skip them: {model: {"temperature", ...}}
_REFUSED: dict[str, set[str]] = {}
# Room for a model's thinking, which counts toward its output cap.
THINK_ROOM = 6000


async def llm(system: str, user: str, stage: str, max_tokens: int = 1800,
              family: str | None = None, temperature: float | None = None) -> str:
    """One LLM call. `stage` is only used to pick sample output in mock mode.
    `family` forces a provider ("anthropic", "openai", "gemini"); default is the first available.
    `temperature` None means the provider's default."""
    if config.MOCK:
        return mock.llm(stage, user)
    family = family or (available_llms() or ["none"])[0]
    call = {"anthropic": _anthropic, "openai": _openai, "gemini": _gemini,
            "claude-cli": _claude_cli}.get(family)
    if call is None:
        raise RuntimeError("No LLM key in .env")
    key = (family, id(asyncio.get_running_loop()))
    sem = _SEMAPHORES.setdefault(key, asyncio.Semaphore(config.LLM_CONCURRENCY))
    async with sem:
        return await call(system, user, max_tokens, model_name(family), temperature)


async def _claude_cli(system: str, user: str, max_tokens: int, model: str,
                      temperature: float | None = None) -> str:
    """One call through the local `claude` command-line tool (no API key needed).
    Each call is a fresh session with no search tool granted."""
    prompt = (system.strip() + "\n\n" + user.strip()).strip()
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", "--model", config.LLM_CLI_MODEL, prompt,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("The local Claude tool did not answer within 120 seconds.")
    if proc.returncode != 0 or not out.strip():
        raise RuntimeError("The local Claude tool failed: " + (err.decode(errors="replace")[:200] or "no output"))
    return out.decode(errors="replace").strip()


# Claude models from Opus 4.7 on run only at temperature 1 (any other value is a 400) and
# think before they answer; that thinking counts toward max_tokens.
_CLAUDE_FIXED_SAMPLING = re.compile(r"claude-(?:haiku-5|sonnet-5|opus-5|opus-4-[78]|fable|mythos)")


async def _anthropic(system: str, user: str, max_tokens: int, model: str,
                     temperature: float | None = None) -> str:
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    refused = _REFUSED.setdefault(
        model, {"temperature"} if _CLAUDE_FIXED_SAMPLING.search(model) else {"effort"})
    # The extraction steps ask for a low temperature. Where the model has no temperature
    # setting, ask for low effort instead (less thinking, faster). The hivemind samples ask
    # for temperature 1.0, which is these models' fixed setting, so they are sent with the
    # provider's defaults and nothing else.
    extraction = temperature is not None and temperature < 1

    def body() -> dict:
        thinks = "temperature" in refused
        payload = {
            "model": model,
            "max_tokens": max_tokens + (THINK_ROOM if thinks else 0),
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        if temperature is not None and temperature != 1 and "temperature" not in refused:
            payload["temperature"] = temperature
        if extraction and thinks and "effort" not in refused and config.LLM_EFFORT:
            payload["output_config"] = {"effort": config.LLM_EFFORT}
        return payload

    resp = await post_json(ANTHROPIC_URL, headers, body(), 90, "Anthropic")
    for _ in range(2):
        if resp.status_code != 400:
            break
        reason = error_text(resp).lower()
        hit = [k for k, words in (("temperature", ("temperature",)), ("effort", ("effort", "output_config")))
               if k not in refused and any(w in reason for w in words)]
        if not hit:
            break
        refused.update(hit)
        resp = await post_json(ANTHROPIC_URL, headers, body(), 90, "Anthropic")
    if resp.status_code >= 400:
        raise RuntimeError(f"Anthropic returned {resp.status_code}: {error_text(resp)}")
    data = resp.json()
    text = "".join(
        block.get("text", "") for block in data.get("content", [])
        if block.get("type") == "text"
    )
    stop = data.get("stop_reason")
    if stop == "refusal":
        category = (data.get("stop_details") or {}).get("category") or "no category given"
        raise RuntimeError(f"Anthropic declined this request ({category})")
    if stop == "max_tokens" and not text.strip():
        raise RuntimeError("Anthropic used its whole output budget before answering")
    return text


async def _openai(system: str, user: str, max_tokens: int, model: str,
                  temperature: float | None = None) -> str:
    headers = {"Authorization": f"Bearer {config.OPENAI_API_KEY}"}
    refused = _REFUSED.setdefault(model, set())

    def body() -> dict:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if "max_tokens" in refused:
            # Newer models count their reasoning toward this cap, so leave room for it.
            payload["max_completion_tokens"] = max_tokens + THINK_ROOM
        else:
            payload["max_tokens"] = max_tokens
        if temperature is not None and "temperature" not in refused:
            payload["temperature"] = temperature
        if "json" not in refused:
            payload["response_format"] = {"type": "json_object"}  # every prompt asks for JSON
        return payload

    resp = await post_json(OPENAI_URL, headers, body(), 90, "OpenAI")
    for _ in range(3):
        if resp.status_code != 400:
            break
        reason = error_text(resp).lower()
        hit = [k for k, words in (("max_tokens", ("max_tokens", "max_completion_tokens")),
                                  ("temperature", ("temperature",)),
                                  ("json", ("response_format", "json_object")))
               if k not in refused and any(w in reason for w in words)]
        if not hit:
            break
        refused.update(hit)
        resp = await post_json(OPENAI_URL, headers, body(), 90, "OpenAI")
    if resp.status_code >= 400:
        raise RuntimeError(f"OpenAI returned {resp.status_code}: {error_text(resp)}")
    data = resp.json()
    choice = (data.get("choices") or [{}])[0]
    text = (choice.get("message") or {}).get("content") or ""
    if not text.strip() and choice.get("finish_reason") == "length":
        raise RuntimeError("OpenAI used its whole output budget before answering")
    return text


async def _gemini_pick_model() -> str:
    """Ask Google which models this key can use and take the newest plain Flash one."""
    headers = {"x-goog-api-key": config.GEMINI_API_KEY}
    resp = await request_json("GET", f"{GEMINI_MODELS_URL}?pageSize=200", headers, None, 30, "Gemini models")
    if resp.status_code >= 400:
        raise RuntimeError(f"Gemini model list returned {resp.status_code}: {error_text(resp)}")
    names = []
    for m in resp.json().get("models", []):
        name = str(m.get("name", "")).removeprefix("models/")
        if ("generateContent" in (m.get("supportedGenerationMethods") or []) and "flash" in name
                and not re.search(r"lite|image|tts|live|audio|embedding|thinking-exp|learnlm|8b", name)):
            names.append(name)
    if not names:
        raise RuntimeError("Gemini lists no Flash model for this key. Set GEMINI_MODEL in .env.")

    def rank(name: str) -> tuple:
        version = re.search(r"gemini-(\d+(?:\.\d+)?)", name)
        return (float(version.group(1)) if version else 0.0, "preview" not in name and "exp" not in name,
                "latest" not in name, -len(name))
    return max(names, key=rank)


async def _gemini(system: str, user: str, max_tokens: int, model: str,
                  temperature: float | None = None) -> str:
    headers = {"x-goog-api-key": config.GEMINI_API_KEY}
    refused = _REFUSED.setdefault(model, set())

    def body() -> dict:
        gen = {"maxOutputTokens": max_tokens * 2, "responseMimeType": "application/json"}
        if temperature is not None and "temperature" not in refused:
            gen["temperature"] = temperature
        if "flash" in model and "thinking" not in refused:
            gen["thinkingConfig"] = {"thinkingBudget": 0}  # faster; Flash allows thinking off
        else:
            gen["maxOutputTokens"] = max_tokens + THINK_ROOM  # thinking counts toward the cap
        return {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }

    url = GEMINI_URL.format(model=model)
    resp = await post_json(url, headers, body(), 90, "Gemini")
    if resp.status_code == 404 and not config.GEMINI_MODEL and "gemini" not in _MODEL_SWITCH:
        # The default model name is no longer served. Use the newest Flash model instead.
        picked = await _gemini_pick_model()
        _MODEL_SWITCH["gemini"] = picked
        note(f"Gemini no longer serves {model}; using {picked}.")
        return await _gemini(system, user, max_tokens, picked, temperature)
    for _ in range(2):
        if resp.status_code != 400:
            break
        reason = error_text(resp).lower()
        hit = [k for k, words in (("thinking", ("thinking",)), ("temperature", ("temperature",)))
               if k not in refused and any(w in reason for w in words)]
        if not hit:
            hit = [k for k in ("thinking",) if k not in refused]  # the usual cause on newer models
        if not hit:
            break
        refused.update(hit)
        resp = await post_json(url, headers, body(), 90, "Gemini")
    if resp.status_code >= 400:
        raise RuntimeError(f"Gemini returned {resp.status_code}: {error_text(resp)}")
    data = resp.json()
    candidate = (data.get("candidates") or [{}])[0]
    parts = candidate.get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    if not text.strip():
        why = candidate.get("finishReason") or (data.get("promptFeedback") or {}).get("blockReason") or "no reason given"
        raise RuntimeError(f"Gemini returned no text ({why})")
    return text


def parse_json(text: str) -> dict:
    """Pull the JSON object out of a model reply, even with code fences around it."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in model reply")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model reply is not a JSON object")
    return data


async def llm_json(system: str, user: str, stage: str, max_tokens: int = 1800,
                   family: str | None = None, temperature: float | None = None) -> dict:
    """LLM call that must return JSON. Retries once if the reply does not parse."""
    reply = await llm(system, user, stage, max_tokens, family, temperature)
    try:
        return parse_json(reply)
    except (ValueError, json.JSONDecodeError):
        reply = await llm(
            system + "\nReturn ONLY one valid JSON object. No other text.",
            user,
            stage,
            max_tokens,
            family,
            temperature,
        )
        try:
            return parse_json(reply)
        except (ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"the model's reply was not valid JSON twice ({exc})") from None
