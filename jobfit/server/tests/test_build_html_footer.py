"""build_html embeds the scoring engine's fingerprint in the page footer
(next to "generated at") so a page built before a scoring fix is visibly
different from one built after - see jobs_v2.meta.json, written by
aggregate_to_jobs_v2."""

import json

from jobfit import build_html, config, cv


def test_build_reads_scoring_engine_from_meta_file_and_embeds_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "JOBS_OUTPUT_META_JSON", tmp_path / "jobs_v2.meta.json")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    config.JOBS_OUTPUT_JSON.write_text("[]", encoding="utf-8")
    config.JOBS_OUTPUT_META_JSON.write_text(json.dumps({"scoring_engine": "abc123def456"}), encoding="utf-8")
    monkeypatch.setattr(cv, "load_registry", lambda: {})

    build_html.build()

    html = config.OUTPUT_HTML.read_text(encoding="utf-8")
    assert "abc123def456" in html


def test_build_tolerates_a_missing_meta_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOBS_OUTPUT_JSON", tmp_path / "jobs_v2.json")
    monkeypatch.setattr(config, "JOBS_OUTPUT_META_JSON", tmp_path / "jobs_v2.meta.json")
    monkeypatch.setattr(config, "OUTPUT_HTML", tmp_path / "jobfit.html")
    config.JOBS_OUTPUT_JSON.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(cv, "load_registry", lambda: {})

    build_html.build()  # must not raise even though jobs_v2.meta.json doesn't exist

    assert config.OUTPUT_HTML.exists()
