"""PySide6 desktop interface."""

import argparse
import json
import sys
from pathlib import Path
from threading import Event

from PySide6.QtCore import QThread, QTimer, Signal, Slot, qVersion
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
    QVBoxLayout,
    QWidget,
)

from photo_file_manager.engine import (
    FileResult,
    RunSummary,
    build_plan,
    execute_plan,
    validate_folders,
)
from photo_file_manager.rules import ClassificationOptions


class MoveWorker(QThread):
    result_ready = Signal(object)
    progress = Signal(int, int)
    completed = Signal(object)
    failed = Signal(str)
    phase = Signal(str)
    notice = Signal(str)

    def __init__(self, source: str, output: str, options: ClassificationOptions, parent=None):
        super().__init__(parent)
        self.source = source
        self.output = output
        self.options = options
        self.cancel_event = Event()

    def run(self) -> None:
        try:
            self.phase.emit("입력 파일과 EXIF 정보를 읽고 있습니다…")
            plan = build_plan(
                self.source,
                self.output,
                classification=self.options,
                is_cancelled=self.cancel_event.is_set,
            )
            for note in plan.notes:
                self.notice.emit(note)
            if self.cancel_event.is_set():
                self.completed.emit(RunSummary(0, 0, 0, True, len(plan.items)))
                return
            self.phase.emit(f"{len(plan.items):,}개 파일 이동 중…")
            summary = execute_plan(
                plan,
                on_result=self.result_ready.emit,
                on_progress=self.progress.emit,
                is_cancelled=self.cancel_event.is_set,
            )
            self.completed.emit(summary)
        except Exception as error:
            self.failed.emit(str(error))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.worker: MoveWorker | None = None
        self.setWindowTitle("PhotoFileManager")
        self.resize(880, 700)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("PhotoFileManager")
        title.setStyleSheet("font-size: 24px; font-weight: 600;")
        layout.addWidget(title)
        description = QLabel("입력 폴더의 파일을 읽고 출력 폴더로 이동합니다.")
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
            group_layout.addWidget(checkbox)
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
        self.run_button = QPushButton("Run · 이동")
        self.run_button.setMinimumHeight(36)
        self.run_button.clicked.connect(self.start_run)
        self.cancel_button = QPushButton("중지")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_run)
        actions.addWidget(self.run_button)
        actions.addWidget(self.cancel_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        self.status_label = QLabel("폴더를 지정한 뒤 Run을 눌러 주세요.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setPlaceholderText("파일별 이동 결과가 여기에 표시됩니다. (최근 5,000줄)")
        layout.addWidget(self.log, 1)

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
            self.run_button,
            self.date_checkbox,
            self.extension_checkbox,
            self.camera_checkbox,
        ):
            widget.setEnabled(not running)
        self.cancel_button.setEnabled(running)

    @Slot()
    def start_run(self) -> None:
        if self.worker is not None:
            return
        source = self.source_edit.text().strip()
        output = self.output_edit.text().strip()
        try:
            validate_folders(source, output)
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "폴더 확인", str(error))
            return

        self.log.clear()
        self._set_running(True)
        self.progress_bar.setRange(0, 0)
        self.status_label.setText("입력 폴더를 읽고 있습니다…")
        worker = MoveWorker(source, output, self.classification_options(), self)
        self.worker = worker
        worker.phase.connect(self.show_phase)
        worker.notice.connect(self.log.appendPlainText)
        worker.result_ready.connect(self.show_result)
        worker.progress.connect(self.show_progress)
        worker.completed.connect(self.show_summary)
        worker.failed.connect(self.show_error)
        worker.finished.connect(self.worker_finished)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    @Slot()
    def cancel_run(self) -> None:
        if self.worker:
            self.worker.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status_label.setText("중지 요청됨 · 현재 파일 처리가 끝나면 중지합니다.")

    @Slot(str)
    def show_phase(self, message: str) -> None:
        if self.worker is not None and not self.worker.cancel_event.is_set():
            self.status_label.setText(message)

    @Slot(object)
    def show_result(self, result: FileResult) -> None:
        labels = {"moved": "이동", "skipped": "건너뜀", "failed": "실패"}
        line = f"[{labels[result.status]}] {result.source}"
        if result.destination is not None:
            line += f" → {result.destination}"
        if result.message:
            line += f" · {result.message}"
        self.log.appendPlainText(line)

    @Slot(int, int)
    def show_progress(self, done: int, total: int) -> None:
        self.progress_bar.setRange(0, max(total, 1))
        self.progress_bar.setValue(done if total else 1)

    @Slot(object)
    def show_summary(self, summary: RunSummary) -> None:
        state = "중지됨" if summary.cancelled else "완료"
        message = (
            f"{state} · 이동 {summary.moved:,} · 건너뜀 {summary.skipped:,}"
            f" · 실패 {summary.failed:,}"
        )
        if summary.cancelled:
            message += f" · 미처리 {summary.pending:,} (검색 중 중지하면 검색된 항목 기준)"
        self.status_label.setText(message)
        self.log.appendPlainText(message)
        if self.progress_bar.maximum() == 0:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(0)

    @Slot(str)
    def show_error(self, message: str) -> None:
        self.status_label.setText(f"작업 오류 · {message}")
        self.log.appendPlainText(f"[오류] {message}")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

    @Slot()
    def worker_finished(self) -> None:
        self.worker = None
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
    window = MainWindow()
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
