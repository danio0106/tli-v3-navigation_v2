"""Qt theme constants aligned with existing dark GitHub-like palette."""

COLORS = {
    "bg_dark": "#0D1117",
    "bg_medium": "#161B22",
    "bg_light": "#21262D",
    "bg_card": "#1C2128",
    "bg_elevated": "#242B36",
    "border": "#30363D",
    "border_strong": "#3D4757",
    "text_primary": "#E6EDF3",
    "text_secondary": "#8B949E",
    "text_muted": "#6E7681",
    "accent_blue": "#58A6FF",
    "accent_green": "#3FB950",
    "accent_red": "#F85149",
    "accent_orange": "#D29922",
    "accent_purple": "#BC8CFF",
    "accent_cyan": "#39D2C0",
}

WINDOW_STYLESHEET = f"""
QMainWindow {{
    background: {COLORS['bg_dark']};
}}
QFrame#Sidebar {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {COLORS['bg_elevated']},
        stop:1 {COLORS['bg_medium']});
    border-right: 1px solid {COLORS['border']};
}}
QFrame#Content {{
    background: {COLORS['bg_dark']};
}}
QLabel {{
    color: {COLORS['text_primary']};
}}
QPushButton {{
    background: {COLORS['bg_light']};
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    padding: 7px 12px;
    text-align: left;
    font-weight: 600;
}}
QPushButton:hover {{
    border: 1px solid {COLORS['border_strong']};
    background: {COLORS['bg_elevated']};
}}
QPushButton:checked {{
    background: #25364A;
    color: #BBD9FF;
    border: 1px solid #4E6E95;
}}
QPushButton#NavButton {{
    background: transparent;
    color: {COLORS['text_secondary']};
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 8px 10px;
}}
QPushButton#NavButton:hover {{
    background: {COLORS['bg_light']};
    border: 1px solid {COLORS['border']};
}}
QPushButton#NavButton:checked {{
    background: #25364A;
    color: #BBD9FF;
    border: 1px solid #4E6E95;
}}
QPushButton[variant="primary"] {{
    background: #2F81F7;
    color: #F2F8FF;
    border: 1px solid #4A92F8;
}}
QPushButton[variant="primary"]:hover {{
    background: #4A92F8;
}}
QPushButton[variant="success"] {{
    background: #238636;
    color: #F3FFF6;
    border: 1px solid #2EA043;
}}
QPushButton[variant="success"]:hover {{
    background: #2EA043;
}}
QPushButton[variant="warning"] {{
    background: #9A6700;
    color: #FFF8E8;
    border: 1px solid #BF8700;
}}
QPushButton[variant="warning"]:hover {{
    background: #BF8700;
}}
QPushButton[variant="danger"] {{
    background: #B62324;
    color: #FFF2F2;
    border: 1px solid #DA3633;
}}
QPushButton[variant="danger"]:hover {{
    background: #DA3633;
}}
QPushButton[variant="info"] {{
    background: #1F6FEB;
    color: #EEF6FF;
    border: 1px solid #388BFD;
}}
QPushButton[variant="info"]:hover {{
    background: #388BFD;
}}
QPushButton[variant="muted"] {{
    background: {COLORS['bg_light']};
    color: {COLORS['text_secondary']};
    border: 1px solid {COLORS['border']};
}}
QLabel#Title {{
    color: {COLORS['accent_cyan']};
    font-size: 19px;
    font-weight: 800;
}}
QLabel#Subtitle {{
    color: {COLORS['text_muted']};
    font-size: 11px;
}}
QLabel#PageTitle {{
    color: {COLORS['text_primary']};
    font-size: 15px;
    font-weight: 700;
}}
QLabel#PageBody {{
    color: {COLORS['text_secondary']};
    font-size: 12px;
}}
QFrame#Card {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 #212833,
        stop:1 {COLORS['bg_card']});
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
}}
QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {{
    background: #11161D;
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    selection-background-color: #264F78;
    selection-color: {COLORS['text_primary']};
}}
QLineEdit, QComboBox {{
    padding: 5px 8px;
}}
QComboBox {{
    padding-right: 24px;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 20px;
    border-left: 1px solid {COLORS['border']};
    background: #18202A;
    border-top-right-radius: 8px;
    border-bottom-right-radius: 8px;
}}
QComboBox::down-arrow {{
    width: 0px;
    height: 0px;
    border-left: 5px solid transparent;
    border-right: 5px solid transparent;
    border-top: 6px solid {COLORS['text_secondary']};
    margin-top: 2px;
}}
QComboBox QAbstractItemView {{
    background: #11161D;
    color: {COLORS['text_primary']};
    border: 1px solid {COLORS['border_strong']};
    selection-background-color: #264F78;
    selection-color: {COLORS['text_primary']};
    outline: 0;
}}
QComboBox QAbstractItemView::item {{
    min-height: 22px;
    padding: 4px 8px;
    color: {COLORS['text_primary']};
    background: #11161D;
}}
QComboBox QAbstractItemView::item:selected {{
    background: #264F78;
    color: {COLORS['text_primary']};
}}
QComboBox QAbstractItemView::item:hover {{
    background: #1B2A3A;
    color: {COLORS['text_primary']};
}}
QComboBox QAbstractItemView::item:disabled {{
    color: {COLORS['text_muted']};
}}
QPlainTextEdit, QTableWidget {{
    alternate-background-color: #0E141B;
}}
QHeaderView::section {{
    background: #18202A;
    color: {COLORS['text_secondary']};
    border: 0px;
    border-bottom: 1px solid {COLORS['border']};
    padding: 6px;
    font-weight: 700;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0px;
}}
QScrollBar::handle:vertical {{
    background: #303A46;
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical:hover {{
    background: #3C4A5C;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QCheckBox {{
    color: {COLORS['text_secondary']};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1px solid {COLORS['border']};
    background: #11161D;
}}
QCheckBox::indicator:checked {{
    background: {COLORS['accent_blue']};
    border: 1px solid #79B8FF;
}}
"""


def set_button_variant(button, variant: str):
    """Apply semantic button color variants via Qt dynamic property."""
    button.setProperty("variant", variant)
    style = button.style()
    if style is not None:
        style.unpolish(button)
        style.polish(button)
    button.update()
