"""Five research topics in parallel, merged into one result."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from jobfit_agent.agent import research
from jobfit_agent.agent.tools import jobfit_store


def merge_dicts(left: dict, right: dict) -> dict:
    return {**(left or {}), **(right or {})}


class CompanyState(TypedDict, total=False):
    company_id: str
    company_name: str
    now: str
    topics: Annotated[dict, merge_dicts]
    costs: Annotated[list, operator.add]


def _topic_node(name: str):
    def node(state: CompanyState) -> dict:
        extra = None
        if name == "salary":
            try:
                extra = jobfit_store.salary_snippets(jobfit_store.get_conn(), state["company_id"])
            except Exception:       # no store wired (e.g. benchmark): the topic still works from the web
                extra = None
        result, costs = research.run_topic(research.TOPICS[name], state["company_name"],
                                           now=state["now"], extra_pages=extra)
        return {"topics": {name: result}, "costs": costs}
    node.__name__ = name
    return node


def build_company_graph():
    graph = StateGraph(CompanyState)
    for name in research.TOPICS:
        graph.add_node(name, _topic_node(name))
        graph.add_edge(START, name)
        graph.add_edge(name, END)
    return graph.compile()
