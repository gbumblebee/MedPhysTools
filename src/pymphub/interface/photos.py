"""Photos annotées : affichage, vignettes et dialogue d'annotation.

Une photo n'est jamais écrite : ses annotations sont dans `page.json`, en coordonnées de 0 à
1. Elles se redessinent donc au bon endroit quelle que soit la taille d'affichage.
"""

import math
from collections import OrderedDict
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QImage,
    QImageReader,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pymphub.disque import chemin_affichable
from pymphub.edition import point_normalise
from pymphub.fichiers import ETATS_ALERTE
from pymphub.interface.texte_en_grand import afficher_en_grand
from pymphub.interface.theme import ANNOTATION, ANNOTATION_CHOISIE
from pymphub.page import Annotation, PhotoAnnotee

_COTE_VIGNETTE = 150
_HAUTEUR_MIN_BANDE = 110
# L'annotation n'a pas de zoom : au-delà, on décoderait des pixels jamais affichés.
_COTE_MAX_ANNOTATION = 2048
_VIGNETTES_EN_MEMOIRE = 128
_LIMITE_MEMOIRE_IMAGE_MO = 1024
_DISTANCE_MIN_PIXELS = 3.0
_LONGUEUR_POINTE = 12.0
_ANGLE_POINTE = math.radians(25)
_RAYON_POINT = 5.0
_MARGE_TEXTE = 3.0
_ECART_TEXTE = 8.0
_FORMES = (
    ("Point", "point"),
    ("Flèche", "fleche"),
    ("Rectangle", "rectangle"),
    ("Ellipse", "ellipse"),
    ("Trait libre", "trace"),
)
_LIBELLES_FORMES = {valeur: libelle for libelle, valeur in _FORMES}
_MESSAGES_ETAT = {
    "altéré": "Fichier altéré : il a changé depuis son ajout.",
    "manquant": "Fichier manquant.",
    "illisible": "Fichier illisible.",
}


def message_etat(etat: str) -> str:
    """Décrit un fichier à signaler ; rien pour un fichier intact ou non vérifié."""
    return _MESSAGES_ETAT.get(etat, "") if etat in ETATS_ALERTE else ""


def est_image(chemin: Path) -> bool:
    """Indique si Qt reconnaît le contenu de ce fichier comme une image."""
    return not QImageReader(chemin_affichable(chemin)).format().isEmpty()


def charger_image(chemin: Path, cote_max: int | None = None) -> QImage:
    """Lit une image redressée selon son orientation EXIF ; image nulle si c'est impossible.

    Avec `cote_max`, l'image est décodée en petit, ce qui évite de lire toute la photo pour
    une vignette.
    """
    QImageReader.setAllocationLimit(_LIMITE_MEMOIRE_IMAGE_MO)
    lecteur = QImageReader(chemin_affichable(chemin))
    lecteur.setAutoTransform(True)
    taille = lecteur.size()
    if cote_max is not None and taille.isValid() and max(taille.toTuple()) > cote_max:
        rapport = cote_max / max(taille.toTuple())
        lecteur.setScaledSize(
            QSize(
                max(round(taille.width() * rapport), 1),
                max(round(taille.height() * rapport), 1),
            )
        )
    return lecteur.read()


_vignettes: OrderedDict[tuple[str, int, int, int], QImage] = OrderedDict()


def charger_vignette(chemin: Path, cote: int = _COTE_VIGNETTE) -> QImage:
    """Lit une vignette une seule fois, tant que le fichier ne change pas sur le disque.

    Une page se réaffiche à chaque enregistrement : sans cela, toutes ses photos seraient
    décodées à nouveau à chaque fois.
    """
    try:
        etat = chemin.stat()
    except OSError:
        return QImage()
    cle = (str(chemin), etat.st_mtime_ns, etat.st_size, cote)
    if cle in _vignettes:
        _vignettes.move_to_end(cle)
        return _vignettes[cle]
    image = charger_image(chemin, cote)
    _vignettes[cle] = image
    while len(_vignettes) > _VIGNETTES_EN_MEMOIRE:
        _vignettes.popitem(last=False)
    return image


