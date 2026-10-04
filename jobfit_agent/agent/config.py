"""Every path and tuning constant of the agent."""

from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = AGENT_ROOT / "out"
CACHE_DIR = AGENT_ROOT / "data" / "cache"
CHECKPOINT_DB = AGENT_ROOT / "data" / "checkpoints.sqlite"

DEFAULT_TOP_N = 5
RESEARCH_TTL_DAYS = 14
MAX_PLAN_PASSES = 3        # first plan + at most two critic-driven revisions
PAGE_CHARS = 3000          # per page sent to the model when retrieval is off
FETCHES_PER_QUERY = 2
MIN_DOMAIN_DELAY_S = 1.5   # be polite: one request per domain per 1.5s
USE_RETRIEVAL = False      # BM25 chunk selection, switched on in Task 10
NUM_CTX = 8192             # Ollama's default window (4096) would silently cut the CV and job text
# qwen3 thinks before answering, which on a CPU costs ~4x the wall time for these
# short, schema-bound tasks (76s vs 20s measured on one fit call). Off by default;
# turn it on when you care more about the judgement than the wait.
REASONING = False
# A 4B model asked for a list of edits will happily write edits forever. One real
# run spent 20 minutes emitting 3,000 tokens at 2.5 tok/s for an answer that needs
# about 300. Nothing here legitimately needs more, and an answer that overruns the
# cap is caught as a parse failure rather than running until the context fills.
MAX_OUTPUT_TOKENS = 900
# Reading the prompt is the other half of the wall time on a CPU.
JD_CHARS = 4000
CV_CHARS = 4000

# node -> "provider:model". ollama = local and free; anthropic = paid, needs ANTHROPIC_API_KEY.
# One small model everywhere: this machine has no GPU.
SMALL = "ollama:qwen3:4b"
LARGE = SMALL
NODE_MODELS = {
    "fit_analysis": LARGE,
    "cv_planner": LARGE,
    "critic": LARGE,
    "facts": SMALL,
    "funding_exit": SMALL,
    "reviews": SMALL,
    "salary": SMALL,
    "interview_questions": SMALL,
}
