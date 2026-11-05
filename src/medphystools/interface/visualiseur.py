"""Visualiseur de volumes : coupes axiale, sagittale et coronale, et vue 3D en transparence.

Il ne connaît ni les pages ni les archives : `VisualiseurVolume.afficher` reçoit un volume, et
éventuellement des structures et une dose. Il pourra servir tel quel au programme de CQ.

La vue 3D est calculée sur le processeur, sur un petit volume réduit et dans un fil à part :
elle ne demande pas de carte graphique et ne fige pas l'interface.
"""

import contextlib
import math
import threading
from collections.abc import Sequence
from functools import partial

import numpy as np
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from medphystools.interface.ramasse_miettes import assurer_ramasse_miettes_principal
from medphystools.interface.theme import ANNOTATION_CHOISIE
from medphystools.volumes import (
    ETIQUETTES_PLAN,
    PLANS,
    Structure,
    Volume,
    Volume3D,
    coupe,
    fenetre_automatique,
    nombre_de_coupes,
    pixel_du_voxel,
    preparer_3d,
    projeter_3d,
    reechantillonner_sur_coupe,
    rendre_3d,
    repere_du_plan,
    segments_sur_coupe,
    taille_pixel,
    valeur_au_point,
    voxel_du_pixel,
)

_FOND_IMAGE = QColor("#000000")
_TEXTE_IMAGE = QColor("#e8eaed")
_LIBELLES_PLAN = {"axial": "Axiale", "sagittal": "Sagittale", "coronal": "Coronale"}
# Niveaux habituels en scanner, en HU : (bas, haut).
_FENETRES_SCANNER = {
    "Tissus mous": (-160.0, 240.0),
    "Poumon": (-1350.0, 150.0),
    "Os": (-450.0, 1050.0),
    "Cerveau": (0.0, 80.0),
}
_FENETRE_SCANNER_DEFAUT = "Tissus mous"
_AUTOMATIQUE = "Automatique"
_PERSONNALISEE = "Personnalisée"
# Une seule teinte chaude qui s'éclaircit avec la dose : plus de dose, plus clair.
_POINTS_DOSE = np.array(
    [
        (0.00, 40, 11, 84),
        (0.25, 120, 28, 109),
        (0.50, 187, 55, 84),
        (0.75, 249, 142, 9),
        (1.00, 252, 255, 164),
    ]
)
_NIVEAUX_DOSE = np.linspace(0.0, 1.0, 256)
PALETTE_DOSE = np.stack(
    [
        np.interp(_NIVEAUX_DOSE, _POINTS_DOSE[:, 0], _POINTS_DOSE[:, k])
        for k in (1, 2, 3)
    ],
    axis=1,
).astype(np.uint8)
_POINTS_3D_MAX = 20_000
_TAILLE_3D = 96
_MAX_DOSES_GARDEES = 24


def image_grise(
    valeurs: np.ndarray, bas: float, haut: float, inverser: bool = False
) -> QImage:
    """Convertit des valeurs physiques en image, selon la fenêtre : `bas` noir, `haut` blanc."""
    niveaux = np.clip((valeurs - bas) / max(haut - bas, 1e-12), 0.0, 1.0)
    niveaux = np.nan_to_num(niveaux, nan=0.0)
    if inverser:
        niveaux = 1.0 - niveaux
    octets = (niveaux * 255.0 + 0.5).astype(np.uint8)
    lignes, colonnes = octets.shape
    # `copy` : l'image garde ses propres octets une fois le tableau disparu.
    return QImage(
        octets.tobytes(), colonnes, lignes, colonnes, QImage.Format.Format_Grayscale8
    ).copy()


def image_dose(valeurs: np.ndarray, bas: float, haut: float, opacite: float) -> QImage:
    """Colore une dose : sous le seuil `bas` et hors de la grille, rien ne se superpose."""
    lignes, colonnes = valeurs.shape
    niveaux = np.nan_to_num((valeurs - bas) / max(haut - bas, 1e-12), nan=-1.0)
    rangs = (np.clip(niveaux, 0.0, 1.0) * 255.0).astype(np.uint8)
    couleurs = np.empty((lignes, colonnes, 4), dtype=np.uint8)
    couleurs[..., :3] = PALETTE_DOSE[rangs]
    couleurs[..., 3] = np.where(niveaux >= 0.0, round(opacite * 255), 0)
    return QImage(
        couleurs.tobytes(),
        colonnes,
        lignes,
        colonnes * 4,
        QImage.Format.Format_RGBA8888,
    ).copy()


