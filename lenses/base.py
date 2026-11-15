"""Shared helpers for every lens: names, quotes, dates, the page X-ray, topic checks, YC data.

Everything here is plain code. These are the checks that decide what counts, so none of
them asks a model.
"""
import asyncio
import json
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import config

# ---------- names ----------

GENERIC = {
    "inc", "llc", "ltd", "corp", "corporation", "co", "company", "the", "ai",
    "labs", "lab", "technologies", "technology", "tech", "hq", "io", "systems",
    "group", "gmbh", "sa", "plc", "app", "software", "platform",
}
STOP = GENERIC | {
    "and", "for", "with", "tools", "tool", "startup", "startups", "companies",
    "solutions", "service", "services", "based", "powered", "new", "best", "top",
}


_CJK = re.compile(r"[\u3400-\u9fff]")


def has_cjk(s: str) -> bool:
    return bool(_CJK.search(s or ""))


def squash(s: str) -> str:
    """Lower case, no spaces: how a Chinese name is compared (as written)."""
    return re.sub(r"\s+", "", (s or "").lower())


def name_tokens(name: str) -> list[str]:
    s = re.sub(r"\.(ai|io|com|co|dev|so|app)\b", " ", name.lower())
    tokens = re.findall(r"[a-z0-9]+", s)
    core = [t for t in tokens if t not in GENERIC]
    if has_cjk(name):
        # A Chinese name is matched as written. Only a distinctive Latin part ("MemoMind"
        # in "极米MemoMind眼镜") counts as a word; "AI" alone says nothing.
        return [t for t in core if len(t) >= 3]
    return core or tokens


def same_company(a: str, b: str) -> bool:
    if has_cjk(a) or has_cjk(b):
        if has_cjk(a) and has_cjk(b):
            short, long = sorted((squash(a), squash(b)), key=len)
            return short == long or (len(short) >= 2 and short in long)
        ta, tb = set(name_tokens(a)), set(name_tokens(b))  # one Chinese, one Latin
        return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)
    ta, tb = set(name_tokens(a)), set(name_tokens(b))
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(small) == 1 and len(next(iter(small))) < 3:
        return ta == tb
    return small <= big


def find_match(name: str, names: list[str]) -> str | None:
    for other in names:
        if same_company(name, other):
            return other
    return None


def norm_text(s: str) -> str:
    s = s.lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = s.replace("“", '"').replace("”", '"')
    s = s.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", s).strip()


# Words too ordinary to prove a company is named: "AI Insurance" must appear as that
# phrase, not as any page that says "insurance".
COMMON_WORDS = {
    "insurance", "claims", "claim", "support", "service", "customer", "design", "chip", "chips",
    "data", "cloud", "health", "legal", "finance", "security", "search", "voice", "sales",
    "marketing", "agent", "agents", "assistant", "analytics", "robotics", "energy", "mobile",
    "digital", "global", "network", "networks", "systems", "silicon", "logic", "circuit",
    "circuits", "automation", "intelligence", "machine", "model", "models", "open", "smart",
    "first", "next", "one", "pro", "hub", "works", "code", "copilot", "studio", "labs",
}


def name_in_text(name: str, text: str) -> bool:
    """Is this company named in the text? The name as a phrase counts (any punctuation or
    spacing between its words). Failing that, its distinctive words must appear together and
    in order ("Cadence Design Systems, Inc." is found in "Cadence Design"). A one-word match
    only counts if the word is not an ordinary one."""
    if has_cjk(name):  # a Chinese name: exactly as written, ignoring spaces and case
        return bool(squash(name)) and squash(name) in squash(text)
    text = norm_text(text)
    every = re.findall(r"[a-z0-9]+", norm_text(name))
    if not every:  # a name with no latin letters or digits: plain substring
        return bool(norm_text(name)) and norm_text(name) in text
    if len(every) == 1 and every[0] in COMMON_WORDS:
        return False
    if re.search(r"\b" + r"\W+".join(map(re.escape, every)) + r"\b", text):
        return True
    core = name_tokens(name)
    if len(core) >= 2:
        return bool(re.search(r"\b" + r"\W+(?:\w+\W+)?".join(map(re.escape, core)) + r"\b", text))
    return bool(core) and core[0] not in COMMON_WORDS and len(core[0]) >= 2 \
        and bool(re.search(rf"\b{re.escape(core[0])}\b", text))


