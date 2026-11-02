"""Clic droit « Afficher en grand » : relire en entier un texte trop long pour sa case."""

from collections.abc import Callable

from PySide6.QtCore import QObject, QPoint, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

LIBELLE_ACTION = "Afficher en grand"

Titre = str | Callable[[], str]


class DialogueTexte(QDialog):
    """Affiche un texte en grand, en lecture seule."""

    def __init__(self, titre: str, texte: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(titre)
        self.resize(820, 560)
        self.texte = QPlainTextEdit(texte)
        self.texte.setReadOnly(True)
        self.texte.setStyleSheet("font-size: 13pt;")
        boutons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        boutons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        boutons.rejected.connect(self.reject)
        disposition = QVBoxLayout(self)
        disposition.addWidget(self.texte)
        disposition.addWidget(boutons)


class AffichageEnGrand(QObject):
    """Ajoute l'action « Afficher en grand » au menu contextuel d'un widget.

    Le menu habituel du widget (copier, coller…) reste disponible en dessous.
    """

    def __init__(
        self, widget: QWidget, titre: Titre, lire_texte: Callable[[QPoint], str]
    ) -> None:
        super().__init__(widget)
        self.widget = widget
        self.titre = titre
        self.lire_texte = lire_texte
        widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        widget.customContextMenuRequested.connect(self._montrer_menu)

    def _menu_habituel(self) -> QMenu:
        if isinstance(self.widget, (QLineEdit, QPlainTextEdit)):
            return self.widget.createStandardContextMenu()
        menu = QMenu(self.widget)
        if isinstance(self.widget, QLabel):
            menu.addAction("Copier", self._copier_etiquette)
        return menu

    def _copier_etiquette(self) -> None:
        etiquette = self.widget
        texte = etiquette.selectedText() or self.lire_texte(QPoint())
        QGuiApplication.clipboard().setText(texte)

    def menu(self, position: QPoint = QPoint()) -> QMenu:  # noqa: B008 - QPoint immuable ici
        """Construit le menu, avec l'action grisée s'il n'y a rien à afficher."""
        menu = self._menu_habituel()
        texte = self.lire_texte(position)
        action = QAction(LIBELLE_ACTION, menu)
        action.setEnabled(bool(texte.strip()))
        action.triggered.connect(lambda: self.afficher(texte))
        premiere = menu.actions()[0] if menu.actions() else None
        menu.insertAction(premiere, action)
        if premiere is not None:
            menu.insertSeparator(premiere)
        return menu

    def afficher(self, texte: str) -> None:
        """Ouvre le texte en grand."""
        titre = self.titre() if callable(self.titre) else self.titre
        dialogue = DialogueTexte(titre, texte, self.widget.window())
        dialogue.exec()
        dialogue.deleteLater()

    def _montrer_menu(self, position: QPoint) -> None:
        # Dans une zone défilante, Qt donne la position dans sa partie visible.
        repere = (
            self.widget.viewport()
            if isinstance(self.widget, QAbstractScrollArea)
            else self.widget
        )
        menu = self.menu(position)
        menu.exec(repere.mapToGlobal(position))
        menu.deleteLater()


def afficher_en_grand(
    widget: QLineEdit | QPlainTextEdit | QLabel,
    titre: Titre,
    lire_texte: Callable[[], str] | None = None,
) -> AffichageEnGrand:
    """Installe l'action sur un champ de texte ou une étiquette.

    `lire_texte` est nécessaire quand le texte affiché diffère du texte à relire, par
    exemple sur une étiquette qui barre les anciennes valeurs.
    """
    if lire_texte is not None:
        lire = lire_texte
    elif isinstance(widget, QPlainTextEdit):
        lire = widget.toPlainText
    else:
        lire = widget.text
    return AffichageEnGrand(widget, titre, lambda _position: lire())


def afficher_en_grand_colonne(
    table: QTableWidget, colonne: int, titre: Titre
) -> AffichageEnGrand:
    """Installe l'action sur une colonne de texte d'une table."""

    def lire(position: QPoint) -> str:
        element = table.itemAt(position)
        return element.text() if element and element.column() == colonne else ""

    return AffichageEnGrand(table, titre, lire)
