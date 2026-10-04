"""Briefs + research -> one JSON-serialisable report dict."""

from urllib.parse import urlparse


def best_score(brief: dict) -> float:
    return max((s.get("score") or 0 for s in brief["scores"].values()), default=0)


def company_domain(brief: dict) -> str | None:
    """The company's own domain, for a logo. Taken from its careers page, not from
    the job url, which usually belongs to the ATS (greenhouse, comeet, workday)."""
    host = urlparse(brief["job"].get("career_url") or "").netloc.lower()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def _cost_summary(costs: list[dict]) -> dict:
    by_node: dict[tuple, dict] = {}
    for c in costs:
        row = by_node.setdefault((c["node"], c["model"]), {
            "node": c["node"], "model": c["model"], "calls": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0})
        row["calls"] += 1
        row["input_tokens"] += c["input_tokens"]
        row["output_tokens"] += c["output_tokens"]
        row["seconds"] = round(row["seconds"] + c["seconds"], 2)
    return {"by_node": list(by_node.values()),
            "total_seconds": round(sum(c["seconds"] for c in costs), 2),
            "total_input_tokens": sum(c["input_tokens"] for c in costs),
            "total_output_tokens": sum(c["output_tokens"] for c in costs)}


def build_report(*, briefs, research, costs, profile, now, kept_ids=None) -> dict:
    kept = [b for b in briefs if kept_ids is None or b["job"]["id"] in kept_ids]
    by_company: dict[str, list[dict]] = {}
    for brief in kept:
        by_company.setdefault(brief["job"]["company_id"], []).append(brief)
    companies = []
    for company_id, items in by_company.items():
        items.sort(key=best_score, reverse=True)
        companies.append({
            "id": company_id, "name": items[0]["job"]["company"], "best_score": best_score(items[0]),
            "domain": next((d for d in map(company_domain, items) if d), None),
            "contacts": items[0]["referrals"].get("contacts", []),
            "research": research.get(company_id), "jobs": items,
        })
    companies.sort(key=lambda c: (-c["best_score"], c["name"].lower()))
    return {"generated_at": now, "profile": profile, "companies": companies, "costs": _cost_summary(costs)}
