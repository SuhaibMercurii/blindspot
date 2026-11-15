"""Sample data so the page works with no keys and no internet.

Every company and site here is MADE UP (the .example domain is reserved for
examples). The only real data in mock mode is the bundled YC directory.
Never show sample mode to judges as a real result.
"""
import hashlib
import json
import re

_NORMAL = [
    {
        "title": "The 10 best AI chip design tools in 2026",
        "url": "https://chipreview.example/best-ai-chip-design-tools",
        "date": "2026-02-03",
        "content": "Our ranking of the best AI chip design tools. 1. Veridex, the market "
        "standard for AI-assisted synthesis. 2. Cadmus Design, full-flow design suite with "
        "AI placement. 3. Siltrace for timing closure. 4. Gatewise for verification.",
    },
    {
        "title": "Why Siltrace is the best AI EDA tool (and how it compares)",
        "url": "https://siltrace.example/blog/best-ai-eda-tools",
        "date": "2026-05-11",
        "content": "We compared the top AI EDA tools. Siltrace ranks first for timing closure. "
        "Veridex and Nanoflow EDA follow. Cadmus Design is strong for large teams.",
    },
    {
        "title": "AI in semiconductor design: tools engineers use",
        "url": "https://semiweekly.example/ai-tools-engineers-use",
        "date": "2024-07-19",
        "content": "Engineers named Veridex, Orbit Silicon and Cadmus Design as the tools they "
        "use most. Nanoflow EDA was popular for analog work.",
    },
    {
        "title": "Comparing AI verification tools",
        "url": "https://hardwarenotes.example/ai-verification-compared",
        "date": "2025-01-08",
        "content": "Gatewise and Quarry Logic both generate testbenches with AI. Quarry Logic "
        "is cheaper; Gatewise has deeper simulator support.",
    },
]

_DISCOVERY = {
    "seed": [{
        "title": "Kestrel Silicon raises $6M seed to automate RTL verification",
        "url": "https://startupwire.example/kestrel-silicon-seed",
        "date": "2026-09-22",
        "content": "Kestrel Silicon, a San Jose startup, raised $6 million in seed funding led "
        "by Northbeam Ventures. Its AI agents write and run RTL verification tests, the "
        "slowest part of chip design.",
    }],
    "series a": [{
        "title": "Lumen Gate lands Series A for AI floorplanning",
        "url": "https://fundingdaily.example/lumen-gate-series-a",
        "date": "2026-08-14",
        "content": "Lumen Gate has raised an $18 million Series A from Granite Arc Partners and "
        "Tallow Capital. The company uses reinforcement learning to plan chip floorplans in "
        "hours instead of weeks.",
    }],
    "stealth": [{
        "title": "Tessellate comes out of stealth with agentic chip layout",
        "url": "https://deeptechnews.example/tessellate-stealth",
        "date": "2026-10-02",
        "content": "Tessellate emerged from stealth this week with $4.5 million from Harbor Peak "
        "Capital. Its agents turn a chip spec into a first layout draft.",
    }],
    "startups 20": [{
        "title": "Veridex adds AI agents; newcomer Halcyon Circuits launches",
        "url": "https://eetoday.example/veridex-halcyon",
        "date": "2026-10-20",
        "content": "Veridex added AI agents to its synthesis suite. Separately, Halcyon "
        "Circuits launched a tool that finds power bugs in chip designs before tape-out.",
    }],
}


def extract(urls: list[str]) -> dict[str, str]:
    """Sample page text. One page carries affiliate wording so the check shows."""
    pages = {}
    for r in _NORMAL:
        if r["url"] in urls:
            text = r["content"]
            if "chipreview" in r["url"]:
                text = ("Disclosure: this guide contains affiliate links, and we may earn a "
                        "commission when you sign up through them. " + text)
            if "semiweekly" in r["url"]:
                # A one-word menu link must NOT flag the page.
                text = "Home | News | Sponsored | Events\n" + text
            pages[r["url"].rstrip("/")] = text
    return pages


