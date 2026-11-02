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
    METHODES,
    MODELES,
    coefficients_de,
    creer_ajustement,
    creer_courbe,
    creer_indicateur,
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
    COLONNES_INDICATEURS,
    libelle_methode,
    remplir_coefficients,
    remplir_correlation,
    remplir_indicateurs,
    resume_ajustement,
)
from pymphub.interface.messages import message_erreur
from pymphub.interface.onglet_graphes import creer_toile
from pymphub.interface.onglets import EditeurOnglet, declarer_onglet
from pymphub.interface.texte_en_grand import (
    afficher_en_grand,
    afficher_en_grand_colonne,
)
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
_COLONNE_RESULTAT = 5


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
        self.indicateurs: list[Indicateur] = []
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
        defilement = QScrollArea()
        defilement.setWidgetResizable(True)
        defilement.setWidget(conteneur)
        defilement.setFixedWidth(_LARGEUR_REGLAGES)

        self.champ_k = QDoubleSpinBox()
        self.champ_k.setRange(0.1, 10.0)
        self.champ_k.setSingleStep(0.5)
        self.champ_k.setValue(2.0)
        self.champ_k.setToolTip(
            "Facteur d'élargissement : il change seulement l'affichage."
        )
        self.champ_k.valueChanged.connect(self._k_change)

        self.resume = QLabel("Aucun ajustement.")
        self.resume.setObjectName("discret")
        self.resume.setWordWrap(True)
        self.resume.setTextFormat(Qt.TextFormat.PlainText)
        self.alerte = QLabel("")
        self.alerte.setObjectName("alerte")
        self.alerte.setWordWrap(True)
        self.alerte.setTextFormat(Qt.TextFormat.PlainText)
        self.alerte.setVisible(False)
        self.table_coefficients = _table_lecture(COLONNES_COEFFICIENTS)
        self.table_correlation = _table_lecture(["Corrélation"])
        ligne_tables = QHBoxLayout()
        ligne_tables.addWidget(self.table_coefficients, stretch=3)
        ligne_tables.addWidget(self.table_correlation, stretch=2)
        conteneur_tables = QWidget()
        conteneur_tables.setLayout(ligne_tables)
        conteneur_tables.setMaximumHeight(170)
        self.zone_toile = QVBoxLayout()
        page_resultat = QWidget()
        disposition_resultat = QVBoxLayout(page_resultat)
        disposition_resultat.addWidget(self.resume)
        disposition_resultat.addWidget(self.alerte)
        disposition_resultat.addWidget(conteneur_tables)
        disposition_resultat.addLayout(self.zone_toile, stretch=1)

        self.champ_nom_indicateur = QLineEdit()
        self.champ_nom_indicateur.setPlaceholderText("nom")
        self.champ_methode = QComboBox()
        for methode in METHODES:
            self.champ_methode.addItem(libelle_methode(methode), methode)
        self.champ_source = QComboBox()
        self.champ_source.addItem("Points", "points")
        self.champ_source.addItem("Ajustement", "ajustement")
        self.champ_de = QLineEdit()
        self.champ_de.setPlaceholderText("début")
        self.champ_a = QLineEdit()
        self.champ_a.setPlaceholderText("fin")
        self.champ_position = QLineEdit()
        self.champ_position.setPlaceholderText("x de la dérivée")
        self.champ_commentaire_indicateur = QLineEdit()
        self.champ_commentaire_indicateur.setPlaceholderText("Commentaire")
        afficher_en_grand(self.champ_commentaire_indicateur, "Commentaire")
        self.bouton_indicateur = QPushButton("Calculer")
        self.bouton_indicateur.setObjectName("principal")
        self.bouton_indicateur.clicked.connect(self.demander_indicateur)
        self.bouton_retirer_indicateur = QPushButton("Retirer")
        self.bouton_retirer_indicateur.clicked.connect(self.retirer_indicateur)
        self.message_indicateur = QLabel("")
        self.message_indicateur.setObjectName("alerte")
        self.message_indicateur.setWordWrap(True)
        self.message_indicateur.setTextFormat(Qt.TextFormat.PlainText)
        self.message_indicateur.setVisible(False)
        self.table_indicateurs = _table_lecture(COLONNES_INDICATEURS)
        self.table_indicateurs.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        afficher_en_grand_colonne(self.table_indicateurs, 6, "Commentaire")
        # Le résultat est ce qu'on vient lire : il s'affiche toujours en entier.
        self.table_indicateurs.horizontalHeader().setSectionResizeMode(
            _COLONNE_RESULTAT, QHeaderView.ResizeMode.ResizeToContents
        )
        ligne_indicateur = QHBoxLayout()
        for champ in (
            self.champ_nom_indicateur,
            self.champ_methode,
            self.champ_source,
            self.champ_de,
            self.champ_a,
            self.champ_position,
            self.champ_commentaire_indicateur,
            self.bouton_indicateur,
        ):
            ligne_indicateur.addWidget(champ)
        explication = QLabel(
            "Sans intervalle, toute l'étendue des données est utilisée. Sur l'ajustement, "
            "l'intervalle ne peut pas sortir des données."
        )
        explication.setObjectName("discret")
        explication.setWordWrap(True)
        page_indicateurs = QWidget()
        disposition_indicateurs = QVBoxLayout(page_indicateurs)
        disposition_indicateurs.addLayout(ligne_indicateur)
        disposition_indicateurs.addWidget(explication)
        disposition_indicateurs.addWidget(self.message_indicateur)
        disposition_indicateurs.addWidget(self.table_indicateurs, stretch=1)
        disposition_indicateurs.addWidget(
            self.bouton_retirer_indicateur, alignment=Qt.AlignmentFlag.AlignRight
        )

        self.vues = QTabWidget()
        self.vues.addTab(page_resultat, "Résultat")
        self.vues.addTab(page_indicateurs, "Indicateurs")
        self.vues.setCornerWidget(self._zone_k())

        disposition = QHBoxLayout(self)
        disposition.addWidget(defilement)
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
        ligne.addWidget(QLabel("k ="))
        ligne.addWidget(self.champ_k)
        return zone

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
                self.indicateurs.append(grandeur)
            elif not isinstance(grandeur, Tableau):
                self._references_conservees.append(reference)
        if self.ajustement is not None:
            courbe = par_nom.get(self.ajustement.courbe)
            self.tableau_courbe = courbe if isinstance(courbe, Tableau) else None
        self._noms_possedes = [g.nom for g in self.grandeurs()]
        self._remplir_reglages()
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
        self.indicateurs = [i for i in self.indicateurs if i.ajustement != nom]
        self._afficher_resultat()
        self.tracer_si_visible()
        self.modifie.emit()

    # --- Indicateurs ----------------------------------------------------------------------

    def _lire_borne(self, champ: QLineEdit) -> float | None:
        texte = champ.text().strip()
        return lire_nombre(texte) if texte else None

    def demander_indicateur(self) -> bool:
        """Calcule un indicateur sur les colonnes choisies. Une erreur est expliquée."""
        if self.page is None:
            return False
        methode = self.champ_methode.currentData()
        pris = self._noms_de_la_page() | {i.nom for i in self.indicateurs}
        if self.ajustement is not None:
            pris |= {self.ajustement.nom, self.ajustement.courbe}
        nom = self.champ_nom_indicateur.text().strip() or nom_libre(methode, pris)
        source = self.champ_source.currentData()
        ajustement = self.ajustement if source == "ajustement" else None
        try:
            if nom in pris:
                raise ErreurCalcul(
                    f"Le nom « {nom} » est déjà utilisé dans cette page."
                )
            indicateur = creer_indicateur(
                self.page,
                nom,
                methode,
                source,
                ajustement.tableau if ajustement else self.champ_tableau.currentText(),
                ajustement.x if ajustement else self.champ_x.currentText(),
                ajustement.y if ajustement else self.champ_y.currentText(),
                self.lire_tableau,
                self._lire_borne(self.champ_de),
                self._lire_borne(self.champ_a),
                self._lire_borne(self.champ_position),
                ajustement,
                self.champ_commentaire_indicateur.text(),
            )
        except (ValueError, KeyError, OSError) as erreur:
            self.message_indicateur.setText(message_erreur(erreur))
            self.message_indicateur.setVisible(True)
            return False
        self.message_indicateur.setVisible(False)
        self.indicateurs.append(indicateur)
        self.champ_nom_indicateur.clear()
        self.champ_commentaire_indicateur.clear()
        self._afficher_indicateurs()
        self.modifie.emit()
        return True

    def retirer_indicateur(self) -> None:
        """Retire l'indicateur choisi : l'historique le garde."""
        rang = self.table_indicateurs.currentRow()
        if 0 <= rang < len(self.indicateurs):
            del self.indicateurs[rang]
            self._afficher_indicateurs()
            self.modifie.emit()

    # --- Affichage ------------------------------------------------------------------------

    def _k_change(self) -> None:
        self._afficher_resultat()
        self.tracer_si_visible()

    def _afficher_indicateurs(self) -> None:
        remplir_indicateurs(
            self.table_indicateurs, self.indicateurs, self.champ_k.value()
        )

    def _etats(self) -> list[str]:
        """Dit si l'ajustement ou un indicateur n'est plus à jour avec les points."""
        etats: list[str] = []
        if self.page is None or self.archive is None:
            return etats
        for resultat in (self.ajustement, *self.indicateurs):
            if resultat is None:
                continue
            etat = etat_resultat(self.page, resultat, self.lire_tableau)
            if etat is not None:
                etats.append(f"{resultat.nom} : {etat}")
        return etats

    def _afficher_resultat(self) -> None:
        ajustement = self.ajustement
        self.resume.setText(
            resume_ajustement(ajustement) if ajustement else "Aucun ajustement."
        )
        remplir_coefficients(self.table_coefficients, ajustement, self.champ_k.value())
        remplir_correlation(self.table_correlation, ajustement)
        self.bouton_retirer.setEnabled(ajustement is not None)
        messages = [*self._avertissements, *self._etats()]
        self.alerte.setText(" ".join(messages))
        self.alerte.setVisible(bool(messages))
        self._afficher_indicateurs()

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
                    if g.nom not in (ajustement.nom, ajustement.courbe)
                )
                + (ajustement, self.tableau_courbe)
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
        resultat.extend(self.indicateurs)
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
        self._afficher_resultat()
        self.tracer_si_visible()


declarer_onglet("ajustements", "Ajustements", OngletDonnees, EditeurAjustements)
