import json

import docx as docx_lib
import pytest

from jobfit import config, cv


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CV_PROFILES_DIR", tmp_path / "cvs")
    monkeypatch.setattr(config, "CV_PROFILES_REGISTRY", tmp_path / "profiles.json")
    yield tmp_path


def _make_docx(path, text="Python engineer with Kubernetes experience."):
    document = docx_lib.Document()
    document.add_paragraph(text)
    document.save(str(path))


def test_load_registry_returns_empty_dict_when_no_file(tmp_path):
    assert cv.load_registry() == {}


def test_save_then_load_registry_round_trips(tmp_path):
    cv.save_registry({"default": {"name": "Default", "filename": "default.docx", "uploaded_at": "x"}})
    assert cv.load_registry() == {"default": {"name": "Default", "filename": "default.docx", "uploaded_at": "x"}}


def test_register_profile_copies_file_and_adds_registry_entry(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source)

    profile_id = cv.register_profile("Data Engineering", source)

    assert profile_id == "data_engineering"
    registry = cv.load_registry()
    assert registry[profile_id]["name"] == "Data Engineering"
    assert (config.CV_PROFILES_DIR / registry[profile_id]["filename"]).exists()


def test_register_profile_dedupes_id_on_name_collision(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source)

    first_id = cv.register_profile("Infra", source)
    second_id = cv.register_profile("Infra", source)

    assert first_id == "infra"
    assert second_id == "infra_2"
    assert set(cv.load_registry()) == {"infra", "infra_2"}


def test_remove_profile_deletes_file_and_registry_entry(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source)
    profile_id = cv.register_profile("Default", source)
    file_path = config.CV_PROFILES_DIR / cv.load_registry()[profile_id]["filename"]
    assert file_path.exists()

    cv.remove_profile(profile_id)

    assert profile_id not in cv.load_registry()
    assert not file_path.exists()


def test_remove_profile_is_a_noop_for_unknown_id():
    cv.remove_profile("does-not-exist")  # must not raise


def test_load_profiles_builds_one_entry_per_registered_cv(tmp_path):
    source = tmp_path / "resume.docx"
    _make_docx(source, "Kubernetes and Python and MLOps experience.")
    profile_id = cv.register_profile("Platform", source)

    profiles = cv.load_profiles()

    assert set(profiles) == {profile_id}
    assert "kubernetes" in profiles[profile_id]["must_have_keywords"]


def test_load_profiles_is_empty_when_no_profiles_registered():
    assert cv.load_profiles() == {}
