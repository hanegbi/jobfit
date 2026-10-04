"""Time one real job through one model, so the speed numbers are measured rather than guessed:

    PYTHONPATH=. uv run --project jobfit_agent python -m jobfit_agent.benchmark --model ollama:qwen3:4b
"""

import argparse

from jobfit_agent.agent import config, job_graph
from jobfit_agent.agent.tools import jobfit_store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help='e.g. "ollama:qwen3:4b" or "anthropic:claude-haiku-4-5"')
    parser.add_argument("--job", help="job id; defaults to the top-scored one")
    parser.add_argument("--url", help="job url instead of an id")
    parser.add_argument("--profile", default="default")
    parser.add_argument("--reasoning", action="store_true", help="let the model think first (slower)")
    args = parser.parse_args()

    config.REASONING = args.reasoning
    for node in ("fit_analysis", "cv_planner", "critic"):
        config.NODE_MODELS[node] = args.model

    conn = jobfit_store.get_conn()
    if args.url:
        job_id = jobfit_store.select_by_url(conn, args.url)[0]["id"]
    else:
        job_id = args.job or jobfit_store.select_jobs(conn, args.profile, 1)[0]["id"]

    brief = job_graph.build_job_graph().invoke({"job_id": job_id, "profile": args.profile})["brief"]
    print(f"job:   {brief['job']['company']} — {brief['job']['title']}")
    print(f"model: {args.model}   reasoning={args.reasoning}")
    for cost in brief["costs"]:
        print(f"  {cost['node']:<14}{cost['seconds']:>7.1f}s   in={cost['input_tokens']:>6}  out={cost['output_tokens']:>5}")
    total = sum(c["seconds"] for c in brief["costs"])
    print(f"verdict={brief['fit']['verdict']}  passes={brief['iterations']}  total={total:.1f}s")


if __name__ == "__main__":
    main()
