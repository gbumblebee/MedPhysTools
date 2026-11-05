"""De vrais clics droits pour les tests : le menu s'ouvre, se lit, puis se ferme."""

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QTimer
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QMenu, QWidget


class Menus:
    """Laisse le vrai menu s'ouvrir, le lit, lance l'action voulue puis le ferme.

    Un minuteur est nécessaire : ouvrir un menu bloque jusqu'à ce qu'il se referme.
    """

    def __init__(self) -> None:
        self.ouverts: list[list[tuple[str, bool]]] = []
        self.action_a_lancer: str | None = None
        self.pendant_le_menu: Callable[[], None] | None = None
        self._minuteur = QTimer()
        self._minuteur.setInterval(10)
        self._minuteur.timeout.connect(self._lire_le_menu_ouvert)
        self._minuteur.start()

    def _lire_le_menu_ouvert(self) -> None:
        menu = QApplication.activePopupWidget()
        if not isinstance(menu, QMenu):
            return
        if self.pendant_le_menu is not None:
            # Ce qui arrive menu ouvert ; il n'est lu qu'au passage suivant du minuteur.
            action, self.pendant_le_menu = self.pendant_le_menu, None
            action()
            return
        actions = [a for a in menu.actions() if not a.isSeparator()]
        self.ouverts.append([(a.text(), a.isEnabled()) for a in actions])
        menu.close()
        for action in actions:
            if action.text() == self.action_a_lancer:
                action.trigger()

    def arreter(self) -> None:
        self._minuteur.stop()


def clic_droit(cible: QWidget, position: QPoint) -> None:
    """Envoie l'événement que Qt envoie pour un clic droit : il suit le vrai chemin."""
    evenement = QContextMenuEvent(
        QContextMenuEvent.Reason.Mouse, position, cible.mapToGlobal(position)
    )
    assert evenement.type() == QEvent.Type.ContextMenu
    QApplication.sendEvent(cible, evenement)
