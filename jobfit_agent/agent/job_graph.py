"""Per-job reasoning: load -> fit analysis -> CV plan <-> critic -> brief."""

import operator
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from jobfit_agent.agent import config, models
from jobfit_agent.agent.schemas import Critique, CvPlan, FitAnalysis
from jobfit_agent.agent.tools import jobfit_store

JOB_FIELDS = ("id", "company_id", "company", "title", "url", "location", "city", "is_remote",
              "department", "description", "posted_at", "years_required")
_JD_CHARS = 6000
_CV_CHARS = 6000


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
              "whether your own view agrees, is higher or is lower.")
    user = (f"ATS score: {score}\nMatched skills: {matched}\n\n"
            f"<job_description>\n{state['job']['description'][:_JD_CHARS]}\n</job_description>\n\n"
            f"<cv>\n{state['cv_text'][:_CV_CHARS]}\n</cv>")
    fit, usage = models.get_llm("fit_analysis").run(FitAnalysis, system, user)
    return {"fit": fit.model_dump(), "costs": [models.cost_entry("fit_analysis", usage)]}


def cv_planner(state: JobState) -> dict:
    system = ("Propose concrete CV edits for this job. Each edit must change a line that exists in the CV "
              "(quote it in `target`) or add something new, in which case set only_if_true=true. Never fabricate "
              "experience, employers, titles or numbers.")
    feedback = state.get("critique", {}).get("feedback", "")
    user = (f"Gaps to address: {state['fit']['gaps']}\n"
            + (f"Reviewer feedback on your previous plan: {feedback}\n" if feedback else "")
            + f"\n<job_description>\n{state['job']['description'][:_JD_CHARS]}\n</job_description>\n\n"
              f"<cv>\n{state['cv_text'][:_CV_CHARS]}\n</cv>")
    plan, usage = models.get_llm("cv_planner").run(CvPlan, system, user)
    return {"plan": plan.model_dump(), "iterations": state["iterations"] + 1,
            "costs": [models.cost_entry("cv_planner", usage)]}


def critic(state: JobState) -> dict:
    system = ("You review a CV edit plan. grounded=true only if every edit quotes real CV text or is flagged "
              "only_if_true. List any fabricated claims. Give short, actionable feedback.")
    user = f"<cv>\n{state['cv_text'][:_CV_CHARS]}\n</cv>\n\nPlan: {state['plan']}\nGaps: {state['fit']['gaps']}"
    critique, usage = models.get_llm("critic").run(Critique, system, user)
    data = critique.model_dump()
    data["ok"] = critique.grounded and critique.addresses_gaps and not critique.fabricated_claims
    return {"critique": data, "costs": [models.cost_entry("critic", usage)]}


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
