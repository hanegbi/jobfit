import pytest

from jobfit_agent.agent import config, job_graph, models
from jobfit_agent.agent.schemas import Critique, CvEdit, CvPlan, FitAnalysis
from jobfit_agent.agent.testing import FakeLLM
from jobfit_agent.agent.tools import jobfit_store

FIT = FitAnalysis(verdict="possible", strengths=["python"], gaps=["terraform"], deal_breakers=[],
                  score_agreement="agrees", rationale="close")
PLAN = CvPlan(summary="add k8s", edits=[CvEdit(target="Built services in Python", change="mention Kubernetes",
                                               reason="JD asks for it", only_if_true=True)])
WEAK = Critique(grounded=False, addresses_gaps=True, fabricated_claims=[], feedback="quote real lines")
GOOD = Critique(grounded=True, addresses_gaps=True, fabricated_claims=[], feedback="")


@pytest.fixture
def wired(store, monkeypatch):
    monkeypatch.setattr(jobfit_store, "load_cv_text", lambda profile: "Built services in Python")

    def wire(critiques):
        fake = FakeLLM({"FitAnalysis": FIT, "CvPlan": PLAN, "Critique": critiques})
        monkeypatch.setattr(models, "get_llm", lambda node: fake)
        return fake
    return wire


def run(job_id="j1"):
    return job_graph.build_job_graph().invoke({"job_id": job_id, "profile": "default"})["brief"]


def test_one_pass_when_the_critic_approves(wired):
    wired([GOOD])
    brief = run()
    assert brief["iterations"] == 1 and brief["critique"]["ok"] is True
    assert brief["job"]["title"] == "Senior Backend Engineer"
    assert brief["scores"]["default"]["score"] == 90
    assert [c["name"] for c in brief["referrals"]["contacts"]] == ["Jane"]
    assert [c["node"] for c in brief["costs"]] == ["fit_analysis", "cv_planner", "critic"]


def test_the_critic_loop_revises_once_then_stops(wired):
    fake = wired([WEAK, GOOD])
    brief = run()
    assert brief["iterations"] == 2
    planner_prompts = [u for name, u in fake.calls if name == "CvPlan"]
    assert "quote real lines" in planner_prompts[1]      # critic feedback reaches the second pass


def test_the_loop_is_bounded_when_the_critic_never_approves(wired):
    wired([WEAK])
    brief = run()
    assert brief["iterations"] == config.MAX_PLAN_PASSES and brief["critique"]["ok"] is False
