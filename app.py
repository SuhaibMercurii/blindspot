"""Web server. Run with:  uvicorn app:app --reload --port 8000"""
import asyncio
import json
import re
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import config
import report
from lenses import china
import providers

app = FastAPI(title="Blindspot")
ROOT = Path(__file__).parent
STATIC = ROOT / "static"
RUNS = ROOT / "runs"
RUNS.mkdir(exist_ok=True)
PINNED = RUNS / "pinned.txt"  # the id of the run the "Replay demo run" button plays
CURVE = ROOT / "data" / "yc_curve.json"
RUN_ID = re.compile(r"^[a-z0-9-]{1,80}$")


class Ask(BaseModel):
    question: str
    need: str = ""


def line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False) + "\n"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "run"


def pinned_id() -> str:
    try:
        run_id = PINNED.read_text().strip()
    except OSError:
        return ""
    return run_id if RUN_ID.match(run_id) and (RUNS / f"{run_id}.json").exists() else ""


@app.get("/")
async def home():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/curve")
async def curve_page():
    return FileResponse(STATIC / "curve.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
async def health():
    families = [] if config.MOCK else providers.available_llms()
    return {
        "mode": "mock" if config.MOCK else "live",
        "mock_reason": config.MOCK_REASON,
        "search_provider": config.SEARCH_PROVIDER,
        "llm": families[0] if families else "none",
        # Who does each step, for the line under the search box.
        "search": {kind: providers.search_label(kind) for kind in ("normal", "discovery", "profile")},
        "opens_pages": ("sample data" if config.MOCK else
                        "Tavily Extract" + (", then Telem fetch" if config.SEARCH_PROVIDER == "telem" else "")),
        "models": [providers.model_name(f) for f in families],
        "samples": config.HIVEMIND_SAMPLES,
        "proof": PROOF.exists(),
        "china": china.availability()[1] if not config.MOCK and not china.availability()[0] else "",
    }


@app.post("/api/ask")
async def ask(body: Ask):
    question = body.question.strip()
    if not question:
        return JSONResponse({"error": "Type a category or question first."}, status_code=400)
    if len(question) > 300 or len(body.need) > 300:
        return JSONResponse({"error": "Keep the question and the need under 300 characters."}, status_code=400)

    async def stream():
        events = []
        try:
            async for event in report.run(question, body.need.strip()):
                events.append(event)
                yield line(event)
        except Exception as exc:  # show the real reason on the page during the demo
            yield line({"stage": "error", "message": f"{type(exc).__name__}: {exc}"[:400]})
            return
        if not config.MOCK:
            run_id = f"{time.strftime('%H%M%S')}-{slug(question)}"
            (RUNS / f"{run_id}.json").write_text(json.dumps(
                {"question": question, "need": body.need.strip(), "saved": time.strftime("%Y-%m-%d %H:%M"), "events": events},
                ensure_ascii=False, indent=1))
            yield line({"stage": "saved", "run_id": run_id})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@app.get("/api/runs")
async def runs():
    out, pin = [], pinned_id()
    for path in sorted(RUNS.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        done = next((e for e in data.get("events", []) if e.get("stage") == "done"), {})
        out.append({"id": path.stem, "question": data.get("question", ""), "saved": data.get("saved", ""),
                    "seconds": done.get("seconds"), "provider": done.get("provider", ""),
                    "errors": len(done.get("errors", [])), "pinned": path.stem == pin})
    return out


@app.post("/api/pin/{run_id}")
async def pin(run_id: str):
    """Mark one saved run as the demo run (one click replays it). Pinning it again unpins."""
    if not RUN_ID.match(run_id) or not (RUNS / f"{run_id}.json").exists():
        return JSONResponse({"error": "No saved run with that name."}, status_code=404)
    if pinned_id() == run_id:
        PINNED.unlink(missing_ok=True)
        return {"pinned": ""}
    PINNED.write_text(run_id)
    return {"pinned": run_id}


@app.get("/api/replay/{run_id}")
async def replay(run_id: str, fast: int = 0):
    if run_id == "pinned":
        run_id = pinned_id()
    path = RUNS / f"{run_id}.json"
    if not RUN_ID.match(run_id) or not path.exists():
        return JSONResponse({"error": "No saved run with that name."}, status_code=404)
    data = json.loads(path.read_text())

    async def stream():
        # Say first that this is a saved run, so the page never presents it as live.
        yield line({"stage": "replaying", "run_id": run_id, "saved": data.get("saved", ""),
                    "question": data.get("question", ""), "need": data.get("need", "")})
        for event in data["events"]:
            if not fast:
                await asyncio.sleep(0.9)  # pace it like a live run
            yield line(event)
        yield line({"stage": "replayed", "run_id": run_id, "saved": data.get("saved", "")})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


class Check(BaseModel):
    name: str


@app.post("/api/check")
async def check(body: Check):
    """Check anything: one company or product name against AI memory, the Western internet,
    the Chinese internet and the engines."""
    name = body.name.strip()
    if not name or len(name) > 80:
        return JSONResponse({"error": "Type a company or product name (up to 80 characters)."}, status_code=400)
    try:
        return await report.check(name)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"[:300]}, status_code=502)


PROOF = STATIC / "proof" / "index.html"


class RepeatBody(BaseModel):
    question: str
    times: int = 8


REPEAT_SYSTEM = ("Answer the question with your single best answer only: one name or a phrase of at most "
                 "six words. No explanation, no list, no punctuation at the end.")


def _answer_key(text: str) -> str:
    """Fold spelling so the same answer counts once: case, punctuation, a leading 'the'."""
    key = re.sub(r"[^a-z0-9 ]+", " ", text.lower())
    return re.sub(r"^(the|a|an) ", "", " ".join(key.split()))


@app.post("/api/repeat")
async def repeat(body: RepeatBody):
    """Ask the AI the same question several times, each as a fresh user, and count the answers."""
    question = " ".join(body.question.split())[:300]
    if len(question) < 4:
        return JSONResponse({"error": "Type a question first."}, status_code=400)
    if config.MOCK:
        return JSONResponse({"error": "This needs live mode: no AI is connected on this computer."}, status_code=400)
    times = max(3, min(body.times, 12))
    started = time.time()
    replies = await asyncio.gather(
        *(asyncio.wait_for(providers.llm(REPEAT_SYSTEM, question, "check", 60), 90) for _ in range(times)),
        return_exceptions=True)
    groups: dict[str, dict] = {}
    failed = 0
    for r in replies:
        if isinstance(r, BaseException) or not str(r).strip():
            failed += 1
            continue
        text = str(r).strip().splitlines()[0][:120]
        g = groups.setdefault(_answer_key(text) or text, {"answer": text, "count": 0})
        g["count"] += 1
    ranked = sorted(groups.values(), key=lambda g: -g["count"])
    asked = times - failed
    return {"question": question, "asked": asked, "failed": failed, "distinct": len(ranked),
            "top_share": round(ranked[0]["count"] / asked, 3) if asked else None, "answers": ranked,
            "model": providers.model_name((providers.available_llms() or ["none"])[0]),
            "seconds": round(time.time() - started, 1)}


@app.get("/proof")
async def proof_page():
    """The Proof page: the Sealed card, copied from Suhayb's write-up and demo page.
    His code folder (vendor/sealed) is not in this repo, so the page says where its numbers
    come from and does not re-check the hash."""
    if PROOF.exists():
        return FileResponse(PROOF, headers={"Cache-Control": "no-store"})
    return JSONResponse({"error": "The Proof page is not in this repo yet (static/proof/index.html is missing)."},
                        status_code=404)


@app.get("/api/curve")
async def curve_data():
    if not CURVE.exists():
        return JSONResponse({"error": "No chart data yet. Run: python yc_curve.py"}, status_code=404)
    return json.loads(CURVE.read_text())
