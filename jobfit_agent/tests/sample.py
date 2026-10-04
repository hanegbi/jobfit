"""A hand-made report for render tests and for eyeballing the page:

    PYTHONPATH=. uv run --project jobfit_agent python -m jobfit_agent.tests.sample <dir>
"""

import sys
from pathlib import Path

HOSTILE = '<img src=x onerror=alert(1)><script>alert(2)</script>'


def _job(job_id, title, score, verdict, company, company_id):
    return {
        "job": {"id": job_id, "company_id": company_id, "company": company, "title": title,
                "url": f"https://{company_id}.test/{job_id}", "location": "Tel Aviv, Israel", "city": "Tel Aviv",
                "is_remote": False, "department": "Engineering", "posted_at": "2026-09-20",
                "years_required": 5, "career_url": f"https://{company_id}.com/careers",
                "description": f"We need a {title}. Python, Kubernetes, AWS.\n\nNice to have: Terraform."},
        "scores": {"default": {"score": score, "matched": ["python", "kubernetes"], "confidence": "high"}},
        "fit": {"verdict": verdict, "strengths": ["Strong Python backend background", "Kubernetes in production"],
                "gaps": ["No Terraform", HOSTILE], "deal_breakers": [], "score_agreement": "agrees",
                "rationale": "Solid match on the core stack; infrastructure-as-code is the gap."},
        "referrals": {"is_referral": False, "referral_contact": None,
                      "contacts": [{"name": "Jane Cohen", "position": "Staff Engineer",
                                    "url": "https://linkedin.com/in/jane"}]},
        "plan": {"summary": "Lead with platform work and add Terraform if true.", "edits": [
            {"target": "Built backend services in Python", "change": "Mention Kubernetes deployment ownership",
             "reason": "JD asks for Kubernetes", "only_if_true": False},
            {"target": "new", "change": "Add a Terraform bullet", "reason": "Listed as nice to have",
             "only_if_true": True}]},
        "critique": {"grounded": True, "addresses_gaps": True, "fabricated_claims": [], "feedback": "", "ok": True},
        "iterations": 2,
        "costs": [],
    }


def _topic(data):
    return {"data": data, "retrieved_at": "2026-10-01T10:00:00+00:00",
            "sources": ["https://example.test/source"] if data else [],
            "error": None if data else "no search results"}


def sample_report() -> dict:
    research = {"company_id": "acme", "company_name": "Acme", "fetched_at": "2026-10-01T10:00:00+00:00", "topics": {
        "facts": _topic({"employees": "~200", "location": "Tel Aviv", "founded": "2017", "stage": "Series B",
                         "funding_total": "$85M", "last_round": "Series B, 2025",
                         "evidence_urls": ["https://example.test/source"]}),
        "funding_exit": _topic({"outlook": "uncertain", "reasoning": "Series B, no filings or acquisition news.",
                                "signals": ["Raised $60M in 2025"],
                                "evidence_urls": ["https://example.test/source"]}),
        "reviews": _topic({"pros": [{"text": "Smart colleagues", "mentions": 4}],
                           "cons": [{"text": "Long hours", "mentions": 2}],
                           "evidence_urls": ["https://example.test/source"]}),
        "salary": _topic({"role": "Backend Engineer", "currency": "USD", "low": 140000, "high": 170000,
                          "basis": "base", "evidence_urls": ["https://example.test/source"]}),
        "interview_questions": _topic({"stages": [
            {"stage": "Recruiter call", "questions": ["Why Acme?"]},
            {"stage": "Technical", "questions": ["Design a rate limiter", "Explain Kubernetes pod scheduling"]}],
            "evidence_urls": ["https://example.test/source"]}),
    }}
    empty = {"company_id": "beta", "company_name": "Beta", "fetched_at": "2026-10-01T10:00:00+00:00",
             "topics": {name: _topic(None)
                        for name in ("facts", "funding_exit", "reviews", "salary", "interview_questions")}}
    return {
        "generated_at": "2026-10-01T10:00:00+00:00", "profile": "default",
        "companies": [
            {"id": "acme", "name": "Acme", "best_score": 90, "domain": "acme.com",
             "contacts": [{"name": "Jane Cohen", "position": "Staff Engineer", "url": "https://linkedin.com/in/jane"}],
             "research": research,
             "jobs": [_job("j1", "Senior Backend Engineer", 90, "strong", "Acme", "acme"),
                      _job("j2", "Platform Engineer", 70, "possible", "Acme", "acme")]},
            {"id": "beta", "name": "Beta", "best_score": 80, "domain": None, "contacts": [], "research": empty,
             "jobs": [_job("j3", "DevOps Engineer", 80, "possible", "Beta", "beta")]},
        ],
        "costs": {"by_node": [{"node": "fit_analysis", "model": "ollama:qwen3:4b", "calls": 3,
                               "input_tokens": 9000, "output_tokens": 900, "seconds": 210.5}],
                  "total_seconds": 210.5, "total_input_tokens": 9000, "total_output_tokens": 900},
    }


if __name__ == "__main__":
    from jobfit_agent.agent.report import render
    print(render.write_report(sample_report(), Path(sys.argv[1] if len(sys.argv) > 1 else "sample_out")))
