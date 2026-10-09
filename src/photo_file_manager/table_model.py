"""Lightweight read-only tables, without creating a widget per file."""

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor


class TextTableModel(QAbstractTableModel):
    def __init__(self, headers, parent=None):
        super().__init__(parent)
        self.headers = headers
        self.rows = []

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def update_cell(self, row, column, value):
        values = list(self.rows[row])
        values[column] = value
        self.rows[row] = tuple(values)
        index = self.index(row, column)
        self.dataChanged.emit(index, index)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if (
            index.isValid()
            and role == Qt.ItemDataRole.ForegroundRole
            and self.headers[index.column()] == "상태"
        ):
            value = str(self.rows[index.row()][index.column()])
            if any(word in value for word in ("실패", "오류")):
                return QColor("#B83E4B")
            if any(word in value for word in ("충돌", "건너뜀", "중지", "중단")):
                return QColor("#986313")
            if any(word in value for word in ("완료", "복원", "이동")):
                return QColor("#267161") if "예정" not in value else QColor("#3566B1")
        if index.isValid() and role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return str(self.rows[index.row()][index.column()])

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return self.headers[section]
