# Blindspot

**See what AI search can't.**

AI search gives everyone the same answer, and that answer has blind spots you can measure. Type a category ("AI glasses", "AI tools for chip design", "AI office apps") and Blindspot returns a Blind Spot Report with one headline:

> The AI's answer named **K of the N** things we found.

| # | Blind spot | Question it answers | Where it lives |
|---|---|---|---|
| 1 | **Memory** | How much does the AI repeat the same names from training? | `lenses/memory.py` |
| 2 | **Money** | Who wrote the pages the answer is built on? | `lenses/money.py` |
| 3 | **Time** | Which recently funded or launched companies did it leave out? | `lenses/time.py` |
| 4 | **China** | What is big on Chinese platforms that it never mentions? | `lenses/china.py` (wraps Suhayb's Arrivals) |
| 5 | **Engines** | How much would a single search engine have missed? | `lenses/engines.py` |

It is not a ranking of who is better and never says "better". It says "the AI didn't show you this, and here is the evidence".

## Status on 2026-11-14, 15:14 (read this before the demo)

| Part | Status |
|---|---|
| Memory, Money, Time, China, Engines, the report, the page, Check anything | **Run live** with `claude-haiku-5-5`, Telem and Tavily. Two categories re-run at 15:11 under the new counting rule, 34 and 37 seconds, no errors. Run files and numbers are under the pitch. |
| Telem | **Live.** Normal search through auto-routing; recent-news searches on Exa, Brave, Tavily and Parallel with the engine kept on every result; Chinese platforms, Reddit and X through its platform sources. |
| China lens | **Live, and this repo's own implementation** on Telem's Xiaohongshu, Bilibili and Zhihu sources. It is not Suhayb's Arrivals code (`vendor/arrivals` never arrived). In the three saved runs, 10 well-known things (RayNeo, Rokid, Ray-Ban Meta, XREAL, Qwen, Doubao, Kimi, NotebookLM, Cadence Virtuoso, WorkBuddy) were checked by hand: none was wrongly labelled "No Western posts found". |
| Page flags | Live. Every flag in the saved runs was read against its page. Only a page's own date line can call it old. |
| Proof page (`/proof`) | **Built from Suhayb's two write-ups** (`SEALED_what_I_did_and_found.pdf` and `SEALED_demo_page.pdf`), not from his code, which is not in this repo. The card, its shares and the posted hash are copied unchanged. The hash is shown but not re-checked here, because the card file is on his laptop. First choice is shown per model family (Claude 70 of 72, GPT 0 of 12), "13 squares cover 100%" is gone, the room is scored by hand at two levels (same square, same core idea), and the automatic idea check is left out. The room score is empty until the real projects are typed in. |
| Page design | Redone at 14:59: one headline with a dot per thing found, five tiles, bars and flags instead of paragraphs, details behind "Evidence" and "How this was counted". Same data, same checks. |
| Second model | Not connected. Every answer comes from one model, and the page says so. |

**Still to get:**

1. Optional: Suhayb's `vendor/sealed` folder, if you want his live page (hash re-check, automatic sorting) instead of the copy from his write-ups, and `vendor/arrivals` if you want his China code instead of this one.
2. Optional: `GEMINI_API_KEY` or `OPENAI_API_KEY`, so the memory lens is cross-model.

Runs take 35 to 50 seconds through Telem. On stage, replay a saved run and use Check anything (3 to 4 seconds) for the live moment.

## Run it

```bash
cd ~/Desktop/blindspot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app:app --reload --port 8000
```

Open http://localhost:8000. Without an LLM key it runs on made-up sample data and an orange banner says so. All settings are in `.env.example`.

```bash
python check_run.py "AI glasses"            # through the server, saved for replay
python check_run.py --direct "AI glasses"   # no server, not saved
python check_run.py --replay <run id>       # validate a saved run
```

`check_run.py` prints the report and recomputes the headline, the missed-list order and the one-engine count from the saved entities, so every number on the page can be matched to the run file.

On stage:

- **Saved runs** replays any live run; every replay says it is a replay and when it was saved.
- **Pin as demo run**, then **Replay demo run** (or the bookmark `http://localhost:8000/?replay=pinned`) plays the best one in one click.
- `http://localhost:8000/?q=AI+glasses` starts a live run. `http://localhost:8000/?check=<name>` runs Check anything.

## How the report is made

| Step | What happens | Decided in plain code |
|---|---|---|
| Plan | One LLM call: category, news phrase, keywords, need, 2–3 Chinese queries | |
| Memory | The no-search question, 8 times in 8 wordings, at temperature 1.0, across every LLM family with a key | Name variants merged, one count per answer, counts split per model |
| Money | `best <category>` search; LLM lists the companies in the results; every page is opened | Name must be in the result it is cited from. Flags: vendor's own site, page says it ranks its own product, disclosure sentence, affiliate link, over a year old. If the search comes back off topic it is re-run once at advanced depth |
| Filter test | The LLM names its single best pick from all pages, then from only the unflagged pages | The two names are compared in code and the page says whether filtering changed the pick, either way. A pick not named in the pages it was given does not count |
| Time | Seed, Series A, out-of-stealth and plain "new startups" news searches, plus YC's directory; LLM picks young companies that fit | Results that never mention the category are dropped before the model. Name must be in the cited source. A quote is shown only if it is in that source word for word; funding only with its quote |
| China | Xiaohongshu, Bilibili and Zhihu through Telem, then Reddit, X and one web search under every name variant | Kept only if the Chinese name is in a post. Status from counts: no Western posts found (top results), few (1–5), seen (more than 5). An English name counts only if a source shows it next to the Chinese name; otherwise "Name unverified", never "No Western posts found". A check that failed says "Western check failed". A thing named in only one post is left out. It is counted in the headline and the China tile only with the status no Western posts found or few; one that is seen in the West and still not named by the AI goes on its own line, "Known in the West, but the AI still didn't name them" |
| Engines | Telem's engine label is kept on every result | "X of N found by only one engine", only with 3 or more engines in the run |
| Merge | Same thing from two lenses becomes one entity. A product goes into its company when both names start with the same brand word (Even G2 into Even Realities) | `named_by_ai` = named in any memory answer or listed by normal search. N = verified entities from Time, plus those from China with few or no Western posts. K = those named. Missed list ordered by independent sources, then engines, then newest date |
| Check anything | One name: 3 no-search AI answers, Reddit / X / web through site-limited search, Chinese platforms, each engine | Counts are of the top 10 results that name it, and say so |

Events (NDJSON from `POST /api/ask`): `plan`, `memory`, `money`, `time`, `china`, `engines`, `report`, `progress`, `done`, `saved`, `error`. A lens that fails sends `{"stage": "<lens>", "failed": "<reason>"}` and the rest of the report carries on.

Files: `app.py` (routes, streaming, saved runs, pin, replay), `report.py` (plan, lenses, merge, coverage, Check anything), `lenses/` (one file per blind spot, `base.py` for shared checks), `providers.py` (Tavily, Telem, the three LLM families), `mock.py` (sample data, all made up), `check_run.py`, `sealed_card.py`, `yc_curve.py`, `pipeline.py` (a shim so old imports work), `static/`, `data/`.

## Search: Tavily or Telem

| Step | `SEARCH_PROVIDER=tavily` | `SEARCH_PROVIDER=telem` |
|---|---|---|
| Normal "best X" search | Tavily | Telem auto-routing picks the engines; the page names them |
| Opening the pages | Tavily Extract | Tavily Extract, then Telem fetch for any page it could not open |
| China lens | "Needs Telem" | Xiaohongshu, Bilibili, Zhihu; Reddit and X (Tavily site search if those fail) |
| Recent-news searches | Tavily news, last year | Telem on Exa, Brave, Tavily and Parallel, last year; every source credits its engine |
| Engines tile | "Needs Telem" (one engine) | Computed |

Telem shapes used in `providers.py`, from the docs: `POST /v1/search` with `{"user_input": {"query"}, "search": {"providers": {"include": [...]}, "num_results", "auto_routing", "published_after", "tier"}}`; results under `preprocessor_runs[].output_payload.results[]`, one run per engine, engine name in `preprocessor_name`. If Suhayb's client differs, his wins.

## Team

| Person | Owns |
|---|---|
| Muhammadjon | This repo: lenses, report, page |
| Suhayb | Arrivals (China lens) and Sealed (Proof page) |

## The 2-minute pitch

1. **Open (20s, Proof page):** "AI gives everyone the same answer. We asked AI models 354 times what to build here, sealed the prediction at 12:09, and [N of M] projects in this room are on the card."
2. **Problem (15s):** "The same thing happens when you ask AI which companies or products matter. The answer has blind spots, and we measure them."
3. **Demo (60s, report):** type the category. "Asked 8 ways, [name] came back [8 of 8]. [X of Y] pages behind normal search had a stake, and filtering them [did/didn't] change the pick. So we searched: [n] recently funded companies, [m] things big on Chinese platforms. The AI's answer named [K of N]."
4. **Judge moment (15s):** Check anything with a name from a judge.
5. **Close (10s):** "[X of N] were found by only one engine. One engine and one memory aren't enough. That's Blindspot, on Telem and Tavily."

Numbers from the two live runs saved on 2026-11-14 after the counting rule changed at 15:10 (Telem + Tavily, `claude-haiku-5-5`, one model, all five lenses). Each column is one run file in `runs/`, and every cell was read from that file by code. `151221-ai-office-apps` is pinned as the demo run.

| Number | `151130-ai-glasses.json` | `151221-ai-office-apps.json` |
|---|---|---|
| Named in all 8 answers | Meta Platforms, Snap, Rokid, Even Realities | Microsoft, Notion Labs, Grammarly, Zoho, Canva |
| Different companies across 8 answers | 18 | 17 |
| Pages with a stake | 4 of 8 | 1 of 8 |
| Of which: vendor / affiliate or disclosure / over a year old | 1 / 3 / 0 | 0 / 1 / 0 |
| Filter test: pick from all pages, then from unflagged pages | Meta, then Meta: did not change | Microsoft CoPilot, then Microsoft Copilot: did not change |
| Recently funded or launched companies found (named by the AI) | 12 (0) | 12 (0) |
| Of those, found by the plain "new startups" search | 4 | 2 |
| Things from Chinese platforms with 2 or more posts | 3 | 6 |
| Of those, few or no Western posts, so counted | 0 | 0 |
| Known in the West, but the AI did not name them | none | WorkBuddy, Doubao (豆包) |
| Seen in the West and named by the AI, not counted | Rokid, RayNeo, XREAL | Claude, Codex |
| Name unverified, not counted | none | 千问办公, 百度搭子 |
| Named in only one Chinese post, left out | 5 | 2 |
| Found by only one engine | 7 of 12 | 7 of 10 |
| Engines that failed on at least one search | exa, parallel, tavily | exa, parallel, tavily |
| **The AI's answer named K of N** | **0 of 12** | **0 of 12** |
| Seconds | 34.2 | 37.2 |
| Errors | 0 | 0 |

Say them as they are:

- **The headline is 0 of 12 in both runs, and it comes from the Time lens alone.** Under the new rule a thing from Chinese platforms counts only with 2 or more posts there and few or no Western posts. Nothing passed in either run: every verified China find was already seen on Reddit and X.
- The China lens still shows something in the office-apps run: WorkBuddy and Doubao are known in the West and in none of the AI's answers. That is the line to read out, not "unknown in the West".
- Filtering the flagged pages did not change the pick in either run. In the 14:39 office-apps run it did. Say what the run on screen shows.
- Some engines failed on at least one search in both runs, so "found by only one engine" is partly engines not answering. The page shows that note under the list.
- A Western count is for the name searched. "Qwen" also counts posts about the model, not only the glasses.
- The runs from 14:38 to 14:40 (`143831-ai-glasses` 4 of 18, `143922-ai-office-apps` 4 of 17, `144004-ai-tools-for-chip-design` 5 of 15) used the old rule, where every verified China find counted. They still replay with their own numbers. Don't mix the two sets.
- Re-validate a run before quoting it: `python check_run.py --replay <run id>`.

## Questions judges will ask

- **"Are the things you found better?"** We don't claim that. We show what the AI left out, with evidence, so you can judge.
- **"Isn't 'not found in the West' just a translation miss?"** Every row lists the names we searched, and unverified names are marked, not counted.
- **"Is the hivemind just one model?"** The page shows the split per model, and says so when only one model answered.
- **"Can't I just ask for new startups?"** That is one of our searches, and the page shows how few it finds alone.
- **"Isn't asking 8 times just randomness?"** Eight different wordings at the normal temperature. They should vary. That they converge on the same names is the finding.
- **"How do you know a page is paid?"** We only flag what the page shows itself: its own site, its own disclosure sentence or an affiliate link, quoted on screen, or its date. Undisclosed deals are missed.
- **"Who pays?"** Scouting teams, cross-border investors and corporate venture arms, as a check on AI research.

## Honest limits

- **"The AI's answer" is the models and the search of that run,** not all AI. With one LLM key it is one model, and the page says so.
- **N is what our searches found,** not the whole market. The headline is coverage of our finds, nothing more.
- **Flags only catch what a page discloses.** On the 23 real pages tested on 2026-11-14 there were no affiliate links at all; the stakes were vendors' own pages, two "we rank our own product" disclosures, and old pages.
- **Page age needs a date line near the title or a date from the search engine.** Plain Tavily search gives none, so many pages have no age.
- **"Not found" means not in the top results** of the searches named on the page.
- **Name matching is by words.** A company with an ordinary name ("AI Insurance") can match a page that uses those words as a phrase.
- **Own-site detection is by name,** so a site like playsparq.com for SPARQ is counted as independent. YC entries use the website the directory lists.
- **Check anything counts answers that describe a name;** it does not check the description is right.
- **Current Claude models have no temperature setting** (fixed at 1.0). The memory samples use the provider's defaults; the extraction steps ask for low effort instead.
- **The blind ranking was removed.** It scored marketing copy as proof and, with 6 new candidates in a pool of 15, a random order already put 2 new ones in the top 5.
- **One saved run holds a row from before a fix.** `runs/145900-what-is-the-best-gaming-phone.json` merged two Chinese finds of one product line (REDMI K90至尊版 and 红米K90) and showed the first one's unverified name with the second one's "Seen in the West" label. The headline count was not affected. `report.merge` now takes the name, the verified flag and the Western check from the same find; the other saved runs come out identical under the new code. Don't replay that file; run the category again.

## Don't say

- Any number you didn't see in a saved run.
- "Better", about anything on the missed list.
- "All AIs agree", unless the per-model split shows it.

## Sources

- [Artificial Hivemind (Jiang et al., arXiv 2510.22954)](https://arxiv.org/abs/2510.22954)
- [Telem docs](https://docs.telem.ai)
- [Search Engine Land: AI models favor familiar brands in search (geoSurge study)](https://searchengineland.com/ai-models-favor-familiar-brands-search-study-484054) and [geoSurge's own post](https://geosurge.ai/posts/model-memory-predicts-which-brands-get-searched). geoSurge sells AI-visibility services; say "a study by geoSurge".
- [Exa Websets overview](https://exa.ai/docs/websets/overview), [Parallel FindAll](https://docs.parallel.ai/findall)
- YC company data: the public [yc-oss/api](https://github.com/yc-oss/api) mirror of YC's directory
