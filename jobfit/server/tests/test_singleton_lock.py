import os

from jobfit.server import singleton_lock


def test_acquire_succeeds_when_no_lock_file_exists(tmp_path):
    lock_path = tmp_path / "server.lock"
    singleton_lock.acquire(lock_path)
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_raises_when_another_live_process_holds_the_lock(tmp_path, monkeypatch):
    lock_path = tmp_path / "server.lock"
    lock_path.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(singleton_lock, "_pid_alive", lambda pid: True)

    try:
        singleton_lock.acquire(lock_path)
        assert False, "expected RuntimeError"
    except RuntimeError as error:
        assert "999999" in str(error)


def test_acquire_succeeds_and_takes_over_when_lock_pid_is_dead(tmp_path, monkeypatch):
    lock_path = tmp_path / "server.lock"
    lock_path.write_text("999999", encoding="utf-8")
    monkeypatch.setattr(singleton_lock, "_pid_alive", lambda pid: False)

    singleton_lock.acquire(lock_path)

    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_is_idempotent_for_the_same_process(tmp_path):
    lock_path = tmp_path / "server.lock"
    singleton_lock.acquire(lock_path)
    singleton_lock.acquire(lock_path)  # must not raise on re-entry (e.g. reload)
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_release_removes_a_lock_file_owned_by_this_process(tmp_path):
    lock_path = tmp_path / "server.lock"
    singleton_lock.acquire(lock_path)
    singleton_lock.release(lock_path)
    assert not lock_path.exists()


def test_release_leaves_a_lock_file_owned_by_a_different_process(tmp_path):
    lock_path = tmp_path / "server.lock"
    lock_path.write_text("999999", encoding="utf-8")
    singleton_lock.release(lock_path)
    assert lock_path.exists()


def test_release_is_a_noop_when_no_lock_file_exists(tmp_path):
    lock_path = tmp_path / "server.lock"
    singleton_lock.release(lock_path)  # must not raise
