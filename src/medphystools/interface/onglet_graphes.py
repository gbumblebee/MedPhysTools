"""Onglet de graphes : une colonne contre une autre, en 2D ou en 3D.

Les réglages sont gardés dans la page. Le graphe montre la dernière version enregistrée du
tableau. Le facteur k ne change que l'affichage, et ne crée donc aucun enregistrement.
"""

import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path

import pandas as pd
from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from medphystools.archive import Archive
from medphystools.edition import Grandeur, suggerer_nom
from medphystools.graphes import (
    GrapheImpossible,
    ajustements_du_tableau,
    courbes_a_relire,
    empreinte_tableau,
    preparer_graphe,
    reglages_par_defaut,
    tableaux_tracables,
)
from medphystools.interface.messages import message_erreur
from medphystools.interface.onglets import EditeurOnglet, declarer_onglet
from medphystools.interface.panneau_indicateurs import PanneauIndicateurs
from medphystools.page import (
    CaptureGraphe,
    Indicateur,
    Onglet,
    OngletGraphes,
    Page,
    PhotoAnnotee,
    ReglagesGraphe,
)

_LARGEUR_REGLAGES = 270
_MESSAGE_SANS_TABLEAU = (
    "Il faut d'abord un tableau enregistré avec au moins deux colonnes numériques."
)


def creer_toile(interactif: bool = True) -> QWidget:
    """Crée la zone de dessin. matplotlib, long à charger, ne s'importe qu'à ce moment."""
    from medphystools.interface.graphique import ToileGraphe  # noqa: PLC0415

    return ToileGraphe(interactif)


def dessiner(
    toile: QWidget,
    archive: Archive,
    page: Page,
    reglages: ReglagesGraphe | None,
    k: float,
    manque: str | None = None,
) -> None:
    """Trace un graphe, ou explique pourquoi c'est impossible, sans jamais planter.

    `manque` dit ce qui empêche d'enregistrer la page : souvent, c'est ce que le graphe attend.
    """
    if reglages is None:
        suite = f" Il manque encore : {manque}." if manque else ""
        toile.signaler(f"{_MESSAGE_SANS_TABLEAU}{suite}")
        return
    if page.gabarit is not None and reglages.tableau not in tableaux_tracables(page):
        suite = f" Il manque encore : {manque}." if manque else ""
        toile.signaler(
            f"Ce graphe se tracera dès que « {reglages.tableau} » aura des données "
            f"enregistrées.{suite}"
        )
        return
    try:
        donnees = {}
        if reglages.tableau in tableaux_tracables(page):
            donnees[reglages.tableau] = archive.lire_tableau(page.id, reglages.tableau)
        for courbe in courbes_a_relire(page, reglages):
            donnees[courbe] = archive.lire_tableau(page.id, courbe)
        toile.tracer(preparer_graphe(page, donnees, reglages, k))
    except (GrapheImpossible, ValueError, KeyError, OSError) as erreur:
        toile.signaler(message_erreur(erreur))


def capturer_graphe(
    toile: QWidget,
    editeur: EditeurOnglet,
    tableau: str,
    lire_tableau: Callable[[str], pd.DataFrame],
    k: float,
) -> str:
    """Enregistre le graphe en image, le joint à la page et propose de l'insérer aux notes.

    Renvoie un message pour l'utilisateur. L'image reste figée : son origine permet de
    signaler plus tard que le tableau a changé. Sa légende dit d'où elle vient.
    """
    if editeur.pieces is None or toile is None or toile.donnees is None:
        return "Aucun graphe à capturer pour l'instant."
    maintenant = datetime.now()
    titre = editeur.onglet.titre or "Graphe"
    nom = f"graphe_{suggerer_nom(titre) or 'capture'}_{maintenant:%Y%m%d_%H%M%S}.png"
    try:
        with tempfile.TemporaryDirectory() as dossier:
            chemin = Path(dossier) / nom
            toile.enregistrer_image(chemin)
            piece = editeur.pieces.joindre(chemin)
        empreinte = empreinte_tableau(editeur.page, tableau, lire_tableau(tableau))
    except (ValueError, KeyError, OSError) as erreur:
        return f"Capture impossible : {message_erreur(erreur)}"
    editeur.capture_proposee.emit(
        PhotoAnnotee(
            chemin=piece.chemin,
            legende=f"{titre}, capturé le {maintenant:%d/%m/%Y %H:%M}, k = {k:g}",
            capture=CaptureGraphe(tableau=tableau, empreinte_donnees=empreinte),
        )
    )
    return "Capture ajoutée à l'onglet de notes."


