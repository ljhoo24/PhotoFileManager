"""Persistent history browser with per-file results."""

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from photo_file_manager.history import HistoryError
from photo_file_manager.table_model import TextTableModel

STATE_LABELS = {
    "running": "실행 중 / 비정상 종료 가능",
    "completed": "완료",
    "cancelled": "중지",
    "interrupted": "오류로 중단",
    "undoing": "복원 중 / 비정상 종료 가능",
    "undo_partial": "일부 복원 / 재시도 가능",
    "undone": "복원 완료",
    "pending": "미처리",
    "moving": "이동 도중 중단 가능",
    "moved": "이동 완료",
    "skipped": "건너뜀",
    "failed": "실패",
}


def make_table(model):
    table = QTableView()
    table.setModel(model)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    table.verticalHeader().setVisible(False)
    table.setColumnWidth(0, 220)
    return table


class HistoryPanel(QWidget):
    undo_requested = Signal(str)
    error = Signal(str)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store, self.jobs, self.busy = store, [], False
        layout = QVBoxLayout(self)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("이력 새로고침")
        self.refresh_button.clicked.connect(self.refresh)
        self.undo_button = QPushButton("선택 작업 되돌리기")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self.request_undo)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.undo_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.info = QLabel("작업을 선택하면 파일별 결과를 볼 수 있습니다.")
        self.info.setWordWrap(True)
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.info)
        self.model = TextTableModel(
            ["시각", "상태", "이동", "건너뜀", "실패", "복원", "입력 → 출력"], self
        )
        self.table = make_table(self.model)
        self.table.setColumnWidth(0, 170)
        self.table.setColumnWidth(1, 170)
        for column in range(2, 6):
            self.table.setColumnWidth(column, 60)
        self.table.selectionModel().selectionChanged.connect(self.show_selection)
        self.details_model = TextTableModel(
            ["입력 내 원래 경로", "출력 내 이동 경로", "상태", "실행 결과", "복원 결과"], self
        )
        self.details = make_table(self.details_model)
        self.details.setColumnWidth(1, 300)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([90, 240])
        layout.addWidget(splitter)

    def selected_job(self):
        rows = self.table.selectionModel().selectedRows()
        return self.jobs[rows[0].row()] if rows and rows[0].row() < len(self.jobs) else None

    def set_busy(self, busy):
        self.busy = busy
        self.refresh_button.setEnabled(not busy)
        self.table.setEnabled(not busy)
        selected = self.selected_job()
        self.undo_button.setEnabled(not busy and bool(selected and selected["undoable"]))

    def refresh(self):
        selected = self.selected_job()
        selected_id = selected["id"] if selected else None
        try:
            self.jobs = self.store.jobs()
        except HistoryError as error:
            self.error.emit(str(error))
            return
        self.model.set_rows(
            [
                (
                    datetime.fromisoformat(job["created_at"])
                    .astimezone()
                    .strftime("%Y-%m-%d %H:%M:%S"),
                    STATE_LABELS.get(job["state"], job["state"]),
                    job["moved"],
                    job["skipped"],
                    job["failed"],
                    job["undone"],
                    f"{job['source_root']} → {job['output_root']}",
                )
                for job in self.jobs
            ]
        )
        self.details_model.set_rows([])
        self.undo_button.setEnabled(False)
        if self.jobs:
            row = next((i for i, job in enumerate(self.jobs) if job["id"] == selected_id), 0)
            self.table.selectRow(row)

    def show_selection(self, *_):
        selected = self.selected_job()
        self.set_busy(self.busy)
        if not selected:
            self.details_model.set_rows([])
            return
        try:
            entries = self.store.entries(selected["id"])
        except HistoryError as error:
            self.error.emit(str(error))
            return

        def relative(path, root):
            if not path:
                return ""
            try:
                return str(Path(path).relative_to(root))
            except ValueError:
                return path

        self.details_model.set_rows(
            [
                (
                    relative(row["source"], selected["source_root"]),
                    relative(row["destination"], selected["output_root"]),
                    STATE_LABELS.get(row["state"], row["state"]),
                    row["message"],
                    row["undo_message"],
                )
                for row in entries
            ]
        )
        options = json.loads(selected["options"])
        filters = " · ".join(
            f"{label} {'ON' if options.get(key) else 'OFF'}"
            for key, label in (
                ("by_date", "촬영 날짜"),
                ("by_extension", "확장자"),
                ("by_camera", "카메라"),
            )
        )
        self.info.setText(
            f"입력: {selected['source_root']}\n출력: {selected['output_root']}\n"
            f"{filters}\n"
            f"복원 확인 대상 {selected['undoable']}개 · 내용 변경 / 원래 경로 충돌 시 보존"
            + (f"\n작업 오류: {selected['error']}" if selected["error"] else "")
        )

    def request_undo(self):
        selected = self.selected_job()
        if not self.busy and selected and selected["undoable"]:
            self.undo_requested.emit(selected["id"])
