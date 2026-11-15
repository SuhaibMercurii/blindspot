"""Lens 5, Engines: how much would a single search engine have missed?

Telem labels every result with the engine that returned it. For each thing found, this
counts the engines that surfaced it. The "found by only one engine" number is only
computed when at least three engines returned results in the run; with fewer it would say
nothing, so the tile is hidden and the reason is given instead.
"""
MIN_ENGINES = 3


def summarise(entities: list[dict], engines_seen: set[str]) -> dict:
    engines = sorted(engines_seen)
    searched = [e for e in entities if e.get("engines")]  # directory-only entries have no engine
    out = {"stage": "engines", "engines": engines, "enough": len(engines) >= MIN_ENGINES,
           "one_engine": None, "of": None, "by_engine": {}, "reason": ""}
    if not out["enough"]:
        out["reason"] = (f"Only {len(engines)} engine{'s' if len(engines) != 1 else ''} returned results in this run"
                         f"{' (' + ', '.join(engines) + ')' if engines else ''}. "
                         f"The count needs at least {MIN_ENGINES}, which means searching through Telem.")
        return out
    if not searched:
        out["enough"] = False
        out["reason"] = "Nothing in this run was found through a search engine, so there is nothing to count."
        return out
    out["of"] = len(searched)
    out["one_engine"] = sum(1 for e in searched if len(e["engines"]) == 1)
    out["by_engine"] = {g: sum(1 for e in searched if g in e["engines"]) for g in engines}
    return out