class EditeurGraphes(EditeurOnglet):
    """Choisit les colonnes et les échelles, et trace le graphe à côté."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        self.archive: Archive | None = None
        self.toile: QWidget | None = None
        self._remplissage = True
        self._a_tracer = True
        self._manque: str | None = None
        self._noms_possedes: list[str] = []
        # Tant qu'on ne touche à rien, la page ne change pas : la regarder ne coûte aucun motif.
        self.reglages: ReglagesGraphe | None = getattr(onglet, "reglages", None)

        self.champ_tableau = QComboBox()
        self.champ_mode = QComboBox()
        self.champ_mode.addItem("2D", "2d")
        self.champ_mode.addItem("3D", "3d")
        self.champ_x = QComboBox()
        self.liste_y = QListWidget()
        self.liste_y.setMaximumHeight(130)
        self.champ_y_3d = QComboBox()
        self.champ_z = QComboBox()
        self.liste_courbes = QListWidget()
        self.liste_courbes.setMaximumHeight(70)
        self.log_x = QCheckBox("Échelle log en x")
        self.log_y = QCheckBox("Échelle log en y")
        self.log_z = QCheckBox("Échelle log en z")
        self.barres = QCheckBox("Barres d'incertitude")
        self.barres.setChecked(True)
        self.relier = QCheckBox("Relier les points")
        self.relier.setChecked(True)
        self.surface = QCheckBox("Surface")
        self.champ_k = QDoubleSpinBox()
        self.champ_k.setRange(0.1, 10.0)
        self.champ_k.setSingleStep(0.5)
        self.champ_k.setValue(2.0)
        self.champ_k.setToolTip(
            "Facteur d'élargissement : il change seulement l'affichage."
        )
        indication = QLabel("Le graphe montre la dernière version enregistrée.")
        indication.setObjectName("discret")
        indication.setWordWrap(True)

        self.formulaire = QFormLayout()
        self.formulaire.addRow("Tableau", self.champ_tableau)
        self.formulaire.addRow("Type", self.champ_mode)
        self.formulaire.addRow("x", self.champ_x)
        self.formulaire.addRow("y", self.liste_y)
        self.formulaire.addRow("y ", self.champ_y_3d)
        self.formulaire.addRow("z", self.champ_z)
        self.formulaire.addRow("Ajustements", self.liste_courbes)
        self.formulaire.addRow("k =", self.champ_k)
        gauche = QVBoxLayout()
        gauche.addLayout(self.formulaire)
        for case in (
            self.log_x,
            self.log_y,
            self.log_z,
            self.barres,
            self.relier,
            self.surface,
        ):
            gauche.addWidget(case)
        gauche.addWidget(indication)
        self.bouton_capture = QPushButton("Capturer dans les notes")
        self.bouton_capture.setToolTip(
            "Garde une image figée de ce graphe dans la page, et l'insère dans les notes"
        )
        self.bouton_capture.clicked.connect(self.demander_capture)
        self.message_capture = QLabel("")
        self.message_capture.setObjectName("discret")
        self.message_capture.setWordWrap(True)
        gauche.addWidget(self.bouton_capture)
        gauche.addWidget(self.message_capture)
        gauche.addStretch()
        conteneur = QWidget()
        conteneur.setLayout(gauche)
        conteneur.setFixedWidth(_LARGEUR_REGLAGES)

        self.zone = QVBoxLayout()
        page_graphe = QWidget()
        page_graphe.setLayout(self.zone)
        self.panneau = PanneauIndicateurs(self.champ_k.value)
        self.panneau.modifie.connect(self._indicateurs_modifies)
        self.panneau.reperes_changes.connect(self.tracer_si_visible)
        self.vues = QTabWidget()
        self.vues.addTab(page_graphe, "Graphe")
        self.vues.addTab(self.panneau, "Indicateurs")
        disposition = QHBoxLayout(self)
        disposition.addWidget(conteneur)
        disposition.addWidget(self.vues, stretch=1)

        self.champ_tableau.currentIndexChanged.connect(self._tableau_change)
        self.champ_mode.currentIndexChanged.connect(self._mode_change)
        for liste in (self.champ_x, self.champ_y_3d, self.champ_z):
            liste.currentIndexChanged.connect(self._reglage_change)
        self.liste_y.itemChanged.connect(self._reglage_change)
        self.liste_courbes.itemChanged.connect(self._reglage_change)
        for case in (
            self.log_x,
            self.log_y,
            self.log_z,
            self.barres,
            self.relier,
            self.surface,
        ):
            case.toggled.connect(self._reglage_change)
        self.champ_k.valueChanged.connect(self.tracer_si_visible)
        self.champ_k.valueChanged.connect(self.panneau.afficher)

    # --- Chargement -----------------------------------------------------------------------

    def charger(self, archive: Archive) -> None:
        self.archive = archive
        par_nom = {g.nom: g for g in self.page.mesures} if self.page else {}
        self.panneau.indicateurs = [
            par_nom[nom]
            for nom in getattr(self.onglet, "indicateurs", ())
            if isinstance(par_nom.get(nom), Indicateur)
        ]
        self._noms_possedes = [i.nom for i in self.panneau.indicateurs]
        self._remplir()
        self._configurer_panneau()

    def lire_tableau(self, nom: str) -> pd.DataFrame:
        """Relit un tableau enregistré de la page."""
        return self.archive.lire_tableau(self.page.id, nom)

    def _noms_reserves(self) -> set[str]:
        return {g.nom for g in self.page.mesures} - set(self._noms_possedes)

    def _configurer_panneau(self) -> None:
        if self.page is None or self.archive is None:
            return
        reglages = self._reglages_affiches()
        if reglages is None:
            colonnes: tuple[str, str, Sequence[str]] = ("", "", ())
            ajustements: list = []
        else:
            colonnes = (
                reglages.tableau,
                reglages.x,
                reglages.y if reglages.mode == "2d" else reglages.y[:1],
            )
            ajustements = ajustements_du_tableau(self.page, reglages.tableau)
        self.panneau.configurer(
            self.page, self.lire_tableau, colonnes, ajustements, self._noms_reserves
        )

    def _indicateurs_modifies(self) -> None:
        self.modifie.emit()

    def _page_affichee(self) -> Page:
        """La page avec les indicateurs à l'écran, même pas encore enregistrés."""
        if self.page is None or not self.panneau.indicateurs:
            return self.page
        noms = {i.nom for i in self.panneau.indicateurs}
        return self.page.model_copy(
            update={
                "mesures": tuple(g for g in self.page.mesures if g.nom not in noms)
                + tuple(self.panneau.indicateurs)
            }
        )

    def _reglages_affiches(self) -> ReglagesGraphe | None:
        if self.page is None:
            return None
        if self.reglages is not None:
            return self.reglages
        return reglages_par_defaut(self.page, self.onglet)

    def _remplir_colonnes(self, table: str, reglages: ReglagesGraphe | None) -> None:
        colonnes = tableaux_tracables(self.page).get(table, [])
        for liste in (self.champ_x, self.champ_y_3d, self.champ_z):
            liste.clear()
            liste.addItems(colonnes)
        self.liste_y.clear()
        coches = reglages.y if reglages is not None else ()
        for nom in colonnes:
            element = QListWidgetItem(nom)
            element.setFlags(element.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            element.setCheckState(
                Qt.CheckState.Checked if nom in coches else Qt.CheckState.Unchecked
            )
            self.liste_y.addItem(element)
        self.liste_courbes.clear()
        cochees = reglages.courbes if reglages is not None else ()
        for ajustement in ajustements_du_tableau(self.page, table):
            element = QListWidgetItem(ajustement.nom)
            element.setFlags(element.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            element.setCheckState(
                Qt.CheckState.Checked
                if ajustement.nom in cochees
                else Qt.CheckState.Unchecked
            )
            self.liste_courbes.addItem(element)
        if reglages is None:
            return
        self.champ_x.setCurrentText(reglages.x)
        self.champ_y_3d.setCurrentText(reglages.y[0])
        self.champ_z.setCurrentText(reglages.z or "")

    def _remplir(self) -> None:
        """Affiche les réglages et les colonnes actuelles, sans considérer cela une modification."""
        self._remplissage = True
        reglages = self._reglages_affiches()
        tables = tableaux_tracables(self.page) if self.page is not None else {}
        self.champ_tableau.clear()
        self.champ_tableau.addItems(list(tables))
        if reglages is not None:
            self.champ_tableau.setCurrentText(reglages.tableau)
            self.champ_mode.setCurrentIndex(0 if reglages.mode == "2d" else 1)
            self.log_x.setChecked(reglages.echelle_x == "log")
            self.log_y.setChecked(reglages.echelle_y == "log")
            self.log_z.setChecked(reglages.echelle_z == "log")
            self.barres.setChecked(reglages.barres)
            self.relier.setChecked(reglages.relier)
            self.surface.setChecked(reglages.surface)
        self._remplir_colonnes(self.champ_tableau.currentText(), reglages)
        self._adapter_au_mode()
        self._remplissage = False

    def _adapter_au_mode(self) -> None:
        trois_d = self.champ_mode.currentData() == "3d"
        for champ, visible in (
            (self.liste_y, not trois_d),
            (self.champ_y_3d, trois_d),
            (self.champ_z, trois_d),
            (self.liste_courbes, not trois_d and self.liste_courbes.count() > 0),
        ):
            self.formulaire.setRowVisible(champ, visible)
        self.log_z.setVisible(trois_d)
        self.surface.setVisible(trois_d)

    # --- Réglages -------------------------------------------------------------------------

    def _tableau_change(self) -> None:
        if self._remplissage:
            return
        self._remplissage = True
        self._remplir_colonnes(self.champ_tableau.currentText(), None)
        self._remplissage = False
        self._reglage_change()

    def _colonnes_y_cochees(self) -> tuple[str, ...]:
        return tuple(
            self.liste_y.item(rang).text()
            for rang in range(self.liste_y.count())
            if self.liste_y.item(rang).checkState() == Qt.CheckState.Checked
        )

    def _lire_reglages(self) -> ReglagesGraphe:
        """Construit les réglages choisis. Lève `ValidationError` s'il manque une colonne."""
        trois_d = self.champ_mode.currentData() == "3d"
        return ReglagesGraphe(
            tableau=self.champ_tableau.currentText(),
            mode=self.champ_mode.currentData(),
            x=self.champ_x.currentText(),
            y=(self.champ_y_3d.currentText(),)
            if trois_d
            else self._colonnes_y_cochees(),
            z=self.champ_z.currentText() if trois_d else None,
            echelle_x="log" if self.log_x.isChecked() else "lineaire",
            echelle_y="log" if self.log_y.isChecked() else "lineaire",
            echelle_z="log" if trois_d and self.log_z.isChecked() else "lineaire",
            barres=self.barres.isChecked(),
            relier=self.relier.isChecked(),
            surface=trois_d and self.surface.isChecked(),
            courbes=()
            if trois_d
            else tuple(
                self.liste_courbes.item(rang).text()
                for rang in range(self.liste_courbes.count())
                if self.liste_courbes.item(rang).checkState() == Qt.CheckState.Checked
            ),
        )

    def _proposer_z(self) -> None:
        """Au passage en 3D, propose pour z une colonne que x et y n'utilisent pas."""
        prises = {self.champ_x.currentText(), self.champ_y_3d.currentText()}
        for rang in range(self.champ_z.count()):
            if self.champ_z.itemText(rang) not in prises:
                self.champ_z.setCurrentIndex(rang)
                return

    def _mode_change(self) -> None:
        if self._remplissage:
            return
        self._remplissage = True
        if self.champ_mode.currentData() == "3d":
            coches = self._colonnes_y_cochees()
            if coches:
                self.champ_y_3d.setCurrentText(coches[0])
            self._proposer_z()
        self._remplissage = False
        self._reglage_change()

    def _reglage_change(self) -> None:
        if self._remplissage:
            return
        self._adapter_au_mode()
        try:
            self.reglages = self._lire_reglages()
            self._configurer_panneau()
        except ValidationError:
            # Rien de coché : les derniers réglages valides restent, et le graphe l'explique.
            self._zone_toile().signaler("Cocher au moins une colonne pour l'axe y.")
            return
        self.tracer_si_visible()
        self.modifie.emit()

    # --- Contrat d'un éditeur -------------------------------------------------------------

    def grandeurs_possedees(self) -> list[str]:
        return list(self._noms_possedes)

    def grandeurs(self) -> list[Grandeur]:
        return list(self.panneau.indicateurs)

    def valeur(self) -> Onglet:
        noms = tuple(i.nom for i in self.panneau.indicateurs)
        if self.reglages is None:
            if noms == tuple(getattr(self.onglet, "indicateurs", ())):
                return self.onglet
            return self.onglet.model_copy(update={"indicateurs": noms})
        # Les réglages remplacent les anciennes références : renommer une colonne ne doit
        # pas empêcher d'enregistrer la page.
        return self.onglet.model_copy(
            update={"reglages": self.reglages, "grandeurs": (), "indicateurs": noms}
        )

    def noter_saisies_incompletes(self, incompletes: Sequence[str]) -> None:
        manque = incompletes[0] if incompletes else None
        if manque != self._manque:
            self._manque = manque
            if self.toile is not None and self.toile.donnees is None:
                self.tracer_si_visible()

    def enregistre(self, page: Page) -> None:
        super().enregistre(page)
        self.onglet = self.valeur()
        self._noms_possedes = [i.nom for i in self.panneau.indicateurs]
        self._remplir()
        self._configurer_panneau()
        self.tracer_si_visible()

    # --- Dessin ---------------------------------------------------------------------------

    def demander_capture(self) -> None:
        """Garde une image de ce graphe dans la page, et l'insère dans les notes."""
        reglages = self._reglages_affiches()
        if reglages is None or self.toile is None:
            self.message_capture.setText("Aucun graphe à capturer pour l'instant.")
            return
        self.message_capture.setText(
            capturer_graphe(
                self.toile,
                self,
                reglages.tableau,
                self.lire_tableau,
                self.champ_k.value(),
            )
        )

    def _zone_toile(self) -> QWidget:
        if self.toile is None:
            self.toile = creer_toile()
            self.zone.addWidget(self.toile)
        return self.toile

    def tracer(self) -> None:
        """Dessine le graphe avec les réglages actuels et le tableau enregistré."""
        self._a_tracer = False
        dessiner(
            self._zone_toile(),
            self.archive,
            self._page_affichee(),
            self._reglages_affiches(),
            self.champ_k.value(),
            self._manque,
        )

    def tracer_si_visible(self) -> None:
        """Un onglet caché attend d'être affiché : ouvrir une page reste ainsi rapide."""
        if self.isVisible():
            self.tracer()
        else:
            self._a_tracer = True

    def showEvent(self, evenement: QShowEvent) -> None:  # noqa: N802
        super().showEvent(evenement)
        if self._a_tracer:
            self.tracer()


declarer_onglet("graphes", "Graphes", OngletGraphes, EditeurGraphes)
