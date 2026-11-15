"""Settings, read from .env (copy .env.example to .env and fill in your keys)."""
import os
from pathlib import Path

ROOT = Path(__file__).parent


def _load_env() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
TELEM_API_KEY = os.getenv("TELEM_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
# Per-family model names. They win over LLM_MODEL, which only applies to the first family.
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "")
# Current Claude models have no temperature setting. For the extraction steps (not the
# hivemind samples, which always run on the provider's defaults) this effort level is sent
# instead: low, medium or high. Empty sends nothing.
LLM_EFFORT = os.getenv("LLM_EFFORT", "low").lower()

# "tavily" or "telem"
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "tavily").lower()
# "accuracy" lets Telem's router pick the engines for the normal "best X" search and
# report which it chose. "off" runs that search on every engine in TELEM_ENGINES.
TELEM_AUTO_ROUTING = os.getenv("TELEM_AUTO_ROUTING", "accuracy").lower()
# The engines Telem may use. Discovery searches run on all of them, and each source is
# credited to the engine that found it. Names: https://router.telem.ai/v1/preprocessors
TELEM_ENGINES = os.getenv("TELEM_ENGINES", "exa,brave,tavily,parallel")
TELEM_BASE_URL = os.getenv("TELEM_BASE_URL", "https://router.telem.ai").rstrip("/")

# How far back the discovery searches look: day, week, month or year.
LOOKBACK = os.getenv("LOOKBACK", "year").lower()
# Results per search call.
NORMAL_RESULTS = int(os.getenv("NORMAL_RESULTS", "8"))
DISCOVERY_RESULTS = int(os.getenv("DISCOVERY_RESULTS", "8"))
# Hivemind check: ask the no-search memory question this many times (in parallel,
# spread across every LLM you have a key for) and count how often each company repeats.
HIVEMIND_SAMPLES = max(1, int(os.getenv("HIVEMIND_SAMPLES", "8")))
# Sampling temperature for the hivemind calls. 1.0 is the normal setting; lower values
# would make answers repeat more and weaken the evidence, so don't lower it for the demo.
HIVEMIND_TEMPERATURE = float(os.getenv("HIVEMIND_TEMPERATURE", "1.0"))
# Max AI calls running at the same time per provider (free tiers rate-limit hard).
# 10 lets the 8 hivemind questions and the planning call of step 1 go out together.
LLM_CONCURRENCY = max(1, int(os.getenv("LLM_CONCURRENCY", "10")))
# Open the pages behind normal search (Tavily Extract) to look for affiliate or
# sponsor wording. Needs TAVILY_API_KEY. How many pages to open per run:
EXTRACT_PAGES = int(os.getenv("EXTRACT_PAGES", "8"))
# Switch a lens off with 0. Memory cannot be switched off: the headline needs it.
LENS_MONEY = os.getenv("LENS_MONEY", "1") == "1"
LENS_TIME = os.getenv("LENS_TIME", "1") == "1"
LENS_CHINA = os.getenv("LENS_CHINA", "1") == "1"
# Use YC's public directory (bundled in data/yc_recent.json) as a second source.
USE_YC = os.getenv("USE_YC", "1") == "1"

_search_key = TELEM_API_KEY if SEARCH_PROVIDER == "telem" else TAVILY_API_KEY
# LLM_CLI=claude uses the Claude command-line tool installed on this computer for the AI
# steps, so a machine with no AI API key can still do live runs.
LLM_CLI = os.getenv("LLM_CLI", "").strip().lower()
LLM_CLI_MODEL = os.getenv("LLM_CLI_MODEL", "haiku").strip()
_have_llm = bool(ANTHROPIC_API_KEY or OPENAI_API_KEY or GEMINI_API_KEY or LLM_CLI == "claude")
MOCK = os.getenv("MOCK", "") == "1" or not (_search_key and _have_llm)

if os.getenv("MOCK", "") == "1":
    MOCK_REASON = "MOCK=1 is set."
elif not _search_key:
    MOCK_REASON = f"No {SEARCH_PROVIDER.upper()}_API_KEY in .env."
elif not _have_llm:
    MOCK_REASON = "No ANTHROPIC_API_KEY, OPENAI_API_KEY or GEMINI_API_KEY in .env."
else:
    MOCK_REASON = ""
