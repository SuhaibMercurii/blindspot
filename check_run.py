"""Run one category and print the Blind Spot Report, plus the acceptance checks.

  python check_run.py "AI glasses"
        through the running server (http://localhost:8000), so the run is saved for replay
  python check_run.py --direct "AI glasses"
        in this process, no server needed, nothing saved
  python check_run.py --replay 141502-ai-glasses
        validate a saved run (reads runs/<id>.json, calls nothing)

Add --need "..." for the optional specific need, and --port 8001 for another port.
Everything printed is read from the run's own events. The checks recompute the headline,
the missed-list order and the one-engine count from the saved entities and compare them
with what the run reported, so a number on the page can be trusted to match the file.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).parent


async def from_server(question: str, need: str, port: int) -> list[dict]:
    events = []
    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream("POST", f"http://localhost:{port}/api/ask",
                                 json={"question": question, "need": need}) as resp:
            if resp.status_code >= 400:
                raise SystemExit(f"Server returned {resp.status_code}: {(await resp.aread()).decode()[:300]}")
            async for line in resp.aiter_lines():
                if line.strip():
                    events.append(json.loads(line))
    return events


async def direct(question: str, need: str) -> list[dict]:
    import report
    events = []
    try:
        async for event in report.run(question, need):
            events.append(event)
    except Exception as exc:
        events.append({"stage": "error", "message": f"{type(exc).__name__}: {exc}"})
    return events


def date_key(e: dict) -> int:
    return -int((e.get("newest") or "0000-00-00").replace("-", "")[:8] or 0)


def report(events: list[dict]) -> bool:
    by = {e["stage"]: e for e in events}  # the last event of each kind
    if "error" in by:
        print("RUN FAILED:", by["error"]["message"])
    plan, mem, mon, tim, chi, eng, rep, done = (
        by.get(k) for k in ("plan", "memory", "money", "time", "china", "engines", "report", "done"))
    checks = []

    if plan:
        print(f"\nQUESTION  {plan['question']}" + (f"   NEED (typed)  {plan['specific']}" if plan.get("specific") else ""))
        print(f"CATEGORY  {plan['category']}   AREA  {plan['area']}\nNEED      {plan['need']}")
        if plan.get("zh_queries"):
            print("CHINESE QUERIES  " + " | ".join(plan["zh_queries"]))

    if rep:
        cov = rep["coverage"]
        if cov["ready"]:
            print(f"\nHEADLINE  The AI's answer named {cov['named']} of the {cov['found']} things we found.")
        else:
            print(f"\nHEADLINE  not shown: {cov['why_not']}")
        for lens, c in cov["by_lens"].items():
            print(f"   {lens}: {c['named']} of {c['found']} named by the AI, {c['missed']} missed")
        print("   lenses: " + ", ".join(f"{k}: {v}" for k, v in rep["lenses"].items()))

    if mem and "failed" in mem:
        print(f"\n1. MEMORY  failed: {mem['failed']}")
    elif mem:
        hm = mem["hivemind"]
        print(f"\n1. MEMORY  {hm['samples']} of {hm['asked']} answers, temperature {hm['temperature']}, "
              f"models: {', '.join(hm['models']) or '-'}")
        print(f"   {hm['distinct']} different companies; the top 5 took {hm['top5_share']}% of all mentions")
        for c in mem["companies"]:
            split = ", ".join(f"{m} {n}" for m, n in c.get("by_model", {}).items()) if len(hm.get("per_model", [])) > 1 else ""
            extra = f"   (also written: {', '.join(c['variants'])})" if c.get("variants") else ""
            print(f"   {c['count']} of {hm['samples']}  {c['name']}" + (f"   [{split}]" if split else "") + extra)
        for pm in hm.get("per_model", []):
            print(f"   per model: {pm['model']}: {pm['samples']} answers, {pm['distinct']} companies, top: "
                  + ", ".join(f"{t['name']} ({t['count']})" for t in pm["top"]))
        checks.append(("at least 6 hivemind samples", hm["samples"] >= 6, hm["samples"]))
        checks.append(("at least 5 memory companies", len(mem["companies"]) >= 5, len(mem["companies"])))

    if mon and "failed" in mon:
        print(f"\n2. MONEY  failed: {mon['failed']}")
    elif mon:
        st, echo, ft = mon["stake"], mon["echo"], mon.get("filter_test", {})
        print(f"\n2. MONEY  \"{mon['query']}\"  via {mon.get('searched_with', '?')}"
              + (f"  (Telem chose: {', '.join(mon['routing'])})" if mon.get("routing") else "")
              + (f"  [re-run at {mon['retried']} depth: first answer was off topic]" if mon.get("retried") else ""))
        print(f"   echo: {echo['repeated']} of {echo['total']} companies were already in the AI's memory")
        print(f"   flagged pages: {st['flagged']} of {st['total']}  (vendor {st['vendor']}, affiliate/disclosure "
              f"{st['paid']}, over a year old {st['stale']}); opened {st['opened']} of {st.get('tried', st['total'])} tried")
        status = ft.get("status")
        if status == "done":
            print(f"   filter test: pick from all {ft['all_pages']} pages: {ft['all_pick']}; from the "
                  f"{ft['clean_pages']} unflagged: {ft['clean_pick']} -> changed: {'YES' if ft['changed'] else 'NO'}")
        else:
            print("   filter test: " + {"no_clean": "every page is flagged, nothing to compare",
                                        "no_flagged": "no page is flagged, nothing to filter",
                                        "no_pick": f"no usable pick (all: {ft.get('all_pick') or '-'}, unflagged: {ft.get('clean_pick') or '-'})",
                                        "running": "did not finish"}.get(status, str(status)))
        for c in mon["companies"]:
            tags = ("  [in memory]" if c["in_memory"] else "") + ("  [flagged pages only]" if c["flagged_only"] else "")
            print(f"   - {c['name']}{tags}  <- {', '.join(c['sources'])}")
        for s in mon["sources"]:
            flags = []
            if s.get("vendor"):
                flags.append(f"VENDOR site of {s['vendor']}")
            if s.get("paid"):
                flags.append(f"{ {'link': 'AFFILIATE LINK', 'wording': 'DISCLOSURE', 'self': 'SELF-RANKING'}.get(s.get('paid_kind'), 'STAKE')}: {s['paid']!r}")
            if s.get("stale"):
                where = f"the page's own line {s['date_note']!r}" if s.get("date_from") == "page" else "the search engine"
                flags.append(f"OLD: dated {s['date']} by {where}")
            opened = f"opened by {s.get('opened_by') or '?'}" if s.get("opened") else f"not opened{': ' + s['open_error'] if s.get('open_error') else ''}"
            print(f"   {s['id']:>4} {s['domain']:<28} {s.get('date') or 'no date':<10} {opened}; via {s.get('provider') or '?'}")
            for f in flags:
                print(f"          FLAG {f}")
        checks.append(("normal search listed at least 1 company", echo["total"] >= 1, echo["total"]))
        checks.append(("at least 1 page opened", st["opened"] >= 1, st["opened"]))
        checks.append(("filter test finished", status in ("done", "no_clean", "no_flagged", "no_pick"), status))

    if tim and "failed" in tim:
        print(f"\n3. TIME  failed: {tim['failed']}")
    elif tim:
        cos = tim["companies"]
        print(f"\n3. TIME  {len(cos)} young companies, searched via {tim.get('searched_with', '?')}; "
              f"{tim.get('off_topic_results', 0)} off-topic results dropped before the model; "
              f"{tim['not_in_source']} names dropped (not in their cited source)")
        print(f"   the plain \"new startups\" search found {tim['plain_found']} of these {len(cos)} on its own")
        checks.append(("at least 3 companies from the time lens", len(cos) >= 3, len(cos)))

    if chi:
        if "failed" in chi:
            print(f"\n4. CHINA  failed: {chi['failed']}")
        elif chi.get("status") == "needs":
            print(f"\n4. CHINA  not run. {chi['reason']}")
        else:
            print(f"\n4. CHINA  {len(chi['companies'])} things kept (Chinese name found in a post)"
                  + (f", from {chi['posts_read']} recent posts on {', '.join(chi.get('platforms', []))}" if chi.get("posts_read") else ""))
            for c in chi["companies"]:
                w = c["west"]
                print(f"   - {c['name']}" + (f" ({c['zh']})" if c["zh"] != c["name"] else "") + f": {w['status']}; "
                      f"China {c['china']['posts']} posts, Reddit and X {w['posts']} posts; searched as {', '.join(w['checked_names'])}")

    if eng:
        print("\n5. ENGINES  " + (f"{eng['one_engine']} of {eng['of']} things were found by only one engine "
                                 f"(engines: {', '.join(eng['engines'])}; {eng['by_engine']})" if eng["enough"]
                                 else f"not shown. {eng['reason']}"))

    if rep:
        ents = {e["id"]: e for e in rep["entities"]}
        print(f"\nWHAT IT MISSED  ({len(rep['missed'])}, sorted by {rep['sort']})")
        for i in rep["missed"]:
            e = ents[i]
            f = e.get("funding") or {}
            money = " ".join(x for x in (f.get("round"), f.get("amount")) if x) or ("YC " + e["yc_batch"] if e.get("yc_batch") else "")
            print(f"   - {e['name']}" + (f" ({e['zh']})" if e.get("zh") and e["zh"] != e["name"] else "")
                  + f": {e['does']}" + (f"  [{money}]" if money else ""))
            print(f"       why: {'; '.join(e['why_missed'])}")
            if e.get("west"):
                print(f"       West: {e['west']['posts']} posts under {e['west']['checked_names']}; China: {e['china']['posts']} posts on {', '.join(e['china']['platforms'])}")
            for x in e["evidence"]:
                via = f" via {', '.join(x['engine'])}" if x.get("engine") else ""
                print(f"       {x['source_type']:<11} {x['url']}{via}" + (f"\n                   \"{x['quote']}\"" if x.get("quote") else ""))
        named = [e for e in rep["entities"] if e["named_by_ai"]]
        if named:
            print("   found by us and also named by the AI: " + ", ".join(
                f"{e['name']} ({e['ai']['memory_count']} of {e['ai']['memory_samples']} answers"
                + (", in normal search" if e["ai"]["in_normal_search"] else "") + ")" for e in named))
        unverified = [e for e in rep["entities"] if not e["name_verified"]]
        if unverified:
            print("   not counted, name unverified: " + ", ".join(e["name"] for e in unverified))

        # Recompute what the page shows from the saved entities.
        # Runs saved before the China counting rule have no "counted" field: there, every verified name counted.
        counted = [e for e in rep["entities"] if e.get("counted", e["name_verified"])]
        cov = rep["coverage"]
        checks.append(("every entity cites a source", all(e["evidence"] and all(x.get("url") for x in e["evidence"]) for e in rep["entities"]), ""))
        if cov["ready"]:
            k, n = sum(e["named_by_ai"] for e in counted), len(counted)
            checks.append(("headline matches the entities", (cov["named"], cov["found"]) == (k, n), f"{k} of {n}"))
        want = [e["id"] for e in sorted((e for e in counted if not e["named_by_ai"]),
                                        key=lambda e: (-e["independent_sources"], -len(e["engines"]), date_key(e)))]
        checks.append(("missed list is in the stated order", want == rep["missed"], ""))
        checks.append(("named_by_ai follows the counts", all(
            e["named_by_ai"] == (e["ai"]["memory_count"] >= 1 or e["ai"]["in_normal_search"]) for e in rep["entities"]), ""))
        cn = [e for e in rep["entities"] if e.get("china")]
        if cn:
            checks.append(("every China thing has its Chinese name in a quoted post",
                           all(any(e["zh"] in (x.get("quote") or "") for x in e["evidence"]) for e in cn), len(cn)))
            none = [e for e in cn if e["west"]["status"].startswith("No Western posts")]
            checks.append(("every 'No Western posts found' lists the names searched and had an answer",
                           all(e["west"]["checked_names"] and e["west"].get("answered", True) and e["name_verified"] for e in none), len(none)))
            if rep.get("china_rule"):
                checks.append(("a counted China thing has 2+ posts and few or no Western posts", all(
                    e["china"]["posts"] >= 2 and (e["west"]["status"].startswith("No Western posts") or e["west"]["status"] == "Few Western posts")
                    for e in cn if e["counted"] and "time" not in e["lenses"]), sum(1 for e in cn if e["counted"])))
            checks.append(("no unverified name is counted or called 'not found'",
                           all(e["west"]["status"] == "Name unverified" for e in cn if not e["name_verified"]), ""))
        if eng and eng["enough"]:
            searched = [e for e in rep["entities"] if e["engines"]]
            one = sum(1 for e in searched if len(e["engines"]) == 1)
            checks.append(("one-engine count matches the entities", (eng["one_engine"], eng["of"]) == (one, len(searched)), f"{one} of {len(searched)}"))

    if done:
        print(f"\nDONE in {done['seconds']}s  ({done['mode']}, search: {done['provider']}, "
              f"models: {', '.join(done.get('models', []))})")
        for e in done["errors"]:
            print("   ERROR:", e)
        for e in done.get("notes", []):
            print("   note:", e)
        checks.append(("empty errors list", not done["errors"], len(done["errors"])))
        checks.append(("under 60 seconds", done["seconds"] < 60, done["seconds"]))
    saved = by.get("saved")
    if saved:
        print(f"saved as runs/{saved['run_id']}.json")

    print("\nACCEPTANCE")
    for label, ok, value in checks:
        print(f"   {'PASS' if ok else 'FAIL'}  {label}" + (f"  ({value})" if value != "" else ""))
    ok = bool(checks) and all(c[1] for c in checks) and "done" in by and "report" in by
    print("   RESULT:", "PASS" if ok else "FAIL")
    return ok


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("question", nargs="?", default="")
    p.add_argument("--need", default="")
    p.add_argument("--direct", action="store_true", help="run in this process instead of through the server")
    p.add_argument("--replay", default="", help="validate a saved run id")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args()
    if a.replay:
        events = json.loads((ROOT / "runs" / f"{a.replay}.json").read_text())["events"]
    elif not a.question:
        p.error("give a category, or --replay <run id>")
    elif a.direct:
        events = asyncio.run(direct(a.question, a.need))
    else:
        try:
            events = asyncio.run(from_server(a.question, a.need, a.port))
        except httpx.ConnectError:
            raise SystemExit(f"No server on port {a.port}. Start it, or add --direct.")
    sys.exit(0 if report(events) else 1)


if __name__ == "__main__":
    main()
