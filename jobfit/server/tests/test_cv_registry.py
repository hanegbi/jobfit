import json
from pathlib import Path

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


def _make_pdf(path, text="Python engineer with Kubernetes experience."):
    """Hand-built minimal single-page PDF with a real text content stream -
    no PDF-writing library is a project dependency, so this constructs one
    directly rather than pulling one in just for tests."""
    content = f"BT /F1 12 Tf 10 100 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 300 144] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    Path(path).write_bytes(bytes(out))


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


def test_extract_text_reads_a_pdf(tmp_path):
    source = tmp_path / "resume.pdf"
    _make_pdf(source, "Kubernetes and Terraform experience.")

    text = cv.extract_text(source)

    assert "Kubernetes" in text
    assert "Terraform" in text


def test_register_and_load_profile_works_for_a_pdf_cv(tmp_path):
    source = tmp_path / "resume.pdf"
    _make_pdf(source, "Kubernetes and Python and MLOps experience.")

    profile_id = cv.register_profile("PDF Profile", source)
    profiles = cv.load_profiles()

    assert (config.CV_PROFILES_DIR / f"{profile_id}.pdf").exists()
    assert "kubernetes" in profiles[profile_id]["must_have_keywords"]
