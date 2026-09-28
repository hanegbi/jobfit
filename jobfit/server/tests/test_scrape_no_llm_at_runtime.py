"""Executable form of spec principle 2: a runtime scrape through the
production composition root never imports the model client module or
the vendor SDK."""

import sys
from datetime import datetime, timezone

from jobfit import config
from jobfit.scrape import bootstrap


class _Resp:
    status_code = 200
    url = "https://acme.com/careers/"
    text = "<ul><li><a href='/careers/backend-1'>Backend Engineer</a></li><li><a href='/careers/frontend-2'>Frontend Engineer</a></li><li><a href='/careers/devops-3'>DevOps Engineer</a></li></ul>"


class _Session:
    def request(self, method, url, **kwargs):
        return _Resp()


def test_runtime_scrape_never_imports_llm_client_or_anthropic(tmp_path, monkeypatch):
    for name in list(sys.modules):
        if name == "jobfit.scrape.llm_client" or name == "anthropic" or name.startswith("anthropic."):
            sys.modules.pop(name)
    monkeypatch.setattr(config, "PAGE_CACHE_DIR", tmp_path / "pages")
    monkeypatch.setattr(config, "LINK_REJECTS_PATH", tmp_path / "rejects.json")

    service = bootstrap.build_scrape_service(_Session(), techmap_index={}, plans_dir=tmp_path / "plans", playwright_available=False)
    result = service.scrape("Acme", "https://acme.com/careers/")

    assert len(result.postings) == 3
    assert "jobfit.scrape.llm_client" not in sys.modules
    assert "anthropic" not in sys.modules


def test_importing_update_jobs_does_not_import_llm_client():
    for name in list(sys.modules):
        if name == "jobfit.scrape.llm_client":
            sys.modules.pop(name)
    import importlib
    import jobfit.scripts.update_jobs as uj
    importlib.reload(uj)
    assert "jobfit.scrape.llm_client" not in sys.modules