class VueCoupe(QWidget):
    """Une coupe : molette pour défiler, clic pour viser, clic droit tiré pour le fenêtrage."""

    defilement = Signal(int)
    point_choisi = Signal(object)
    survol = Signal(object)
    fenetre_tiree = Signal(float, float)
    agrandissement = Signal()

    def __init__(self, plan: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.volume: Volume | None = None
        self.dose: Volume | None = None
        self.structures: list[Structure] = []
        self.indice = 0
        self.t = 0
        self.bas = 0.0
        self.haut = 1.0
        self.dose_bas = 0.0
        self.dose_haut = 1.0
        self.opacite_dose = 0.4
        self.croix: tuple[float, float, float] | None = None
        self._image: QImage | None = None
        self._image_dose: QImage | None = None
        self._doses: dict[tuple[int, int], np.ndarray] = {}
        self._traces: dict[tuple[int, int], list[np.ndarray]] = {}
        self._dernier_droit: QPointF | None = None
        self.setMouseTracking(True)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # --- Contenu --------------------------------------------------------------------------

    def definir(self, volume: Volume | None, dose: Volume | None = None) -> None:
        """Change de volume : tout ce qui en dépendait est oublié."""
        self.volume = volume
        self.dose = dose
        self._doses.clear()
        self._traces.clear()
        self.rafraichir()

    def definir_dose(self, dose: Volume | None) -> None:
        self.dose = dose
        self.rafraichir()

    def rafraichir(self) -> None:
        """Recalcule les images au prochain affichage."""
        self._image = None
        self._image_dose = None
        self.update()

    def valeurs_affichees(self) -> np.ndarray:
        """Les valeurs physiques de la coupe, telles que chaque pixel les montre."""
        return coupe(self.volume, self.plan, self.indice, self.t)

    def dose_affichee(self) -> np.ndarray | None:
        """La dose relue sur la grille de cette coupe, gardée pour un défilement fluide."""
        if self.dose is None or self.volume is None:
            return None
        cle = (self.indice, self.t)
        if cle not in self._doses:
            if len(self._doses) >= _MAX_DOSES_GARDEES:
                self._doses.clear()
            self._doses[cle] = reechantillonner_sur_coupe(
                self.dose, self.volume, self.plan, self.indice, self.t
            )
        return self._doses[cle]

    def _preparer(self) -> None:
        if self._image is None:
            self._image = image_grise(
                self.valeurs_affichees(),
                self.bas,
                self.haut,
                self.volume.inverse_monochrome,
            )
        dose = self.dose_affichee()
        if dose is not None and self._image_dose is None:
            self._image_dose = image_dose(
                dose, self.dose_bas, self.dose_haut, self.opacite_dose
            )

    # --- Géométrie de l'écran -------------------------------------------------------------

    def _forme(self) -> tuple[int, int]:
        repere = repere_du_plan(self.volume, self.plan)
        forme = self.volume.donnees.shape[1:]
        return forme[repere.axe_lignes], forme[repere.axe_colonnes]

    def zone_image(self) -> QRectF:
        """Le rectangle occupé par l'image : ses proportions sont celles du patient, en mm."""
        lignes, colonnes = self._forme()
        pas_ligne, pas_colonne = taille_pixel(self.volume, self.plan)
        hauteur_mm = lignes * pas_ligne
        largeur_mm = colonnes * pas_colonne
        echelle = min(self.width() / largeur_mm, self.height() / hauteur_mm)
        largeur = largeur_mm * echelle
        hauteur = hauteur_mm * echelle
        return QRectF(
            (self.width() - largeur) / 2,
            (self.height() - hauteur) / 2,
            largeur,
            hauteur,
        )

    def ecran_du_pixel(self, ligne: float, colonne: float) -> QPointF:
        """Le centre d'un pixel à l'écran : le pixel 0 va de 0 à 1 fois sa taille."""
        zone = self.zone_image()
        lignes, colonnes = self._forme()
        return QPointF(
            zone.left() + (colonne + 0.5) * zone.width() / colonnes,
            zone.top() + (ligne + 0.5) * zone.height() / lignes,
        )

    def pixel_de_l_ecran(self, point: QPointF) -> tuple[int, int] | None:
        """Le pixel sous un point de l'écran, ou None hors de l'image."""
        if self.volume is None:
            return None
        zone = self.zone_image()
        if not zone.contains(point):
            return None
        lignes, colonnes = self._forme()
        ligne = int((point.y() - zone.top()) * lignes / zone.height())
        colonne = int((point.x() - zone.left()) * colonnes / zone.width())
        return min(ligne, lignes - 1), min(colonne, colonnes - 1)

    def _voxel_sous(self, point: QPointF) -> tuple[float, float, float] | None:
        pixel = self.pixel_de_l_ecran(point)
        if pixel is None:
            return None
        return voxel_du_pixel(self.volume, self.plan, self.indice, *pixel)

    # --- Dessin ---------------------------------------------------------------------------

    def _traces_de(self, structure: Structure) -> list[np.ndarray]:
        cle = (structure.numero, self.indice)
        if cle not in self._traces:
            self._traces[cle] = segments_sur_coupe(
                structure, self.volume, self.plan, self.indice
            )
        return self._traces[cle]

    def _dessiner_structures(self, peintre: QPainter) -> None:
        for structure in self.structures:
            peintre.setPen(QPen(QColor(*structure.couleur), 1.5))
            for trace in self._traces_de(structure):
                chemin = QPainterPath(self.ecran_du_pixel(*trace[0]))
                for point in trace[1:]:
                    chemin.lineTo(self.ecran_du_pixel(*point))
                peintre.drawPath(chemin)

    def _dessiner_croix(self, peintre: QPainter, zone: QRectF) -> None:
        if self.croix is None:
            return
        _indice, ligne, colonne = pixel_du_voxel(self.volume, self.plan, self.croix)
        centre = self.ecran_du_pixel(ligne, colonne)
        couleur = QColor(ANNOTATION_CHOISIE)
        couleur.setAlpha(150)
        peintre.setPen(QPen(couleur, 1))
        peintre.drawLine(
            QPointF(zone.left(), centre.y()), QPointF(zone.right(), centre.y())
        )
        peintre.drawLine(
            QPointF(centre.x(), zone.top()), QPointF(centre.x(), zone.bottom())
        )

    def _dessiner_reperes(self, peintre: QPainter, zone: QRectF) -> None:
        if not self.volume.geometrie_connue:
            return
        haut, bas, gauche, droite = ETIQUETTES_PLAN[self.plan]
        rectangle = zone.adjusted(4, 2, -4, -2)
        drapeaux = Qt.AlignmentFlag
        reperes = (
            (drapeaux.AlignTop | drapeaux.AlignHCenter, haut),
            (drapeaux.AlignBottom | drapeaux.AlignHCenter, bas),
            (drapeaux.AlignLeft | drapeaux.AlignVCenter, gauche),
            (drapeaux.AlignRight | drapeaux.AlignVCenter, droite),
        )
        # Un contour sombre : la lettre reste lisible sur une image claire comme sur le noir.
        for alignement, lettre in reperes:
            peintre.setPen(_FOND_IMAGE)
            for ecart_x, ecart_y in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                peintre.drawText(
                    rectangle.translated(ecart_x, ecart_y), alignement, lettre
                )
            peintre.setPen(_TEXTE_IMAGE)
            peintre.drawText(rectangle, alignement, lettre)

    def paintEvent(self, _evenement: QPaintEvent) -> None:  # noqa: N802
        peintre = QPainter(self)
        peintre.fillRect(self.rect(), _FOND_IMAGE)
        if self.volume is None:
            return
        self._preparer()
        zone = self.zone_image()
        # Sans lissage : chaque carré est une vraie valeur du fichier.
        peintre.drawImage(zone, self._image)
        if self._image_dose is not None:
            peintre.drawImage(zone, self._image_dose)
        peintre.setRenderHint(QPainter.RenderHint.Antialiasing)
        self._dessiner_structures(peintre)
        peintre.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self._dessiner_croix(peintre, zone)
        self._dessiner_reperes(peintre, zone)

    # --- Souris ---------------------------------------------------------------------------

    def mousePressEvent(self, evenement: QMouseEvent) -> None:  # noqa: N802
        if evenement.button() == Qt.MouseButton.RightButton:
            self._dernier_droit = evenement.position()
        elif evenement.button() == Qt.MouseButton.LeftButton:
            voxel = self._voxel_sous(evenement.position())
            if voxel is not None:
                self.point_choisi.emit(voxel)

    def mouseMoveEvent(self, evenement: QMouseEvent) -> None:  # noqa: N802
        position = evenement.position()
        boutons = evenement.buttons()
        if boutons & Qt.MouseButton.RightButton and self._dernier_droit is not None:
            ecart = position - self._dernier_droit
            self._dernier_droit = position
            self.fenetre_tiree.emit(ecart.x(), ecart.y())
            return
        voxel = self._voxel_sous(position)
        if boutons & Qt.MouseButton.LeftButton and voxel is not None:
            self.point_choisi.emit(voxel)
        self.survol.emit(voxel)

    def mouseReleaseEvent(self, _evenement: QMouseEvent) -> None:  # noqa: N802
        self._dernier_droit = None

    def mouseDoubleClickEvent(self, _evenement: QMouseEvent) -> None:  # noqa: N802
        self.agrandissement.emit()

    def leaveEvent(self, _evenement: object) -> None:  # noqa: N802
        self.survol.emit(None)

    def wheelEvent(self, evenement: QWheelEvent) -> None:  # noqa: N802
        ecart = evenement.angleDelta().y()
        if ecart:
            self.defilement.emit(1 if ecart > 0 else -1)
        evenement.accept()


class _SignauxRendu(QObject):
    """Rapporte une vue 3D calculée dans son fil de travail."""

    fini = Signal(object)


class Vue3D(QWidget):
    """Le volume vu en transparence ou en projection d'intensité maximale, à faire tourner."""

    agrandissement = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        assurer_ramasse_miettes_principal()
        self.volume: Volume | None = None
        self.structures: list[Structure] = []
        self.t = 0
        self.bas = 0.0
        self.haut = 1.0
        self.azimut = 0.0
        self.elevation = 0.0
        self.mode = "transparence"
        # Ce que montre l'écran : l'image va avec les angles qui l'ont produite.
        self.image: np.ndarray | None = None
        self._rendu_cube: Volume3D | None = None
        self._rendu_angles = (0.0, 0.0)
        self._cube: Volume3D | None = None
        self._cube_cle: tuple[int, int] | None = None
        self._occupe = False
        self._demande = False
        self._dernier: QPointF | None = None
        self._inverser = False
        self._signaux = _SignauxRendu(self)
        self._signaux.fini.connect(self._rendu_fini)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def definir(self, volume: Volume | None) -> None:
        self.volume = volume
        self.image = None
        self._rendu_cube = None
        self._cube = None
        self._cube_cle = None
        self._inverser = bool(volume is not None and volume.inverse_monochrome)
        self.update()

    def demander_rendu(self) -> None:
        """Lance un calcul, ou le retient si un autre tourne : seul le dernier état compte."""
        if self.volume is None:
            return
        if self._occupe:
            self._demande = True
            return
        self._occupe = True
        self._demande = False
        cle = (id(self.volume), self.t)
        travail = partial(
            self._calculer,
            self.volume,
            self.t,
            cle,
            self._cube if self._cube_cle == cle else None,
            self.azimut,
            self.elevation,
            self.bas,
            self.haut,
            self.mode,
        )
        threading.Thread(target=travail, daemon=True).start()

    def _calculer(
        self,
        volume: Volume,
        t: int,
        cle: tuple[int, int],
        cube: Volume3D | None,
        azimut: float,
        elevation: float,
        bas: float,
        haut: float,
        mode: str,
    ) -> None:
        """Hors du fil de l'interface : réduit le volume si besoin, puis le rend."""
        if cube is None:
            cube = preparer_3d(volume, t, _TAILLE_3D)
        image = rendre_3d(cube, azimut, elevation, bas, haut, mode)
        with contextlib.suppress(RuntimeError):
            self._signaux.fini.emit((volume, cle, cube, image, azimut, elevation))

    def _rendu_fini(self, resultat: tuple) -> None:
        volume, cle, cube, image, azimut, elevation = resultat
        self._occupe = False
        if volume is self.volume:
            self._cube = cube
            self._cube_cle = cle
            self.image = image
            self._rendu_cube = cube
            self._rendu_angles = (azimut, elevation)
            self.update()
        if self._demande:
            self.demander_rendu()

    # --- Dessin ---------------------------------------------------------------------------

    def _carre(self) -> QRectF:
        cote = min(self.width(), self.height())
        return QRectF((self.width() - cote) / 2, (self.height() - cote) / 2, cote, cote)

    def _contours_a_dessiner(self) -> list[tuple[QColor, np.ndarray]]:
        """Les contours projetés, allégés : le dessin reste fluide quand la vue tourne."""
        total = sum(len(c) for s in self.structures for c in s.contours)
        pas = max(1, math.ceil(total / _POINTS_3D_MAX))
        resultat = []
        for structure in self.structures:
            couleur = QColor(*structure.couleur)
            couleur.setAlpha(170)
            for contour in structure.contours:
                points = np.vstack((contour[::pas], contour[:1]))
                resultat.append(
                    (
                        couleur,
                        projeter_3d(self._rendu_cube, points, *self._rendu_angles),
                    )
                )
        return resultat

    def paintEvent(self, _evenement: QPaintEvent) -> None:  # noqa: N802
        peintre = QPainter(self)
        peintre.fillRect(self.rect(), _FOND_IMAGE)
        if self.image is None:
            return
        carre = self._carre()
        peintre.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        peintre.drawImage(carre, image_grise(self.image, 0.0, 1.0, self._inverser))
        taille = self.image.shape[0]
        echelle = carre.width() / taille
        peintre.setRenderHint(QPainter.RenderHint.Antialiasing)
        for couleur, points in self._contours_a_dessiner():
            peintre.setPen(QPen(couleur, 1))
            chemin = QPainterPath(
                QPointF(
                    carre.left() + (points[0, 1] + 0.5) * echelle,
                    carre.top() + (points[0, 0] + 0.5) * echelle,
                )
            )
            for ligne, colonne in points[1:]:
                chemin.lineTo(
                    carre.left() + (colonne + 0.5) * echelle,
                    carre.top() + (ligne + 0.5) * echelle,
                )
            peintre.drawPath(chemin)

    # --- Souris ---------------------------------------------------------------------------

    def mousePressEvent(self, evenement: QMouseEvent) -> None:  # noqa: N802
        self._dernier = evenement.position()

    def mouseMoveEvent(self, evenement: QMouseEvent) -> None:  # noqa: N802
        if self._dernier is None or not evenement.buttons():
            return
        ecart = evenement.position() - self._dernier
        self._dernier = evenement.position()
        self.azimut = (self.azimut + ecart.x() * 0.6) % 360.0
        self.elevation = (self.elevation + ecart.y() * 0.6) % 360.0
        self.demander_rendu()

    def mouseReleaseEvent(self, _evenement: QMouseEvent) -> None:  # noqa: N802
        self._dernier = None

    def mouseDoubleClickEvent(self, _evenement: QMouseEvent) -> None:  # noqa: N802
        self.agrandissement.emit()


class LegendeDose(QWidget):
    """La correspondance entre les couleurs et la dose : sans elle, une couleur ne dit rien."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(14)

    def paintEvent(self, _evenement: QPaintEvent) -> None:  # noqa: N802
        peintre = QPainter(self)
        degrade = QLinearGradient(0, 0, self.width(), 0)
        for niveau in np.linspace(0.0, 1.0, 9):
            degrade.setColorAt(float(niveau), QColor(*PALETTE_DOSE[int(niveau * 255)]))
        peintre.fillRect(self.rect(), degrade)


class _PanneauCoupe(QWidget):
    """Une vue, son titre et son curseur de défilement."""

    def __init__(self, vue: QWidget, titre: str, curseur: bool = True) -> None:
        super().__init__()
        self.vue = vue
        self.titre = QLabel(titre)
        self.titre.setObjectName("discret")
        self.curseur = QSlider(Qt.Orientation.Horizontal) if curseur else None
        disposition = QVBoxLayout(self)
        disposition.setContentsMargins(2, 2, 2, 2)
        disposition.setSpacing(2)
        disposition.addWidget(self.titre)
        disposition.addWidget(vue, stretch=1)
        if self.curseur is not None:
            disposition.addWidget(self.curseur)


class VisualiseurVolume(QWidget):
    """Affiche un volume dans trois coupes liées et une vue 3D. Réutilisable partout."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.volume: Volume | None = None
        self.dose: Volume | None = None
        self.structures: tuple[Structure, ...] = ()
        self._dose_maximale = 0.0
        self.voxel: list[int] = [0, 0, 0]
        self.t = 0
        self._agrandie: QWidget | None = None
        self._remplissage = False

        self.vues = {plan: VueCoupe(plan) for plan in PLANS}
        self.vue_3d = Vue3D()
        self.vue_3d.agrandissement.connect(partial(self._agrandir, "3d"))
        self.panneaux = {
            plan: _PanneauCoupe(vue, _LIBELLES_PLAN[plan])
            for plan, vue in self.vues.items()
        }
        self.panneau_3d = _PanneauCoupe(
            self.vue_3d, "Vue 3D : glisser pour tourner", curseur=False
        )
        for plan, vue in self.vues.items():
            vue.defilement.connect(partial(self._defiler, plan))
            vue.point_choisi.connect(self.aller_au_voxel)
            vue.survol.connect(self._survol)
            vue.fenetre_tiree.connect(self._fenetre_tiree)
            vue.agrandissement.connect(partial(self._agrandir, plan))
            self.panneaux[plan].curseur.valueChanged.connect(
                partial(self._curseur_bouge, plan)
            )
        grille = QGridLayout()
        grille.setContentsMargins(0, 0, 0, 0)
        grille.setSpacing(4)
        grille.addWidget(self.panneaux["axial"], 0, 0)
        grille.addWidget(self.panneaux["sagittal"], 0, 1)
        grille.addWidget(self.panneaux["coronal"], 1, 0)
        grille.addWidget(self.panneau_3d, 1, 1)
        zone_vues = QWidget()
        zone_vues.setLayout(grille)

        self.lecture = QLabel("")
        self.lecture.setTextFormat(Qt.TextFormat.PlainText)
        self.lecture.setToolTip(
            "Valeur physique sous la souris. Molette : coupe. Clic : viser. "
            "Clic droit tiré : fenêtrage. Double-clic : agrandir une vue."
        )

        self.choix_fenetre = QComboBox()
        self.choix_fenetre.activated.connect(self._fenetre_choisie)
        self.champ_bas = self._champ_fenetre("Bas de la fenêtre")
        self.champ_haut = self._champ_fenetre("Haut de la fenêtre")
        self.choix_3d = QComboBox()
        self.choix_3d.addItem("Transparence", "transparence")
        self.choix_3d.addItem("Intensité maximale", "mip")
        self.choix_3d.currentIndexChanged.connect(self._mode_3d_change)
        self.curseur_temps = QSlider(Qt.Orientation.Horizontal)
        self.curseur_temps.valueChanged.connect(self.aller_a_la_phase)
        self.libelle_temps = QLabel("")
        self.zone_temps = QWidget()
        ligne_temps = QHBoxLayout(self.zone_temps)
        ligne_temps.setContentsMargins(0, 0, 0, 0)
        ligne_temps.addWidget(self.libelle_temps)
        ligne_temps.addWidget(self.curseur_temps, stretch=1)

        commandes = QHBoxLayout()
        commandes.addWidget(QLabel("Fenêtre"))
        commandes.addWidget(self.choix_fenetre)
        commandes.addWidget(self.champ_bas)
        commandes.addWidget(self.champ_haut)
        commandes.addSpacing(12)
        commandes.addWidget(QLabel("3D"))
        commandes.addWidget(self.choix_3d)
        commandes.addSpacing(12)
        commandes.addWidget(self.zone_temps, stretch=1)

        self.remarques = QLabel("")
        self.remarques.setObjectName("discret")
        self.remarques.setWordWrap(True)
        self.remarques.setTextFormat(Qt.TextFormat.PlainText)

        gauche = QWidget()
        disposition_gauche = QVBoxLayout(gauche)
        disposition_gauche.setContentsMargins(0, 0, 0, 0)
        disposition_gauche.addWidget(zone_vues, stretch=1)
        disposition_gauche.addWidget(self.lecture)
        disposition_gauche.addLayout(commandes)
        disposition_gauche.addWidget(self.remarques)

        self.liste_structures = QListWidget()
        self.liste_structures.itemChanged.connect(self._structures_changees)
        self.titre_structures = QLabel("Structures")

        self.case_dose = QCheckBox("Afficher la dose")
        self.case_dose.setChecked(True)
        self.case_dose.toggled.connect(self._dose_changee)
        self.curseur_opacite = QSlider(Qt.Orientation.Horizontal)
        self.curseur_opacite.setRange(5, 100)
        self.curseur_opacite.setValue(40)
        self.curseur_opacite.setToolTip("Opacité de la dose")
        self.curseur_opacite.valueChanged.connect(self._dose_changee)
        self.champ_seuil = QSpinBox()
        self.champ_seuil.setRange(0, 99)
        self.champ_seuil.setValue(10)
        self.champ_seuil.setSuffix(" % du max")
        self.champ_seuil.setToolTip("En dessous, la dose ne se superpose pas à l'image")
        self.champ_seuil.valueChanged.connect(self._dose_changee)
        self.legende_dose = LegendeDose()
        self.echelle_dose = QLabel("")
        self.echelle_dose.setObjectName("discret")
        self.zone_dose = QWidget()
        disposition_dose = QVBoxLayout(self.zone_dose)
        disposition_dose.setContentsMargins(0, 0, 0, 0)
        disposition_dose.addWidget(self.case_dose)
        ligne_dose = QHBoxLayout()
        ligne_dose.addWidget(self.curseur_opacite, stretch=1)
        ligne_dose.addWidget(self.champ_seuil)
        disposition_dose.addLayout(ligne_dose)
        disposition_dose.addWidget(self.legende_dose)
        disposition_dose.addWidget(self.echelle_dose)

        self.table_balises = QTableWidget(0, 2)
        self.table_balises.setHorizontalHeaderLabels(["Balise", "Valeur"])
        self.table_balises.verticalHeader().setVisible(False)
        self.table_balises.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table_balises.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table_balises.horizontalHeader().setStretchLastSection(True)

        droite = QWidget()
        disposition_droite = QVBoxLayout(droite)
        disposition_droite.setContentsMargins(0, 0, 0, 0)
        disposition_droite.addWidget(self.titre_structures)
        disposition_droite.addWidget(self.liste_structures, stretch=1)
        disposition_droite.addWidget(self.zone_dose)
        disposition_droite.addWidget(QLabel("Balises principales"))
        disposition_droite.addWidget(self.table_balises, stretch=2)

        separation = QSplitter(Qt.Orientation.Horizontal)
        separation.addWidget(gauche)
        separation.addWidget(droite)
        separation.setStretchFactor(0, 4)
        separation.setStretchFactor(1, 1)
        separation.setSizes([800, 240])
        disposition = QVBoxLayout(self)
        disposition.setContentsMargins(0, 0, 0, 0)
        disposition.addWidget(separation)
        self.vider()

    def _champ_fenetre(self, aide: str) -> QDoubleSpinBox:
        champ = QDoubleSpinBox()
        champ.setRange(-1e12, 1e12)
        champ.setDecimals(3)
        champ.setToolTip(aide)
        champ.setKeyboardTracking(False)
        champ.valueChanged.connect(self._champs_fenetre_changes)
        return champ

    # --- Contenu --------------------------------------------------------------------------

    def vider(self) -> None:
        """Retire ce qui était affiché."""
        self.volume = None
        self.dose = None
        self.structures = ()
        for vue in self.vues.values():
            vue.structures = []
            vue.definir(None)
        self.vue_3d.structures = []
        self.vue_3d.definir(None)
        self.lecture.setText("")
        self.remarques.setText("")
        self.liste_structures.clear()
        self.table_balises.setRowCount(0)
        self.titre_structures.setVisible(False)
        self.liste_structures.setVisible(False)
        self.zone_dose.setVisible(False)
        self.zone_temps.setVisible(False)

    def afficher(
        self,
        volume: Volume,
        structures: Sequence[Structure] = (),
        dose: Volume | None = None,
        balises: Sequence[tuple[str, str]] = (),
        remarques: Sequence[str] = (),
    ) -> None:
        """Montre un volume, avec ses structures et sa dose s'ils partagent son repère."""
        self.volume = volume
        self.structures = tuple(structures)
        lignes_remarques = [*volume.avertissements, *remarques]
        self.dose = dose
        if dose is not None and (
            dose.cadre_reference is None
            or dose.cadre_reference != volume.cadre_reference
        ):
            self.dose = None
            lignes_remarques.append(
                "Dose non superposée : son repère diffère de celui de l'image."
            )
        self.t = 0
        forme = volume.donnees.shape[1:]
        self.voxel = [taille // 2 for taille in forme]
        # Un scanner a des niveaux habituels. Sur toute sa dynamique, il serait saturé ou gris.
        scanner = volume.unite == "HU"
        bas, haut = (
            _FENETRES_SCANNER[_FENETRE_SCANNER_DEFAUT]
            if scanner
            else fenetre_automatique(volume)
        )
        self._remplissage = True
        for plan, vue in self.vues.items():
            vue.t = 0
            vue.definir(volume, self.dose)
            curseur = self.panneaux[plan].curseur
            curseur.setRange(0, nombre_de_coupes(volume, plan) - 1)
        self.vue_3d.t = 0
        self.vue_3d.definir(volume)
        self.choix_fenetre.clear()
        self.choix_fenetre.addItem(_AUTOMATIQUE)
        if scanner:
            self.choix_fenetre.addItems(list(_FENETRES_SCANNER))
        self.choix_fenetre.addItem(_PERSONNALISEE)
        if scanner:
            self.choix_fenetre.setCurrentText(_FENETRE_SCANNER_DEFAUT)
        self.curseur_temps.setRange(0, volume.phases - 1)
        self.curseur_temps.setValue(0)
        self.zone_temps.setVisible(volume.phases > 1)
        self._remplir_structures()
        self._remplir_balises(balises)
        self.remarques.setText("\n".join(lignes_remarques))
        self._preparer_dose()
        self._remplissage = False
        self._montrer_volumes_utiles()
        self.definir_fenetre(bas, haut)
        self._ecrire_phase()
        self._appliquer_voxel()
        self._afficher_lecture(tuple(self.voxel))

    def _montrer_volumes_utiles(self) -> None:
        """Une image seule n'a qu'une vue : ses autres coupes ne montreraient qu'une ligne."""
        volume_entier = (
            self.volume.geometrie_connue and min(self.volume.donnees.shape[1:]) > 1
        )
        self.panneaux["sagittal"].setVisible(volume_entier)
        self.panneaux["coronal"].setVisible(volume_entier)
        self.panneau_3d.setVisible(volume_entier)
        self.choix_3d.setEnabled(volume_entier)
        self._agrandie = None
        self.panneaux["axial"].setVisible(True)
        titre = _LIBELLES_PLAN["axial"] if volume_entier else "Image"
        self.panneaux["axial"].titre.setText(titre)

    def _remplir_structures(self) -> None:
        self.liste_structures.clear()
        for structure in self.structures:
            element = QListWidgetItem(structure.nom)
            element.setFlags(element.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            element.setCheckState(Qt.CheckState.Checked)
            pastille = QPixmap(12, 12)
            pastille.fill(QColor(*structure.couleur))
            element.setIcon(QIcon(pastille))
            self.liste_structures.addItem(element)
        presentes = bool(self.structures)
        self.titre_structures.setVisible(presentes)
        self.liste_structures.setVisible(presentes)
        self._appliquer_structures()

    def _remplir_balises(self, balises: Sequence[tuple[str, str]]) -> None:
        self.table_balises.setRowCount(len(balises))
        for rang, (libelle, valeur) in enumerate(balises):
            self.table_balises.setItem(rang, 0, QTableWidgetItem(libelle))
            self.table_balises.setItem(rang, 1, QTableWidgetItem(valeur))

    def structures_visibles(self) -> list[Structure]:
        return [
            structure
            for rang, structure in enumerate(self.structures)
            if self.liste_structures.item(rang).checkState() == Qt.CheckState.Checked
        ]

    def _appliquer_structures(self) -> None:
        visibles = self.structures_visibles()
        for vue in self.vues.values():
            vue.structures = visibles
            vue.update()
        self.vue_3d.structures = visibles
        self.vue_3d.update()

    def _structures_changees(self, _element: QListWidgetItem) -> None:
        self._appliquer_structures()

    # --- Dose -----------------------------------------------------------------------------

    def _preparer_dose(self) -> None:
        self.zone_dose.setVisible(self.dose is not None)
        # Une seule fois : ce parcours de tout le volume ne suit pas le curseur d'opacité.
        if self.dose is not None:
            self._dose_maximale = float(np.nanmax(self.dose.donnees))
        self._dose_changee()

    def _dose_changee(self, *_args: object) -> None:
        if self.volume is None:
            return
        dose = self.dose if self.case_dose.isChecked() else None
        if self.dose is not None:
            maximum = self._dose_maximale
            seuil = maximum * self.champ_seuil.value() / 100.0
            self.echelle_dose.setText(f"{seuil:.4g} à {maximum:.4g} {self.dose.unite}")
            for vue in self.vues.values():
                vue.dose_bas = seuil
                vue.dose_haut = maximum
                vue.opacite_dose = self.curseur_opacite.value() / 100.0
        for vue in self.vues.values():
            vue.definir_dose(dose)

    # --- Navigation -----------------------------------------------------------------------

    def _defiler(self, plan: str, sens: int) -> None:
        self.aller_a_la_coupe(plan, self.vues[plan].indice + sens)

    def _curseur_bouge(self, plan: str, valeur: int) -> None:
        if not self._remplissage and self.volume is not None:
            self.aller_a_la_coupe(plan, valeur)

    def aller_a_la_coupe(self, plan: str, indice: int) -> None:
        """Change de coupe dans un plan : la croix suit, et les autres vues avec elle."""
        if self.volume is None:
            return
        repere = repere_du_plan(self.volume, plan)
        voxel = list(self.voxel)
        voxel[repere.axe_fixe] = indice
        self.aller_au_voxel(voxel)

    def aller_au_voxel(self, voxel: Sequence[float]) -> None:
        """Place la croix sur un voxel (z, y, x) : les trois coupes passent par lui."""
        forme = self.volume.donnees.shape[1:]
        self.voxel = [
            min(max(round(float(v)), 0), taille - 1)
            for v, taille in zip(voxel, forme, strict=True)
        ]
        self._appliquer_voxel()
        self._afficher_lecture(tuple(self.voxel))

    def _appliquer_voxel(self) -> None:
        self._remplissage = True
        for plan, vue in self.vues.items():
            indice = round(pixel_du_voxel(self.volume, plan, self.voxel)[0])
            if indice != vue.indice:
                vue.indice = indice
                vue.rafraichir()
            vue.croix = tuple(self.voxel)
            vue.update()
            panneau = self.panneaux[plan]
            panneau.curseur.setValue(indice)
            if plan != "axial" or panneau.titre.text() != "Image":
                panneau.titre.setText(
                    f"{_LIBELLES_PLAN[plan]} : {indice + 1}/{nombre_de_coupes(self.volume, plan)}"
                )
        self._remplissage = False

    def aller_a_la_phase(self, t: int) -> None:
        """Change de phase d'un volume 4D."""
        if self.volume is None or self._remplissage:
            return
        self.t = t
        for vue in self.vues.values():
            vue.t = t
            vue.rafraichir()
        self.vue_3d.t = t
        self.vue_3d.demander_rendu()
        self._ecrire_phase()
        self._afficher_lecture(tuple(self.voxel))

    def _ecrire_phase(self) -> None:
        if self.volume.phases > 1:
            self.libelle_temps.setText(f"Phase {self.t + 1}/{self.volume.phases}")

    def _agrandir(self, cle: str) -> None:
        """Double-clic : une vue prend toute la place, un autre double-clic la rend."""
        if not self.volume.geometrie_connue or min(self.volume.donnees.shape[1:]) <= 1:
            return
        cible = self.panneau_3d if cle == "3d" else self.panneaux[cle]
        tous = [*self.panneaux.values(), self.panneau_3d]
        if self._agrandie is cible:
            self._agrandie = None
            for panneau in tous:
                panneau.setVisible(True)
            return
        self._agrandie = cible
        for panneau in tous:
            panneau.setVisible(panneau is cible)

    # --- Lecture des valeurs --------------------------------------------------------------

    def _survol(self, voxel: tuple[float, float, float] | None) -> None:
        self._afficher_lecture(voxel if voxel is not None else tuple(self.voxel))

    def texte_de_lecture(self, voxel: Sequence[float]) -> str:
        """Dit la valeur physique d'un voxel, son unité et sa position dans le patient."""
        z, y, x = (int(v) for v in voxel)
        valeur = self.volume.valeur(self.t, z, y, x)
        unite = f" {self.volume.unite}" if self.volume.unite else ""
        morceaux = [f"{valeur:.6g}{unite}"]
        if self.volume.geometrie_connue:
            px, py, pz = self.volume.vers_patient(x, y, z)
            morceaux.append(f"x = {px:.1f}, y = {py:.1f}, z = {pz:.1f} mm")
        if self.dose is not None:
            point = self.volume.vers_patient(x, y, z)
            dose = valeur_au_point(self.dose, point, self.t)
            texte = (
                "hors de la grille"
                if math.isnan(dose)
                else f"{dose:.5g} {self.dose.unite}"
            )
            morceaux.append(f"dose : {texte}")
        return "  |  ".join(morceaux)

    def _afficher_lecture(self, voxel: Sequence[float]) -> None:
        self.lecture.setText(self.texte_de_lecture(voxel))

    # --- Fenêtrage ------------------------------------------------------------------------

    def definir_fenetre(self, bas: float, haut: float) -> None:
        """Change la fenêtre d'affichage. Les données, elles, ne changent jamais."""
        if not bas < haut:
            return
        self._remplissage = True
        self.champ_bas.setValue(bas)
        self.champ_haut.setValue(haut)
        self._remplissage = False
        for vue in self.vues.values():
            vue.bas = bas
            vue.haut = haut
            vue.rafraichir()
        self.vue_3d.bas = bas
        self.vue_3d.haut = haut
        self.vue_3d.demander_rendu()

    def _fenetre_choisie(self, _rang: int) -> None:
        if self.volume is None:
            return
        choix = self.choix_fenetre.currentText()
        if choix == _AUTOMATIQUE:
            self.definir_fenetre(*fenetre_automatique(self.volume, self.t))
        elif choix in _FENETRES_SCANNER:
            self.definir_fenetre(*_FENETRES_SCANNER[choix])

    def _marquer_personnalisee(self) -> None:
        self.choix_fenetre.setCurrentText(_PERSONNALISEE)

    def _champs_fenetre_changes(self, _valeur: float) -> None:
        if self._remplissage or self.volume is None:
            return
        self._marquer_personnalisee()
        self.definir_fenetre(self.champ_bas.value(), self.champ_haut.value())

    def _fenetre_tiree(self, ecart_x: float, ecart_y: float) -> None:
        """Vers la droite, la fenêtre s'élargit. Vers le haut, son centre monte."""
        bas = self.vues["axial"].bas
        haut = self.vues["axial"].haut
        pas = max(haut - bas, 1e-9) / 256.0
        largeur = max(haut - bas + ecart_x * pas, pas)
        centre = (bas + haut) / 2 - ecart_y * pas
        self._marquer_personnalisee()
        self.definir_fenetre(centre - largeur / 2, centre + largeur / 2)

    def _mode_3d_change(self, _rang: int) -> None:
        self.vue_3d.mode = self.choix_3d.currentData()
        self.vue_3d.demander_rendu()