def named_on_pages(name: str, text: str) -> bool:
    """Is this name on a long page, as a name? Stricter than name_in_text, because whole
    pages are full of ordinary words: the name must appear exactly as written (same
    capitals), as a whole word. "14.ai" does not match "14 AI tools", and "Minimal AI" does
    not match "minimal AI involvement". A name that is one ordinary capitalised word
    ("Flip", "Wonderful") must appear mid-sentence, where a capital marks a proper noun."""
    n = re.sub(r"\s+", " ", name).strip()
    if len(n) < 2:
        return False
    if has_cjk(n):
        return squash(n) in squash(text)
    if re.fullmatch(r"[A-Z][a-z]+", n):
        return re.search(r"(?<=[a-z0-9,;:)\]] )" + re.escape(n) + r"(?![A-Za-z0-9])", text) is not None
    body = r"\s+".join(re.escape(part) for part in n.split(" "))
    return re.search(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", text) is not None


def evidence_found(evidence: str, texts: list[str]) -> bool:
    ev = norm_text(evidence).strip(" .,;:\"'")
    if len(ev) < 12:
        return False
    return any(ev in norm_text(t) for t in texts)


def amount_in(amount: str, evidence: str) -> bool:
    digits = re.findall(r"\d+(?:\.\d+)?", amount or "")
    return not digits or all(d in evidence for d in digits)


# ---------- page X-ray: who has a stake in this page? ----------

# Wording that shows a page has a commercial stake in what it recommends. Each pattern
# is a multi-word disclosure phrase, and it only counts inside a real sentence (at least
# 40 characters and 7 words, not link text), so a one-word "Sponsored" label, a menu
# link or an "Affiliate program" footer link does not flag a page.
STAKE_PATTERNS = [
    r"affiliate (?:links?|commissions?|disclosure|disclaimer|advertising)",
    r"(?:we|i|this site|this website|our team) (?:may |might |could |will |can )?(?:earn|receive|get|be paid) (?:an? )?(?:small )?(?:affiliate |referral )?(?:commission|fee|compensation)",
    r"earns? (?:an? )?(?:small )?(?:affiliate |referral )?commissions?",
    r"as an amazon associate",
    r"paid (?:partnership|placement|promotion)",
    r"(?:this|our|the) (?:post|article|page|review|guide|list|content|video|episode|newsletter|report|comparison|ranking) (?:is|was|has been) (?:proudly |kindly )?sponsored by",
    r"this (?:post|article|page|review|guide|list) (?:contains|may contain|includes) (?:affiliate|sponsored)",
    r"advertis(?:er|ing) disclosure",
    r"(?:we|i) (?:are|were|may be|might be) compensated",
]
_STAKE = re.compile("|".join(f"(?:{p})" for p in STAKE_PATTERNS), re.I)

# The page itself says that its publisher ranks or includes its own product.
SELF_PATTERNS = [
    r"(?:ranks?|lists?|includes?|features?|recommends?|reviews?) (?:its|our) own (?:products?|platform|tool|software|solution|service)",
    r"(?:its|our) own (?:product|platform|tool|software|solution|service) (?:is|appears|ranks|comes) (?:first|included|listed|featured|on (?:this|the) list|in (?:this|the) (?:list|guide|ranking))",
]
_SELF = re.compile("|".join(f"(?:{p})" for p in SELF_PATTERNS), re.I)

# Links that go through known affiliate networks or carry affiliate parameters.
AFFILIATE_LINK = re.compile(
    r"https?://[^\s)\]\"'<>]*(?:"
    r"shareasale\.com|awin1\.com|anrdoezrs\.net|dpbolvw\.net|jdoqocy\.com|tkqlhce\.com|kqzyfj\.com"
    r"|go\.skimresources\.com|redirect\.viglink\.com|click\.linksynergy\.com|prf\.hn|pntra\.com"
    r"|sjv\.io|tidd\.ly|amzn\.to|[a-z0-9-]+\.partnerstack\.com|grsm\.io|impact\.com/campaign"
    r"|clickbank\.net|refersion\.com"
    r"|[?&](?:aff_id|affid|affiliate_id|aff)=|[?&]utm_medium=affiliate|amazon\.[a-z.]+/[^\s)]*[?&]tag="
    r")[^\s)\]\"'<>]*", re.I)


def _sentence(text: str, start: int, end: int) -> tuple[str, int]:
    """The sentence around text[start:end], and where the match begins inside it."""
    a = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    ends = [i for i in (text.find(c, end) for c in ".!?\n") if i != -1]
    b = min(ends) + 1 if ends else len(text)
    return text[a:b], start - a


def _prose(text: str, m: re.Match) -> str:
    """The sentence a match sits in, if it is real prose: long enough, enough words, and
    the match is not inside a markdown link's text. Else ""."""
    raw, at = _sentence(text, m.start(), m.end())
    if raw.rfind("[", 0, at) > raw.rfind("]", 0, at):  # inside [link text](...)
        return ""
    # Shown as the reader sees it: link targets and markdown emphasis marks removed.
    sentence = re.sub(r"\]\([^)]*\)", "]", raw)
    sentence = re.sub(r"\*{1,3}|_{2,}|[\[\]]", "", sentence)
    sentence = re.sub(r"\s+", " ", sentence).strip(" #>-|")
    if len(sentence) < 40 or len(sentence.split()) < 7:
        return ""
    return sentence[:240]


def find_stake(text: str) -> tuple[str, str]:
    """Return (kind, evidence) for the first commercial stake the page itself shows:
    ("link", the affiliate URL), ("wording", the disclosure sentence),
    ("self", the sentence where the publisher says it ranks its own product),
    or ("", "") when the page shows none."""
    text = text or ""
    link = AFFILIATE_LINK.search(text)
    if link:
        return "link", link.group(0)[:160]
    # Drop link targets first: the dots inside a URL would cut a sentence in half.
    prose = re.sub(r"\]\([^)\s]*(?:\s+\"[^\"]*\")?\)", "]", text)
    for kind, pattern in (("wording", _STAKE), ("self", _SELF)):
        for m in pattern.finditer(prose):
            sentence = _prose(prose, m)
            if sentence:
                return kind, sentence
    return "", ""


_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sept?|oct|nov|dec)[a-z]*\.?"
_DATE = re.compile(
    rf"\b(?:(?P<m1>{_MON}) (?P<d1>\d{{1,2}})(?:st|nd|rd|th)?,? (?P<y1>20\d\d)"
    rf"|(?P<d2>\d{{1,2}})(?:st|nd|rd|th)? (?P<m2>{_MON}),? (?P<y2>20\d\d)"
    r"|(?P<y3>20\d\d)-(?P<m3>\d\d)-(?P<d3>\d\d))", re.I)
