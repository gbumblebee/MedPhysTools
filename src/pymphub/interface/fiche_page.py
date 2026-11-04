"""Fiche d'une page en lecture : contenu, valeurs corrigées barrées et chronologie."""

import html
import json
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

import pandas as pd
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pymphub.ajustements import etat_resultat
from pymphub.archive import Archive
from pymphub.calculs import etat_derivee
from pymphub.dates import afficher_date, lire_date_iso
from pymphub.equations import RESOLUTION_ECRAN
from pymphub.fichiers import ETATS_ALERTE
from pymphub.graphes import reglages_par_defaut
from pymphub.historique import (
    CorrectionChamp,
    EntreeHistorique,
    corrections_par_champ,
)
from pymphub.interface.affichage_ajustements import (
    COLONNES_COEFFICIENTS,
    COLONNES_INDICATEURS,
    remplir_coefficients,
    remplir_correlation,
    remplir_indicateurs,
    resume_ajustement,
)
from pymphub.interface.onglets import remarque_de_capture
from pymphub.interface.photos import (
    BandePhotos,
    DialogueAnnotation,
    VignettePhoto,
    message_etat,
)
from pymphub.interface.pieces import ajouter_chemin, ajouter_chemin_fichier
from pymphub.interface.texte_en_grand import (
    afficher_en_grand,
    afficher_en_grand_colonne,
)
from pymphub.interface.theme import ALERTE, FOND
from pymphub.page import (
    Ajustement,
    ColonneCalculee,
    ColonneDate,
    ColonneIdentifiant,
    Copie,
    Fichier,
    GrandeurDerivee,
    Identifiant,
    Indicateur,
    OngletGraphes,
    OngletNotes,
    Page,
    PhotoAnnotee,
    ReferenceFichier,
    ReglagesGraphe,
    Scalaire,
    Tableau,
)
from pymphub.tags import decouper_note, note_en_html, note_en_texte

# Au-delà, la fiche n'affiche que le début du tableau : elle reste ainsi réactive.
LIGNES_MAX_AFFICHEES = 500
_LONGUEUR_MAX_TEXTE = 120
_LARGEUR_MAX_COLONNE = 280
_HAUTEUR_GRAPHE = 340


def formater_valeur(valeur: Any) -> str:  # noqa: ANN401 - contenu JSON quelconque
    """Écrit une valeur pour l'affichage, sans arrondi visible sur un nombre saisi."""
    if valeur is None:
        return "—"
    if isinstance(valeur, float):
        return f"{valeur:.15g}"
    if isinstance(valeur, str):
        texte = valeur
    elif isinstance(valeur, (int, bool)):
        texte = str(valeur)
    else:
        texte = json.dumps(valeur, ensure_ascii=False)
    if len(texte) > _LONGUEUR_MAX_TEXTE:
        return f"{texte[: _LONGUEUR_MAX_TEXTE - 1]}…"
    return texte


def formater_date(valeur: datetime | str) -> str:
    """Écrit une date dans son propre fuseau, au format français."""
    instant = datetime.fromisoformat(valeur) if isinstance(valeur, str) else valeur
    return instant.strftime("%d/%m/%Y %H:%M")


def _infobulle(corrections: Sequence[CorrectionChamp]) -> str:
    lignes = [
        f"{formater_date(c.date)}, {html.escape(c.auteur)} : "
        f"{html.escape(c.motif or 'sans motif')} "
        f"({html.escape(formater_valeur(c.ancien))} → {html.escape(formater_valeur(c.nouveau))})"
        for c in corrections
    ]
    return f"<p>{'<br>'.join(lignes)}</p>"


def etiquette_tracee(
    actuel: str, corrections: Sequence[CorrectionChamp] = ()
) -> QLabel:
    """Crée une étiquette qui barre les anciennes valeurs, comme sur un cahier papier.

    Le texte saisi par l'utilisateur est échappé : il ne peut pas être interprété comme
    une mise en forme. L'info-bulle donne le motif de chaque correction.
    """
    etiquette = QLabel()
    etiquette.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    etiquette.setWordWrap(True)
    if not corrections:
        etiquette.setTextFormat(Qt.TextFormat.PlainText)
        etiquette.setText(actuel)
        return etiquette
    barrees = " ".join(
        f"<s>{html.escape(formater_valeur(c.ancien))}</s>" for c in corrections
    )
    etiquette.setTextFormat(Qt.TextFormat.RichText)
    etiquette.setText(f"{barrees} {html.escape(actuel)}")
    etiquette.setToolTip(_infobulle(corrections))
    return etiquette