def search(kind: str, query: str) -> list[dict]:
    if kind == "normal":
        return [{**r, "provider": "sample"} for r in _NORMAL]
    if kind == "profile":
        # Every sample page; the pipeline keeps only pages that name the company.
        pages = _NORMAL + [r for rs in _DISCOVERY.values() for r in rs]
        from lenses.base import yc_data  # real YC directory text for YC names
        for c in yc_data():
            if query.lower().startswith(c["name"].lower() + " "):
                pages = [{"title": c["name"], "url": c["yc_url"], "date": c["batch"],
                          "content": f"{c['name']}: {c['one_liner']}. {c['description']}"}]
                break
        return [{**r, "provider": "sample"} for r in pages]
    q = query.lower()
    for key, results in _DISCOVERY.items():
        if key in q:
            return [{**r, "provider": "sample"} for r in results]
    return []


_PLAN = {
    "zh_queries": ["AI 芯片设计 工具 推荐", "芯片设计 AI 助手"],
    "category": "AI chip design tools",
    "area": "AI chip design",
    "need": "Find tools that use AI to speed up chip design and verification.",
    "keywords": ["chip design", "rtl", "verification", "eda", "semiconductor", "silicon",
                 "asic", "verilog", "floorplanning", "layout"],
}

_MEMORY = {"companies": [
    {"name": "Veridex", "does": "Design suite that uses AI to speed up logic synthesis."},
    {"name": "Cadmus Design", "does": "Full chip design flow with AI-driven placement and routing."},
    {"name": "Siltrace", "does": "Timing analysis and closure tool with machine learning."},
    {"name": "Gatewise", "does": "Verification platform that generates tests with AI."},
    {"name": "Nanoflow EDA", "does": "Analog circuit design tool with AI sizing."},
    {"name": "Orbit Silicon", "does": "Cloud chip design environment with AI assistants."},
]}

_NORMAL_EXTRACT = {"companies": [
    {"name": "Veridex", "does": "Design suite using AI for logic synthesis.", "sources": ["n-1", "n-2", "n-3"]},
    {"name": "Cadmus Design", "does": "Full design flow with AI placement.", "sources": ["n-1", "n-2", "n-3"]},
    {"name": "Siltrace", "does": "AI timing closure tool.", "sources": ["n-1", "n-2"]},
    {"name": "Gatewise", "does": "AI testbench and verification tool.", "sources": ["n-1", "n-4"]},
    {"name": "Nanoflow EDA", "does": "Analog design tool with AI.", "sources": ["n-2", "n-3"]},
    {"name": "Orbit Silicon", "does": "Cloud chip design environment.", "sources": ["n-3"]},
    {"name": "Quarry Logic", "does": "Low-cost AI testbench generation.", "sources": ["n-4"]},
]}


def _new_extract(user: str) -> dict:
    ids = {}
    for line in user.splitlines():
        m = re.match(r"\[(d-\d+)\] (.+)", line)
        if m:
            ids[m.group(2)] = m.group(1)

    def sid(title_start: str) -> list[str]:
        return [i for t, i in ids.items() if t.startswith(title_start)]

    companies = [
        {"name": "Kestrel Silicon", "does": "AI agents that write and run RTL verification tests.",
         "round": "Seed", "amount": "$6M", "when": "Sep 2026", "investors": ["Northbeam Ventures"],
         "evidence": "raised $6 million in seed funding led by Northbeam Ventures",
         "sources": sid("Kestrel")},
        # Evidence below is deliberately NOT in the source, to show the check.
        {"name": "Lumen Gate", "does": "Reinforcement learning that plans chip floorplans quickly.",
         "round": "Series A", "amount": "$18M", "when": "Aug 2026",
         "investors": ["Granite Arc Partners"], "evidence": "closed an $18 million Series A round",
         "sources": sid("Lumen")},
        {"name": "Tessellate", "does": "Agents that turn a chip spec into a first layout draft.",
         "round": "Pre-seed", "amount": "$4.5M", "when": "Oct 2026",
         "investors": ["Harbor Peak Capital"],
         "evidence": "emerged from stealth this week with $4.5 million from Harbor Peak Capital",
         "sources": sid("Tessellate")},
        {"name": "Halcyon Circuits", "does": "Finds power bugs in chip designs before tape-out.",
         "round": "", "amount": "", "when": "Oct 2026", "investors": [],
         "evidence": "launched a tool that finds power bugs in chip designs before tape-out",
         "sources": sid("Veridex adds")},
        {"name": "Veridex", "does": "Design suite with AI agents.", "sources": sid("Veridex adds"),
         "evidence": "", "round": "", "amount": "", "when": "", "investors": []},
        # Not in any source: the name check should drop it.
        {"name": "Phantom Chips", "does": "AI chip design.", "sources": sid("Kestrel"),
         "evidence": "", "round": "Seed", "amount": "$2M", "when": "", "investors": []},
    ]
    # Pass through the first two YC directory entries, using their real one-liners.
    for line in user.splitlines():
        m = re.match(r"\[(y-\d+)\] (.+) \(YC (.+)\)", line)
        if m and sum(1 for c in companies if c["sources"][:1] and c["sources"][0].startswith("y-")) < 2:
            companies.append({"name": m.group(2), "does": "", "round": "", "amount": "",
                              "when": m.group(3), "investors": [], "evidence": "",
                              "sources": [m.group(1)]})
    # Fill YC one-liners from the prompt text.
    for c in companies:
        if not c["does"] and c["sources"]:
            m = re.search(rf"\[{re.escape(c['sources'][0])}\].*?\n.*?\n.*?\n[^:]+: ([^.]+)", user)
            c["does"] = m.group(1)[:90] if m else "Startup from a recent YC batch."
    return {"companies": companies}


