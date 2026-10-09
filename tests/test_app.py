import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from photo_file_manager.app import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def wait_for_worker(app, window):
    deadline = time.monotonic() + 10
    while window.worker is not None and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    if window.worker is not None:
        window.worker.cancel_event.set()
        window.worker.wait(10000)
        app.processEvents()
        pytest.fail("Worker did not complete within 10 seconds")
    app.processEvents()


def test_run_button_moves_file_and_restores_controls(app, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    original = source / "사진.jpg"
    original.write_bytes(b"image")
    output = tmp_path / "output"
    window = MainWindow(history_path=tmp_path / "history.sqlite3")
    for checkbox in (window.date_checkbox, window.extension_checkbox, window.camera_checkbox):
        checkbox.setChecked(False)
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(output))
    window.preview_button.click()
    wait_for_worker(app, window)
    window.run_button.click()
    assert not window.run_button.isEnabled()
    wait_for_worker(app, window)
    assert (output / original.name).read_bytes() == b"image"
    assert not original.exists()
    assert window.preview_button.isEnabled()
    assert not window.run_button.isEnabled()
    assert not window.cancel_button.isEnabled()
    assert "이동 1" in window.status_label.text()
    assert "[이동]" in window.log.toPlainText()
    window.close()


def test_invalid_folder_never_starts_worker(app, tmp_path, monkeypatch):
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[2]))
    window = MainWindow(history_path=tmp_path / "history.sqlite3")
    for checkbox in (window.date_checkbox, window.extension_checkbox, window.camera_checkbox):
        checkbox.setChecked(False)
    window.source_edit.setText(str(tmp_path))
    window.output_edit.setText(str(tmp_path))
    window.preview_button.click()
    wait_for_worker(app, window)
    window.run_button.click()
    assert messages
    assert window.worker is None and window.preview_button.isEnabled()
    window.close()


def test_second_run_reports_conflict_and_preserves_input(app, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    original = source / "a.jpg"
    original.write_bytes(b"first")
    output = tmp_path / "output"
    window = MainWindow(history_path=tmp_path / "history.sqlite3")
    for checkbox in (window.date_checkbox, window.extension_checkbox, window.camera_checkbox):
        checkbox.setChecked(False)
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(output))
    window.preview_button.click()
    wait_for_worker(app, window)
    window.run_button.click()
    wait_for_worker(app, window)
    original.write_bytes(b"second")
    window.preview_button.click()
    wait_for_worker(app, window)
    window.run_button.click()
    wait_for_worker(app, window)
    assert original.read_bytes() == b"second"
    assert (output / "a.jpg").read_bytes() == b"first"
    assert "건너뜀 1" in window.status_label.text()
    window.close()


def test_ui_default_filters_and_background_worker(app, tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "a.jpg")
    photo_factory(source / "b.arw")
    window = MainWindow(history_path=tmp_path / "history.sqlite3")
    assert all(
        checkbox.isChecked()
        for checkbox in (window.date_checkbox, window.extension_checkbox, window.camera_checkbox)
    )
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(output))
    window.preview_button.click()
    wait_for_worker(app, window)
    window.run_button.click()
    assert not window.date_checkbox.isEnabled()
    wait_for_worker(app, window)
    assert (output / "2026년 10월 09일/a.jpg").exists()
    assert (output / "2026년 10월 09일 RAW/b.arw").exists()
    assert "카메라 분류 생략" in window.log.toPlainText()
    assert window.date_checkbox.isEnabled()
    window.close()


def test_ui_camera_off_combines_models(app, tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "a.jpg", model="Sony")
    photo_factory(source / "b.jpg", model="Canon")
    window = MainWindow(history_path=tmp_path / "history.sqlite3")
    window.camera_checkbox.setChecked(False)
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(output))
    window.preview_button.click()
    wait_for_worker(app, window)
    window.run_button.click()
    wait_for_worker(app, window)
    assert (output / "2026년 10월 09일/a.jpg").exists()
    assert (output / "2026년 10월 09일/b.jpg").exists()
    assert len(list(output.iterdir())) == 1
    window.close()


def test_preview_does_not_move_and_settings_invalidate_it(app, tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    original = photo_factory(source / "a.jpg")
    window = MainWindow(history_path=tmp_path / "history.sqlite3")
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(output))
    assert not window.run_button.isEnabled()
    window.preview_button.click()
    wait_for_worker(app, window)
    assert original.exists() and not output.exists()
    assert window.run_button.isEnabled()
    assert window.preview_model.rows[0][1] == "2026-10-09"
    assert not window.store.path.exists()
    window.date_checkbox.setChecked(False)
    assert window.plan is None and not window.run_button.isEnabled()
    assert window.preview_model.rowCount() == 0
    window.close()


def test_ui_history_survives_reopen_and_undo_restores(app, tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    original = photo_factory(source / "trip/a.raf")
    database = tmp_path / "history.sqlite3"
    window = MainWindow(history_path=database)
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(output))
    window.preview_button.click()
    wait_for_worker(app, window)
    window.run_button.click()
    wait_for_worker(app, window)
    assert not original.exists()
    window.close()
    reopened = MainWindow(history_path=database)
    assert len(reopened.history_panel.jobs) == 1
    assert reopened.history_panel.undo_button.isEnabled()
    reopened.history_panel.undo_button.click()
    wait_for_worker(app, reopened)
    assert original.exists()
    assert reopened.history_panel.jobs[0]["state"] == "undone"
    assert not reopened.history_panel.undo_button.isEnabled()
    assert "복원 1" in reopened.status_label.text()
    reopened.close()


def test_ui_preview_marks_duplicate_destination(app, tmp_path, photo_factory):
    source, output = tmp_path / "input", tmp_path / "output"
    photo_factory(source / "one/a.jpg")
    photo_factory(source / "two/a.jpg")
    window = MainWindow(history_path=tmp_path / "history.sqlite3")
    window.source_edit.setText(str(source))
    window.output_edit.setText(str(output))
    window.preview_button.click()
    wait_for_worker(app, window)
    assert "충돌 1" in window.status_label.text()
    assert window.preview_model.rows[1][4] == "충돌 · 건너뜀"
    assert len(list(source.rglob("*.jpg"))) == 2
    window.close()
