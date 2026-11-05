"""Export du cahier de labo en PDF, sans rien à installer : Qt écrit le document.

Le contenu vient de `medphystools.export`. Ici, on fournit les images et on produit le fichier.
La couverture et chaque page de l'archive forment chacune un document Qt, écrit puis libéré :
une grande archive ne garde pas toutes ses images en mémoire, et l'avancement est réel.
"""

import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt, QUrl
from PySide6.QtGui import (
    QAbstractTextDocumentLayout,
    QColor,
    QFontMetrics,
    QImage,
    QPageSize,
    QPainter,
    QPalette,
    QPdfWriter,
    QTextDocument,
)

from medphystools.archive import Archive
from medphystools.export import couverture_en_html, en_document, page_en_html
from medphystools.interface.onglet_graphes import creer_toile, dessiner
from medphystools.interface.photos import charger_image, rendre_photo
from medphystools.page import Page, PhotoAnnotee, ReglagesGraphe

_COTE_MAX_IMAGE = 1400
_RESOLUTION = 300
_MARGE_MM = 20.0
_MM_PAR_POUCE = 25.4
_PIXELS_PAR_POUCE = 96.0
_A4_LARGEUR_MM = 210.0
_ECART_NUMERO_POINTS = 5


def largeur_du_texte() -> float:
    """La largeur utile d'une page A4, dans l'unité des images du document."""
    utile = _A4_LARGEUR_MM - 2 * _MARGE_MM
    return utile / _MM_PAR_POUCE * _PIXELS_PAR_POUCE


class ImagesDuDocument:
    """Prépare les images du cahier, et les range dans le document Qt en cours."""

    def __init__(self, archive: Archive) -> None:
        self.archive = archive
        self.document: QTextDocument | None = None
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


class _EcrivainPdf:
    """Écrit des documents Qt les uns à la suite des autres, avec un numéro de page continu.

    Les réglages sont ceux de `QTextDocument.print_` : mêmes marges, même mise en page.
    """

    def __init__(self, chemin: Path, titre: str) -> None:
        self.ecrivain = QPdfWriter(str(chemin))
        self.ecrivain.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
        self.ecrivain.setResolution(_RESOLUTION)
        self.ecrivain.setPageMargins(QMarginsF(0.0, 0.0, 0.0, 0.0))
        self.ecrivain.setTitle(titre)
        self.ecrivain.setCreator("MedPhysNotebook")
        self.peintre = QPainter(self.ecrivain)
        self.pages = 0

    @property
    def pret(self) -> bool:
        return self.peintre.isActive()

    def ecrire(self, document: QTextDocument) -> None:
        """Met en page un document, et ajoute ses feuilles au fichier."""
        disposition = document.documentLayout()
        disposition.setPaintDevice(self.ecrivain)
        marge = round(_MARGE_MM / _MM_PAR_POUCE * _PIXELS_PAR_POUCE)
        cadre = document.rootFrame().frameFormat()
        cadre.setLeftMargin(marge)
        cadre.setRightMargin(marge)
        cadre.setTopMargin(marge)
        cadre.setBottomMargin(marge)
        document.rootFrame().setFrameFormat(cadre)
        largeur, hauteur = self.ecrivain.width(), self.ecrivain.height()
        document.setPageSize(QSizeF(largeur, hauteur))
        # Le papier reste blanc et le texte noir, quel que soit le thème de l'ordinateur.
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Text, QColor(Qt.GlobalColor.black))
        contexte = QAbstractTextDocumentLayout.PaintContext()
        contexte.palette = palette
        echelle = _RESOLUTION / _PIXELS_PAR_POUCE
        for rang in range(document.pageCount()):
            if self.pages:
                self.ecrivain.newPage()
            self.pages += 1
            vue = QRectF(0, rang * hauteur, largeur, hauteur)
            contexte.clip = vue
            self.peintre.save()
            self.peintre.translate(0, -vue.top())
            self.peintre.setClipRect(vue)
            disposition.draw(self.peintre, contexte)
            self.peintre.restore()
            self._numeroter(document, largeur - marge * echelle, hauteur)

    def _numeroter(
        self, document: QTextDocument, droite: float, hauteur: float
    ) -> None:
        """Écrit le numéro de page en bas à droite, dans la marge."""
        police = document.defaultFont()
        metriques = QFontMetrics(police, self.ecrivain)
        marge = _MARGE_MM / _MM_PAR_POUCE * _RESOLUTION
        ecart = _ECART_NUMERO_POINTS * _RESOLUTION / 72.0
        numero = str(self.pages)
        self.peintre.setFont(police)
        self.peintre.setPen(QColor(Qt.GlobalColor.black))
        self.peintre.drawText(
            round(droite - metriques.horizontalAdvance(numero)),
            round(hauteur - marge + metriques.ascent() + ecart),
            numero,
        )

    def terminer(self) -> None:
        """Ferme le fichier : il ne peut être renommé qu'après."""
        self.peintre.end()
        del self.peintre
        del self.ecrivain


def exporter_pdf(
    archive: Archive,
    identifiants: Sequence[str],
    chemin: Path,
    k: float = 2.0,
    avancer: Callable[[int], bool] | None = None,
) -> bool:
    """Écrit le cahier en PDF. Renvoie False si l'utilisateur a interrompu l'export.

    `avancer` reçoit le nombre de pages de l'archive écrites, et peut interrompre l'export en
    renvoyant False. Le fichier passe par un nom temporaire : un export interrompu ne laisse
    pas de PDF à moitié écrit. Lève `OSError` si le fichier ne peut pas être écrit.
    """
    temporaire = chemin.with_name(f".{chemin.name}.tmp")
    sortie = _EcrivainPdf(temporaire, f"Cahier de labo : {archive.configuration.nom}")
    images = ImagesDuDocument(archive)
    termine = False
    try:
        if not sortie.pret:
            raise OSError(f"Écriture impossible dans « {chemin} ».")
        document = QTextDocument()
        document.setHtml(en_document(couverture_en_html(archive, identifiants, k)))
        sortie.ecrire(document)
        for rang, identifiant in enumerate(identifiants, start=1):
            document = QTextDocument()
            images.document = document
            corps = page_en_html(archive, identifiant, images, k, saut_de_page=False)
            document.setHtml(en_document(corps))
            sortie.ecrire(document)
            if avancer is not None and not avancer(rang):
                return False
        termine = True
    finally:
        sortie.terminer()
        if not termine:
            temporaire.unlink(missing_ok=True)
    if not temporaire.is_file() or temporaire.stat().st_size == 0:
        raise OSError(f"Écriture impossible dans « {chemin} ».")
    temporaire.replace(chemin)
    return True
