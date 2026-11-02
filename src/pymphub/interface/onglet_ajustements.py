"""Onglet d'ajustement : un modèle ajusté sur deux colonnes, et des indicateurs de courbe.

L'ajustement se fait à la demande et reste figé : si les points changent, l'onglet le signale.
Les coefficients, la courbe et les indicateurs sont enregistrés dans la page, et les valeurs
brutes ne changent jamais. Le facteur k ne change que l'affichage.
"""

import pandas as pd
from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pymphub.ajustements import (
    MODELES,
    coefficients_de,
    creer_ajustement,
    creer_courbe,
    etat_resultat,
    lire_points,
    unites_des_coefficients,
)
from pymphub.archive import Archive
from pymphub.calculs import ErreurCalcul
from pymphub.edition import Grandeur, lire_nombre, nom_libre
from pymphub.graphes import GrapheImpossible, preparer_graphe, tableaux_tracables
from pymphub.interface.affichage_ajustements import (
    COLONNES_COEFFICIENTS,
    ecrire_coefficients,
    remplir_coefficients,
    remplir_correlation,
    resume_ajustement,
)
from pymphub.interface.messages import message_erreur
from pymphub.interface.onglet_graphes import capturer_graphe, creer_toile
from pymphub.interface.onglets import EditeurOnglet, declarer_onglet
from pymphub.interface.panneau_indicateurs import PanneauIndicateurs
from pymphub.interface.texte_en_grand import afficher_en_grand
from pymphub.page import (
    Ajustement,
    Indicateur,
    Onglet,
    OngletDonnees,
    Page,
    ReglagesGraphe,
    Tableau,
)

_LARGEUR_REGLAGES = 330
_PERSONNALISE = "personnalise"
_COLONNE_DEPART = 1
_COLONNE_UNITE = 2


def _table_lecture(colonnes: tuple[str, ...] | list[str]) -> QTableWidget:
    table = QTableWidget(0, len(colonnes))
    table.setHorizontalHeaderLabels(list(colonnes))
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return table