_MEMORY_CALLS = 0
_DOES = {c["name"]: c["does"] for c in _MEMORY["companies"]}
_DOES.update({
    "Kestrel Silicon": "AI agents write and run RTL verification tests.",
    "Lumen Gate": "Reinforcement learning plans chip floorplans in hours instead of weeks.",
    "Tessellate": "Agents turn a chip spec into a first layout draft.",
    "Halcyon Circuits": "Finds power bugs in chip designs before tape-out.",
})


def _cards(user: str) -> dict:
    cards = []
    for cid, name, body in re.findall(r"^\[(C\d+)\] Company: (.+)\n((?:- .*\n?)*)", user, flags=re.M):
        first = body.split("\n")[0]
        text = first.split(": ", 1)[1] if ": " in first else ""
        text = _DOES.get(name) or re.sub(re.escape(name), "[company]", text, flags=re.I)
        words = text.split()
        if "(no pages found)" in body or not words:
            cards.append({"id": cid, "approach": "Not stated in sources",
                          "solves": "Not stated in sources", "proof": "Not stated in sources"})
            continue
        cards.append({"id": cid, "approach": " ".join(words[:16]),
                      "solves": "Speeds up a step of chip design.",
                      "proof": "Not stated in sources"})
    return {"cards": cards}


def _audition(user: str) -> dict:
    words = ["verification", "rtl", "layout", "floorplan", "agents", "power bugs", "synthesis"]
    cands = re.findall(r"^(C\d+): (.+)$", user, flags=re.M)
    scored = sorted(cands, key=lambda c: -sum(w in c[1].lower() for w in words))
    return {"ranking": [
        {"id": cid, "why": "Description matches speeding up design or verification."}
        for cid, _ in scored[:8]
    ]}


def _curve_guess(user: str) -> dict:
    out = []
    for cid, name, year in re.findall(r"^(\S+) \| (.+?) \| \w+ (\d{4})$", user, flags=re.M):
        h = int(hashlib.md5(name.encode()).hexdigest()[:4], 16) % 100
        chance = max(0, 60 - (int(year) - 2016) * 7)  # sample only, not measured
        out.append({"id": cid, "guess": "Sample guess" if h < chance else "UNKNOWN"})
    return {"answers": out}


def _curve_grade(user: str) -> dict:
    out = []
    for cid, guess in re.findall(r"^(\S+) \| guess: (.+?) \| truth:", user, flags=re.M):
        out.append({"id": cid, "correct": guess.strip() != "UNKNOWN"})
    return {"grades": out}


_SEALED_IDEAS = [
    ("Search agent for supplier discovery", "Finds small suppliers that normal search hides, for procurement teams."),
    ("Citation checker for AI answers", "Opens every page an AI answer cites and marks the claims the page does not support."),
    ("Multi-engine search comparison", "Runs one query on several search engines and shows where their results disagree."),
    ("Research assistant for investors", "Collects recent funding news on a market into one brief for investors."),
]
_SEALED_CALLS = 0


def _sealed_group(user: str) -> dict:
    """Sample grouping: by the idea's first word."""
    groups: dict[str, list[str]] = {}
    for iid, title in re.findall(r"^(i\d+) \| ([^|]*) \|", user, flags=re.M):
        groups.setdefault(title.split()[0] if title.split() else "Other", []).append(iid)
    return {"types": [{"name": f"{k} projects", "describes": "Sample group.", "ids": v} for k, v in groups.items()]}


