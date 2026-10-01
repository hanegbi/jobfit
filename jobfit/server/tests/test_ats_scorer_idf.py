"""compute_idf() is pure (strings in, {skill: idf} out) - see
jobfit/ats_scorer/idf.py and docs/superpowers/specs/2026-10-01-scoring-redesign-design.md
section 4. These tests never touch the store or the real corpus."""

import math

from jobfit.ats_scorer.idf import compute_idf
from jobfit.ats_scorer.taxonomy import load_skill_idf, load_skills_taxonomy


def test_a_skill_in_every_document_gets_the_lowest_idf():
    texts = ["I know Python well"] * 10
    weights = compute_idf(texts)
    assert weights["Python"] == math.log(10 / 10)


def test_a_skill_in_no_document_gets_the_corpus_size_floor_not_zero_or_infinity():
    texts = ["I know Python well"] * 10
    weights = compute_idf(texts)
    assert weights["Rust"] == math.log(10)


def test_rarer_skills_get_a_higher_idf_than_common_ones():
    texts = ["Python and Kubernetes"] * 8 + ["Python and Rust"] * 2
    weights = compute_idf(texts)
    assert weights["Rust"] > weights["Kubernetes"] > weights["Python"]


def test_every_taxonomy_skill_appears_in_the_output_even_with_an_empty_corpus():
    weights = compute_idf([])
    taxonomy = load_skills_taxonomy()
    assert set(weights) == {e["canonical"] for e in taxonomy.entries}
    assert all(v == 0.0 for v in weights.values())


def test_a_bullet_mentioning_a_skill_twice_only_counts_once_per_document():
    texts = ["Python Python Python"] + ["Rust"] * 9
    weights = compute_idf(texts)
    assert weights["Python"] == math.log(10 / 1)


def test_load_skill_idf_returns_empty_dict_when_the_file_is_missing(monkeypatch):
    from jobfit.ats_scorer import taxonomy as taxonomy_mod

    monkeypatch.setattr(taxonomy_mod, "SKILL_IDF_PATH", taxonomy_mod.DATA_DIR / "does_not_exist.json")
    taxonomy_mod.load_skill_idf.cache_clear()
    try:
        assert taxonomy_mod.load_skill_idf() == {}
    finally:
        taxonomy_mod.load_skill_idf.cache_clear()


# --- the Phase 2 vocabulary split (see skills_taxonomy.json) -------------

def test_llm_and_genai_are_separate_skills_from_large_language_models():
    taxonomy = load_skills_taxonomy()
    assert taxonomy.find_in_text("we use LLMs daily") == ["LLM"]
    assert taxonomy.find_in_text("a genai product") == ["GenAI"]
    assert taxonomy.find_in_text("deep expertise in large language models") == ["Large Language Models"]


def test_the_new_signature_skills_are_found_in_text():
    taxonomy = load_skills_taxonomy()
    assert "Model Inference" in taxonomy.find_in_text("optimizing model inference latency")
    assert "Distributed Inference" in taxonomy.find_in_text("distributed inference across GPUs")
    assert "ONNX" in taxonomy.find_in_text("exported to ONNX runtime")
    assert "Triton" in taxonomy.find_in_text("served with NVIDIA Triton")
    assert "Observability" in taxonomy.find_in_text("owns observability for the platform")
    assert "LLM Evaluation" in taxonomy.find_in_text("built an LLM evaluation harness")
