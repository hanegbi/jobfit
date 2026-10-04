"""Parent graph: select -> research each company once -> run each job -> (ask) -> render.

The report is written first and the decisions come after reading it, so the
approval pause is opt-in (`ask=True`) rather than the default.
"""

import operator
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send, interrupt

from jobfit_agent.agent import cache, config
from jobfit_agent.agent.company_graph import build_company_graph, merge_dicts
from jobfit_agent.agent.job_graph import build_job_graph
from jobfit_agent.agent.report import build, render
from jobfit_agent.agent.tools import jobfit_store


class RunState(TypedDict, total=False):
    profile: str
    top_n: int
    url: str
    refresh: bool
    skip_research: bool
    ask: bool
    out_dir: str
    now: str
    selected: list
    research: Annotated[dict, merge_dicts]
    briefs: Annotated[list, operator.add]
    costs: Annotated[list, operator.add]
    kept_ids: list
    report_path: str


def select_jobs(state: RunState) -> dict:
    conn = jobfit_store.get_conn()
    if state.get("url"):
        return {"selected": jobfit_store.select_by_url(conn, state["url"])}
    return {"selected": jobfit_store.select_jobs(conn, state["profile"], state["top_n"])}


def after_select(state: RunState):
    if not state["selected"]:
        return "render"
    if state.get("skip_research"):
        return [Send("dispatch", state)]
    companies = {j["company_id"]: j["company"] for j in state["selected"]}
    return [Send("research_company", {"company_id": cid, "company_name": name, "now": state["now"],
                                      "refresh": state.get("refresh", False)})
            for cid, name in companies.items()]


def research_company(payload: dict) -> dict:
    company_id = payload["company_id"]
    if not payload["refresh"]:
        cached = cache.load(company_id, payload["now"])
        if cached:
            return {"research": {company_id: cached}}
    result = build_company_graph().invoke(
        {"company_id": company_id, "company_name": payload["company_name"], "now": payload["now"]})
    research = {"company_id": company_id, "company_name": payload["company_name"],
                "fetched_at": payload["now"], "topics": result["topics"]}
    cache.save(company_id, research)
    return {"research": {company_id: research}, "costs": result["costs"]}


def dispatch(state: RunState) -> dict:
    return {}


def fan_out_jobs(state: RunState):
    return [Send("run_job", {"job_id": j["id"], "profile": state["profile"]}) for j in state["selected"]]


def run_job(payload: dict) -> dict:
    brief = build_job_graph().invoke({"job_id": payload["job_id"], "profile": payload["profile"]})["brief"]
    return {"briefs": [brief], "costs": brief["costs"]}


def approve(state: RunState) -> dict:
    briefs = state.get("briefs", [])
    if not briefs or not state.get("ask"):
        return {"kept_ids": [b["job"]["id"] for b in briefs]}
    jobs = [{"id": b["job"]["id"], "company": b["job"]["company"], "title": b["job"]["title"],
             "best_score": build.best_score(b)} for b in briefs]
    kept = interrupt({"jobs": jobs})
    return {"kept_ids": kept if kept is not None else [j["id"] for j in jobs]}


def render_report(state: RunState) -> dict:
    report = build.build_report(briefs=state.get("briefs", []), research=state.get("research", {}),
                                costs=state.get("costs", []), profile=state["profile"], now=state["now"],
                                kept_ids=state.get("kept_ids"))
    out_dir = Path(state["out_dir"]) if state.get("out_dir") else config.OUT_DIR / state["now"].replace(":", "-")
    return {"report_path": str(render.write_report(report, out_dir))}


def build_graph(checkpointer=None):
    graph = StateGraph(RunState)
    graph.add_node("select_jobs", select_jobs)
    graph.add_node("research_company", research_company)
    graph.add_node("dispatch", dispatch)
    graph.add_node("run_job", run_job)
    graph.add_node("approve", approve)
    graph.add_node("render", render_report)
    graph.add_edge(START, "select_jobs")
    graph.add_conditional_edges("select_jobs", after_select, ["research_company", "dispatch", "render"])
    graph.add_edge("research_company", "dispatch")
    graph.add_conditional_edges("dispatch", fan_out_jobs, ["run_job"])
    graph.add_edge("run_job", "approve")
    graph.add_edge("approve", "render")
    graph.add_edge("render", END)
    return graph.compile(checkpointer=checkpointer)
