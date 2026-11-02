"""Export du cahier de labo en PDF, sans rien à installer : Qt écrit le document.

Le contenu vient de `pymphub.export`. Ici, on fournit les images et on produit le fichier.
"""

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QMarginsF, QUrl
from PySide6.QtGui import QImage, QPageSize, QPdfWriter, QTextDocument

from pymphub.archive import Archive
from pymphub.export import cahier_en_html
from pymphub.interface.onglet_graphes import creer_toile, dessiner
from pymphub.interface.photos import charger_image, rendre_photo
from pymphub.page import Page, PhotoAnnotee, ReglagesGraphe

_COTE_MAX_IMAGE = 1400
_RESOLUTION = 300
# Qt ajoute lui-même 2 cm de marge autour du texte, et y place le numéro de page.
_MARGE_DE_QT_MM = 20.0
_MM_PAR_POUCE = 25.4
_PIXELS_PAR_POUCE = 96.0
_A4_LARGEUR_MM = 210.0


def largeur_du_texte() -> float:
    """La largeur utile d'une page A4, dans l'unité des images du document."""
    utile = _A4_LARGEUR_MM - 2 * _MARGE_DE_QT_MM
    return utile / _MM_PAR_POUCE * _PIXELS_PAR_POUCE


class ImagesDuDocument:
    """Prépare les images du cahier, et les range dans le document Qt."""

    def __init__(self, archive: Archive, document: QTextDocument) -> None:
        self.archive = archive
        self.document = document
        self.toile = None
        self._rang = 0

    def _ranger(self, image: QImage) -> str | None:
        if image.isNull():
            return None
        self._rang += 1
        adresse = f"image_{self._rang}"
        self.document.addResource(
            QTextDocument.ResourceType.ImageResource, QUrl(adresse), image
        )
        return adresse

    def photo(self, page: Page, photo: PhotoAnnotee) -> str | None:
        """La photo avec ses annotations. Le fichier d'origine n'est jamais écrit."""
        chemin = self.archive.emplacement_piece_jointe(page.id, photo.chemin)
        image = charger_image(chemin, _COTE_MAX_IMAGE)
        if image.isNull():
            return None
        return self._ranger(rendre_photo(image, photo.annotations).toImage())

    def graphe(self, page: Page, reglages: ReglagesGraphe, k: float) -> str | None:
        """Dessine un graphe hors écran. S'il est impossible, le cahier continue sans lui."""
        if self.toile is None:
            self.toile = creer_toile(interactif=False)
        dessiner(self.toile, self.archive, page, reglages, k)
        if self.toile.donnees is None:
            return None
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / "graphe.png"
            self.toile.enregistrer_image(chemin)
            return self._ranger(QImage(str(chemin)))


def exporter_pdf(
    archive: Archive,
    identifiants: Sequence[str],
    chemin: Path,
    k: float = 2.0,
    avancer: Callable[[int], bool] | None = None,
) -> bool:
    """Écrit le cahier en PDF. Renvoie False si l'utilisateur a interrompu l'export.

    Le fichier passe par un nom temporaire : un export interrompu ne laisse pas de PDF à
    moitié écrit. Lève `OSError` si le fichier ne peut pas être écrit.
    """
    document = QTextDocument()
    html = cahier_en_html(
        archive, identifiants, ImagesDuDocument(archive, document), k, avancer=avancer
    )
    if html is None:
        return False
    document.setHtml(html)
    temporaire = chemin.with_name(f".{chemin.name}.tmp")
    ecrivain = QPdfWriter(str(temporaire))
    ecrivain.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    ecrivain.setResolution(_RESOLUTION)
    ecrivain.setPageMargins(QMarginsF(0.0, 0.0, 0.0, 0.0))
    ecrivain.setTitle(f"Cahier de labo : {archive.configuration.nom}")
    ecrivain.setCreator("pymphub")
    document.print_(ecrivain)
    del ecrivain
    if not temporaire.is_file() or temporaire.stat().st_size == 0:
        raise OSError(f"Écriture impossible dans « {chemin} ».")
    temporaire.replace(chemin)
    return True
