import json

import pytest
from langgraph.checkpoint.memory import MemorySaver

from jobfit_agent.agent import graph, models, research, runner
from jobfit_agent.agent.report import build
from jobfit_agent.agent.schemas import (Critique, CvEdit, CvPlan, ExitOut, FactsOut, FitAnalysis, InterviewOut,
                                        InterviewStage, ReviewsOut, SalaryOut, Theme)
from jobfit_agent.agent.testing import FakeLLM
from jobfit_agent.agent.tools import jobfit_store
from jobfit_agent.agent.tools.web_search import SearchHit

URL = "https://example.test/p"


@pytest.fixture
def fake_world(store, monkeypatch):
    fake = FakeLLM({
        "FitAnalysis": FitAnalysis(verdict="strong", strengths=["python"], gaps=[], deal_breakers=[],
                                   score_agreement="agrees", rationale="good"),
        "CvPlan": CvPlan(summary="s", edits=[CvEdit(target="line", change="c", reason="r")]),
        "Critique": Critique(grounded=True, addresses_gaps=True, fabricated_claims=[], feedback=""),
        "FactsOut": FactsOut(employees="200", evidence_urls=[URL]),
        "ExitOut": ExitOut(outlook="uncertain", evidence_urls=[URL]),
        "ReviewsOut": ReviewsOut(pros=[Theme(text="people")], evidence_urls=[URL]),
        "SalaryOut": SalaryOut(currency="USD", low=1, high=2, evidence_urls=[URL]),
        "InterviewOut": InterviewOut(stages=[InterviewStage(stage="phone", questions=["q"])], evidence_urls=[URL]),
    })
    monkeypatch.setattr(models, "get_llm", lambda node: fake)
    monkeypatch.setattr(research, "search", lambda q: [SearchHit("t", URL, "snippet")])
    monkeypatch.setattr(research, "fetch_url", lambda u: "page text")
    monkeypatch.setattr(jobfit_store, "load_cv_text", lambda p: "cv text")
    return fake


def _run(tmp_path, name="run", **kwargs):
    kwargs.setdefault("profile", "default")
    kwargs.setdefault("top_n", 3)
    return runner.run_agent(thread_id=name, graph=graph.build_graph(MemorySaver()),
                            out_dir=tmp_path / name, **kwargs)


def _report(tmp_path, name="run"):
    return json.loads((tmp_path / name / "report.json").read_text(encoding="utf-8"))


def test_full_run_researches_each_company_once_and_writes_a_report(fake_world, tmp_path):
    path = _run(tmp_path)
    report = _report(tmp_path)
    assert path.endswith("report.html")
    assert [c["name"] for c in report["companies"]] == ["Acme", "Beta"]       # ordered by best score
    assert [len(c["jobs"]) for c in report["companies"]] == [2, 1]            # j1 + j2 share a tab
    assert report["companies"][0]["research"]["topics"]["facts"]["data"]["employees"] == "200"
    # 2 companies x 5 topics, not 3 jobs x 5 topics
    assert sum(1 for name, _ in fake_world.calls if name == "FactsOut") == 2


def test_no_approval_pause_by_default_so_the_report_comes_first(fake_world, tmp_path):
    _run(tmp_path)
    assert len(_report(tmp_path)["companies"]) == 2      # nothing asked, nothing dropped


def test_an_approval_callback_can_still_drop_jobs(fake_world, tmp_path):
    _run(tmp_path, name="asked", ask=lambda payload: ["j1"])
    report = _report(tmp_path, "asked")
    assert [(c["name"], [j["job"]["id"] for j in c["jobs"]]) for c in report["companies"]] == [("Acme", ["j1"])]


def test_url_mode_runs_exactly_one_job(fake_world, tmp_path):
    from jobfit_agent.tests.conftest import URL_JOB_URL

    _run(tmp_path, name="one", url=URL_JOB_URL)
    report = _report(tmp_path, "one")
    assert [c["name"] for c in report["companies"]] == ["Beta"]
    assert [j["job"]["title"] for j in report["companies"][0]["jobs"]] == ["SRE"]


def test_skip_research_runs_the_job_without_touching_the_web(fake_world, tmp_path):
    _run(tmp_path, name="fast", skip_research=True)
    report = _report(tmp_path, "fast")
    assert report["companies"][0]["research"] is None
    assert not any(name == "FactsOut" for name, _ in fake_world.calls)


def test_research_cache_skips_the_web_on_the_next_run(fake_world, tmp_path):
    for name in ("a", "b"):
        _run(tmp_path, name=name)
    assert sum(1 for name, _ in fake_world.calls if name == "FactsOut") == 2     # second run hit the cache


def test_refresh_ignores_the_cache(fake_world, tmp_path):
    for name, refresh in (("a", False), ("b", True)):
        _run(tmp_path, name=name, refresh=refresh)
    assert sum(1 for name, _ in fake_world.calls if name == "FactsOut") == 4


def test_a_run_with_no_jobs_still_writes_an_empty_report(store, tmp_path):
    # no fake_world: an unscored profile selects nothing, so no model, search or CV is ever touched
    _run(tmp_path, name="empty", profile="no-such-profile")
    assert _report(tmp_path, "empty")["companies"] == []


def test_costs_are_summarised_per_node():
    briefs = [{"job": {"id": "j1", "company_id": "a", "company": "A"}, "scores": {"d": {"score": 5}}, "referrals": {},
               "costs": [{"node": "critic", "model": "m", "input_tokens": 10, "output_tokens": 2, "seconds": 1.0}] * 2}]
    report = build.build_report(briefs=briefs, research={}, costs=briefs[0]["costs"], profile="d", now="n")
    assert report["costs"]["by_node"] == [{"node": "critic", "model": "m", "calls": 2, "input_tokens": 20,
                                           "output_tokens": 4, "seconds": 2.0}]
