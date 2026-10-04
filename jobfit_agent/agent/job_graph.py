"""Per-job reasoning: load -> fit analysis -> CV plan <-> critic -> brief."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from jobfit_agent.agent import config, models
from jobfit_agent.agent.schemas import Critique, CvPlan, FitAnalysis
from jobfit_agent.agent.tools import jobfit_store

JOB_FIELDS = ("id", "company_id", "company", "title", "url", "location", "city", "is_remote",
              "department", "description", "posted_at", "years_required", "career_url")


def _ask(node: str, schema, system: str, user: str, fallback: dict) -> tuple[dict, list[dict]]:
    """Run one node's model call. A local model that will not produce the schema
    costs that section of the brief, not the whole run."""
    try:
        out, usage = models.get_llm(node).run(schema, system, user)
    except Exception as error:
        return {**fallback, "error": str(error)[:300]}, []
    return out.model_dump(), [models.cost_entry(node, usage)]


class JobState(TypedDict, total=False):
    job_id: str
    profile: str
    job: dict
    cv_text: str
    scores: dict
    referrals: dict
    fit: dict
    plan: dict
    critique: dict
    iterations: int
    costs: Annotated[list, operator.add]
    brief: dict


def load_job(state: JobState) -> dict:
    job = jobfit_store.job_with_context(jobfit_store.get_conn(), state["job_id"])
    return {
        "job": job,
        "cv_text": jobfit_store.load_cv_text(state["profile"]),
        "scores": job["scores"],
        "referrals": {"is_referral": job["is_referral"], "referral_contact": job.get("referral_contact"),
                      "contacts": job["contacts"]},
        "iterations": 0,
    }


def _headline_score(state: JobState):
    entry = state["scores"].get(state["profile"]) or max(
        state["scores"].values(), key=lambda s: s.get("score") or 0, default={})
    return entry.get("score"), entry.get("matched", [])


def fit_analysis(state: JobState) -> dict:
    score, matched = _headline_score(state)
    system = ("You are a careful career analyst. Judge how well the candidate fits the job using only the CV and "
              "job description given. Never invent experience. The ATS score is a deterministic baseline: say "
              "whether your own view agrees, is higher or is lower. Always fill strengths and gaps with at least "
              "two short items each - a verdict with no evidence behind it is useless. Keep every item one line.")
    user = (f"ATS score: {score}\nMatched skills: {matched}\n\n"
            f"<job_description>\n{state['job']['description'][:config.JD_CHARS]}\n</job_description>\n\n"
            f"<cv>\n{state['cv_text'][:config.CV_CHARS]}\n</cv>")
    blank = {"verdict": "possible", "strengths": [], "gaps": [], "deal_breakers": [],
             "score_agreement": "agrees", "rationale": ""}
    fit, costs = _ask("fit_analysis", FitAnalysis, system, user, blank)
    return {"fit": fit, "costs": costs}


def cv_planner(state: JobState) -> dict:
    system = ("Propose concrete CV edits for this job. Each edit must change a line that exists in the CV "
              "(quote it in `target`) or add something new, in which case set only_if_true=true. Never fabricate "
              "experience, employers, titles or numbers. At most four edits. `change` is the replacement text "
              "only - put the justification in `reason`, never inside the change itself.")
    feedback = state.get("critique", {}).get("feedback", "")
    user = (f"Gaps to address: {state['fit']['gaps']}\n"
            + (f"Reviewer feedback on your previous plan: {feedback}\n" if feedback else "")
            + f"\n<job_description>\n{state['job']['description'][:config.JD_CHARS]}\n</job_description>\n\n"
              f"<cv>\n{state['cv_text'][:config.CV_CHARS]}\n</cv>")
    plan, costs = _ask("cv_planner", CvPlan, system, user, {"summary": "", "edits": []})
    return {"plan": plan, "iterations": state["iterations"] + 1, "costs": costs}


def critic(state: JobState) -> dict:
    system = ("You review a CV edit plan. grounded=true only if every edit quotes real CV text or is flagged "
              "only_if_true. List any fabricated claims. Give short, actionable feedback.")
    user = (f"<cv>\n{state['cv_text'][:config.CV_CHARS]}\n</cv>\n\n"
            f"Plan: {state['plan']}\nGaps: {state['fit']['gaps']}")
    blank = {"grounded": False, "addresses_gaps": False, "fabricated_claims": [], "feedback": ""}
    data, costs = _ask("critic", Critique, system, user, blank)
    # With no gaps to address there is nothing to fail at, and insisting otherwise
    # sent a real run round the loop three times over a plan nobody faulted.
    addressed = data["addresses_gaps"] or not state["fit"]["gaps"]
    # A review that did not happen is not an approval.
    data["ok"] = bool(data["grounded"] and addressed and not data["fabricated_claims"]
                      and not data.get("error"))
    return {"critique": data, "costs": costs}


def route_after_critic(state: JobState) -> str:
    if state["critique"]["ok"] or state["iterations"] >= config.MAX_PLAN_PASSES:
        return "finish"
    return "cv_planner"


def finish(state: JobState) -> dict:
    job = state["job"]
    return {"brief": {
        "job": {field: job.get(field) for field in JOB_FIELDS},
        "scores": state["scores"], "fit": state["fit"], "referrals": state["referrals"],
        "plan": state["plan"], "critique": state["critique"], "iterations": state["iterations"],
        "costs": state["costs"],
    }}


def build_job_graph():
    graph = StateGraph(JobState)
    for name, fn in (("load_job", load_job), ("fit_analysis", fit_analysis), ("cv_planner", cv_planner),
                     ("critic", critic), ("finish", finish)):
        graph.add_node(name, fn)
    graph.add_edge(START, "load_job")
    graph.add_edge("load_job", "fit_analysis")
    graph.add_edge("fit_analysis", "cv_planner")
    graph.add_edge("cv_planner", "critic")
    graph.add_conditional_edges("critic", route_after_critic, {"cv_planner": "cv_planner", "finish": "finish"})
    graph.add_edge("finish", END)
    return graph.compile()
