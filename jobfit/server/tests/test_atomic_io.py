import json

from jobfit.atomic_io import write_json_atomic


def test_write_json_atomic_creates_the_file_with_correct_content(tmp_path):
    path = tmp_path / "data.json"
    write_json_atomic(path, {"a": 1, "b": [1, 2, 3]})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": [1, 2, 3]}


def test_write_json_atomic_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "data.json"
    write_json_atomic(path, {"a": 1})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1}


def test_write_json_atomic_overwrites_existing_content_completely(tmp_path):
    path = tmp_path / "data.json"
    write_json_atomic(path, {"a": 1, "b": 2, "c": 3})
    write_json_atomic(path, {"x": 9})

    assert json.loads(path.read_text(encoding="utf-8")) == {"x": 9}


def test_write_json_atomic_leaves_no_leftover_tmp_file(tmp_path):
    path = tmp_path / "data.json"
    write_json_atomic(path, {"a": 1})

    assert not (tmp_path / "data.json.tmp").exists()