def zone_image(taille_image: QSize, largeur: float, hauteur: float) -> QRectF:
    """Calcule où se place une image centrée dans une zone, sans la déformer."""
    if taille_image.isEmpty() or largeur <= 0 or hauteur <= 0:
        return QRectF()
    rapport = min(largeur / taille_image.width(), hauteur / taille_image.height())
    l_image, h_image = taille_image.width() * rapport, taille_image.height() * rapport
    return QRectF((largeur - l_image) / 2, (hauteur - h_image) / 2, l_image, h_image)


def _vers_zone(point: tuple[float, float], zone: QRectF) -> QPointF:
    return QPointF(
        zone.x() + point[0] * zone.width(), zone.y() + point[1] * zone.height()
    )


def _pointe(depart: QPointF, arrivee: QPointF) -> QPolygonF:
    """Le triangle au bout d'une flèche."""
    angle = math.atan2(arrivee.y() - depart.y(), arrivee.x() - depart.x())
    return QPolygonF(
        [
            arrivee,
            *(
                arrivee
                - QPointF(
                    _LONGUEUR_POINTE * math.cos(angle + ecart),
                    _LONGUEUR_POINTE * math.sin(angle + ecart),
                )
                for ecart in (_ANGLE_POINTE, -_ANGLE_POINTE)
            ),
        ]
    )


def _tracer_forme(
    peintre: QPainter, annotation: Annotation, zone: QRectF, couleur: QColor
) -> None:
    points = [_vers_zone(p, zone) for p in annotation.points]
    premier, dernier = points[0], points[-1]
    forme = annotation.forme
    if forme == "point" or len(points) == 1:
        peintre.setBrush(couleur)
        peintre.drawEllipse(premier, _RAYON_POINT, _RAYON_POINT)
        peintre.setBrush(Qt.BrushStyle.NoBrush)
    elif forme == "rectangle":
        peintre.drawRect(QRectF(premier, dernier).normalized())
    elif forme == "ellipse":
        peintre.drawEllipse(QRectF(premier, dernier).normalized())
    elif forme == "fleche":
        peintre.drawLine(premier, dernier)
        peintre.setBrush(couleur)
        peintre.drawPolygon(_pointe(premier, dernier))
        peintre.setBrush(Qt.BrushStyle.NoBrush)
    else:
        peintre.drawPolyline(points)


def _ecrire_texte(
    peintre: QPainter, annotation: Annotation, zone: QRectF, couleur: QColor
) -> None:
    """Écrit le texte d'une annotation sur un fond clair, sans sortir de l'image."""
    mesure = QRectF(peintre.fontMetrics().boundingRect(annotation.texte))
    cadre = QRectF(0, 0, mesure.width() + 2 * _MARGE_TEXTE, mesure.height())
    ancre = _vers_zone(annotation.points[0], zone) + QPointF(
        _ECART_TEXTE, -_ECART_TEXTE
    )
    cadre.moveBottomLeft(ancre)
    # Un texte plus large que l'image reste aligné sur son bord gauche.
    cadre.moveLeft(max(min(cadre.left(), zone.right() - cadre.width()), zone.left()))
    cadre.moveTop(max(min(cadre.top(), zone.bottom() - cadre.height()), zone.top()))
    peintre.fillRect(cadre, QColor(255, 255, 255, 215))
    peintre.setPen(QPen(couleur, 1))
    peintre.drawText(cadre, Qt.AlignmentFlag.AlignCenter, annotation.texte)


def dessiner_annotations(
    peintre: QPainter,
    zone: QRectF,
    annotations: Sequence[Annotation],
    choisie: int | None = None,
) -> None:
    """Dessine des annotations sur une image placée dans cette zone.

    Un liseré blanc les garde visibles, que la photo soit claire ou sombre.
    """
    peintre.save()
    peintre.setRenderHint(QPainter.RenderHint.Antialiasing)
    for rang, annotation in enumerate(annotations):
        couleur = QColor(ANNOTATION_CHOISIE if rang == choisie else ANNOTATION)
        for teinte, epaisseur in ((QColor("white"), 4.0), (couleur, 2.0)):
            peintre.setPen(QPen(teinte, epaisseur))
            peintre.setBrush(Qt.BrushStyle.NoBrush)
            _tracer_forme(peintre, annotation, zone, teinte)
        if annotation.texte:
            _ecrire_texte(peintre, annotation, zone, couleur)
    peintre.restore()


