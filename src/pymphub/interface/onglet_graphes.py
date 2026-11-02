"""Onglet de graphes : une colonne contre une autre, en 2D ou en 3D.

Les réglages sont gardés dans la page. Le graphe montre la dernière version enregistrée du
tableau. Le facteur k ne change que l'affichage, et ne crée donc aucun enregistrement.
"""

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
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import Archive
from pymphub.graphes import (
    GrapheImpossible,
    preparer_graphe,
    reglages_par_defaut,
    tableaux_tracables,
)
from pymphub.interface.messages import message_erreur
from pymphub.interface.onglets import EditeurOnglet, declarer_onglet
from pymphub.page import Onglet, OngletGraphes, Page, ReglagesGraphe

_LARGEUR_REGLAGES = 270
_MESSAGE_SANS_TABLEAU = (
    "Il faut d'abord un tableau enregistré avec au moins deux colonnes numériques."
)


def creer_toile(interactif: bool = True) -> QWidget:
    """Crée la zone de dessin. matplotlib, long à charger, ne s'importe qu'à ce moment."""
    from pymphub.interface.graphique import ToileGraphe  # noqa: PLC0415

    return ToileGraphe(interactif)


def dessiner(
    toile: QWidget,
    archive: Archive,
    page: Page,
    reglages: ReglagesGraphe | None,
    k: float,
) -> None:
    """Trace un graphe, ou explique pourquoi c'est impossible, sans jamais planter."""
    if reglages is None:
        toile.signaler(_MESSAGE_SANS_TABLEAU)
        return
    try:
        donnees = {}
        if reglages.tableau in tableaux_tracables(page):
            donnees[reglages.tableau] = archive.lire_tableau(page.id, reglages.tableau)
        toile.tracer(preparer_graphe(page, donnees, reglages, k))
    except (GrapheImpossible, ValueError, KeyError, OSError) as erreur:
        toile.signaler(message_erreur(erreur))


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
        gauche.addStretch()
        conteneur = QWidget()
        conteneur.setLayout(gauche)
        conteneur.setFixedWidth(_LARGEUR_REGLAGES)

        self.zone = QVBoxLayout()
        disposition = QHBoxLayout(self)
        disposition.addWidget(conteneur)
        disposition.addLayout(self.zone, stretch=1)

        self.champ_tableau.currentIndexChanged.connect(self._tableau_change)
        self.champ_mode.currentIndexChanged.connect(self._mode_change)
        for liste in (self.champ_x, self.champ_y_3d, self.champ_z):
            liste.currentIndexChanged.connect(self._reglage_change)
        self.liste_y.itemChanged.connect(self._reglage_change)
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

    # --- Chargement -----------------------------------------------------------------------

    def charger(self, archive: Archive) -> None:
        self.archive = archive
        self._remplir()

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
        except ValidationError:
            # Rien de coché : les derniers réglages valides restent, et le graphe l'explique.
            self._zone_toile().signaler("Cocher au moins une colonne pour l'axe y.")
            return
        self.tracer_si_visible()
        self.modifie.emit()

    # --- Contrat d'un éditeur -------------------------------------------------------------

    def valeur(self) -> Onglet:
        if self.reglages is None:
            return self.onglet
        # Les réglages remplacent les anciennes références : renommer une colonne ne doit
        # pas empêcher d'enregistrer la page.
        return self.onglet.model_copy(
            update={"reglages": self.reglages, "grandeurs": ()}
        )

    def enregistre(self, page: Page) -> None:
        super().enregistre(page)
        self.onglet = self.valeur()
        self._remplir()
        self.tracer_si_visible()

    # --- Dessin ---------------------------------------------------------------------------

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
            self.page,
            self._reglages_affiches(),
            self.champ_k.value(),
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
