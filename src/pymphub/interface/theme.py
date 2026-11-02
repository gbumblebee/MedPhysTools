"""Thème de l'application : sobre, moderne et lisible.

Toutes les couleurs sont définies ici, pour pouvoir les ajuster en un seul endroit.
"""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

FOND = "#f6f7f9"
SURFACE = "#ffffff"
BORDURE = "#d9dde3"
TEXTE = "#1f2933"
TEXTE_DISCRET = "#5f6b7a"
ACCENT = "#1f6f8b"
ACCENT_FONCE = "#185a71"
DESACTIVE = "#a7b0bb"
ALERTE = "#a4372a"
ANNOTATION = "#d7263d"
ANNOTATION_CHOISIE = "#e08e00"
# Ordre fixe des séries d'un graphe : une série garde sa couleur, quel que soit le filtre.
SERIES = (
    "#2a78d6",
    "#eb6834",
    "#1baf7a",
    "#eda100",
    "#e87ba4",
    "#008300",
    "#6250d6",
    "#e34948",
)

FEUILLE_DE_STYLE = f"""
QWidget {{ font-size: 10pt; }}
QMainWindow, QDialog {{ background: {FOND}; }}
QStatusBar {{ color: {TEXTE_DISCRET}; }}
QLabel#titre {{ font-size: 20pt; font-weight: 600; }}
QLabel#sous_titre, QLabel#discret {{ color: {TEXTE_DISCRET}; }}
QLabel#titre_page {{ font-size: 13pt; font-weight: 600; }}
QLabel#section {{ font-weight: 600; margin-top: 10px; }}
QLabel#alerte {{ color: {ALERTE}; font-weight: 600; }}
QHeaderView::section {{
    padding: 5px 7px;
    background: {FOND};
    border: none;
    border-bottom: 1px solid {BORDURE};
    font-weight: 600;
}}
QTableView, QTableWidget {{
    border: 1px solid {BORDURE};
    gridline-color: {BORDURE};
}}
QComboBox {{
    padding: 4px 7px;
    background: {SURFACE};
    border: 1px solid {BORDURE};
    border-radius: 4px;
}}
QTabWidget::pane {{ border: 1px solid {BORDURE}; background: {SURFACE}; }}
QTabBar::tab {{
    padding: 8px 20px;
    border: 1px solid transparent;
    color: {TEXTE_DISCRET};
}}
QTabBar::tab:selected {{
    color: {TEXTE};
    font-weight: 600;
    border-bottom: 2px solid {ACCENT};
}}
QPushButton {{
    padding: 7px 16px;
    background: {SURFACE};
    border: 1px solid {BORDURE};
    border-radius: 4px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: {DESACTIVE}; }}
QPushButton#outil:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: white;
}}
QPushButton#principal {{
    background: {ACCENT};
    border-color: {ACCENT};
    color: white;
    font-weight: 600;
}}
QPushButton#principal:hover {{ background: {ACCENT_FONCE}; }}
QPushButton#principal:disabled {{ background: {BORDURE}; border-color: {BORDURE}; }}
QLineEdit, QDateEdit {{
    padding: 5px 7px;
    background: {SURFACE};
    border: 1px solid {BORDURE};
    border-radius: 4px;
}}
QLineEdit:focus, QDateEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit[invalide="true"] {{ border: 1px solid {ALERTE}; }}
"""


def appliquer_theme(application: QApplication) -> None:
    """Applique le thème, avec une apparence identique sous Windows et Linux."""
    application.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(FOND))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXTE))
    palette.setColor(QPalette.ColorRole.Base, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(FOND))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXTE))
    palette.setColor(QPalette.ColorRole.Button, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXTE))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(SURFACE))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXTE))
    application.setPalette(palette)
    application.setStyleSheet(FEUILLE_DE_STYLE)