_DATE_LABELS = re.compile(
    r"\b(?:published|updated|posted|last|modified|reviewed|on|by|date|mins?|minutes?|read|reading|time)\b", re.I)


def _to_date(m: re.Match) -> date | None:
    try:
        if m.group("y1"):
            return date(int(m.group("y1")), _MONTHS[m.group("m1")[:3].lower()], int(m.group("d1")))
        if m.group("y2"):
            return date(int(m.group("y2")), _MONTHS[m.group("m2")[:3].lower()], int(m.group("d2")))
        return date(int(m.group("y3")), int(m.group("m3")), int(m.group("d3")))
    except (ValueError, KeyError):
        return None


def page_date(text: str) -> tuple[str, str]:
    """The page's own date as (ISO date, the line it was read from), or ("", "").

    Pages are full of other dates (related articles, a study they cite, image file names),
    so only two shapes near the main heading count:
      - a short line that is little more than a date ("Updated: Nov 12, 2026", "Apr 16, 2026",
        "September 26, 2024 Science"),
      - a byline ("by A, B on Oct 16, 2024").
    If several qualify, the latest wins, so an updated page is not called old."""
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    h1 = next((i for i, l in enumerate(lines) if l.startswith("# ")), None)
    if h1 is None:
        return "", ""
    today = date.today()
    found = []
    for raw in lines[max(0, h1 - 4): h1 + 15]:
        line = raw.lstrip("*-+>#• ").strip()
        byline = re.match(r"(?:posted |written |published )?by\b", line, re.I)
        if byline:
            line = re.split(r"[|\[]", line)[0].strip()
        if "](" in line or "http" in line or len(line) > (220 if byline else 90):
            continue
        dates = list(_DATE.finditer(line))
        if len(dates) != 1:
            continue
        d = _to_date(dates[0])
        if d is None or d > today + timedelta(days=2):
            continue
        if byline:
            if not re.search(r"\bon\s*$", line[:dates[0].start()], re.I):
                continue
        else:
            rest = line[:dates[0].start()] + " " + line[dates[0].end():]
            rest = re.sub(r"[\d\W_]+", " ", _DATE_LABELS.sub(" ", rest)).strip()
            if len(rest) > 24:
                continue
        found.append((d, re.sub(r"\s+", " ", line)[:120]))
    if not found:
        return "", ""
    d, line = max(found, key=lambda x: x[0])
    return d.isoformat(), line


def domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def domain_label(url: str) -> str:
    parts = domain(url).split(".")
    if len(parts) >= 3 and parts[-2] in {"co", "com", "org"}:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else parts[0]


_SITE_PREFIX = ("get", "try", "use", "go", "join", "hello", "with", "my", "the", "team", "meet")
_SITE_SUFFIX = ("hq", "app", "ai", "io", "inc", "labs", "lab", "tech", "software", "cloud",
                "group", "global", "corp", "systems", "technologies", "usa")


def is_vendor_site(url: str, name: str) -> bool:
    """Is this page on the company's own site? The site's name must equal the company's
    name, one of its distinctive words, or either of those with a common site prefix or
    suffix: intercom.com for "Intercom", fin.ai for "Intercom Fin", getperspective.ai for
    "Perspective AI", neurons-lab.com for "Neurons Lab", deepmind.google for "Google DeepMind".
    A name that merely appears inside a longer site name (linearb.io for "Linear") is not a match."""
    tokens = name_tokens(name)
    if not tokens:
        return False
    label = re.sub(r"[^a-z0-9]", "", domain_label(url))
    raw = "".join(re.findall(r"[a-z0-9]+", name.lower()))
    wanted = {raw, "".join(tokens)} | {t for t in tokens if len(t) >= 3}
    forms = {label}
    for p in _SITE_PREFIX:
        if label.startswith(p) and len(label) - len(p) >= 3:
            forms.add(label[len(p):])
    for form in list(forms):
        for s in _SITE_SUFFIX:
            if form.endswith(s) and len(form) - len(s) >= 2:
                forms.add(form[:-len(s)])
    return bool(wanted & forms)


def mask(text: str, name: str) -> str:
    """Hide a company's name inside its own description."""
    out = re.sub(re.escape(name), "[company]", text, flags=re.I)
    tokens = name_tokens(name)
    # Hide the brand word too ("Kestrel" in "Kestrel Silicon"), but not plain
    # words like "design" that would wreck the description.
    if tokens and len(tokens[0]) >= 4:
        out = re.sub(rf"\b{re.escape(tokens[0])}\b", "[company]", out, flags=re.I)
    return out


# ---------- is a result about the category at all? ----------

def topic_words(*phrases: str) -> list[str]:
    """The words that make a category what it is: "AI insurance claims processing software"
    -> insurance, claim, processing."""
    out = []
    for phrase in phrases:
        for w in re.findall(r"[a-z0-9]+", phrase.lower()):
            if len(w) > 2 and w not in STOP:
                w = w[:-1] if len(w) > 4 and w.endswith("s") else w
                if w not in out:
                    out.append(w)
    return out


def on_topic(text: str, words: list[str], need: int = 2) -> bool:
    text = text.lower()
    hits = sum(1 for w in words if re.search(rf"\b{re.escape(w)}", text))
    return hits >= min(need, len(words))


# ---------- YC directory ----------

_YC: list[dict] | None = None
SEASON = {"winter": 1, "spring": 2, "summer": 3, "fall": 4}


def yc_data() -> list[dict]:
    global _YC
    if _YC is None:
        path = Path(__file__).parent.parent / "data" / "yc_recent.json"
        _YC = json.loads(path.read_text()) if path.exists() else []
    return _YC


def batch_rank(batch: str) -> int:
    m = re.match(r"(\w+)\s+(\d{4})", batch or "")
    if not m:
        return 0
    return int(m.group(2)) * 10 + SEASON.get(m.group(1).lower(), 0)