def _creer_table(colonnes: Sequence[str], lignes: int) -> QTableWidget:
    table = QTableWidget(lignes, len(colonnes))
    table.setHorizontalHeaderLabels(list(colonnes))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    table.horizontalHeader().setStretchLastSection(True)
    return table


def _titre_de_section(texte: str) -> QLabel:
    titre = QLabel(texte)
    titre.setObjectName("section")
    return titre


def _hauteur_ajustee(table: QTableWidget, lignes_max: int = 15) -> None:
    """Donne à une table la hauteur de son contenu, pour éviter un défilement imbriqué.

    La hauteur est mesurée après application du thème, qui change celle des en-têtes.
    """
    entete = table.horizontalHeader()
    table.ensurePolished()
    entete.ensurePolished()
    table.resizeColumnsToContents()
    table.resizeRowsToContents()
    lignes = min(table.rowCount(), lignes_max)
    # La place de l'ascenseur horizontal est réservée : il masquerait sinon la dernière ligne.
    hauteur = entete.sizeHint().height() + 2 * table.frameWidth()
    hauteur += table.horizontalScrollBar().sizeHint().height()
    hauteur += sum(table.rowHeight(i) for i in range(lignes))
    table.setFixedHeight(hauteur)


class FichePage(QWidget):
    """Affiche une page en lecture seule."""

    edition_demandee = Signal(str)
    suppression_demandee = Signal(str)
    export_demande = Signal(str, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.archive: Archive | None = None
        self.page: Page | None = None
        self.corrections: dict[str, list[CorrectionChamp]] = {}
        self.champs: dict[str, QLabel] = {}
        self.tables: dict[str, QTableWidget] = {}
        self.commentaires_colonnes: dict[str, QLabel] = {}
        self.table_mesures: QTableWidget | None = None
        self.notes: list[QTextEdit] = []
        self.notes_sources: list[tuple[QTextEdit, str]] = []
        self.bandes_photos: list[BandePhotos] = []
        self.graphes: list[tuple[QWidget, ReglagesGraphe]] = []
        self.alertes_calculs: list[QLabel] = []
        self.tables_coefficients: list[tuple[QTableWidget, Ajustement]] = []
        self.table_indicateurs: QTableWidget | None = None
        self.cellules_calculees: list[tuple[QTableWidget, int, ColonneCalculee]] = []
        self.etats: dict[str, str] = {}
        self.alerte_fichiers: QLabel | None = None
        self.alerte_externe: QLabel | None = None

        self.entete = QLabel("Sélectionnez une page.")
        self.entete.setObjectName("titre_page")
        self.entete.setTextFormat(Qt.TextFormat.PlainText)
        self.entete.setWordWrap(True)

        self.champ_k = QDoubleSpinBox()
        self.champ_k.setRange(0.1, 10.0)
        self.champ_k.setSingleStep(0.5)
        self.champ_k.setValue(2.0)
        self.champ_k.setToolTip(
            "Facteur d'élargissement : il change seulement l'affichage."
        )
        self.champ_k.valueChanged.connect(self._actualiser_incertitudes)
        self.champ_k.valueChanged.connect(self._tracer_graphes)
        self.champ_k.valueChanged.connect(self._ecrire_cellules_calculees)
        self.champ_k.valueChanged.connect(self._ecrire_resultats_ajustements)
        self.champ_k.valueChanged.connect(self._ecrire_notes)
        ligne_k = QHBoxLayout()
        self.bouton_modifier = QPushButton("Modifier")
        self.bouton_modifier.setEnabled(False)
        self.bouton_modifier.clicked.connect(self._demander_edition)
        self.bouton_exporter = QPushButton("PDF…")
        self.bouton_exporter.setToolTip(
            "Exporte cette page du cahier en PDF, au facteur k choisi"
        )
        self.bouton_exporter.setEnabled(False)
        self.bouton_exporter.clicked.connect(self._demander_export)
        self.bouton_supprimer = QPushButton("Supprimer…")
        self.bouton_supprimer.setEnabled(False)
        self.bouton_exporter.setEnabled(False)
        self.bouton_supprimer.clicked.connect(self._demander_suppression)
        ligne_k.addWidget(self.entete, stretch=1)
        ligne_k.addWidget(self.bouton_modifier)
        ligne_k.addWidget(self.bouton_exporter)
        ligne_k.addWidget(self.bouton_supprimer)
        ligne_k.addWidget(QLabel("k ="))
        ligne_k.addWidget(self.champ_k)

        self.contenu = QWidget()
        self.disposition_contenu = QVBoxLayout(self.contenu)
        defilement = QScrollArea()
        defilement.setWidgetResizable(True)
        defilement.setWidget(self.contenu)

        self.table_historique = _creer_table(
            ["Date", "Auteur", "Nature", "Champ", "Ancienne", "Nouvelle", "Motif"], 0
        )

        self.onglets = QTabWidget()
        self.onglets.addTab(defilement, "Contenu")
        self.onglets.addTab(self.table_historique, "Historique")
        self.onglets.currentChanged.connect(self._actualiser_historique)
        self._historique_a_jour = False

        disposition = QVBoxLayout(self)
        disposition.addLayout(ligne_k)
        disposition.addWidget(self.onglets)

    def _demander_edition(self) -> None:
        if self.page is not None:
            self.edition_demandee.emit(self.page.id)

    def _demander_export(self) -> None:
        if self.page is not None:
            self.export_demande.emit(self.page.id, self.champ_k.value())

    def _demander_suppression(self) -> None:
        if self.page is not None:
            self.suppression_demandee.emit(self.page.id)

    def _ajouter(self, widget: QWidget) -> None:
        """Ajoute un bloc à la fiche, qui garde toujours sa hauteur nécessaire.

        Sans cela, la zone défilante écrase les blocs au lieu de faire défiler la fiche.
        """
        politique = widget.sizePolicy()
        politique.setVerticalPolicy(QSizePolicy.Policy.Minimum)
        widget.setSizePolicy(politique)
        self.disposition_contenu.addWidget(widget)

    def vider(self, message: str = "Sélectionnez une page.") -> None:
        """Efface la fiche et affiche un message."""
        self.page = None
        self.corrections = {}
        self.champs = {}
        self.tables = {}
        self.commentaires_colonnes = {}
        self.table_mesures = None
        self.notes = []
        self.notes_sources = []
        self.bandes_photos = []
        self.graphes = []
        self.alertes_calculs = []
        self.tables_coefficients = []
        self.table_indicateurs = None
        self.cellules_calculees = []
        self.etats = {}
        self.alerte_fichiers = None
        self.entete.setText(message)
        self.bouton_modifier.setEnabled(False)
        self.bouton_supprimer.setEnabled(False)
        self.bouton_exporter.setEnabled(False)
        self._vider_contenu()
        self.table_historique.setRowCount(0)
        self._historique_a_jour = False

    def _vider_contenu(self) -> None:
        while (element := self.disposition_contenu.takeAt(0)) is not None:
            if (widget := element.widget()) is not None:
                widget.setParent(None)
                widget.deleteLater()

    def afficher(self, archive: Archive, id_page: str) -> None:
        """Affiche une page. Une page illisible donne un message, sans plantage."""
        self.vider()
        self.archive = archive
        try:
            self.page = archive.lire_page(id_page)
            # Un brouillon n'a pas de corrections : on évite ainsi de parcourir l'historique.
            # Une page validée peut en avoir, si un fichier a changé hors du logiciel.
            if self.page.statut != "brouillon":
                self.corrections = corrections_par_champ(archive.corrections(id_page))
            externes = archive.modifications_externes(id_page)
        except (ValueError, KeyError, OSError) as erreur:
            self.page = None
            self.entete.setText(f"Page « {id_page} » illisible.\n{erreur}")
            return

        self.entete.setText(f"{self.page.titre}  ({self.page.statut})")
        self.bouton_modifier.setEnabled(True)
        self.bouton_supprimer.setEnabled(True)
        self.bouton_exporter.setEnabled(True)
        self.etats = archive.etats_fichiers(self.page)
        self.alerte_externe = None
        if externes:
            self._signaler_modification_externe(externes[-1])
        self._construire_informations(self.page)
        self._construire_mesures(self.page)
        self._construire_tableaux(archive, self.page)
        self._signaler_calculs_perimes(self.page, self.page.mesures)
        self._construire_fichiers(self.page)
        self._construire_notes(self.page)
        self._construire_ajustements(self.page)
        self._construire_graphes(self.page)
        self.disposition_contenu.addStretch()
        self._actualiser_historique()

    def _signaler_modification_externe(self, entree: EntreeHistorique) -> None:
        """Montre qu'une page a changé hors du logiciel, même si elle reste « validée »."""
        self.alerte_externe = QLabel(
            f"Page modifiée hors du logiciel : détecté le {formater_date(entree.date)}. "
            "Le détail est dans l'historique."
        )
        self.alerte_externe.setObjectName("alerte")
        self.alerte_externe.setTextFormat(Qt.TextFormat.PlainText)
        self.alerte_externe.setWordWrap(True)
        self._ajouter(self.alerte_externe)

    def _construire_informations(self, page: Page) -> None:
        formulaire = QFormLayout()
        valeurs: list[tuple[str, str, str]] = [
            ("Titre", "titre", page.titre),
            ("Identifiant", "id", page.id),
            ("Date de mesure", "date_mesure", formater_date(page.date_mesure)),
            ("Date de saisie", "date_saisie", formater_date(page.date_saisie)),
            ("Auteur", "auteur", page.auteur),
            ("Machine", "machine", page.machine or "—"),
            ("Site", "site", page.site or "—"),
            ("Gabarit", "gabarit", page.gabarit or "Page libre"),
            ("Tags", "tags", ", ".join(page.tags) or "—"),
            ("Description", "description", page.description or "—"),
        ]
        for libelle, champ, valeur in valeurs:
            etiquette = etiquette_tracee(valeur, self.corrections.get(champ, ()))
            self.champs[champ] = etiquette
            if champ == "description":
                afficher_en_grand(
                    etiquette, "Description de la page", lambda: page.description
                )
            formulaire.addRow(libelle, etiquette)
        conteneur = QWidget()
        conteneur.setLayout(formulaire)
        self._ajouter(conteneur)

    def _construire_mesures(self, page: Page) -> None:
        grandeurs = [
            g
            for g in page.mesures
            if isinstance(g, (Scalaire, Identifiant, GrandeurDerivee))
        ]
        if not grandeurs:
            return
        self._ajouter(_titre_de_section("Mesures"))
        table = _creer_table(
            ["Nom", "Valeur", "Unité", "U", "U relative", "Commentaire", "Formule"],
            len(grandeurs),
        )
        self.table_mesures = table
        afficher_en_grand_colonne(table, 5, "Commentaire")
        afficher_en_grand_colonne(table, 6, "Formule")
        for ligne, grandeur in enumerate(grandeurs):
            base = f"mesures.{grandeur.nom}"
            table.setItem(ligne, 0, QTableWidgetItem(grandeur.nom))
            # Un nombre saisi s'affiche tel quel ; un résultat de calcul n'a pas 15 chiffres utiles.
            texte_valeur = (
                f"{grandeur.valeur:.6g}"
                if isinstance(grandeur, GrandeurDerivee)
                else formater_valeur(grandeur.valeur)
            )
            corrections = self.corrections.get(f"{base}.valeur", ())
            etiquette_valeur = etiquette_tracee(texte_valeur, corrections)
            if (
                isinstance(grandeur, Scalaire)
                and grandeur.repetitions
                and not corrections
            ):
                valeurs = ", ".join(formater_valeur(v) for v in grandeur.repetitions)
                etiquette_valeur.setToolTip(f"Moyenne de : {valeurs}")
            table.setCellWidget(ligne, 1, etiquette_valeur)
            if isinstance(grandeur, GrandeurDerivee):
                etiquette_valeur.setToolTip(f"Valeur complète : {grandeur.valeur!r}")
                formule = QTableWidgetItem(grandeur.formule)
                formule.setToolTip(grandeur.formule)
                table.setItem(ligne, 6, formule)
            if isinstance(grandeur, (Scalaire, GrandeurDerivee)):
                table.setCellWidget(
                    ligne,
                    2,
                    etiquette_tracee(
                        grandeur.unite, self.corrections.get(f"{base}.unite", ())
                    ),
                )
            table.setItem(ligne, 5, QTableWidgetItem(grandeur.commentaire))
        self._ajouter(table)
        self._actualiser_incertitudes()
        _hauteur_ajustee(table, 30)

    def _signaler_calculs_perimes(
        self, page: Page, grandeurs: Sequence[object]
    ) -> None:
        """Un résultat enregistré ne change jamais seul : la fiche dit s'il n'est plus à jour."""

        def lire_tableau(nom: str) -> pd.DataFrame:
            return self.archive.lire_tableau(page.id, nom)

        for grandeur in grandeurs:
            if isinstance(grandeur, (Ajustement, Indicateur)):
                etat = etat_resultat(page, grandeur, lire_tableau)
            elif isinstance(grandeur, (GrandeurDerivee, ColonneCalculee)):
                etat = etat_derivee(page, grandeur, lire_tableau)
            else:
                continue
            if etat is not None:
                alerte = QLabel(f"{grandeur.nom} : {etat}")
                alerte.setObjectName("alerte")
                alerte.setTextFormat(Qt.TextFormat.PlainText)
                alerte.setWordWrap(True)
                self.alertes_calculs.append(alerte)
                self._ajouter(alerte)

    def _actualiser_incertitudes(self) -> None:
        """Calcule U et U relative au facteur k choisi, sans toucher aux données."""
        if self.page is None or self.table_mesures is None:
            return
        k = self.champ_k.value()
        table = self.table_mesures
        for ligne in range(table.rowCount()):
            grandeur = self.page.grandeur(table.item(ligne, 0).text())
            if not isinstance(grandeur, (Scalaire, GrandeurDerivee)):
                continue
            budget = grandeur.budget()
            relative = budget.elargie_relative(k)
            corrigee = any(
                champ.startswith(f"mesures.{grandeur.nom}.composantes")
                for champ in self.corrections
            )
            unite = "" if budget.unite == "1" else f" {budget.unite}"
            element = QTableWidgetItem(
                f"{budget.elargie(k):.2g}{unite}" + (" *" if corrigee else "")
            )
            infobulle = "\n".join(
                f"{ligne_budget.source} : u = {ligne_budget.incertitude_type:.3g} {budget.unite} "
                f"({ligne_budget.type}, {ligne_budget.loi}, "
                f"{ligne_budget.contribution_pourcent:.0f} %)"
                for ligne_budget in budget.lignes
            )
            if corrigee:
                infobulle += "\n* Incertitude corrigée : voir l'historique."
            element.setToolTip(infobulle)
            table.setItem(ligne, 3, element)
            table.setItem(
                ligne,
                4,
                QTableWidgetItem("—" if relative is None else f"{relative:.2g} %"),
            )

    def _construire_tableaux(self, archive: Archive, page: Page) -> None:
        # Le tableau d'une courbe ajustée ne sert qu'à la retracer : le graphe la montre.
        courbes = {g.courbe for g in page.mesures if isinstance(g, Ajustement)}
        for grandeur in page.mesures:
            if not isinstance(grandeur, Tableau) or grandeur.nom in courbes:
                continue
            self._ajouter(_titre_de_section(f"Tableau {grandeur.nom}"))
            if grandeur.commentaire:
                commentaire = QLabel(grandeur.commentaire)
                commentaire.setObjectName("discret")
                commentaire.setTextFormat(Qt.TextFormat.PlainText)
                commentaire.setWordWrap(True)
                afficher_en_grand(commentaire, f"Commentaire de {grandeur.nom}")
                self._ajouter(commentaire)
            try:
                donnees = archive.lire_tableau(page.id, grandeur.nom)
            except (ValueError, KeyError, OSError) as erreur:
                alerte = QLabel(f"Tableau illisible : {erreur}")
                alerte.setObjectName("alerte")
                alerte.setWordWrap(True)
                self._ajouter(alerte)
                continue

            unites = donnees.attrs.get("unites", {})
            entetes = [
                f"{c.nom} ({unites[c.nom]})"
                if c.nom in unites and unites[c.nom]
                else c.nom
                for c in grandeur.colonnes
            ]
            lignes = min(len(donnees), LIGNES_MAX_AFFICHEES)
            calculees = [
                g
                for g in page.mesures
                if isinstance(g, ColonneCalculee) and grandeur.nom in g.tableaux
            ]
            entetes += [
                f"{c.nom} ({c.unite}), calculée"
                if c.unite != "1"
                else f"{c.nom}, calculée"
                for c in calculees
            ]
            table = _creer_table(entetes, lignes)
            for rang, calculee in enumerate(calculees, start=len(grandeur.colonnes)):
                table.horizontalHeaderItem(rang).setToolTip(calculee.formule)
                self.cellules_calculees.append((table, rang, calculee))
            for j, colonne in enumerate(grandeur.colonnes):
                valeurs = donnees[colonne.nom]
                for i in range(lignes):
                    valeur = valeurs.iloc[i]
                    texte = (
                        afficher_date(lire_date_iso(valeur))
                        if isinstance(colonne, ColonneDate)
                        else valeur
                        if isinstance(colonne, ColonneIdentifiant)
                        else f"{float(valeur):.6g}"
                        if getattr(colonne, "formule", None)
                        else formater_valeur(float(valeur))
                    )
                    # L'historique numérote les lignes à partir de 1.
                    corrections = self.corrections.get(
                        f"mesures.{grandeur.nom}.{colonne.nom}[{i + 1}]"
                    )
                    if corrections:
                        table.setCellWidget(i, j, etiquette_tracee(texte, corrections))
                    else:
                        table.setItem(i, j, QTableWidgetItem(texte))
            for j, colonne in enumerate(grandeur.colonnes):
                if colonne.commentaire:
                    table.horizontalHeaderItem(j).setToolTip(
                        f"<p>{html.escape(colonne.commentaire)}</p>"
                    )
            self.tables[grandeur.nom] = table
            self._ecrire_cellules_calculees()
            self._ajouter(table)
            _hauteur_ajustee(table)
            self._construire_commentaires_colonnes(grandeur)
            if len(donnees) > lignes:
                suite = QLabel(f"{lignes} premières lignes sur {len(donnees)}.")
                suite.setObjectName("discret")
                self._ajouter(suite)

    def _ecrire_cellules_calculees(self) -> None:
        """Écrit les colonnes calculées au facteur k choisi, sans toucher aux données."""
        k = self.champ_k.value()
        for table, colonne, calculee in self.cellules_calculees:
            for rang in range(table.rowCount()):
                texte = "—"
                if rang < len(calculee.valeurs) and calculee.valeurs[rang] is not None:
                    u = calculee.incertitudes_types[rang]
                    texte = f"{calculee.valeurs[rang]:.6g} ± {k * u:.2g}"
                element = QTableWidgetItem(texte)
                element.setBackground(QColor(FOND))
                table.setItem(rang, colonne, element)

    def _construire_commentaires_colonnes(self, tableau: Tableau) -> None:
        """Écrit sous le tableau le commentaire de chaque colonne qui en a un."""
        for colonne in tableau.colonnes:
            if not colonne.commentaire:
                continue
            commentaire = QLabel(f"{colonne.nom} : {colonne.commentaire}")
            commentaire.setObjectName("discret")
            commentaire.setTextFormat(Qt.TextFormat.PlainText)
            commentaire.setWordWrap(True)
            commentaire.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self.commentaires_colonnes[f"{tableau.nom}.{colonne.nom}"] = commentaire
            afficher_en_grand(
                commentaire,
                f"Commentaire de {tableau.nom}.{colonne.nom}",
                lambda texte=colonne.commentaire: texte,
            )
            self._ajouter(commentaire)

    def _construire_fichiers(self, page: Page) -> None:
        fichiers = [g for g in page.mesures if isinstance(g, Fichier)]
        if not fichiers and not page.pieces_jointes:
            return
        self._ajouter(_titre_de_section("Fichiers"))
        a_signaler = [c for c, etat in self.etats.items() if etat in ETATS_ALERTE]
        if a_signaler:
            self.alerte_fichiers = QLabel(
                "À vérifier : "
                + " ; ".join(f"{c} ({self.etats[c]})" for c in a_signaler)
            )
            self.alerte_fichiers.setObjectName("alerte")
            self.alerte_fichiers.setTextFormat(Qt.TextFormat.PlainText)
            self.alerte_fichiers.setWordWrap(True)
            self._ajouter(self.alerte_fichiers)
        lignes: list[tuple[str, str, str, str, str]] = []
        for fichier in fichiers:
            stockage = fichier.stockage
            etat = self.etats.get(fichier.nom, "—")
            if fichier.dicom is not None:
                nature, detail = "Copie DICOM", fichier.dicom.resume
            elif isinstance(stockage, Copie):
                nature, detail = "Copie", stockage.nom_origine
            elif isinstance(stockage, ReferenceFichier):
                nature, detail = "Référence fichier", stockage.chemin
            else:
                nature = "Référence DICOM"
                detail = f"{stockage.noeud} — {stockage.series_instance_uid}"
            lignes.append((fichier.nom, etat, nature, detail, fichier.commentaire))
        lignes += [
            (p.chemin, self.etats[p.chemin], "Pièce jointe", "", p.commentaire)
            for p in page.pieces_jointes
        ]
        table = _creer_table(
            ["Nom", "État", "Stockage", "Détail", "Commentaire"], len(lignes)
        )
        archive = self.archive

        def completer_menu(menu: QMenu, position: QPoint) -> None:
            element = table.itemAt(position)
            if element is None:
                return
            rang = element.row()
            if rang < len(fichiers):
                ajouter_chemin_fichier(menu, archive, fichiers[rang], table)
                return
            piece = page.pieces_jointes[rang - len(fichiers)]
            chemin = archive.emplacement_piece_jointe(page.id, piece.chemin)
            ajouter_chemin(menu, chemin, table)

        afficher_en_grand_colonne(table, 4, "Commentaire").complement = completer_menu
        for i, ligne in enumerate(lignes):
            for j, texte in enumerate(ligne):
                table.setItem(i, j, QTableWidgetItem(texte))
            if ligne[1] in ETATS_ALERTE:
                table.item(i, 1).setForeground(QColor(ALERTE))
                table.item(i, 1).setToolTip(message_etat(ligne[1]))
        self.tables["__fichiers__"] = table
        self._ajouter(table)
        _hauteur_ajustee(table)
        if any(f.dicom is not None for f in fichiers):
            self.bouton_dicom = QPushButton("Voir les séries DICOM…")
            self.bouton_dicom.clicked.connect(self.voir_series_dicom)
            self._ajouter(self.bouton_dicom)

    def voir_series_dicom(self) -> None:
        """Ouvre les séries DICOM de la page dans le visualiseur, sans rien pouvoir y changer."""
        from pymphub.interface.onglet_dicom import DialogueSeries  # noqa: PLC0415

        dialogue = DialogueSeries(self.archive, self.page, self)
        dialogue.exec()
        dialogue.deleteLater()

    def _construire_notes(self, page: Page) -> None:
        for onglet in page.onglets:
            if not isinstance(onglet, OngletNotes) or not (
                onglet.texte or onglet.photos
            ):
                continue
            self._ajouter(_titre_de_section(onglet.titre or "Notes"))
            if onglet.texte:
                texte = QTextEdit()
                texte.setReadOnly(True)
                texte.setFixedHeight(100)
                self.notes_sources.append((texte, onglet.texte))
                self.notes.append(texte)
                self._ajouter(texte)
                afficher_en_grand(
                    texte,
                    onglet.titre or "Notes",
                    lambda source=onglet.texte: note_en_texte(
                        decouper_note(
                            source,
                            page,
                            self._lecteur_tableau(page),
                            self.champ_k.value(),
                        )
                    ),
                )
            if onglet.photos:
                self._construire_photos(page, onglet.photos)
        self._ecrire_notes()

    def _lecteur_tableau(self, page: Page) -> Callable[[str], pd.DataFrame]:
        return lambda nom: self.archive.lire_tableau(page.id, nom)

    def _ecrire_notes(self) -> None:
        """Remplace les tags par les valeurs, au facteur k choisi. La note ne change pas."""
        if self.page is None:
            return
        for texte, source in self.notes_sources:
            texte.setHtml(
                note_en_html(
                    decouper_note(
                        source,
                        self.page,
                        self._lecteur_tableau(self.page),
                        self.champ_k.value(),
                    ),
                    resolution=RESOLUTION_ECRAN * self.devicePixelRatioF(),
                )
            )

    def _construire_photos(self, page: Page, photos: Sequence[PhotoAnnotee]) -> None:
        vignettes: list[VignettePhoto] = []
        for photo in photos:
            vignette = VignettePhoto(
                self.archive.emplacement_piece_jointe(page.id, photo.chemin),
                photo,
                self.etats[photo.chemin],
                modifiable=False,
                remarque=remarque_de_capture(
                    page, photo.capture, self._lecteur_tableau(page)
                )
                if photo.capture is not None
                else "",
            )
            vignette.ouverture_demandee.connect(lambda p=photo: self.ouvrir_photo(p))
            vignettes.append(vignette)
        bande = BandePhotos()
        bande.remplacer(vignettes)
        self.bandes_photos.append(bande)
        self._ajouter(bande)

    def _construire_ajustements(self, page: Page) -> None:
        """Montre les coefficients, leurs corrélations et les indicateurs de la page."""
        for grandeur in page.mesures:
            if isinstance(grandeur, Ajustement):
                self._ajouter(_titre_de_section(f"Ajustement {grandeur.nom}"))
                resume = QLabel(resume_ajustement(grandeur))
                resume.setObjectName("discret")
                resume.setTextFormat(Qt.TextFormat.PlainText)
                resume.setWordWrap(True)
                self._ajouter(resume)
                coefficients = _creer_table(list(COLONNES_COEFFICIENTS), 0)
                self.tables_coefficients.append((coefficients, grandeur))
                self._ajouter(coefficients)
                correlation = _creer_table([""], 0)
                remplir_correlation(correlation, grandeur)
                self.tables[f"{grandeur.nom}.correlation"] = correlation
                self._ajouter(correlation)
                _hauteur_ajustee(correlation)
                self.tables[f"{grandeur.nom}.coefficients"] = coefficients
        indicateurs = [g for g in page.mesures if isinstance(g, Indicateur)]
        if indicateurs:
            self._ajouter(_titre_de_section("Indicateurs"))
            table = _creer_table(list(COLONNES_INDICATEURS), 0)
            self.table_indicateurs = table
            afficher_en_grand_colonne(table, 6, "Commentaire")
            self._ajouter(table)
        self._ecrire_resultats_ajustements()
        for table, _grandeur in self.tables_coefficients:
            _hauteur_ajustee(table)
        if self.table_indicateurs is not None:
            _hauteur_ajustee(self.table_indicateurs)

    def _ecrire_resultats_ajustements(self) -> None:
        """Écrit les incertitudes au facteur k choisi, sans toucher aux données."""
        k = self.champ_k.value()
        for table, grandeur in self.tables_coefficients:
            remplir_coefficients(table, grandeur, k)
        if self.table_indicateurs is not None:
            remplir_indicateurs(
                self.table_indicateurs,
                [g for g in self.page.mesures if isinstance(g, Indicateur)],
                k,
            )

    def _construire_graphes(self, page: Page) -> None:
        """Trace les graphes de la page. matplotlib ne se charge que s'il y en a."""
        for onglet in page.onglets:
            if not isinstance(onglet, OngletGraphes):
                continue
            reglages = reglages_par_defaut(page, onglet)
            if reglages is None:
                continue
            from pymphub.interface.onglet_graphes import (  # noqa: PLC0415
                creer_toile,
            )

            self._ajouter(_titre_de_section(onglet.titre or "Graphe"))
            toile = creer_toile(interactif=False)
            toile.setFixedHeight(_HAUTEUR_GRAPHE)
            self.graphes.append((toile, reglages))
            self._ajouter(toile)
        self._tracer_graphes()

    def _tracer_graphes(self) -> None:
        if not self.graphes:
            return
        from pymphub.interface.onglet_graphes import dessiner  # noqa: PLC0415

        for toile, reglages in self.graphes:
            dessiner(toile, self.archive, self.page, reglages, self.champ_k.value())

    def _montrer_dialogue(self, dialogue: QDialog) -> None:
        dialogue.exec()
        dialogue.deleteLater()

    def ouvrir_photo(self, photo: PhotoAnnotee) -> None:
        """Montre une photo en grand avec ses annotations, sans pouvoir les changer."""
        self._montrer_dialogue(
            DialogueAnnotation(
                self.archive.emplacement_piece_jointe(self.page.id, photo.chemin),
                photo,
                self.etats[photo.chemin],
                modifiable=False,
                parent=self,
            )
        )

    def _actualiser_historique(self) -> None:
        """Charge la chronologie seulement quand elle est affichée."""
        if (
            self._historique_a_jour
            or self.page is None
            or self.archive is None
            or self.onglets.currentWidget() is not self.table_historique
        ):
            return
        lignes: list[list[str]] = []
        for entree in reversed(self.archive.historique(self.page.id)):
            debut = [formater_date(entree.date), entree.auteur, entree.nature]
            motif = entree.motif or ""
            if not entree.modifications:
                lignes.append([*debut, "", "", "", motif])
            for modification in entree.modifications:
                lignes.append(
                    [
                        *debut,
                        modification.champ,
                        formater_valeur(modification.ancien),
                        formater_valeur(modification.nouveau),
                        motif,
                    ]
                )
        table = self.table_historique
        table.setRowCount(len(lignes))
        for i, ligne in enumerate(lignes):
            for j, texte in enumerate(ligne):
                table.setItem(i, j, QTableWidgetItem(texte))
        table.resizeColumnsToContents()
        for colonne in range(table.columnCount()):
            table.setColumnWidth(
                colonne, min(table.columnWidth(colonne), _LARGEUR_MAX_COLONNE)
            )
        self._historique_a_jour = True
