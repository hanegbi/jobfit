"""Run the agent.

    python -m jobfit_agent.cli --url https://job-boards.eu.greenhouse.io/<board>/jobs/<id>
    python -m jobfit_agent.cli --top 5 --profile default

The report is written first; you decide what to do after reading it. Pass --ask
if you want the run to pause for approval before the report instead.
"""

import argparse
import re
import sqlite3
import sys
import webbrowser
from pathlib import Path

from jobfit_agent.agent import config, graph, runner
from jobfit_agent.agent.timeutil import utc_now


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="jobfit_agent", description="Reason about jobs jobfit already scored.")
    parser.add_argument("--url", help="one job's url, instead of the top-scored ones")
    parser.add_argument("--top", type=int, default=config.DEFAULT_TOP_N, help="how many top-scored jobs")
    parser.add_argument("--profile", default="default", help="CV profile id from jobfit's profiles.json")
    parser.add_argument("--refresh", action="store_true", help="ignore the company research cache")
    parser.add_argument("--skip-research", action="store_true", help="fit and CV plan only, no web research")
    parser.add_argument("--ask", action="store_true", help="pause for approval before writing the report")
    parser.add_argument("--resume", metavar="THREAD", help="continue a stopped run")
    parser.add_argument("--thread", help="name for this run (default: a timestamp)")
    parser.add_argument("--out", help="write the report here instead of out/<timestamp>")
    parser.add_argument("--open", dest="open_browser", action="store_true", help="open the report when done")
    return parser.parse_args(argv)


def approve_interactively(payload: dict, *, input_fn=input, say=print) -> list[str]:
    jobs = payload["jobs"]
    for number, job in enumerate(jobs, 1):
        say(f"{number}. {job['company']} — {job['title']} ({job['best_score']:.0f})")
    answer = input_fn("Enter keeps all, or 'drop 2,3' to remove jobs: ").strip().lower()
    match = re.fullmatch(r"drop\s+([\d,\s]+)", answer)
    if not match:
        return [j["id"] for j in jobs]
    dropped = {int(n) for n in re.findall(r"\d+", match.group(1))}
    return [job["id"] for number, job in enumerate(jobs, 1) if number not in dropped]


def main(argv=None, *, input_fn=input) -> int:
    args = parse_args(argv)
    from langgraph.checkpoint.sqlite import SqliteSaver

    config.CHECKPOINT_DB.parent.mkdir(parents=True, exist_ok=True)
    thread = args.resume or args.thread or utc_now().replace(":", "-")
    print(f"thread: {thread}   (resume with --resume {thread})", flush=True)
    conn = sqlite3.connect(config.CHECKPOINT_DB, check_same_thread=False)
    ask = (lambda payload: approve_interactively(payload, input_fn=input_fn)) if args.ask else None
    path = runner.run_agent(profile=args.profile, top_n=args.top, url=args.url, refresh=args.refresh,
                            skip_research=args.skip_research, thread_id=thread, ask=ask,
                            graph=graph.build_graph(SqliteSaver(conn)),
                            out_dir=Path(args.out) if args.out else None, resume=bool(args.resume))
    print(f"report: {path}", flush=True)
    if args.open_browser:
        webbrowser.open(Path(path).resolve().as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())
