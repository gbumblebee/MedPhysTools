"""Panneau d'indicateurs, commun aux onglets de graphes et d'ajustement.

L'onglet qui l'accueille fournit le tableau, les colonnes et les ajustements disponibles. Les
indicateurs restent des grandeurs de la page : ce panneau les calcule et les liste.
"""

from collections.abc import Callable, Sequence

import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from medphystools.ajustements import (
    METHODES_AJUSTEMENT,
    METHODES_EN_Y,
    METHODES_POINTS,
    creer_indicateur,
    etat_resultat,
)
from medphystools.calculs import ErreurCalcul
from medphystools.edition import lire_nombre, nom_libre
from medphystools.interface.affichage_ajustements import (
    COLONNES_INDICATEURS,
    libelle_methode,
    remplir_indicateurs,
)
from medphystools.interface.messages import message_erreur
from medphystools.interface.texte_en_grand import (
    afficher_en_grand,
    afficher_en_grand_colonne,
)
from medphystools.page import Ajustement, Indicateur, Page

_SOURCE_POINTS = "points"
_COLONNE_RESULTAT = 5
_COLONNE_COMMENTAIRE = 6
_COLONNE_REPERE = 7
_EXPLICATION = (
    "Sans intervalle, toute l'étendue des données est utilisée. Sur l'ajustement, "
    "l'intervalle ne peut pas sortir des données."
)
_AIDE_ECART_TYPE = (
    "Son incertitude est propagée depuis les incertitudes déclarées. Sur une courbe qui suit "
    "une tendance, il mesure surtout la tendance : il a du sens sur un plateau ou des "
    "mesures répétées."
)


