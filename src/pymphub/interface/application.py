"""Point d'entrée de l'application : commande `pymphub`."""

import sys

from PySide6.QtWidgets import QApplication

from pymphub.interface.fenetre import FenetrePrincipale
from pymphub.interface.theme import appliquer_theme


def main() -> int:
    """Lance l'application. Un dossier passé en argument s'ouvre directement."""
    application = QApplication(sys.argv)
    application.setApplicationName("pymphub")
    appliquer_theme(application)

    fenetre = FenetrePrincipale()
    arguments = application.arguments()[1:]
    if arguments:
        fenetre.ouvrir_archive(arguments[0])
    fenetre.show()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
