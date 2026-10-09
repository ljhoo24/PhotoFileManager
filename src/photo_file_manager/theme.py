"""Shared visual language and compact, accessible desktop controls."""

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QFrame, QLabel, QPushButton, QTableView

STYLE = """
QMainWindow, QWidget#appRoot { background: #F3F5F9; }
QWidget { color: #202B40; }
QLabel { background: transparent; border: none; }
QLabel[role="title"] { font-size: 23px; font-weight: 700; color: #142139; }
QLabel[role="heading"] { font-size: 15px; font-weight: 600; }
QLabel[role="muted"] { color: #6B778C; font-size: 12px; }
QLabel[role="caption"] { color: #526077; font-size: 12px; font-weight: 600; }
QLabel#brand { background: #2563EB; color: white; border-radius: 12px;
    font-size: 18px; font-weight: 700; }
QFrame[role="card"] { background: white; border: 1px solid #E1E6EF; border-radius: 14px; }
QFrame[role="filter"] { background: #F7F9FC; border: none; border-radius: 10px; }
QLabel#optionsHint { background: #EFF5FF; color: #48638D; border-radius: 10px;
    padding: 12px; font-size: 12px; }
QLineEdit { background: #F8FAFC; border: 1px solid #DCE3ED; border-radius: 8px;
    padding: 0 11px; color: #24334B; selection-background-color: #DCEAFF; }
QLineEdit:hover { border-color: #B8C8DF; }
QLineEdit:focus { border: 1px solid #2563EB; background: white; }
QLineEdit:disabled { color: #8793A5; background: #F1F4F8; }
QPushButton { background: white; color: #33435C; border: 1px solid #D9E1ED;
    border-radius: 8px; padding: 0 14px; font-weight: 600; }
QPushButton:hover { background: #F0F5FF; border-color: #A8BFE8; color: #2158C4; }
QPushButton:pressed { background: #E2ECFF; }
QPushButton:focus { border: 2px solid #729EF2; }
QPushButton[role="primary"] { background: #2563EB; color: white; border-color: #2563EB; }
QPushButton[role="primary"]:hover { background: #1D54D2; border-color: #1D54D2; }
QPushButton[role="primary"]:pressed { background: #1846B4; }
QPushButton[role="stop"] { color: #B44848; }
QPushButton:disabled, QPushButton[role="primary"]:disabled, QPushButton[role="stop"]:disabled {
    background: #EEF1F6; color: #98A3B5; border: 1px solid #E5E9F0; }
QTabWidget::pane { border: none; background: white; }
QTabBar::tab { background: transparent; color: #758096; border: none;
    border-bottom: 2px solid transparent; padding: 13px 18px; font-weight: 600; }
QTabBar::tab:selected { color: #2563EB; border-bottom-color: #2563EB; }
QTabBar::tab:hover { color: #2563EB; background: #F7FAFF; }
QTableView { background: white; alternate-background-color: #FAFBFD;
    border: none; outline: none; selection-background-color: #E8F0FF;
    selection-color: #194EAE; gridline-color: #EDF0F5; }
QTableView::item { padding: 8px; border-bottom: 1px solid #F0F2F6; }
QTableView::item:selected { background: #E8F0FF; color: #194EAE; }
QTableView::item:hover { background: #F0F5FF; }
QHeaderView::section { background: #F7F9FC; color: #738198; border: none;
    border-bottom: 1px solid #E8EDF5; padding: 11px 8px; font-size: 12px; font-weight: 600; }
QTableCornerButton::section { background: #F7F9FC; border: none; }
QPlainTextEdit { background: #FAFBFD; color: #42536C; border: none;
    padding: 12px; selection-background-color: #DCEAFF; }
QProgressBar { background: #E9EEF6; border: none; border-radius: 3px; }
QProgressBar::chunk { background: #3B76F0; border-radius: 3px; }
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #CBD5E3; border-radius: 3px; min-height: 24px; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #CBD5E3; border-radius: 3px; min-width: 24px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QSplitter::handle { background: #EFF2F7; height: 5px; }
QToolTip { background: #23334D; color: white; border: none; padding: 8px; }
"""


def apply_theme(window):
    font = QFont()
    font.setFamilies(["Segoe UI", "Malgun Gothic", "Apple SD Gothic Neo", "sans-serif"])
    font.setPixelSize(13)
    window.setFont(font)
    window.setStyleSheet(STYLE)


def label(text, role=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if role:
        widget.setProperty("role", role)
    return widget


def card(role="card"):
    widget = QFrame()
    widget.setProperty("role", role)
    return widget


def button(text, role=None):
    widget = QPushButton(text)
    widget.setFixedHeight(40)
    widget.setMinimumWidth(128)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if role:
        widget.setProperty("role", role)
    return widget


class Toggle(QCheckBox):
    """Keep checkbox keyboard/accessibility behavior with a painted switch."""

    def __init__(self, text):
        super().__init__(text)
        self.setAccessibleName(text)
        self.setFixedSize(44, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def sizeHint(self):
        return QSize(44, 28)

    def hitButton(self, position):
        return self.rect().contains(position)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        active = self.isChecked()
        color = "#2563EB" if active else "#B5C0D0"
        if not self.isEnabled():
            color = "#B5C9ED" if active else "#D8DFE9"
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(2, 4, 40, 20), 10, 10)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(24 if active else 5, 7, 14, 14))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#729EF2"), 1.5))
            painter.drawRoundedRect(QRectF(0.8, 1, 42, 26), 12, 12)


class EmptyTableView(QTableView):
    empty_title = "표시할 파일이 없습니다"
    empty_description = "폴더를 선택하고 미리보기를 실행해 주세요."

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.model() is not None and self.model().rowCount() == 0:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            bounds = self.viewport().rect()
            if bounds.height() < 95:
                painter.setPen(QColor("#8390A5"))
                painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, self.empty_title)
                return
            center = bounds.center().y()
            font = self.font()
            font.setPixelSize(15)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QColor("#53647E"))
            painter.drawText(
                bounds.adjusted(12, center - 30, -12, -bounds.height() + center + 3),
                Qt.AlignmentFlag.AlignCenter,
                self.empty_title,
            )
            font.setPixelSize(12)
            font.setBold(False)
            painter.setFont(font)
            painter.setPen(QColor("#8390A5"))
            painter.drawText(
                bounds.adjusted(12, center + 5, -12, -bounds.height() + center + 48),
                Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap,
                self.empty_description,
            )