def yc_candidates(category: str, keywords: list[str], limit: int = 12) -> list[dict]:
    """Keyword match against recent YC batches. The model later decides which
    of these really fit, so this only has to be a decent shortlist."""
    terms = set()
    for phrase in [category] + list(keywords):
        phrase = phrase.lower().strip()
        if not phrase:
            continue
        words = [w for w in re.findall(r"[a-z0-9]+", phrase) if w not in STOP and len(w) > 2]
        if len(words) > 1:
            terms.add(" ".join(words))
        terms.update(words)
    if not terms:
        return []
    scored = []
    for c in yc_data():
        one = c["one_liner"].lower()
        desc = c["description"].lower()
        tags = " ".join(c["tags"] + c["industries"]).lower()
        score = 0
        for t in terms:
            pattern = rf"\b{re.escape(t)}"
            score += 3 * bool(re.search(pattern, one))
            score += 2 * bool(re.search(pattern, tags))
            score += 1 * bool(re.search(pattern, desc))
        if score >= 4:
            scored.append((score, batch_rank(c["batch"]), c))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [c for _, _, c in scored[:limit]]


# ---------- helpers ----------

def sources_text(sources: list[dict], limit: int = 800) -> str:
    blocks = []
    for s in sources:
        blocks.append(
            f"[{s['id']}] {s['title']}\nDate: {s.get('date') or 'unknown'}\n"
            f"URL: {s['url']}\n{s['content'][:limit]}"
        )
    return "\n\n".join(blocks)


def public(source: dict) -> dict:
    """What the page needs about a source (no full text)."""
    keep = ("id", "title", "url", "date", "date_from", "date_note", "provider", "engines",
            "vendor", "stale", "paid", "paid_kind", "opened", "opened_by", "open_error", "found_by")
    out = {k: source.get(k, "") for k in keep}
    out["domain"] = domain(source["url"])
    return out


def clean_list(companies, limit: int) -> list[dict]:
    out = []
    for c in companies if isinstance(companies, list) else []:
        if not isinstance(c, dict):
            continue
        name = re.sub(r"\s+", " ", str(c.get("name", ""))).strip()
        if not name or len(name) > 80 or find_match(name, [o["name"] for o in out]):
            continue
        c["name"] = name
        c["does"] = str(c.get("does", "")).strip()
        out.append(c)
        if len(out) >= limit:
            break
    return out


def reason(exc: BaseException) -> str:
    text = str(exc).strip()
    if isinstance(exc, asyncio.TimeoutError) and not text:
        text = "took too long and was stopped"
    return (f"{type(exc).__name__}: {text}" if text else type(exc).__name__)[:300]


async def safe(coro, errors: list, label: str, default, timeout: float = 75):
    """Run one step. If it fails or takes too long, record the real reason and carry on."""
    try:
        return await asyncio.wait_for(coro, timeout)
    except Exception as exc:
        errors.append(f"{label}: {reason(exc)}"[:300])
        return default


# ---------- one run's shared state ----------

class Ctx:
    """What the lenses of one run share. report.py fills the plan; each lens adds its results."""

    def __init__(self, question: str, specific: str = ""):
        self.question = question
        self.specific = specific
        self.t0 = 0.0
        self.errors: list[str] = []
        # from the plan
        self.category = question
        self.area = question
        self.need = specific or question
        self.keywords: list[str] = []
        self.zh_queries: list[str] = []
        self.families: list[str] = []
        # from the memory lens
        self.memory_ok = False
        self.memory_samples = 0
        self.memory_spellings: dict[str, str] = {}   # every spelling -> the name shown
        self.memory_counts: dict[str, int] = {}      # name shown -> answers that named it
        # from the money lens
        self.money_ok = False
        self.normal_names: list[str] = []
        self.normal_text = ""   # every result and opened page behind the normal search
        # engines that returned at least one result in this run
        self.engines_seen: set[str] = set()

    def in_memory(self, name: str) -> str | None:
        """The shown name of the memory company this name matches, if any."""
        hit = find_match(name, list(self.memory_spellings))
        return self.memory_spellings[hit] if hit else None

    def saw(self, results) -> None:
        for r in results or []:
            for e in r.get("engines") or []:
                self.engines_seen.add(e)


def source_type(url: str, name: str, directory: bool = False, website: str = "") -> str:
    """own_site: the page is on the company's own site (by name, or the website a directory
    lists for it). directory: a startup directory entry. independent: anything else."""
    if directory:
        return "directory"
    if is_vendor_site(url, name) or (website and domain(website) and domain(url) == domain(website)):
        return "own_site"
    return "independent"
