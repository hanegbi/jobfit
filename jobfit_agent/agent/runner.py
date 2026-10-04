"""Drive the graph: start (or resume) a thread, answer any approval interrupt, return the report path."""

from pathlib import Path

from langgraph.types import Command

from jobfit_agent.agent import graph as graph_module
from jobfit_agent.agent.timeutil import utc_now


def pending_interrupt(compiled, config) -> dict | None:
    for task in compiled.get_state(config).tasks:
        for item in task.interrupts:
            return item.value
    return None


def run_agent(*, profile: str, top_n: int = 1, thread_id: str, ask=None, url: str | None = None,
              refresh: bool = False, skip_research: bool = False, graph=None,
              out_dir: Path | None = None, resume: bool = False) -> str:
    compiled = graph or graph_module.build_graph()
    config = {"configurable": {"thread_id": thread_id}}
    if resume:
        result = compiled.invoke(None, config)
    else:
        result = compiled.invoke({"profile": profile, "top_n": top_n, "url": url or "", "refresh": refresh,
                                  "skip_research": skip_research, "ask": ask is not None, "now": utc_now(),
                                  "out_dir": str(out_dir) if out_dir else ""}, config)
    payload = pending_interrupt(compiled, config)
    if payload is not None:
        result = compiled.invoke(Command(resume=ask(payload)), config)
    return result["report_path"]