class PanneauIndicateurs(QWidget):
    """Calcule et liste des indicateurs, sur les points ou sur un ajustement."""

    modifie = Signal()
    reperes_changes = Signal()

    def __init__(
        self,
        lire_k: Callable[[], float],
        preferer_ajustement: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.lire_k = lire_k
        self.preferer_ajustement = preferer_ajustement
        self.indicateurs: list[Indicateur] = []
        self.page: Page | None = None
        self.lire_tableau: Callable[[str], pd.DataFrame] | None = None
        self.colonnes: tuple[str, str, Sequence[str]] = ("", "", ())
        self.ajustements: list[Ajustement] = []
        self.noms_reserves: Callable[[], set[str]] = set
        self._remplissage = False

        self.champ_nom = QLineEdit()
        self.champ_nom.setPlaceholderText("nom")
        self.champ_y = QComboBox()
        self.champ_y.setToolTip("Colonne sur laquelle lire l'indicateur")
        self.champ_source = QComboBox()
        self.champ_methode = QComboBox()
        self.champ_parametre = QLineEdit()
        self.champ_de = QLineEdit()
        self.champ_de.setPlaceholderText("début")
        self.champ_a = QLineEdit()
        self.champ_a.setPlaceholderText("fin")
        self.champ_position = QLineEdit()
        self.champ_position.setPlaceholderText("x de la dérivée")
        self.champ_commentaire = QLineEdit()
        self.champ_commentaire.setPlaceholderText("Commentaire")
        afficher_en_grand(self.champ_commentaire, "Commentaire")
        self.bouton_calculer = QPushButton("Calculer")
        self.bouton_calculer.setObjectName("principal")
        self.bouton_calculer.clicked.connect(self.demander_indicateur)
        self.bouton_retirer = QPushButton("Retirer")
        self.bouton_retirer.clicked.connect(self.retirer_indicateur)
        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setVisible(False)
        self.alerte = QLabel("")
        self.alerte.setObjectName("alerte")
        self.alerte.setWordWrap(True)
        self.alerte.setTextFormat(Qt.TextFormat.PlainText)
        self.alerte.setVisible(False)
        self.table = QTableWidget(0, len(COLONNES_INDICATEURS) + 1)
        self.table.setHorizontalHeaderLabels([*COLONNES_INDICATEURS, "Sur le graphe"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        entete = self.table.horizontalHeader()
        entete.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        entete.setSectionResizeMode(
            _COLONNE_COMMENTAIRE, QHeaderView.ResizeMode.Stretch
        )
        afficher_en_grand_colonne(self.table, _COLONNE_COMMENTAIRE, "Commentaire")
        self.table.itemChanged.connect(self._repere_coche)

        ligne_un = QHBoxLayout()
        for champ in (
            self.champ_nom,
            self.champ_y,
            self.champ_source,
            self.champ_methode,
            self.champ_parametre,
        ):
            ligne_un.addWidget(champ)
        ligne_deux = QHBoxLayout()
        for champ in (
            self.champ_de,
            self.champ_a,
            self.champ_position,
            self.champ_commentaire,
            self.bouton_calculer,
        ):
            ligne_deux.addWidget(champ)
        explication = QLabel(_EXPLICATION)
        explication.setObjectName("discret")
        explication.setWordWrap(True)
        disposition = QVBoxLayout(self)
        disposition.addLayout(ligne_un)
        disposition.addLayout(ligne_deux)
        disposition.addWidget(explication)
        disposition.addWidget(self.message)
        disposition.addWidget(self.alerte)
        disposition.addWidget(self.table, stretch=1)
        disposition.addWidget(
            self.bouton_retirer, alignment=Qt.AlignmentFlag.AlignRight
        )

        self.champ_source.currentIndexChanged.connect(self._source_changee)
        self.champ_methode.currentIndexChanged.connect(self._methode_changee)
        self.actualiser_sources()

    # --- Sources --------------------------------------------------------------------------

    def configurer(
        self,
        page: Page,
        lire_tableau: Callable[[str], pd.DataFrame],
        colonnes: tuple[str, str, Sequence[str]],
        ajustements: Sequence[Ajustement],
        noms_reserves: Callable[[], set[str]],
    ) -> None:
        """Dit sur quoi calculer : tableau, x, colonnes y et ajustements disponibles."""
        self.page = page
        self.lire_tableau = lire_tableau
        self.colonnes = colonnes
        self.ajustements = list(ajustements)
        self.noms_reserves = noms_reserves
        self.actualiser_sources()
        self.afficher()

    def actualiser_sources(self) -> None:
        self._remplissage = True
        sans_ajustement_avant = self.champ_source.count() <= 1
        choisie = self.champ_source.currentData()
        y_choisi = self.champ_y.currentText()
        self.champ_y.clear()
        self.champ_y.addItems(list(self.colonnes[2]))
        if y_choisi:
            self.champ_y.setCurrentText(y_choisi)
        self.champ_y.setVisible(self.champ_y.count() > 1)
        self.champ_source.clear()
        self.champ_source.addItem("Points", _SOURCE_POINTS)
        for ajustement in self.ajustements:
            self.champ_source.addItem(f"Ajustement {ajustement.nom}", ajustement.nom)
        rang = self.champ_source.findData(choisie) if choisie else -1
        # Un premier ajustement devient la source : c'est lui qu'on vient de faire.
        if self.preferer_ajustement and self.ajustements and sans_ajustement_avant:
            rang = 1
        rang = max(rang, 0)
        self.champ_source.setCurrentIndex(rang)
        self._remplissage = False
        self._source_changee()

    def _source_changee(self) -> None:
        if self._remplissage:
            return
        methodes = (
            METHODES_POINTS
            if self.champ_source.currentData() == _SOURCE_POINTS
            else METHODES_AJUSTEMENT
        )
        self._remplissage = True
        choisie = self.champ_methode.currentData()
        self.champ_methode.clear()
        for methode in methodes:
            self.champ_methode.addItem(libelle_methode(methode), methode)
        if choisie in methodes:
            self.champ_methode.setCurrentIndex(self.champ_methode.findData(choisie))
        self._remplissage = False
        self._methode_changee()

    def _methode_changee(self) -> None:
        if self._remplissage:
            return
        methode = self.champ_methode.currentData()
        self.champ_position.setVisible(methode == "derivee")
        self.champ_parametre.setVisible(methode in ("percentile", "abscisse_de_valeur"))
        self.champ_parametre.setPlaceholderText(
            "rang, de 0 à 100" if methode == "percentile" else "valeur de y"
        )
        self.champ_methode.setToolTip(
            _AIDE_ECART_TYPE
            if methode in ("ecart_type", "coefficient_variation")
            else ""
        )

    # --- Calcul ---------------------------------------------------------------------------

    @staticmethod
    def _lire_nombre(champ: QLineEdit) -> float | None:
        texte = champ.text().strip()
        return lire_nombre(texte) if texte else None

    def _ajustement_choisi(self) -> Ajustement | None:
        return next(
            (a for a in self.ajustements if a.nom == self.champ_source.currentData()),
            None,
        )

    def demander_indicateur(self) -> bool:
        """Calcule l'indicateur demandé. Une erreur est expliquée, et rien n'est gardé."""
        if self.page is None:
            return False
        methode = self.champ_methode.currentData()
        pris = self.noms_reserves() | {i.nom for i in self.indicateurs}
        nom = self.champ_nom.text().strip() or nom_libre(methode, pris)
        ajustement = self._ajustement_choisi()
        tableau, x, _ys = self.colonnes
        y = self.champ_y.currentText()
        if ajustement is not None:
            tableau, x, y = ajustement.tableau, ajustement.x, ajustement.y
        try:
            if nom in pris:
                raise ErreurCalcul(
                    f"Le nom « {nom} » est déjà utilisé dans cette page."
                )
            if not (tableau and x and y):
                raise ErreurCalcul("Choisir d'abord un tableau et ses colonnes.")
            indicateur = creer_indicateur(
                self.page,
                nom,
                methode,
                "ajustement" if ajustement is not None else "points",
                tableau,
                x,
                y,
                self.lire_tableau,
                self._lire_nombre(self.champ_de),
                self._lire_nombre(self.champ_a),
                self._lire_nombre(self.champ_position),
                ajustement,
                self.champ_commentaire.text(),
                self._lire_nombre(self.champ_parametre),
            )
        except (ValueError, KeyError, OSError) as erreur:
            self.message.setText(message_erreur(erreur))
            self.message.setVisible(True)
            return False
        self.message.setVisible(False)
        self.indicateurs.append(indicateur)
        self.champ_nom.clear()
        self.champ_commentaire.clear()
        self.afficher()
        self.modifie.emit()
        return True

    def retirer_indicateur(self) -> None:
        """Retire l'indicateur choisi : l'historique le garde."""
        rang = self.table.currentRow()
        if 0 <= rang < len(self.indicateurs):
            del self.indicateurs[rang]
            self.afficher()
            self.modifie.emit()
            self.reperes_changes.emit()

    def retirer_ceux_de(self, ajustement: str) -> None:
        """Retire les indicateurs calculés sur un ajustement retiré."""
        self.indicateurs = [i for i in self.indicateurs if i.ajustement != ajustement]
        self.afficher()

    # --- Affichage ------------------------------------------------------------------------

    def afficher(self) -> None:
        """Écrit la liste au facteur k choisi, et signale ce qui n'est plus à jour."""
        self._remplissage = True
        remplir_indicateurs(self.table, self.indicateurs, self.lire_k())
        for rang, indicateur in enumerate(self.indicateurs):
            case = QTableWidgetItem()
            if indicateur.methode in METHODES_EN_Y:
                case.setFlags(
                    Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
                )
                case.setCheckState(
                    Qt.CheckState.Checked
                    if indicateur.afficher
                    else Qt.CheckState.Unchecked
                )
            else:
                case.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(rang, _COLONNE_REPERE, case)
        self._remplissage = False
        etats = []
        if self.page is not None and self.lire_tableau is not None:
            for indicateur in self.indicateurs:
                etat = etat_resultat(self.page, indicateur, self.lire_tableau)
                if etat is not None:
                    etats.append(f"{indicateur.nom} : {etat}")
        self.alerte.setText(" ".join(etats))
        self.alerte.setVisible(bool(etats))

    def _repere_coche(self, element: QTableWidgetItem) -> None:
        if self._remplissage or element.column() != _COLONNE_REPERE:
            return
        rang = element.row()
        self.indicateurs[rang] = self.indicateurs[rang].model_copy(
            update={"afficher": element.checkState() == Qt.CheckState.Checked}
        )
        self.modifie.emit()
        self.reperes_changes.emit()
