"""Ramasse-miettes de Python limité au fil principal.

Sans cela, l'application plante au hasard. Le ramasse-miettes se déclenche dans n'importe
quel fil, dès que ce fil crée assez d'objets. S'il démarre dans un fil de travail, il
parcourt les objets Qt pendant que le fil principal les utilise, ce qui provoque une
erreur de segmentation (pile observée : `gc_collect_main` → `libshiboken`).

Le déclenchement automatique est donc coupé, et un minuteur du fil principal fait le même
travail à intervalle régulier. Le comptage de références normal de Python n'est pas
touché : seuls les cycles d'objets attendent ce passage pour être libérés.
"""

import gc

from PySide6.QtCore import QCoreApplication, QObject, QTimer

_INTERVALLE_MS = 1000


class _RamasseMiettes(QObject):
    """Déclenche le ramasse-miettes depuis le fil principal, selon les seuils de Python."""

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._minuteur = QTimer(self)
        self._minuteur.setInterval(_INTERVALLE_MS)
        self._minuteur.timeout.connect(self.collecter)
        self._minuteur.start()

    def collecter(self) -> None:
        """Lance la génération la plus ancienne dont le seuil est dépassé."""
        compteurs = gc.get_count()
        seuils = gc.get_threshold()
        for generation in (2, 1, 0):
            if compteurs[generation] > seuils[generation]:
                gc.collect(generation)
                return


def assurer_ramasse_miettes_principal() -> None:
    """Réserve le ramasse-miettes au fil principal. Peut être appelée plusieurs fois.

    À appeler depuis le fil principal, avant de lancer le moindre fil de travail.
    """
    application = QCoreApplication.instance()
    if application is None:
        raise RuntimeError("L'application Qt doit exister avant le ramasse-miettes.")
    gc.disable()
    if application.findChild(_RamasseMiettes) is None:
        _RamasseMiettes(application)
