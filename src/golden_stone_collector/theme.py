from pathlib import Path
from PySide6.QtGui import QIcon


def icon():
    return QIcon(str(Path(__file__).parent / "assets/feather.svg"))


STYLESHEET = """
QWidget { background: #FFFCF0; color: #100F0F; font-size: 14px; }
QMainWindow { background: #FFFCF0; }
QLabel#title { font-size: 25px; font-weight: 600; }
QLabel#muted { color: #6F6E69; }
QLabel#status { color: #24837B; font-size: 20px; font-weight: 600; }
QPushButton { background: #F2F0E5; border: 1px solid #DAD8CE; border-radius: 6px; padding: 9px 15px; }
QPushButton:hover { background: #E6E4D9; }
QPushButton:pressed { background: #DAD8CE; }
QPushButton:disabled { color: #B7B5AC; }
QPushButton#primary { background: #24837B; color: #FFFCF0; border: 1px solid #24837B; }
QPushButton#primary:disabled { background: #B7B5AC; border-color: #B7B5AC; }
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit, QKeySequenceEdit, QListWidget, QTextBrowser {
 background: #FFFFFF; border: 1px solid #DAD8CE; border-radius: 5px; padding: 6px; selection-background-color: #DAE8EE;
}
QTabWidget::pane { border: 1px solid #DAD8CE; border-radius: 5px; }
QTabBar::tab { padding: 10px 16px; background: #F2F0E5; }
QTabBar::tab:selected { background: #FFFCF0; color: #24837B; }
QGroupBox { border: 1px solid #DAD8CE; border-radius: 5px; margin-top: 12px; padding-top: 15px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; }
QListWidget::item { padding: 10px; border-bottom: 1px solid #F2F0E5; }
QListWidget::item:selected { background: #DAE8EE; color: #100F0F; }
QMenu { border: 1px solid #DAD8CE; padding: 5px; }
QMenu::item { padding: 7px 25px; }
QMenu::item:selected { background: #E6E4D9; }
QToolTip { background: #100F0F; color: #FFFCF0; padding: 6px; }
"""
