"""Pièces jointes de la page en cours de saisie, partagées entre ses onglets."""

from collections.abc import Callable, Iterable
from pathlib import Path

from PySide6.QtCore import QMimeData, QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices

from pymphub.archive import TAILLE_MAX_VERIFICATION, Archive
from pymphub.disque import chemin_affichable
from pymphub.fichiers import EtatFichier, semble_dicom
from pymphub.page import Page, PieceJointe

MESSAGE_DICOM = (
    "« {nom} » est un fichier DICOM : il pourrait contenir des données de patient. "
    "Son import arrivera avec la pseudonymisation."
)


class FichierRefuse(ValueError):
    """Ce fichier ne peut pas entrer dans l'archive."""


def refuser_dicom(chemin: Path) -> None:
    """Refuse un DICOM : aucun fichier de patient n'entre avant la pseudonymisation."""
    if semble_dicom(chemin):
        raise FichierRefuse(MESSAGE_DICOM.format(nom=chemin.name))


def chemins_deposes(donnees: QMimeData) -> list[Path]:
    """Lit les fichiers locaux d'un glisser-déposer."""
    return [Path(url.toLocalFile()) for url in donnees.urls() if url.isLocalFile()]


def ouvrir_avec_le_systeme(chemin: Path) -> bool:
    """Ouvre un fichier avec le logiciel associé par le système."""
    return QDesktopServices.openUrl(QUrl.fromLocalFile(chemin_affichable(chemin)))


class PiecesDeLaPage(QObject):
    """La liste des pièces jointes telle qu'elle est à l'écran, pas encore enregistrée.

    Le fichier est copié dès qu'on le joint. S'il n'est finalement pas enregistré, il reste
    dans le dossier de la page sans y être déclaré, et ne gêne rien.
    """

    changees = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.archive: Archive | None = None
        self.id_page = ""
        self.pieces: tuple[PieceJointe, ...] = ()
        self._references: Callable[[], Iterable[str]] = tuple
        self._etats: dict[str, EtatFichier] = {}

    def charger(
        self, archive: Archive, page: Page, references: Callable[[], Iterable[str]]
    ) -> None:
        """Repart des pièces d'une page. `references` liste celles qu'un onglet affiche."""
        self.archive = archive
        self.id_page = page.id
        self.pieces = page.pieces_jointes
        self._references = references
        self._etats = {}

    def joindre(self, chemin: Path) -> PieceJointe:
        """Copie un fichier dans la page. Lève `ValueError` ou `OSError` s'il est refusé."""
        if not chemin.is_file():
            raise FichierRefuse(f"« {chemin.name} » n'est pas un fichier.")
        refuser_dicom(chemin)
        piece = self.archive.joindre_fichier(self.id_page, chemin)
        if piece not in self.pieces:
            self.pieces = (*self.pieces, piece)
            self.changees.emit()
        return piece

    def retirer(self, chemin: str) -> None:
        """Retire une pièce de la page. Le fichier reste : l'historique y renvoie encore."""
        if chemin in self._references():
            raise FichierRefuse(
                f"« {chemin} » est affichée dans un onglet de notes : l'y retirer d'abord."
            )
        self.pieces = tuple(p for p in self.pieces if p.chemin != chemin)
        self.changees.emit()

    def emplacement(self, chemin: str) -> Path:
        """Renvoie où se trouve une pièce jointe."""
        return self.archive.emplacement_piece_jointe(self.id_page, chemin)

    def etat(self, piece: PieceJointe) -> EtatFichier:
        """Vérifie une pièce une seule fois par ouverture de la page."""
        if piece.chemin not in self._etats:
            self._etats[piece.chemin] = self.archive.etat_piece_jointe(
                self.id_page, piece, TAILLE_MAX_VERIFICATION
            )
        return self._etats[piece.chemin]
