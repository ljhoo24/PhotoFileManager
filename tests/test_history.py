import os
from dataclasses import asdict
from threading import Event

import pytest

from photo_file_manager import history
from photo_file_manager.engine import (
    FileStamp,
    ValidationError,
    build_plan,
    execute_plan,
    move_file,
)
from photo_file_manager.history import (
    HistoryError,
    HistoryStore,
    content_hash,
    execute_recorded_plan,
    undo_job,
)
from photo_file_manager.rules import ClassificationOptions


@pytest.fixture
def setup(tmp_path):
    source, output = tmp_path / "input", tmp_path / "output"
    source.mkdir()
    store = HistoryStore(tmp_path / "history/history.sqlite3")
    return source, output, store


def make(root, name="nested/photo.jpg", data=b"original bytes"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def run(store, source, output, **kwargs):
    return execute_recorded_plan(store, build_plan(source, output), {}, **kwargs)


def test_preview_is_read_only_and_shows_collisions(setup):
    source, output, store = setup
    first = make(source)
    make(output, data=b"existing destination")
    plan = build_plan(source, output)
    assert plan.items[0].conflict and plan.items[0].stamp == FileStamp.read(first)
    assert first.read_bytes() == b"original bytes"
    assert not store.path.exists()


def test_source_change_after_preview_is_not_moved(setup):
    source, output, _ = setup
    first = make(source)
    plan = build_plan(source, output)
    first.write_bytes(b"changed")
    results = []
    summary = execute_plan(plan, on_result=results.append)
    assert summary.failed == 1
    assert not output.exists() and first.read_bytes() == b"changed"
    assert "미리보기 이후" in results[0].message


def test_new_files_after_preview_are_not_silently_added(setup):
    source, output, store = setup
    make(source, "a.jpg")
    plan = build_plan(source, output)
    added = make(source, "new.jpg")
    job, summary = execute_recorded_plan(store, plan, {})
    assert summary.moved == 1 and added.exists()
    assert len(store.entries(job)) == 1


def test_history_persists_and_undo_restores_original_paths(setup):
    source, output, store = setup
    first = make(source)
    job, summary = run(store, source, output)
    assert summary.moved == 1 and not first.exists()
    reopened = HistoryStore(store.path)
    rows = reopened.entries(job)
    assert rows[0]["sha256"] == content_hash(output / "nested/photo.jpg")
    assert reopened.jobs()[0]["moved"] == reopened.jobs()[0]["undoable"] == 1
    undo = undo_job(reopened, job)
    assert undo.restored == 1 and undo.remaining == undo.conflicts == 0
    assert first.read_bytes() == b"original bytes"
    assert not (output / "nested/photo.jpg").exists()
    assert reopened.jobs()[0]["state"] == "undone"
    assert reopened.entries(job)[0]["state"] == "undone"
    assert undo_job(reopened, job).restored == 0


def test_classified_paths_restore_the_original_tree(setup, photo_factory):
    source, output, store = setup
    first = photo_factory(source / "trip/deep/a.raf", model="X-T5")
    options = ClassificationOptions()
    plan = build_plan(source, output, classification=options)
    job, _ = execute_recorded_plan(store, plan, asdict(options))
    assert (output / "2026년 10월 09일 RAW/a.raf").exists()
    undo = undo_job(store, job)
    assert undo.restored == 1 and first.exists()


def test_changed_content_is_not_restored_even_with_same_size_and_mtime(setup):
    source, output, store = setup
    first = make(source, data=b"AAAA")
    job, _ = run(store, source, output)
    moved = output / "nested/photo.jpg"
    stamp = moved.stat()
    moved.write_bytes(b"BBBB")
    os.utime(moved, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    summary = undo_job(store, job)
    assert summary.conflicts == summary.remaining == 1
    assert not first.exists() and moved.read_bytes() == b"BBBB"
    assert "내용이 변경" in store.entries(job)[0]["undo_message"]


def test_restore_collision_preserves_both_and_can_be_retried(setup):
    source, output, store = setup
    original = make(source)
    job, _ = run(store, source, output)
    original.write_bytes(b"new file in original location")
    assert undo_job(store, job).conflicts == 1
    assert original.read_bytes() == b"new file in original location"
    assert (output / "nested/photo.jpg").read_bytes() == b"original bytes"
    original.rename(source / "keep-new.jpg")
    assert undo_job(store, job).restored == 1
    assert original.read_bytes() == b"original bytes"


def test_missing_output_is_a_conflict(setup):
    source, output, store = setup
    make(source)
    job, _ = run(store, source, output)
    (output / "nested/photo.jpg").unlink()
    result = undo_job(store, job)
    assert result.conflicts == 1 and result.restored == 0


def test_cancelled_job_only_undoes_successful_moves(setup):
    source, output, store = setup
    make(source, "a.jpg")
    remaining = make(source, "b.jpg")
    cancel = Event()
    job, result = run(
        store, source, output, on_result=lambda _: cancel.set(), is_cancelled=cancel.is_set
    )
    assert result.moved == result.pending == 1 and result.cancelled
    assert store.job(job)["state"] == "cancelled"
    assert undo_job(store, job).restored == 1
    assert remaining.exists() and (source / "a.jpg").exists()


def test_cancel_undo_leaves_remaining_files_retryable(setup):
    source, output, store = setup
    make(source, "a.jpg")
    make(source, "b.jpg")
    job, _ = run(store, source, output)
    cancel = Event()
    result = undo_job(store, job, on_result=lambda _: cancel.set(), is_cancelled=cancel.is_set)
    assert result.restored == result.remaining == 1 and result.cancelled
    assert undo_job(store, job).restored == 1


def test_history_failure_before_move_leaves_source_untouched(setup, monkeypatch):
    source, output, store = setup
    original = make(source)

    def fail(*args, **kwargs):
        raise HistoryError("disk full")

    monkeypatch.setattr(store, "update_entry", fail)
    with pytest.raises(HistoryError):
        run(store, source, output)
    assert original.exists()
    assert not (output / "nested/photo.jpg").exists()


def test_crash_after_move_recovers_from_precommitted_hash(setup, monkeypatch):
    source, output, store = setup
    original = make(source)
    update = store.update_entry

    def lose_completion(job_id, ordinal, **values):
        if values.get("state") == "moved":
            raise HistoryError("simulated crash after rename")
        update(job_id, ordinal, **values)

    monkeypatch.setattr(store, "update_entry", lose_completion)
    with pytest.raises(HistoryError):
        run(store, source, output)
    job = store.jobs()[0]["id"]
    assert store.entries(job)[0]["state"] == "moving"
    assert not original.exists()
    reopened = HistoryStore(store.path)
    assert undo_job(reopened, job).restored == 1
    assert original.exists()


def test_crash_after_restore_is_reconciled_without_overwriting(setup):
    source, output, store = setup
    original = make(source)
    job, _ = run(store, source, output)
    store.update_entry(job, 0, state="undoing")
    move_file(output / "nested/photo.jpg", original)
    assert undo_job(store, job).restored == 1
    assert store.entries(job)[0]["state"] == "undone"
    assert original.read_bytes() == b"original bytes"


def test_collision_entries_are_never_undone(setup):
    source, output, store = setup
    original = make(source)
    target = make(output, data=b"existing")
    job, result = run(store, source, output)
    assert result.skipped == 1 and store.jobs()[0]["undoable"] == 0
    assert undo_job(store, job).restored == 0
    assert original.read_bytes() == b"original bytes"
    assert target.read_bytes() == b"existing"


def test_cross_volume_move_and_restore(setup, monkeypatch):
    source, output, store = setup
    original = make(source)

    def copy_then_remove(src, dst):
        history_copy(src, dst)

    from photo_file_manager import engine

    history_copy = engine._copy_then_remove
    monkeypatch.setattr(engine, "move_file", copy_then_remove)
    monkeypatch.setattr(history, "move_file", copy_then_remove)
    job, _ = run(store, source, output)
    assert undo_job(store, job).restored == 1
    assert original.read_bytes() == b"original bytes"


def test_preview_collision_stays_skipped_if_destination_disappears(setup):
    source, output, store = setup
    original = make(source)
    target = make(output)
    plan = build_plan(source, output)
    target.unlink()
    _, result = execute_recorded_plan(store, plan, {})
    assert result.skipped == 1 and original.exists()
    assert not target.exists()


def test_cancel_during_hash_does_not_move_unrecorded_file(setup, monkeypatch):
    source, output, store = setup
    original = make(source)

    def cancelled(*args):
        raise history.OperationCancelled()

    monkeypatch.setattr(history, "content_hash", cancelled)
    job, result = run(store, source, output)
    assert result.cancelled and result.pending == 1
    assert original.exists() and store.entries(job)[0]["state"] == "pending"


def test_hash_failure_retains_original(setup, monkeypatch):
    source, output, store = setup
    original = make(source)

    def fail(*args):
        raise PermissionError("cannot read source")

    monkeypatch.setattr(history, "content_hash", fail)
    with pytest.raises(HistoryError):
        run(store, source, output)
    assert original.exists()


def test_source_mutating_during_metadata_scan_is_rejected(setup, monkeypatch):
    from photo_file_manager import engine

    source, output, _ = setup
    original = make(source)

    def change(path):
        path.write_bytes(b"edited during preview")
        return engine.PhotoMetadata()

    monkeypatch.setattr(engine, "read_metadata", change)
    with pytest.raises(ValidationError):
        build_plan(source, output, classification=ClassificationOptions())
    assert original.exists() and not output.exists()


@pytest.mark.parametrize("inside", ["input", "output"])
def test_history_database_cannot_be_a_move_target(setup, inside):
    source, output, _ = setup
    first = make(source)
    root = source if inside == "input" else output
    store = HistoryStore(root / "history.sqlite3")
    with pytest.raises(HistoryError, match="이력 저장 폴더"):
        run(store, source, output)
    assert first.exists() and not store.path.exists() and not output.exists()


def test_undo_does_not_restore_if_journaling_fails(setup, monkeypatch):
    source, output, store = setup
    first = make(source)
    job, _ = run(store, source, output)

    def fail(*args, **kwargs):
        raise HistoryError("journal unavailable")

    monkeypatch.setattr(store, "update_entry", fail)
    with pytest.raises(HistoryError, match="unavailable"):
        undo_job(store, job)
    assert not first.exists()
    assert (output / "nested/photo.jpg").read_bytes() == b"original bytes"
