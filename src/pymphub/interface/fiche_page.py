"""Fiche d'une page en lecture : contenu, valeurs corrigées barrées et chronologie."""

import html
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import Archive
from pymphub.historique import CorrectionChamp, corrections_par_champ
from pymphub.page import (
    ColonneIdentifiant,
    Copie,
    Fichier,
    Identifiant,
    OngletNotes,
    Page,
    ReferenceFichier,
    Scalaire,
    Tableau,
)

# Au-delà, la fiche n'affiche que le début du tableau : elle reste ainsi réactive.
LIGNES_MAX_AFFICHEES = 500
_LONGUEUR_MAX_TEXTE = 120
_LARGEUR_MAX_COLONNE = 280


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
    hauteur = entete.sizeHint().height() + 2 * table.frameWidth()
    hauteur += sum(table.rowHeight(i) for i in range(lignes))
    table.setFixedHeight(hauteur)


class FichePage(QWidget):
    """Affiche une page en lecture seule."""

    edition_demandee = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.archive: Archive | None = None
        self.page: Page | None = None
        self.corrections: dict[str, list[CorrectionChamp]] = {}
        self.champs: dict[str, QLabel] = {}
        self.tables: dict[str, QTableWidget] = {}
        self.table_mesures: QTableWidget | None = None
        self.notes: list[QPlainTextEdit] = []

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
        ligne_k = QHBoxLayout()
        self.bouton_modifier = QPushButton("Modifier")
        self.bouton_modifier.setEnabled(False)
        self.bouton_modifier.clicked.connect(self._demander_edition)
        ligne_k.addWidget(self.entete, stretch=1)
        ligne_k.addWidget(self.bouton_modifier)
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
        self.table_mesures = None
        self.notes = []
        self.entete.setText(message)
        self.bouton_modifier.setEnabled(False)
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
            # Seule une page au statut « corrigée » a des corrections : on évite ainsi
            # de parcourir l'historique pour toutes les autres.
            if self.page.statut == "corrigée":
                self.corrections = corrections_par_champ(archive.corrections(id_page))
        except (ValueError, KeyError, OSError) as erreur:
            self.page = None
            self.entete.setText(f"Page « {id_page} » illisible.\n{erreur}")
            return

        self.entete.setText(f"{self.page.titre}  ({self.page.statut})")
        self.bouton_modifier.setEnabled(True)
        self._construire_informations(self.page)
        self._construire_mesures(self.page)
        self._construire_tableaux(archive, self.page)
        self._construire_fichiers(self.page)
        self._construire_notes(self.page)
        self.disposition_contenu.addStretch()
        self._actualiser_historique()

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
            formulaire.addRow(libelle, etiquette)
        conteneur = QWidget()
        conteneur.setLayout(formulaire)
        self._ajouter(conteneur)

    def _construire_mesures(self, page: Page) -> None:
        grandeurs = [g for g in page.mesures if isinstance(g, (Scalaire, Identifiant))]
        if not grandeurs:
            return
        self._ajouter(_titre_de_section("Mesures"))
        table = _creer_table(
            ["Nom", "Valeur", "Unité", "U", "U relative", "Commentaire"], len(grandeurs)
        )
        self.table_mesures = table
        for ligne, grandeur in enumerate(grandeurs):
            base = f"mesures.{grandeur.nom}"
            table.setItem(ligne, 0, QTableWidgetItem(grandeur.nom))
            texte_valeur = formater_valeur(grandeur.valeur)
            corrections = self.corrections.get(f"{base}.valeur", ())
            table.setCellWidget(ligne, 1, etiquette_tracee(texte_valeur, corrections))
            if isinstance(grandeur, Scalaire):
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

    def _actualiser_incertitudes(self) -> None:
        """Calcule U et U relative au facteur k choisi, sans toucher aux données."""
        if self.page is None or self.table_mesures is None:
            return
        k = self.champ_k.value()
        table = self.table_mesures
        for ligne in range(table.rowCount()):
            grandeur = self.page.grandeur(table.item(ligne, 0).text())
            if not isinstance(grandeur, Scalaire):
                continue
            budget = grandeur.budget()
            relative = budget.elargie_relative(k)
            corrigee = any(
                champ.startswith(f"mesures.{grandeur.nom}.composantes")
                for champ in self.corrections
            )
            element = QTableWidgetItem(
                f"{budget.elargie(k):.2g} {budget.unite}" + (" *" if corrigee else "")
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
        for grandeur in page.mesures:
            if not isinstance(grandeur, Tableau):
                continue
            self._ajouter(_titre_de_section(f"Tableau {grandeur.nom}"))
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
            table = _creer_table(entetes, lignes)
            for j, colonne in enumerate(grandeur.colonnes):
                valeurs = donnees[colonne.nom]
                for i in range(lignes):
                    valeur = valeurs.iloc[i]
                    texte = (
                        valeur
                        if isinstance(colonne, ColonneIdentifiant)
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
            self.tables[grandeur.nom] = table
            self._ajouter(table)
            _hauteur_ajustee(table)
            if len(donnees) > lignes:
                suite = QLabel(f"{lignes} premières lignes sur {len(donnees)}.")
                suite.setObjectName("discret")
                self._ajouter(suite)

    def _construire_fichiers(self, page: Page) -> None:
        fichiers = [g for g in page.mesures if isinstance(g, Fichier)]
        if not fichiers and not page.pieces_jointes:
            return
        self._ajouter(_titre_de_section("Fichiers"))
        lignes: list[tuple[str, str, str]] = []
        for fichier in fichiers:
            stockage = fichier.stockage
            if isinstance(stockage, Copie):
                lignes.append((fichier.nom, "Copie", stockage.nom_origine))
            elif isinstance(stockage, ReferenceFichier):
                lignes.append((fichier.nom, "Référence fichier", stockage.chemin))
            else:
                lignes.append(
                    (
                        fichier.nom,
                        "Référence DICOM",
                        f"{stockage.noeud} — {stockage.series_instance_uid}",
                    )
                )
        lignes += [(p.chemin, "Pièce jointe", "") for p in page.pieces_jointes]
        table = _creer_table(["Nom", "Stockage", "Détail"], len(lignes))
        for i, ligne in enumerate(lignes):
            for j, texte in enumerate(ligne):
                table.setItem(i, j, QTableWidgetItem(texte))
        self.tables["__fichiers__"] = table
        self._ajouter(table)
        _hauteur_ajustee(table)

    def _construire_notes(self, page: Page) -> None:
        for onglet in page.onglets:
            if not isinstance(onglet, OngletNotes) or not onglet.texte:
                continue
            self._ajouter(_titre_de_section(onglet.titre or "Notes"))
            texte = QPlainTextEdit(onglet.texte)
            texte.setReadOnly(True)
            texte.setFixedHeight(100)
            self.notes.append(texte)
            self._ajouter(texte)

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