def rendre_photo(image: QImage, annotations: Sequence[Annotation]) -> QPixmap:
    """Dessine les annotations sur une copie de l'image, pour une vignette."""
    rendu = QPixmap.fromImage(image)
    peintre = QPainter(rendu)
    dessiner_annotations(peintre, QRectF(rendu.rect()), annotations)
    peintre.end()
    return rendu


class VignettePhoto(QFrame):
    """Une photo en petit, avec ses annotations, sa légende et l'état de son fichier."""

    ouverture_demandee = Signal()
    retrait_demande = Signal()

    def __init__(
        self,
        chemin: Path,
        photo: PhotoAnnotee,
        etat: str,
        modifiable: bool,
        parent: QWidget | None = None,
        remarque: str = "",
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.photo = photo
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setFixedSize(_COTE_VIGNETTE, _COTE_VIGNETTE)
        lue = charger_vignette(chemin)
        self.lisible = not lue.isNull()
        if self.lisible:
            self.image.setPixmap(rendre_photo(lue, photo.annotations))
        else:
            self.image.setText("Aperçu impossible")
        self.image.setToolTip(photo.chemin)

        self.alerte = QLabel(" ".join(filter(None, (message_etat(etat), remarque))))
        self.alerte.setObjectName("alerte")
        self.alerte.setWordWrap(True)
        self.alerte.setFixedWidth(_COTE_VIGNETTE)
        self.alerte.setVisible(bool(self.alerte.text()))
        self.legende = QLabel(photo.legende)
        self.legende.setTextFormat(Qt.TextFormat.PlainText)
        self.legende.setWordWrap(True)
        self.legende.setVisible(bool(photo.legende))
        self.legende.setFixedWidth(_COTE_VIGNETTE)
        afficher_en_grand(self.legende, "Légende")

        self.bouton_ouvrir = QPushButton("Annoter…" if modifiable else "Voir…")
        self.bouton_ouvrir.setEnabled(self.lisible)
        self.bouton_ouvrir.clicked.connect(self.ouverture_demandee)
        ligne = QHBoxLayout()
        ligne.addWidget(self.bouton_ouvrir)
        self.bouton_retirer = QPushButton("Retirer")
        self.bouton_retirer.clicked.connect(self.retrait_demande)
        self.bouton_retirer.setVisible(modifiable)
        ligne.addWidget(self.bouton_retirer)

        disposition = QVBoxLayout(self)
        disposition.addWidget(self.image)
        disposition.addWidget(self.alerte)
        disposition.addWidget(self.legende)
        disposition.addLayout(ligne)

    def mouseDoubleClickEvent(self, _evenement: QMouseEvent) -> None:  # noqa: N802
        if self.lisible:
            self.ouverture_demandee.emit()


class BandePhotos(QScrollArea):
    """Une rangée de vignettes qui défile à l'horizontale."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # Sur un petit écran, la bande défile au lieu d'agrandir la fenêtre au-delà de l'écran.
        self.setMinimumHeight(_HAUTEUR_MIN_BANDE)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.vignettes: list[VignettePhoto] = []
        self.remplacer([])

    def sizeHint(self) -> QSize:  # noqa: N802
        """Demande la hauteur complète des vignettes quand il y a la place."""
        contenu = self.widget().sizeHint()
        return QSize(contenu.width(), contenu.height() + 2 * self.frameWidth())

    def remplacer(self, vignettes: Sequence[VignettePhoto]) -> None:
        """Affiche ces vignettes à la place des précédentes."""
        self.vignettes = list(vignettes)
        contenu = QWidget()
        disposition = QHBoxLayout(contenu)
        for vignette in self.vignettes:
            disposition.addWidget(vignette)
        disposition.addStretch()
        ancien = self.takeWidget()
        if ancien is not None:
            ancien.deleteLater()
        self.setWidget(contenu)
        self.setVisible(bool(self.vignettes))


class ToileAnnotation(QWidget):
    """Affiche une photo et permet d'y dessiner des annotations à la souris."""

    annotation_ajoutee = Signal()

    def __init__(
        self,
        image: QImage,
        annotations: Sequence[Annotation],
        modifiable: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.image = image
        self.annotations: list[Annotation] = list(annotations)
        self.modifiable = modifiable
        self.forme = "fleche"
        self.choisie: int | None = None
        self._points: list[QPointF] = []
        self.setMinimumSize(480, 360)
        if modifiable:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def zone(self) -> QRectF:
        """L'emplacement de la photo dans le widget."""
        return zone_image(self.image.size(), self.width(), self.height())

    def _normaliser(self, point: QPointF) -> tuple[float, float]:
        zone = self.zone()
        return point_normalise(
            point.x() - zone.x(), point.y() - zone.y(), zone.width(), zone.height()
        )

    def _annotation_en_cours(self, texte: str = "") -> Annotation | None:
        if not self._points:
            return None
        points = [self._normaliser(p) for p in self._points]
        if self.forme == "point":
            return Annotation(forme="point", points=points[:1], texte=texte)
        if self.forme == "trace":
            return Annotation(forme="trace", points=points, texte=texte)
        return Annotation(forme=self.forme, points=(points[0], points[-1]), texte=texte)

    def mousePressEvent(self, evenement: QMouseEvent) -> None:  # noqa: N802
        if not self.modifiable or evenement.button() != Qt.MouseButton.LeftButton:
            return
        if self.zone().contains(evenement.position()):
            self._points = [evenement.position()]
            self.update()

    def mouseMoveEvent(self, evenement: QMouseEvent) -> None:  # noqa: N802
        if not self._points:
            return
        position = evenement.position()
        if self.forme == "trace":
            ecart = position - self._points[-1]
            if math.hypot(ecart.x(), ecart.y()) >= _DISTANCE_MIN_PIXELS:
                self._points.append(position)
        elif self.forme != "point":
            self._points = [self._points[0], position]
        self.update()

    def mouseReleaseEvent(self, evenement: QMouseEvent) -> None:  # noqa: N802
        if not self._points or evenement.button() != Qt.MouseButton.LeftButton:
            return
        if self.forme not in ("point", "trace"):
            self._points = [self._points[0], evenement.position()]
        depart, arrivee = self._points[0], self._points[-1]
        assez_grande = (
            math.hypot(arrivee.x() - depart.x(), arrivee.y() - depart.y())
            >= _DISTANCE_MIN_PIXELS
            or len(self._points) > 2
        )
        annotation = self._annotation_en_cours()
        self._points = []
        self.update()
        # Un simple clic ne dessine ni flèche ni rectangle : ils seraient invisibles.
        if annotation is not None and (self.forme == "point" or assez_grande):
            self.annotations.append(annotation)
            self.annotation_ajoutee.emit()

    def paintEvent(self, _evenement: QPaintEvent) -> None:  # noqa: N802
        peintre = QPainter(self)
        zone = self.zone()
        peintre.drawImage(zone, self.image)
        dessiner_annotations(peintre, zone, self.annotations, self.choisie)
        en_cours = self._annotation_en_cours()
        if en_cours is not None:
            dessiner_annotations(peintre, zone, [en_cours])
        peintre.end()


class DialogueAnnotation(QDialog):
    """Annote une photo, ou la montre en grand si la page ne se modifie pas ici."""

    def __init__(
        self,
        chemin: Path,
        photo: PhotoAnnotee,
        etat: str,
        modifiable: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(photo.chemin.removeprefix("pieces_jointes/"))
        self.resize(1040, 680)
        self.chemin_photo = photo.chemin
        self.toile = ToileAnnotation(
            charger_image(chemin, _COTE_MAX_ANNOTATION), photo.annotations, modifiable
        )
        self.toile.annotation_ajoutee.connect(self._annotation_ajoutee)
        self._mise_a_jour = False

        self.groupe_formes = QButtonGroup(self)
        ligne_formes = QHBoxLayout()
        self.boutons_formes: dict[str, QPushButton] = {}
        for libelle, valeur in _FORMES:
            bouton = QPushButton(libelle)
            bouton.setCheckable(True)
            bouton.setObjectName("outil")
            bouton.setStyleSheet("padding: 7px 6px;")
            bouton.clicked.connect(lambda _c=False, v=valeur: self._choisir_forme(v))
            self.groupe_formes.addButton(bouton)
            ligne_formes.addWidget(bouton)
            self.boutons_formes[valeur] = bouton
        self.boutons_formes[self.toile.forme].setChecked(True)

        self.liste = QListWidget()
        self.liste.currentRowChanged.connect(self._annotation_choisie)
        self.champ_texte = QLineEdit()
        self.champ_texte.setPlaceholderText("Texte de l'annotation choisie")
        self.champ_texte.textEdited.connect(self._texte_modifie)
        afficher_en_grand(self.champ_texte, "Texte de l'annotation")
        self.bouton_retirer = QPushButton("Retirer l'annotation")
        self.bouton_retirer.clicked.connect(self.retirer_annotation)
        self.champ_legende = QLineEdit(photo.legende)
        self.champ_legende.setPlaceholderText("Légende de la photo")
        afficher_en_grand(self.champ_legende, "Légende")

        formulaire = QFormLayout()
        formulaire.addRow("Texte", self.champ_texte)
        formulaire.addRow("Légende", self.champ_legende)
        self.indication = QLabel("Choisissez une forme, puis dessinez sur la photo.")
        self.indication.setObjectName("discret")
        self.indication.setVisible(modifiable)
        droite = QVBoxLayout()
        droite.addLayout(ligne_formes)
        droite.addWidget(self.indication)
        droite.addWidget(self.liste, stretch=1)
        droite.addLayout(formulaire)
        droite.addWidget(self.bouton_retirer)
        conteneur_droite = QWidget()
        conteneur_droite.setLayout(droite)
        conteneur_droite.setFixedWidth(400)

        self.alerte = QLabel(message_etat(etat))
        self.alerte.setObjectName("alerte")
        self.alerte.setVisible(bool(self.alerte.text()))

        centre = QHBoxLayout()
        centre.addWidget(self.toile, stretch=1)
        centre.addWidget(conteneur_droite)

        if modifiable:
            boutons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel
            )
            boutons.button(QDialogButtonBox.StandardButton.Ok).setText("Appliquer")
            boutons.button(QDialogButtonBox.StandardButton.Ok).setObjectName(
                "principal"
            )
            boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        else:
            boutons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            boutons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
            for bouton in self.boutons_formes.values():
                bouton.setVisible(False)
            self.bouton_retirer.setVisible(False)
            self.champ_texte.setReadOnly(True)
            self.champ_legende.setReadOnly(True)
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)

        disposition = QVBoxLayout(self)
        disposition.addWidget(self.alerte)
        disposition.addLayout(centre, stretch=1)
        disposition.addWidget(boutons)
        self._remplir_liste()

    def _choisir_forme(self, forme: str) -> None:
        self.toile.forme = forme

    def _remplir_liste(self, choisie: int | None = None) -> None:
        self._mise_a_jour = True
        self.liste.clear()
        for rang, annotation in enumerate(self.toile.annotations, start=1):
            libelle = _LIBELLES_FORMES.get(annotation.forme, annotation.forme)
            suite = f" : {annotation.texte}" if annotation.texte else ""
            self.liste.addItem(f"{rang}. {libelle}{suite}")
        self._mise_a_jour = False
        self.liste.setCurrentRow(-1 if choisie is None else choisie)
        self._annotation_choisie(self.liste.currentRow())

    def _annotation_ajoutee(self) -> None:
        self._remplir_liste(len(self.toile.annotations) - 1)
        self.champ_texte.setFocus()

    def _annotation_choisie(self, rang: int) -> None:
        if self._mise_a_jour:
            return
        valide = 0 <= rang < len(self.toile.annotations)
        self.toile.choisie = rang if valide else None
        self.champ_texte.setText(self.toile.annotations[rang].texte if valide else "")
        self.champ_texte.setEnabled(valide)
        self.bouton_retirer.setEnabled(valide)
        self.toile.update()

    def _texte_modifie(self, texte: str) -> None:
        rang = self.toile.choisie
        if rang is None:
            return
        annotation = self.toile.annotations[rang]
        self.toile.annotations[rang] = annotation.model_copy(update={"texte": texte})
        libelle = _LIBELLES_FORMES.get(annotation.forme, annotation.forme)
        suite = f" : {texte}" if texte else ""
        self.liste.item(rang).setText(f"{rang + 1}. {libelle}{suite}")
        self.toile.update()

    def retirer_annotation(self) -> None:
        """Retire l'annotation choisie."""
        rang = self.toile.choisie
        if rang is not None:
            del self.toile.annotations[rang]
            self._remplir_liste()

    def photo(self) -> PhotoAnnotee:
        """Renvoie la photo avec ses annotations et sa légende."""
        return PhotoAnnotee(
            chemin=self.chemin_photo,
            legende=self.champ_legende.text(),
            annotations=tuple(self.toile.annotations),
        )