class EditeurAjustements(EditeurOnglet):
    """Règle un ajustement, montre son résultat et calcule des indicateurs."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        self.archive: Archive | None = None
        self.ajustement: Ajustement | None = None
        self.tableau_courbe: Tableau | None = None
        self.donnees_courbe: dict[str, list[float]] | None = None
        self.toile: QWidget | None = None
        self._a_tracer = True
        self._remplissage = True
        self._noms_possedes: list[str] = []
        self._references_conservees: list[str] = []
        self._avertissements: tuple[str, ...] = ()

        self.champ_nom = QLineEdit()
        self.champ_nom.setPlaceholderText("nom de l'ajustement, par exemple fit")
        self.champ_tableau = QComboBox()
        self.champ_x = QComboBox()
        self.champ_y = QComboBox()
        self.champ_modele = QComboBox()
        for cle, modele in MODELES.items():
            self.champ_modele.addItem(modele.libelle, cle)
        self.champ_modele.addItem("Formule personnalisée", _PERSONNALISE)
        self.champ_formule = QLineEdit()
        self.table_depart = QTableWidget(0, 3)
        self.table_depart.setHorizontalHeaderLabels(["Coefficient", "Départ", "Unité"])
        self.table_depart.verticalHeader().setVisible(False)
        self.table_depart.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table_depart.setMaximumHeight(150)
        self.champ_commentaire = QLineEdit()
        self.champ_commentaire.setPlaceholderText("Commentaire")
        afficher_en_grand(self.champ_commentaire, "Commentaire de l'ajustement")
        self.bouton_ajuster = QPushButton("Ajuster")
        self.bouton_ajuster.setObjectName("principal")
        self.bouton_ajuster.clicked.connect(self.demander_ajustement)
        self.bouton_retirer = QPushButton("Retirer l'ajustement")
        self.bouton_retirer.clicked.connect(self.retirer_ajustement)
        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setVisible(False)
        indication = QLabel(
            "L'ajustement utilise la dernière version enregistrée du tableau, et reste "
            "figé tant qu'on ne le refait pas."
        )
        indication.setObjectName("discret")
        indication.setWordWrap(True)

        formulaire = QFormLayout()
        formulaire.addRow("Nom", self.champ_nom)
        formulaire.addRow("Tableau", self.champ_tableau)
        formulaire.addRow("x", self.champ_x)
        formulaire.addRow("y", self.champ_y)
        formulaire.addRow("Modèle", self.champ_modele)
        formulaire.addRow("y =", self.champ_formule)
        gauche = QVBoxLayout()
        gauche.addLayout(formulaire)
        gauche.addWidget(self.table_depart)
        gauche.addWidget(self.champ_commentaire)
        gauche.addWidget(self.bouton_ajuster)
        gauche.addWidget(self.bouton_retirer)
        gauche.addWidget(self.message)
        gauche.addWidget(indication)
        gauche.addStretch()
        conteneur = QWidget()
        conteneur.setLayout(gauche)
        self.reglages = QScrollArea()
        self.reglages.setWidgetResizable(True)
        self.reglages.setWidget(conteneur)
        self.reglages.setFixedWidth(_LARGEUR_REGLAGES)
        self.bouton_reglages = QPushButton("Masquer les réglages")
        self.bouton_reglages.setCheckable(True)
        self.bouton_reglages.toggled.connect(self._replier_reglages)
        self.bouton_capture = QPushButton("Capturer dans les notes")
        self.bouton_capture.setToolTip(
            "Garde une image figée de ce graphe dans la page, et l'insère dans les notes"
        )
        self.bouton_capture.clicked.connect(self.demander_capture)

        self.champ_k = QDoubleSpinBox()
        self.champ_k.setRange(0.1, 10.0)
        self.champ_k.setSingleStep(0.5)
        self.champ_k.setValue(2.0)
        self.champ_k.setToolTip(
            "Facteur d'élargissement : il change seulement l'affichage."
        )
        self.champ_k.valueChanged.connect(self._k_change)

        self.coefficients = QLabel("Aucun ajustement.")
        self.coefficients.setWordWrap(True)
        self.coefficients.setTextFormat(Qt.TextFormat.PlainText)
        self.coefficients.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.alerte = QLabel("")
        self.alerte.setObjectName("alerte")
        self.alerte.setWordWrap(True)
        self.alerte.setTextFormat(Qt.TextFormat.PlainText)
        self.alerte.setVisible(False)
        self.zone_toile = QVBoxLayout()
        page_resultat = QWidget()
        disposition_resultat = QVBoxLayout(page_resultat)
        disposition_resultat.addWidget(self.coefficients)
        disposition_resultat.addWidget(self.alerte)
        disposition_resultat.addLayout(self.zone_toile, stretch=1)

        self.resume = QLabel("Aucun ajustement.")
        self.resume.setObjectName("discret")
        self.resume.setWordWrap(True)
        self.resume.setTextFormat(Qt.TextFormat.PlainText)
        self.table_coefficients = _table_lecture(COLONNES_COEFFICIENTS)
        self.table_correlation = _table_lecture(["Corrélation"])
        page_details = QWidget()
        disposition_details = QVBoxLayout(page_details)
        disposition_details.addWidget(self.resume)
        disposition_details.addWidget(self.table_coefficients, stretch=1)
        disposition_details.addWidget(self.table_correlation, stretch=1)

        self.panneau = PanneauIndicateurs(self.champ_k.value, preferer_ajustement=True)
        self.panneau.modifie.connect(self.modifie)
        self.panneau.reperes_changes.connect(self.tracer_si_visible)

        self.vues = QTabWidget()
        self.vues.addTab(page_resultat, "Résultat")
        self.vues.addTab(page_details, "Détails")
        self.vues.addTab(self.panneau, "Indicateurs")
        self.vues.setCornerWidget(self._zone_k())

        disposition = QHBoxLayout(self)
        disposition.addWidget(self.reglages)
        disposition.addWidget(self.vues, stretch=1)

        self.champ_tableau.currentIndexChanged.connect(self._tableau_change)
        self.champ_modele.currentIndexChanged.connect(self._modele_change)
        self.champ_formule.textChanged.connect(self._formule_change)
        self.champ_x.currentIndexChanged.connect(self._colonnes_changent)
        self.champ_y.currentIndexChanged.connect(self._colonnes_changent)
        self.champ_commentaire.textChanged.connect(self._commentaire_change)
        self._remplissage = False

    def _zone_k(self) -> QWidget:
        zone = QWidget()
        ligne = QHBoxLayout(zone)
        ligne.setContentsMargins(0, 0, 0, 0)
        ligne.addWidget(self.bouton_capture)
        ligne.addWidget(self.bouton_reglages)
        ligne.addWidget(QLabel("k ="))
        ligne.addWidget(self.champ_k)
        return zone

    def _replier_reglages(self, replie: bool) -> None:
        """Laisse toute la largeur au graphique."""
        self.reglages.setVisible(not replie)
        self.bouton_reglages.setText(
            "Afficher les réglages" if replie else "Masquer les réglages"
        )

    @property
    def indicateurs(self) -> list[Indicateur]:
        return self.panneau.indicateurs

    def _configurer_panneau(self) -> None:
        if self.page is None:
            return
        self.panneau.configurer(
            self.page,
            self.lire_tableau,
            (
                self.champ_tableau.currentText(),
                self.champ_x.currentText(),
                [self.champ_y.currentText()] if self.champ_y.currentText() else [],
            ),
            [self.ajustement] if self.ajustement is not None else [],
            self._noms_reserves,
        )

    def _noms_reserves(self) -> set[str]:
        noms = self._noms_de_la_page()
        if self.ajustement is not None:
            noms |= {self.ajustement.nom, self.ajustement.courbe}
        return noms

    # --- Chargement -----------------------------------------------------------------------

    def lire_tableau(self, nom: str) -> pd.DataFrame:
        """Relit un tableau enregistré de la page."""
        return self.archive.lire_tableau(self.page.id, nom)

    def charger(self, archive: Archive) -> None:
        self.archive = archive
        par_nom = {g.nom: g for g in self.page.mesures}
        references = getattr(self.onglet, "grandeurs", ())
        for reference in references:
            grandeur = par_nom.get(reference)
            if isinstance(grandeur, Ajustement) and self.ajustement is None:
                self.ajustement = grandeur
            elif isinstance(grandeur, Indicateur):
                self.panneau.indicateurs.append(grandeur)
            elif not isinstance(grandeur, Tableau):
                self._references_conservees.append(reference)
        if self.ajustement is not None:
            courbe = par_nom.get(self.ajustement.courbe)
            self.tableau_courbe = courbe if isinstance(courbe, Tableau) else None
        self._noms_possedes = [g.nom for g in self.grandeurs()]
        self._remplir_reglages()
        self._configurer_panneau()
        self._afficher_resultat()

    def _remplir_colonnes(self, table: str) -> None:
        colonnes = tableaux_tracables(self.page).get(table, [])
        for liste in (self.champ_x, self.champ_y):
            liste.clear()
            liste.addItems(colonnes)
        if len(colonnes) >= 2:
            self.champ_y.setCurrentIndex(1)

    def _remplir_reglages(self) -> None:
        """Montre les réglages de l'ajustement, ou propose les premières colonnes."""
        self._remplissage = True
        tables = tableaux_tracables(self.page) if self.page is not None else {}
        self.champ_tableau.clear()
        self.champ_tableau.addItems(list(tables))
        ajustement = self.ajustement
        if ajustement is not None:
            self.champ_tableau.setCurrentText(ajustement.tableau)
        self._remplir_colonnes(self.champ_tableau.currentText())
        if ajustement is not None:
            self.champ_nom.setText(ajustement.nom)
            self.champ_x.setCurrentText(ajustement.x)
            self.champ_y.setCurrentText(ajustement.y)
            self.champ_commentaire.setText(ajustement.commentaire)
            cle = next(
                (
                    c
                    for c, m in MODELES.items()
                    if m.libelle == ajustement.modele
                    and m.formule == ajustement.formule
                ),
                _PERSONNALISE,
            )
            self.champ_modele.setCurrentIndex(self.champ_modele.findData(cle))
            self.champ_formule.setText(ajustement.formule)
        else:
            self.champ_modele.setCurrentIndex(0)
            self.champ_formule.setText(MODELES["droite"].formule)
        self.champ_formule.setReadOnly(self.champ_modele.currentData() != _PERSONNALISE)
        self._remplir_departs()
        self._remplissage = False

    def _remplir_departs(self) -> None:
        """Liste les coefficients. L'unité d'un modèle usuel se déduit des colonnes."""
        anciens = {
            self.table_depart.item(i, 0).text(): (
                self.table_depart.item(i, _COLONNE_DEPART).text(),
                self.table_depart.item(i, _COLONNE_UNITE).text(),
            )
            for i in range(self.table_depart.rowCount())
        }
        cle = self.champ_modele.currentData()
        try:
            noms = coefficients_de(self.champ_formule.text())
        except ErreurCalcul:
            noms = []
        unites = self._unites_deduites(cle)
        enregistres = (
            {p.nom: p.unite for p in self.ajustement.parametres}
            if self.ajustement is not None
            else {}
        )
        self.table_depart.setRowCount(len(noms))
        for rang, nom in enumerate(noms):
            depart, unite = anciens.get(nom, ("", enregistres.get(nom, "")))
            colonne_nom = QTableWidgetItem(nom)
            colonne_nom.setFlags(colonne_nom.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_depart.setItem(rang, 0, colonne_nom)
            self.table_depart.setItem(rang, _COLONNE_DEPART, QTableWidgetItem(depart))
            element = QTableWidgetItem(unites.get(nom, unite))
            if cle != _PERSONNALISE:
                element.setFlags(element.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table_depart.setItem(rang, _COLONNE_UNITE, element)

    def _unites_deduites(self, cle: str) -> dict[str, str]:
        if cle == _PERSONNALISE or self.page is None:
            return {}
        try:
            points = lire_points(
                self.page,
                self.champ_tableau.currentText(),
                self.champ_x.currentText(),
                self.champ_y.currentText(),
                self.lire_tableau,
            )
            return unites_des_coefficients(MODELES[cle], points.x.unite, points.y.unite)
        except (ErreurCalcul, ValueError, KeyError, OSError):
            return {}

    # --- Réglages -------------------------------------------------------------------------

    def _tableau_change(self) -> None:
        if not self._remplissage:
            self._remplissage = True
            self._remplir_colonnes(self.champ_tableau.currentText())
            self._remplissage = False
            self._remplir_departs()
            self._configurer_panneau()

    def _modele_change(self) -> None:
        if self._remplissage:
            return
        cle = self.champ_modele.currentData()
        self.champ_formule.setReadOnly(cle != _PERSONNALISE)
        if cle != _PERSONNALISE:
            self.champ_formule.setText(MODELES[cle].formule)
        self._remplir_departs()

    def _formule_change(self) -> None:
        if not self._remplissage:
            self._remplir_departs()

    def _colonnes_changent(self) -> None:
        if not self._remplissage:
            self._remplir_departs()
            self._configurer_panneau()

    def _commentaire_change(self) -> None:
        if self._remplissage or self.ajustement is None:
            return
        self.ajustement = self.ajustement.model_copy(
            update={"commentaire": self.champ_commentaire.text()}
        )
        self.modifie.emit()

    def _signaler(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def _lire_departs(self) -> tuple[dict[str, float], dict[str, str]]:
        departs: dict[str, float] = {}
        unites: dict[str, str] = {}
        for rang in range(self.table_depart.rowCount()):
            nom = self.table_depart.item(rang, 0).text()
            texte = self.table_depart.item(rang, _COLONNE_DEPART).text().strip()
            if texte:
                departs[nom] = lire_nombre(texte)
            unites[nom] = self.table_depart.item(rang, _COLONNE_UNITE).text()
        return departs, unites

    def _noms_de_la_page(self) -> set[str]:
        return {g.nom for g in self.page.mesures} - set(self._noms_possedes)

    def demander_ajustement(self) -> bool:
        """Ajuste le modèle choisi et garde le résultat. Une erreur est expliquée."""
        if self.page is None:
            return False
        nom = self.champ_nom.text().strip() or nom_libre(
            "ajustement", self._noms_de_la_page()
        )
        cle = self.champ_modele.currentData()
        try:
            departs, unites = self._lire_departs()
            points = lire_points(
                self.page,
                self.champ_tableau.currentText(),
                self.champ_x.currentText(),
                self.champ_y.currentText(),
                self.lire_tableau,
            )
            ajustement, resultat = creer_ajustement(
                points,
                nom,
                self.champ_formule.text(),
                None if cle == _PERSONNALISE else cle,
                departs,
                unites,
                self.champ_commentaire.text(),
            )
            courbe, donnees = creer_courbe(ajustement)
        except (ValueError, KeyError, OSError) as erreur:
            self._signaler(message_erreur(erreur))
            return False
        self._signaler("")
        self.champ_nom.setText(nom)
        self.ajustement = ajustement
        self.tableau_courbe = courbe
        self.donnees_courbe = donnees
        self._avertissements = resultat.avertissements
        self._configurer_panneau()
        self._afficher_resultat()
        self.tracer_si_visible()
        self.modifie.emit()
        return True

    def retirer_ajustement(self) -> None:
        """Retire l'ajustement, et les indicateurs calculés sur lui : l'historique les garde."""
        if self.ajustement is None:
            return
        nom = self.ajustement.nom
        self.ajustement = None
        self.tableau_courbe = None
        self.donnees_courbe = None
        self._avertissements = ()
        self.panneau.retirer_ceux_de(nom)
        self._configurer_panneau()
        self._afficher_resultat()
        self.tracer_si_visible()
        self.modifie.emit()

    # --- Affichage ------------------------------------------------------------------------

    def _k_change(self) -> None:
        self._afficher_resultat()
        self.tracer_si_visible()

    def _etats(self) -> list[str]:
        """Dit si l'ajustement n'est plus à jour avec les points."""
        etats: list[str] = []
        if self.page is None or self.archive is None:
            return etats
        if self.ajustement is not None:
            etat = etat_resultat(self.page, self.ajustement, self.lire_tableau)
            if etat is not None:
                etats.append(f"{self.ajustement.nom} : {etat}")
        return etats

    def _afficher_resultat(self) -> None:
        ajustement = self.ajustement
        k = self.champ_k.value()
        self.resume.setText(
            resume_ajustement(ajustement) if ajustement else "Aucun ajustement."
        )
        self.coefficients.setText(
            ecrire_coefficients(ajustement, k) if ajustement else "Aucun ajustement."
        )
        remplir_coefficients(self.table_coefficients, ajustement, self.champ_k.value())
        remplir_correlation(self.table_correlation, ajustement)
        self.bouton_retirer.setEnabled(ajustement is not None)
        messages = [*self._avertissements, *self._etats()]
        self.alerte.setText(" ".join(messages))
        self.alerte.setVisible(bool(messages))
        self.panneau.afficher()

    def demander_capture(self) -> None:
        """Garde une image de ce graphe dans la page, et l'insère dans les notes."""
        if self.ajustement is None or self.toile is None:
            self._signaler("Aucun graphe à capturer pour l'instant.")
            return
        self.vues.setCurrentIndex(0)
        message = capturer_graphe(
            self.toile,
            self,
            self.ajustement.tableau,
            self.lire_tableau,
            self.champ_k.value(),
        )
        self.coefficients.setText(
            f"{ecrire_coefficients(self.ajustement, self.champ_k.value())} — {message}"
        )

    def _zone_de_dessin(self) -> QWidget:
        if self.toile is None:
            self.toile = creer_toile()
            self.zone_toile.addWidget(self.toile)
        return self.toile

    def tracer(self) -> None:
        """Superpose la courbe aux points, y compris un résultat pas encore enregistré."""
        self._a_tracer = False
        toile = self._zone_de_dessin()
        ajustement = self.ajustement
        if ajustement is None or self.tableau_courbe is None:
            toile.signaler("Choisir un modèle, puis cliquer sur « Ajuster ».")
            return
        reglages = ReglagesGraphe(
            tableau=ajustement.tableau,
            x=ajustement.x,
            y=(ajustement.y,),
            courbes=(ajustement.nom,),
            # Relier les points brouillerait la lecture de la courbe ajustée.
            relier=False,
        )
        page = self.page.model_copy(
            update={
                "mesures": tuple(
                    g
                    for g in self.page.mesures
                    if g.nom
                    not in (
                        ajustement.nom,
                        ajustement.courbe,
                        *(i.nom for i in self.indicateurs),
                    )
                )
                + (ajustement, self.tableau_courbe, *self.indicateurs)
            }
        )
        try:
            courbe = (
                pd.DataFrame(self.donnees_courbe)
                if self.donnees_courbe is not None
                else self.lire_tableau(ajustement.courbe)
            )
            toile.tracer(
                preparer_graphe(
                    page,
                    {
                        ajustement.tableau: self.lire_tableau(ajustement.tableau),
                        ajustement.courbe: courbe,
                    },
                    reglages,
                    self.champ_k.value(),
                )
            )
        except (GrapheImpossible, ValueError, KeyError, OSError) as erreur:
            toile.signaler(message_erreur(erreur))

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

    # --- Contrat d'un éditeur -------------------------------------------------------------

    def grandeurs_possedees(self) -> list[str]:
        return list(self._noms_possedes)

    def grandeurs(self) -> list[Grandeur]:
        resultat: list[Grandeur] = []
        if self.ajustement is not None:
            resultat.append(self.ajustement)
            if self.tableau_courbe is not None:
                resultat.append(self.tableau_courbe)
        resultat.extend(self.panneau.indicateurs)
        return resultat

    def valeur(self) -> Onglet:
        noms = tuple(g.nom for g in self.grandeurs())
        return self.onglet.model_copy(
            update={"grandeurs": noms + tuple(self._references_conservees)}
        )

    def donnees_tableaux(self) -> dict[str, dict[str, list[float]]]:
        if self.donnees_courbe is None or self.tableau_courbe is None:
            return {}
        return {self.tableau_courbe.nom: self.donnees_courbe}

    def tableau_modifie(self) -> bool:
        return self.donnees_courbe is not None

    def enregistre(self, page: Page) -> None:
        """Le résultat est écrit : les nouvelles colonnes du tableau se proposent aussi."""
        super().enregistre(page)
        self.onglet = self.valeur()
        self.donnees_courbe = None
        self._noms_possedes = [g.nom for g in self.grandeurs()]
        selection = (
            self.champ_tableau.currentText(),
            self.champ_x.currentText(),
            self.champ_y.currentText(),
        )
        self._remplissage = True
        tables = tableaux_tracables(page)
        self.champ_tableau.clear()
        self.champ_tableau.addItems(list(tables))
        self.champ_tableau.setCurrentText(selection[0])
        self._remplir_colonnes(self.champ_tableau.currentText())
        self.champ_x.setCurrentText(selection[1])
        self.champ_y.setCurrentText(selection[2])
        self._remplissage = False
        self._remplir_departs()
        self._configurer_panneau()
        self._afficher_resultat()
        self.tracer_si_visible()


declarer_onglet("ajustements", "Ajustements", OngletDonnees, EditeurAjustements)
