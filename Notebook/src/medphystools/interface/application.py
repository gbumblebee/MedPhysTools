"""Point d'entrée de l'application : commande `medphysnotebook`."""

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from medphystools.interface.fenetre import FenetrePrincipale
from medphystools.interface.ramasse_miettes import assurer_ramasse_miettes_principal
from medphystools.interface.theme import appliquer_theme

DOSSIER_RESSOURCES = Path(__file__).parent / "ressources"
LOGO = DOSSIER_RESSOURCES / "logo.png"


def donner_son_identite(application: QApplication) -> None:
    """Donne au logiciel son logo, dans sa fenêtre et dans la liste des applications.

    Sans son propre identifiant, Windows le rangerait sous l'icône de Python.
    """
    application.setApplicationName("MedPhysNotebook")
    application.setDesktopFileName("medphysnotebook")
    application.setWindowIcon(QIcon(str(LOGO)))
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("medphysnotebook")


def main() -> int:
    """Lance l'application, sur l'archive passée en argument ou sur la dernière ouverte."""
    application = QApplication(sys.argv)
    donner_son_identite(application)
    appliquer_theme(application)
    assurer_ramasse_miettes_principal()

    fenetre = FenetrePrincipale()
    arguments = application.arguments()[1:]
    if arguments:
        fenetre.ouvrir_archive(arguments[0])
    else:
        fenetre.ouvrir_derniere_archive()
    fenetre.showMaximized()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
