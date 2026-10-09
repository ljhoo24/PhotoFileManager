"""PySide6 desktop interface."""

import argparse
import json
import sys
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QTimer, Slot, qVersion
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from photo_file_manager.engine import FileResult, MovePlan, validate_folders
from photo_file_manager.history import HistoryStore
from photo_file_manager.history_panel import HistoryPanel, make_table
from photo_file_manager.rules import ClassificationOptions
from photo_file_manager.table_model import TextTableModel
from photo_file_manager.workers import move_worker, preview_worker, undo_worker


class MainWindow(QMainWindow):
    def __init__(self, history_path: Path | None = None):
        super().__init__()
        self.worker = None
        self.plan: MovePlan | None = None
        self.mode = ""
        self.preview_indexes = {}
        default_history = (
            Path(
                QStandardPaths.writableLocation(
                    QStandardPaths.StandardLocation.AppLocalDataLocation
                )
            )
            / "history.sqlite3"
        )
        self.store = HistoryStore(history_path or default_history)
        self.setWindowTitle("PhotoFileManager")
        self.resize(1120, 840)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)

        title = QLabel("PhotoFileManager")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        description = QLabel(
            "미리보기에서 경로를 확인한 뒤 이동합니다. 작업 이력에서 되돌릴 수 있습니다."
        )
        layout.addWidget(description)

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("입력 폴더 경로")
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("출력 폴더 경로 (없는 폴더는 자동 생성)")
        self.source_button = QPushButton("찾아보기…")
        self.output_button = QPushButton("찾아보기…")
        self.source_button.clicked.connect(lambda: self._browse(self.source_edit))
        self.output_button.clicked.connect(lambda: self._browse(self.output_edit))

        form = QFormLayout()
        form.addRow("입력 폴더", self._folder_row(self.source_edit, self.source_button))
        form.addRow("출력 폴더", self._folder_row(self.output_edit, self.output_button))
        layout.addLayout(form)

        group = QGroupBox("분류 옵션 · 각각 ON/OFF")
        group_layout = QVBoxLayout(group)
        filter_row = QHBoxLayout()
        self.date_checkbox = QCheckBox("촬영 날짜별 분류")
        self.extension_checkbox = QCheckBox("확장자별 분류")
        self.camera_checkbox = QCheckBox("카메라 기종별 대분류")
        self.date_checkbox.setToolTip("EXIF 촬영일 기준. 날짜가 없으면 미분류로 이동합니다.")
        self.extension_checkbox.setToolTip("JPG/JPEG는 기본 폴더, RAW 계열은 RAW로 통합합니다.")
        self.camera_checkbox.setToolTip(
            "모든 입력 이미지의 기종이 확인되고 같으면 기종 폴더를 생략합니다."
        )
        for checkbox in (self.date_checkbox, self.extension_checkbox, self.camera_checkbox):
            checkbox.setChecked(True)
            checkbox.toggled.connect(self.update_options_hint)
            filter_row.addWidget(checkbox)
        filter_row.addStretch()
        group_layout.addLayout(filter_row)
        self.options_hint = QLabel()
        self.options_hint.setWordWrap(True)
        group_layout.addWidget(self.options_hint)
        layout.addWidget(group)
        self.update_options_hint()

        options = QLabel(
            "하위 폴더 포함 · 같은 이름은 건너뜀 · 이동한 파일은 입력 폴더에서 사라집니다."
        )
        options.setWordWrap(True)
        layout.addWidget(options)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("1. 미리보기")
        self.preview_button.clicked.connect(self.start_preview)
        self.run_button = QPushButton("2. Run · 이동")
        self.run_button.setEnabled(False)
        self.run_button.setMinimumHeight(36)
        self.run_button.clicked.connect(self.start_run)
        self.cancel_button = QPushButton("중지")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        actions.addWidget(self.preview_button)
        actions.addWidget(self.run_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("폴더를 지정한 뒤 미리보기를 만들어 주세요.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("파일별 이동 결과가 여기에 표시됩니다. (최근 5,000줄)")
        self.tabs = QTabWidget()
        self.preview_model = TextTableModel(
            ["입력 내 경로", "촬영일", "기종", "출력 내 경로", "상태", "안내"], self
        )
        self.preview_table = make_table(self.preview_model)
        self.preview_table.setColumnWidth(3, 280)
        self.tabs.addTab(self.preview_table, "미리보기")
        self.history_panel = HistoryPanel(self.store)
        self.history_panel.undo_requested.connect(self.start_undo)
        self.history_panel.error.connect(self.show_error)
        self.tabs.addTab(self.history_panel, "작업 이력 · 되돌리기")
        self.tabs.addTab(self.log, "실행 로그")
        layout.addWidget(self.tabs, 1)
        self.source_edit.textChanged.connect(self.invalidate_preview)
        self.output_edit.textChanged.connect(self.invalidate_preview)
        for checkbox in (self.date_checkbox, self.extension_checkbox, self.camera_checkbox):
            checkbox.toggled.connect(self.invalidate_preview)
        self.history_panel.refresh()

    def classification_options(self) -> ClassificationOptions:
        return ClassificationOptions(
            by_date=self.date_checkbox.isChecked(),
            by_extension=self.extension_checkbox.isChecked(),
            by_camera=self.camera_checkbox.isChecked(),
        )

    @Slot()
    def update_options_hint(self) -> None:
        # During construction, toggled signals are connected before this label exists.
        if not hasattr(self, "options_hint"):
            return
        options = self.classification_options()
        parts = ["출력"]
        if options.by_camera:
            parts.append("카메라 기종")
        if options.by_date:
            parts.append("2026년 10월 09일" + (" [RAW 등]" if options.by_extension else ""))
        elif options.by_extension:
            parts.append("[RAW 등 · JPG는 폴더 생략]")
        parts.append("파일")
        hint = " / ".join(parts)
        if not (options.by_date or options.by_extension or options.by_camera):
            hint = "분류 OFF · 입력 폴더 구조를 그대로 유지합니다."
        elif options.by_camera:
            hint += "\n기종이 모두 확인되고 같으면 기종 폴더 생략 · 정보가 없으면 기종 미상"
        if options.by_date:
            hint += "\n촬영일이 없으면 미분류"
            if options.by_extension:
                hint += " · JPG/JPEG는 확장자 표시 생략"
        self.options_hint.setText(hint)

    @staticmethod
    def _folder_row(edit: QLineEdit, button: QPushButton) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(edit)
        layout.addWidget(button)
        return row

    def _browse(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "폴더 선택", edit.text() or str(Path.home())
        )
        if folder:
            edit.setText(folder)

    def _set_running(self, running: bool) -> None:
        for widget in (
            self.source_edit,
            self.output_edit,
            self.source_button,
            self.output_button,
            self.preview_button,
            self.date_checkbox,
            self.extension_checkbox,
            self.camera_checkbox,
        ):
            widget.setEnabled(not running)
        self.run_button.setEnabled(not running and self.plan is not None and bool(self.plan.items))
        self.cancel_button.setEnabled(running)
        self.history_panel.set_busy(running)

    @Slot()
    def invalidate_preview(self) -> None:
        if self.plan is not None:
            self.status_label.setText("설정이 바뀌었습니다. 미리보기를 다시 만들어 주세요.")
        self.plan = None
        self.run_button.setEnabled(False)
        self.preview_model.set_rows([])
        self.preview_indexes = {}

    def _launch(self, worker, mode, message):
        self.worker, self.mode = worker, mode
        self._set_running(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText(message)
        worker.result_ready.connect(self.show_result)
        worker.progress.connect(self.show_progress)
        worker.completed.connect(self.show_completed)
        worker.failed.connect(self.show_error)
        worker.finished.connect(self.worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @Slot()
    def start_preview(self) -> None:
        if self.worker is not None:
            return
        source, output = self.source_edit.text().strip(), self.output_edit.text().strip()
        try:
            validate_folders(source, output)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "폴더 확인", str(error))
            return
        self.invalidate_preview()
        self.log.clear()
        self.tabs.setCurrentIndex(0)
        self._launch(
            preview_worker(source, output, self.classification_options(), self),
            "preview",
            "입력 파일과 EXIF를 읽어 이동 계획을 만들고 있습니다…",
        )

    @Slot()
    def start_run(self) -> None:
        if self.worker is not None or self.plan is None:
            return
        plan, self.plan = self.plan, None
        self._launch(
            move_worker(self.store, plan, self.classification_options(), self),
            "move",
            "파일 내용 확인 · 작업 이력 저장 · 이동 중…",
        )

    @Slot(str)
    def start_undo(self, job_id) -> None:
        if self.worker is not None:
            return
        self.invalidate_preview()
        self.log.clear()
        self._launch(
            undo_worker(self.store, job_id, self), "undo", "파일 확인 후 원래 위치로 복원 중…"
        )

    @Slot()
    def cancel_run(self) -> None:
        if self.worker:
            self.worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("중지 요청됨 · 현재 파일 처리가 끝나면 중지합니다.")

    @Slot(object)
    def show_result(self, result: FileResult) -> None:
        labels = {
            "moved": "복원" if self.mode == "undo" else "이동",
            "skipped": "건너뜀",
            "failed": "실패",
        }
        line = f"[{labels[result.status]}] {result.source}"
        if result.destination is not None:
            line += f" → {result.destination}"
        if result.message:
            line += f" · {result.message}"
        self.log.appendPlainText(line)
        row = self.preview_indexes.get(result.source)
        if self.mode == "move" and row is not None:
            self.preview_model.update_cell(row, 4, labels[result.status])
            self.preview_model.update_cell(row, 5, result.message)

    @Slot(int, int)
    def show_progress(self, done: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done if total else 1)

    @Slot(object)
    def show_completed(self, result) -> None:
        if self.mode == "preview":
            if result is None:
                self.status_label.setText("미리보기 중지됨 · 파일은 이동하지 않았습니다.")
                self.progress_bar.setRange(0, 1)
                return
            self.plan = result
            options = self.classification_options()
            metadata_queried = options.by_date or options.by_camera
            self.preview_indexes = {item.source: i for i, item in enumerate(result.items)}
            self.preview_model.set_rows(
                [
                    (
                        str(item.source.relative_to(result.source_root)),
                        item.metadata.captured_on.isoformat()
                        if item.metadata.captured_on
                        else ("없음" if metadata_queried else "조회 안 함"),
                        item.metadata.camera_model
                        or ("기종 미상" if metadata_queried else "조회 안 함"),
                        str(item.destination.relative_to(result.output_root)),
                        "충돌 · 건너뜀" if item.conflict else "이동 예정",
                        item.conflict or item.note,
                    )
                    for item in result.items
                ]
                + [
                    (
                        str(item.source.relative_to(result.source_root)),
                        "",
                        "",
                        str(item.destination or ""),
                        "제외",
                        item.message,
                    )
                    for item in result.skipped
                ]
            )
            conflicts = sum(bool(item.conflict) for item in result.items)
            undated = sum(item.metadata.captured_on is None for item in result.items)
            folders = set()
            for item in result.items:
                if item.conflict:
                    continue
                parent = item.destination.parent
                while parent.is_relative_to(result.output_root):
                    if not parent.exists():
                        folders.add(parent)
                    if parent == result.output_root:
                        break
                    parent = parent.parent
            message = (
                f"미리보기 · 이동 예정 {len(result.items) - conflicts:,} · 충돌 {conflicts:,}"
                f" · 제외 {len(result.skipped):,} · 생성할 폴더 {len(folders):,}"
            )
            if self.classification_options().by_date:
                message += f" · 촬영일 없음 {undated:,}"
            self.status_label.setText(message)
            for note in result.notes:
                self.log.appendPlainText(note)
            self.show_progress(1, 1)
            return
        if self.mode == "move":
            _, summary = result
            state = "중지됨" if summary.cancelled else "완료"
            message = (
                f"{state} · 이동 {summary.moved:,} · 건너뜀 {summary.skipped:,}"
                f" · 실패 {summary.failed:,} · 미처리 {summary.pending:,}"
            )
        else:
            message = (
                f"되돌리기 {'중지됨' if result.cancelled else '완료'} · 복원 {result.restored:,}"
                f" · 충돌/실패 {result.conflicts:,} · 남은 대상 {result.remaining:,}"
            )
        self.status_label.setText(message)
        self.log.appendPlainText(message)

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.status_label.setText(f"작업 오류 · {message}")
        self.log.appendPlainText(f"[오류] {message}")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

    @Slot()
    def worker_finished(self) -> None:
        self.worker = None
        self.history_panel.refresh()
        self._set_running(False)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.worker is not None:
            self.cancel_run()
            event.ignore()
            self.status_label.setText("중지 중입니다. 처리가 끝난 뒤 창을 닫아 주세요.")
            return
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(description="PhotoFileManager")
    parser.add_argument("--self-test-report", type=Path, help=argparse.SUPPRESS)
    args, qt_args = parser.parse_known_args()
    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName("PhotoFileManager")
    window = MainWindow(
        history_path=(args.self_test_report.parent / "history.sqlite3")
        if args.self_test_report
        else None
    )
    if args.self_test_report:
        # Use the native platform plugin and a real (hidden) native window.
        # Report only after the Qt event loop starts successfully.
        def report_startup() -> None:
            try:
                native_window = bool(window.winId())
                window.grab()
                report = {
                    "platform": app.platformName(),
                    "qt_version": qVersion(),
                    "native_window_created": native_window,
                    "window_title": window.windowTitle(),
                }
                args.self_test_report.write_text(
                    json.dumps(report, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                app.exit(1)
            else:
                app.exit(0)

        QTimer.singleShot(0, report_startup)
    else:
        window.show()
    return app.exec()
