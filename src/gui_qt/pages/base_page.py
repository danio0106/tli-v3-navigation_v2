from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout
from src.gui_qt.theme import COLORS


class BasePage(QFrame):
    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("PageTitle")
        layout.addWidget(title_lbl)

        body_lbl = QLabel(subtitle)
        body_lbl.setObjectName("PageBody")
        body_lbl.setWordWrap(True)
        layout.addWidget(body_lbl)

        self._status = QLabel("Status: waiting")
        self._status.setObjectName("PageBody")
        layout.addWidget(self._status)

        layout.addStretch(1)

    def set_status(self, text: str):
        self._status.setText(text)
