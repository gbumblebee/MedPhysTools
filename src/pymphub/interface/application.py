"""Point d'entrée de l'application : commande `pymphub`."""

import sys

from PySide6.QtWidgets import QApplication

from pymphub.interface.fenetre import FenetrePrincipale
from pymphub.interface.ramasse_miettes import assurer_ramasse_miettes_principal
from pymphub.interface.theme import appliquer_theme


def main() -> int:
    """Lance l'application, sur l'archive passée en argument ou sur la dernière ouverte."""
    application = QApplication(sys.argv)
    application.setApplicationName("pymphub")
    appliquer_theme(application)
    assurer_ramasse_miettes_principal()

    fenetre = FenetrePrincipale()
    arguments = application.arguments()[1:]
    if arguments:
        fenetre.ouvrir_archive(arguments[0])
    else:
        fenetre.ouvrir_derniere_archive()
    fenetre.show()
    return application.exec()


if __name__ == "__main__":
    sys.exit(main())