# China lens sample. Every name, post and site here is MADE UP (.example domains).
def china() -> list[dict]:
    return [
        {"zh": "芯绘助手", "en": "Xinhui Copilot", "pinyin": "xinhui zhushou",
         "does": "Draws chip layout drafts from a written spec.", "en_in_source": True,
         "posts": [{"platform": "Xiaohongshu", "url": "https://xhs.example/post/1", "date": "2026-10-12",
                    "text": "芯绘助手 (Xinhui Copilot) 真的把版图初稿时间缩短了一半", "engagement": 4200},
                   {"platform": "Bilibili", "url": "https://bili.example/video/7", "date": "2026-09-30",
                    "text": "实测芯绘助手：十分钟出版图", "engagement": 18000}],
         "west": {"posts": 0, "checked_names": ["Xinhui Copilot", "芯绘助手", "xinhui zhushou"], "sources": []}},
        {"zh": "验芯", "en": "Yanxin", "pinyin": "yanxin",
         "does": "Runs verification tests on chip designs in the browser.", "en_in_source": True,
         "posts": [{"platform": "Zhihu", "url": "https://zhihu.example/answer/3", "date": "2026-08-19",
                    "text": "验芯 Yanxin 在高校里用得很多", "engagement": 950}],
         "west": {"posts": 3, "checked_names": ["Yanxin", "验芯"],
                  "sources": [{"title": "Anyone tried Yanxin?", "url": "https://forum.example/yanxin", "platform": "Reddit"}]}},
        {"zh": "硅语", "en": "", "pinyin": "guiyu",
         "does": "Chat assistant for analog circuit questions.", "en_in_source": False,
         "posts": [{"platform": "Xiaohongshu", "url": "https://xhs.example/post/9", "date": "2026-10-28",
                    "text": "硅语太适合模拟电路新手了", "engagement": 610}],
         "west": {"posts": 0, "checked_names": ["硅语", "guiyu"], "sources": []}},
        # Its Chinese name is not in its post: the lens must drop it.
        {"zh": "不存在的产品", "en": "Ghost Product", "pinyin": "", "does": "Should never be shown.", "en_in_source": True,
         "posts": [{"platform": "Bilibili", "url": "https://bili.example/video/0", "text": "另一个话题", "engagement": 5}],
         "west": {"posts": 0, "checked_names": ["Ghost Product"], "sources": []}},
    ]


def llm(stage: str, user: str = "") -> str:
    if stage == "pick":
        # Sample pick: the first sample company named in the pages it was given.
        for c in _MEMORY["companies"] + [{"name": "Quarry Logic"}]:
            if c["name"] in user:
                return json.dumps({"pick": c["name"], "why": "Named first in the sample pages."})
        return json.dumps({"pick": "", "why": ""})
    if stage == "check":
        known = any(c["name"].lower() in user.lower() for c in _MEMORY["companies"])
        return json.dumps({"answer": "Sample answer: a made-up chip design tool." if known else "UNKNOWN"})
    if stage == "sealed_idea":
        global _SEALED_CALLS
        _SEALED_CALLS += 1
        title, idea = _SEALED_IDEAS[(_SEALED_CALLS * 7) % 10 % len(_SEALED_IDEAS)]
        return json.dumps({"title": title, "idea": idea})
    if stage == "sealed_group":
        return json.dumps(_sealed_group(user))
    if stage == "plan":
        return json.dumps(_PLAN)
    if stage == "memory":
        global _MEMORY_CALLS
        _MEMORY_CALLS += 1
        names = _MEMORY["companies"]
        n = _MEMORY_CALLS
        picked = names[:3] + [c for i, c in enumerate(names[3:]) if (n + i) % 3]
        if n % 4 == 0:
            picked.append({"name": "Tessellate", "does": "Agents that draft chip layouts."})
        return json.dumps({"companies": picked})
    if stage == "normal":
        return json.dumps(_NORMAL_EXTRACT)
    if stage == "new":
        return json.dumps(_new_extract(user))
    if stage == "cards":
        return json.dumps(_cards(user))
    if stage == "audition":
        return json.dumps(_audition(user))
    if stage == "curve_guess":
        return json.dumps(_curve_guess(user))
    if stage == "curve_grade":
        return json.dumps(_curve_grade(user))
    return "{}"
