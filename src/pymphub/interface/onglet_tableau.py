"""Onglet de tableau : grille type tableur, collage Excel et import de fichiers.

Chaque colonne déclare sa nature, son unité et son incertitude. Un tableau incomplet n'est
jamais enregistré : la dernière version valide reste en place tant qu'on le complète.
"""

import html
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import Archive
from pymphub.edition import Grandeur, lire_nombre
from pymphub.importation import (
    Grille,
    ImportImpossible,
    charger_imports_personnalises,
    decouper_entete,
    deviner_nature,
    en_grille,
    executer_import_personnalise,
    lire_fichier,
    lire_presse_papiers,
    noms_uniques,
)
from pymphub.incertitudes import Composante, repetitions
from pymphub.interface.messages import message_erreur
from pymphub.interface.onglet_scalaires import (
    DialogueIncertitudes,
    _marquer,
    _nombre_ecrit,
)
from pymphub.interface.onglets import EditeurOnglet, declarer_onglet
from pymphub.interface.preferences import dossier_preferences
from pymphub.page import (
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Onglet,
    OngletDonnees,
    Page,
    Scalaire,
    Tableau,
)
from pymphub.tableaux import TableauInvalide, ecrire_csv
from pymphub.unites import verifier_unite

_LIGNES_INITIALES = 5
_COLONNES_INITIALES = 2
_LARGEUR_LIBELLES = 110
_LARGEUR_COLONNE = 190
_LIBELLES_CONFIGURATION = ("Nom", "Nature", "Unité", "Incertitude", "Commentaire", "")
_NATURES = (
    ("Nombre", "numerique"),
    ("Identifiant", "identifiant"),
    ("Incertitude de…", "incertitude"),
)
FICHIER_IMPORTS = "imports_personnalises.py"


class ConfigurationColonne:
    """La déclaration d'une colonne : nom, nature, unité et incertitude."""

    def __init__(
        self, editeur: "EditeurTableau", nom: str = "", unite: str = ""
    ) -> None:
        self.editeur = editeur
        self.composantes: tuple[Composante, ...] = ()
        self.modele_cellule: Composante | None = None
        self.nom = QLineEdit(nom)
        self.nom.setPlaceholderText("nom")
        self.nature = QComboBox()
        for libelle, valeur in _NATURES:
            self.nature.addItem(libelle, valeur)
        self.unite = QLineEdit(unite)
        self.unite.setPlaceholderText("MBq")
        self.commentaire = QLineEdit()
        self.commentaire.setPlaceholderText("commentaire")
        self.commentaire.textChanged.connect(self._afficher_commentaire_complet)
        self.bouton_incertitude = QPushButton("Incertitude…")
        self.bouton_incertitude.clicked.connect(self.demander_incertitude)
        # La case partage sa largeur avec la liste des colonnes : le texte doit y tenir.
        self.bouton_incertitude.setStyleSheet("padding: 7px 3px;")
        self.cible = QComboBox()
        self.cible.setToolTip(
            "Colonne dont celle-ci donne l'incertitude, cellule par cellule"
        )
        self.bouton_retirer = QPushButton("Retirer")
        self.bouton_retirer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bouton_retirer.clicked.connect(lambda: editeur.retirer_colonne(self))
        self.cellule_incertitude = QWidget()
        disposition = QHBoxLayout(self.cellule_incertitude)
        disposition.setContentsMargins(0, 0, 0, 0)
        disposition.addWidget(self.bouton_incertitude)
        disposition.addWidget(self.cible)

        self.nom.textChanged.connect(self._modifiee)
        self.nature.currentIndexChanged.connect(self._modifiee)
        self.unite.textChanged.connect(self._modifiee)
        self.unite.returnPressed.connect(self.accepter_suggestion)
        self.cible.currentIndexChanged.connect(self._modifiee)
        self.commentaire.textChanged.connect(self._modifiee)
        self.actualiser()

    def _afficher_commentaire_complet(self, texte: str) -> None:
        """Montre tout le commentaire au survol, même s'il dépasse de sa case."""
        self.commentaire.setToolTip(f"<p>{html.escape(texte)}</p>" if texte else "")

    def _modifiee(self) -> None:
        self.actualiser()
        self.editeur.colonne_modifiee()

    def nature_choisie(self) -> str:
        return self.nature.currentData()

    def actualiser(self) -> None:
        """Adapte les champs à la nature de la colonne, et vérifie l'unité à la frappe."""
        nature = self.nature_choisie()
        self.unite.setEnabled(nature != "identifiant")
        self.bouton_incertitude.setEnabled(nature != "identifiant")
        self.cible.setVisible(nature == "incertitude")
        verification = verifier_unite(self.unite.text())
        fausse = (
            nature != "identifiant"
            and bool(self.unite.text().strip())
            and not verification.valide
        )
        _marquer(self.unite, fausse)
        self.unite.setToolTip(verification.message if fausse else "")
        if nature == "numerique":
            nombre = len(self.composantes)
            self.bouton_incertitude.setText(
                f"{nombre} composante{'s' if nombre > 1 else ''}…"
                if nombre
                else "Incertitude…"
            )
        elif nature == "incertitude":
            self.bouton_incertitude.setText(
                "Loi et source…" if self.modele_cellule is None else "Modifier…"
            )
        else:
            self.bouton_incertitude.setText("—")

    def accepter_suggestion(self) -> bool:
        """Remplace l'unité par la suggestion, avec la touche Entrée."""
        verification = verifier_unite(self.unite.text())
        if verification.valide or verification.suggestion is None:
            return False
        self.unite.setText(verification.suggestion)
        return True

    def _demander_composantes(self) -> tuple[Composante, ...] | None:
        declarees = self.composantes
        if self.nature_choisie() == "incertitude" and self.modele_cellule is not None:
            declarees = (self.modele_cellule,)
        dialogue = DialogueIncertitudes(
            self.nom.text().strip(),
            self.unite.text().strip(),
            declarees,
            parent=self.editeur,
        )
        if dialogue.exec() == QDialog.DialogCode.Accepted:
            return dialogue.composantes()
        return None

    def demander_incertitude(self) -> None:
        """Ouvre la déclaration de l'incertitude de la colonne."""
        composantes = self._demander_composantes()
        if composantes is None:
            return
        if self.nature_choisie() == "incertitude":
            self.modele_cellule = composantes[0]
        else:
            self.composantes = composantes
        self._modifiee()

    def colonne(self) -> ColonneNumerique | ColonneIdentifiant | ColonneIncertitude:
        """Construit la description de la colonne. Lève `ValueError` si elle est invalide."""
        nom = self.nom.text().strip()
        if not nom:
            raise ValueError("chaque colonne doit avoir un nom")
        nature = self.nature_choisie()
        if nature == "identifiant":
            return ColonneIdentifiant(nom=nom, commentaire=self.commentaire.text())
        if not self.unite.text().strip():
            raise ValueError(
                f"colonne « {nom} » : l'unité est obligatoire (1 si sans dimension)"
            )
        unite = self.unite.text().strip()
        if nature == "numerique":
            return ColonneNumerique(
                nom=nom,
                unite=unite,
                composantes=self.composantes,
                commentaire=self.commentaire.text(),
            )
        if self.modele_cellule is None:
            raise ValueError(f"colonne « {nom} » : indiquer la loi et la source")
        if not self.cible.currentText():
            raise ValueError(f"colonne « {nom} » : choisir la colonne concernée")
        modele = self.modele_cellule
        return ColonneIncertitude(
            nom=nom,
            composante_de=self.cible.currentText(),
            type=modele.type,
            unite=None if modele.relative else unite,
            relative=modele.relative,
            loi=modele.loi,
            k=modele.k,
            ddl=modele.ddl,
            source=modele.source,
            erreur_commune=modele.erreur_commune,
            commentaire=self.commentaire.text(),
        )


class EditeurTableau(EditeurOnglet):
    """Édite un tableau : sa description et ses cellules."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        self.archive: Archive | None = None
        self.colonnes: list[ConfigurationColonne] = []
        self.origine: Tableau | None = None
        self.illisible: str | None = None
        self._texte_enregistre: str | None = None
        self._references_conservees: list[str] = []
        self._chargement = True
        self._commentaire = ""

        self.champ_nom = QLineEdit()
        self.champ_nom.setPlaceholderText("nom du tableau, par exemple decroissance")
        self.bouton_colonne = QPushButton("Ajouter une colonne")
        self.bouton_colonne.clicked.connect(lambda: self.ajouter_colonne())
        self.bouton_coller = QPushButton("Coller (Ctrl+V)")
        self.bouton_coller.clicked.connect(self.coller_presse_papiers)
        self.bouton_importer = QPushButton("Importer un fichier…")
        self.bouton_importer.clicked.connect(self.demander_import)
        self.bouton_personnalise = QPushButton("Import personnalisé…")
        self.bouton_personnalise.clicked.connect(self.demander_import_personnalise)
        self.bouton_repetitions = QPushButton("Mesure répétée depuis la sélection")
        self.bouton_repetitions.setToolTip(
            "Sélectionnez des valeurs répétées d'une colonne : leur moyenne devient une mesure "
            "de la page, avec son incertitude de type A."
        )
        self.bouton_repetitions.clicked.connect(self.demander_mesure_repetee)

        ligne_haut = QHBoxLayout()
        ligne_haut.addWidget(QLabel("Tableau"))
        ligne_haut.addWidget(self.champ_nom, stretch=1)
        ligne_haut.addWidget(self.bouton_colonne)
        ligne_haut.addWidget(self.bouton_coller)
        ligne_haut.addWidget(self.bouton_importer)
        ligne_haut.addWidget(self.bouton_personnalise)
        ligne_haut.addWidget(self.bouton_repetitions)

        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)

        self.configuration = QTableWidget(len(_LIBELLES_CONFIGURATION), 0)
        self.configuration.setVerticalHeaderLabels(_LIBELLES_CONFIGURATION)
        self.configuration.horizontalHeader().setVisible(False)
        self.configuration.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.configuration.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.configuration.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.configuration.verticalHeader().setFixedWidth(_LARGEUR_LIBELLES)
        self.configuration.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Fixed
        )

        self.grille = QTableWidget(_LIGNES_INITIALES, 0)
        self.grille.horizontalHeader().setVisible(False)
        self.grille.verticalHeader().setFixedWidth(_LARGEUR_LIBELLES)
        self.grille.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grille.setAlternatingRowColors(True)
        self.grille.itemChanged.connect(self._cellule_changee)
        self.grille.horizontalScrollBar().valueChanged.connect(
            self.configuration.horizontalScrollBar().setValue
        )

        disposition = QVBoxLayout(self)
        disposition.addLayout(ligne_haut)
        disposition.addWidget(self.message)
        disposition.addWidget(self.configuration)
        disposition.addWidget(self.grille, stretch=1)

        self.champ_nom.textChanged.connect(self.colonne_modifiee)
        coller = QShortcut(QKeySequence(QKeySequence.StandardKey.Paste), self.grille)
        coller.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        coller.activated.connect(self.coller_presse_papiers)
        effacer = QShortcut(QKeySequence(Qt.Key.Key_Delete), self.grille)
        effacer.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        effacer.activated.connect(self.effacer_selection)

        for _ in range(_COLONNES_INITIALES):
            self.ajouter_colonne()
        self._chargement = False

    # --- Chargement -------------------------------------------------------------------

    def charger(self, archive: Archive) -> None:
        """Relit le tableau de cet onglet depuis l'archive."""
        self.archive = archive
        self._chargement = True
        tableau = None
        for reference in getattr(self.onglet, "grandeurs", ()):
            grandeur = next((g for g in self.page.mesures if g.nom == reference), None)
            if isinstance(grandeur, Tableau) and tableau is None:
                tableau = grandeur
            else:
                self._references_conservees.append(reference)
        if tableau is not None:
            self._charger_tableau(archive, tableau)
        self._chargement = False
        self._actualiser_message()

    def _charger_tableau(self, archive: Archive, tableau: Tableau) -> None:
        self.origine = tableau
        self._commentaire = tableau.commentaire
        self.champ_nom.setText(tableau.nom)
        self._vider_colonnes()
        for colonne in tableau.colonnes:
            configuration = self.ajouter_colonne(colonne.nom)
            configuration.commentaire.setText(colonne.commentaire)
            if isinstance(colonne, ColonneNumerique):
                configuration.unite.setText(colonne.unite)
                configuration.composantes = colonne.composantes
            elif isinstance(colonne, ColonneIdentifiant):
                configuration.nature.setCurrentIndex(1)
            else:
                configuration.nature.setCurrentIndex(2)
                configuration.unite.setText(colonne.unite or "")
                configuration.modele_cellule = colonne.composante(0.0)
            configuration.actualiser()
        self._actualiser_cibles()
        for configuration, colonne in zip(self.colonnes, tableau.colonnes):
            if isinstance(colonne, ColonneIncertitude):
                configuration.cible.setCurrentText(colonne.composante_de)
        try:
            donnees = archive.lire_tableau(self.page.id, tableau.nom)
        except (ValueError, KeyError, OSError) as erreur:
            self.illisible = (
                f"Tableau illisible sur le disque : {message_erreur(erreur)}"
            )
            return
        self.grille.setRowCount(len(donnees) + 1)
        for j, colonne in enumerate(tableau.colonnes):
            for i, valeur in enumerate(donnees[colonne.nom]):
                texte = (
                    valeur
                    if isinstance(colonne, ColonneIdentifiant)
                    else _nombre_ecrit(valeur)
                )
                self.grille.setItem(i, j, QTableWidgetItem(texte))
        self._texte_enregistre = self._texte_csv()

    def _vider_colonnes(self) -> None:
        for configuration in list(self.colonnes):
            self._retirer_widgets(configuration)
        self.colonnes.clear()
        self.configuration.setColumnCount(0)
        self.grille.setColumnCount(0)

    @staticmethod
    def _retirer_widgets(configuration: ConfigurationColonne) -> None:
        for champ in (
            configuration.nom,
            configuration.nature,
            configuration.unite,
            configuration.cellule_incertitude,
            configuration.commentaire,
            configuration.bouton_retirer,
        ):
            champ.setParent(None)
            champ.deleteLater()

    # --- Colonnes ---------------------------------------------------------------------

    def ajouter_colonne(self, nom: str = "", unite: str = "") -> ConfigurationColonne:
        """Ajoute une colonne à droite."""
        configuration = ConfigurationColonne(self, nom, unite)
        position = len(self.colonnes)
        self.colonnes.append(configuration)
        self.configuration.setColumnCount(position + 1)
        self.grille.setColumnCount(position + 1)
        for rang, champ in enumerate(
            (
                configuration.nom,
                configuration.nature,
                configuration.unite,
                configuration.cellule_incertitude,
                configuration.commentaire,
                configuration.bouton_retirer,
            )
        ):
            self.configuration.setCellWidget(rang, position, champ)
        self.configuration.setColumnWidth(position, _LARGEUR_COLONNE)
        self.grille.setColumnWidth(position, _LARGEUR_COLONNE)
        self._ajuster_hauteur_configuration()
        self._actualiser_cibles()
        self.colonne_modifiee()
        return configuration

    def _ajuster_hauteur_configuration(self) -> None:
        hauteur = 2 * self.configuration.frameWidth()
        for rang in range(self.configuration.rowCount()):
            self.configuration.setRowHeight(rang, 36)
            hauteur += 36
        self.configuration.setFixedHeight(hauteur)

    def retirer_colonne(self, configuration: ConfigurationColonne) -> None:
        """Retire une colonne et ses cellules."""
        position = self.colonnes.index(configuration)
        self.colonnes.remove(configuration)
        self.configuration.removeColumn(position)
        self.grille.removeColumn(position)
        self._actualiser_cibles()
        self.colonne_modifiee()

    def _actualiser_cibles(self) -> None:
        """Propose, pour une colonne d'incertitude, les colonnes numériques du tableau."""
        numeriques = [
            c.nom.text().strip()
            for c in self.colonnes
            if c.nature_choisie() == "numerique" and c.nom.text().strip()
        ]
        for configuration in self.colonnes:
            actuelle = configuration.cible.currentText()
            configuration.cible.blockSignals(True)
            configuration.cible.clear()
            configuration.cible.addItems(numeriques)
            if actuelle in numeriques:
                configuration.cible.setCurrentText(actuelle)
            configuration.cible.blockSignals(False)

    def colonne_modifiee(self) -> None:
        if self._chargement:
            return
        self._actualiser_cibles()
        self._actualiser_message()
        self.modifie.emit()

    # --- Cellules ---------------------------------------------------------------------

    def _cellule_changee(self, _element: QTableWidgetItem) -> None:
        if self._chargement:
            return
        self._garder_une_ligne_vide()
        self._actualiser_message()
        self.modifie.emit()

    def _garder_une_ligne_vide(self) -> None:
        """Garde toujours une ligne libre en bas de la grille."""
        derniere = self.grille.rowCount() - 1
        if derniere < 0 or any(
            self._texte(derniere, j) for j in range(self.grille.columnCount())
        ):
            self.grille.insertRow(derniere + 1)

    def _texte(self, ligne: int, colonne: int) -> str:
        element = self.grille.item(ligne, colonne)
        return element.text().strip() if element is not None else ""

    def lignes_remplies(self) -> list[int]:
        """Les lignes de la grille qui contiennent au moins une cellule."""
        return [
            i
            for i in range(self.grille.rowCount())
            if any(self._texte(i, j) for j in range(self.grille.columnCount()))
        ]

    def ecrire_cellules(
        self, cellules: Sequence[Sequence[str]], ligne: int, colonne: int
    ) -> None:
        """Écrit un bloc de cellules à partir d'une position, en agrandissant la grille."""
        self._chargement = True
        for i, valeurs in enumerate(cellules):
            if ligne + i >= self.grille.rowCount():
                self.grille.setRowCount(ligne + i + 1)
            for j, valeur in enumerate(valeurs):
                while colonne + j >= len(self.colonnes):
                    self.ajouter_colonne()
                self.grille.setItem(ligne + i, colonne + j, QTableWidgetItem(valeur))
        self._chargement = False
        self._garder_une_ligne_vide()
        self.colonne_modifiee()

    def coller(self, texte: str) -> None:
        """Colle du texte venu d'Excel à partir de la cellule courante."""
        cellules = lire_presse_papiers(texte)
        if not cellules:
            return
        ligne = max(self.grille.currentRow(), 0)
        colonne = max(self.grille.currentColumn(), 0)
        self.ecrire_cellules(cellules, ligne, colonne)

    def coller_presse_papiers(self) -> None:
        self.coller(QApplication.clipboard().text())

    def effacer_selection(self) -> None:
        """Vide les cellules sélectionnées."""
        self._chargement = True
        for element in self.grille.selectedItems():
            element.setText("")
        self._chargement = False
        self.colonne_modifiee()

    # --- Tableau saisi ----------------------------------------------------------------

    def est_vide(self) -> bool:
        """Un tableau jamais rempli n'est pas une erreur : il est simplement ignoré."""
        return (
            self.origine is None
            and not self.champ_nom.text().strip()
            and not self.lignes_remplies()
            and not any(c.nom.text().strip() for c in self.colonnes)
        )

    def tableau_saisi(self) -> Tableau:
        """Construit la description du tableau. Lève `ValueError` si elle est incomplète."""
        nom = self.champ_nom.text().strip()
        if not nom:
            raise ValueError("le tableau doit avoir un nom")
        return Tableau(
            nom=nom,
            fichier=f"brut/{nom}.csv",
            colonnes=tuple(c.colonne() for c in self.colonnes),
            commentaire=self._commentaire,
        )

    def donnees(self) -> dict[str, list[str | float]]:
        """Lit les cellules selon la nature des colonnes. Lève `ValueError` si l'une manque."""
        lignes = self.lignes_remplies()
        resultat: dict[str, list[str | float]] = {}
        for j, configuration in enumerate(self.colonnes):
            nom = configuration.nom.text().strip()
            valeurs: list[str | float] = []
            for i in lignes:
                texte = self._texte(i, j)
                if not texte:
                    raise ValueError(f"colonne « {nom} », ligne {i + 1} : cellule vide")
                if configuration.nature_choisie() == "identifiant":
                    valeurs.append(texte)
                else:
                    try:
                        valeurs.append(lire_nombre(texte))
                    except ValueError as erreur:
                        raise ValueError(
                            f"colonne « {nom} », ligne {i + 1} : {message_erreur(erreur)}"
                        ) from erreur
            resultat[nom] = valeurs
        return resultat

    def _texte_csv(self) -> str:
        return ecrire_csv(self.tableau_saisi(), self.donnees())

    def erreur(self) -> str | None:
        """Explique ce qui empêche d'enregistrer le tableau."""
        if self.illisible is not None:
            return self.illisible
        if self.est_vide():
            return None
        try:
            self._texte_csv()
        except (ValueError, TableauInvalide) as erreur:
            texte = message_erreur(erreur)
            if isinstance(erreur, ValidationError):
                return f"Tableau « {self.champ_nom.text().strip()} » : {texte}"
            return f"Tableau : {texte}"
        return None

    def _actualiser_message(self) -> None:
        erreur = self.erreur()
        self.message.setText(erreur or "")
        self.message.setVisible(erreur is not None)

    # --- Contrat d'un éditeur ---------------------------------------------------------

    def grandeurs_possedees(self) -> list[str]:
        return [self.origine.nom] if self.origine is not None else []

    def grandeurs(self) -> list[Grandeur]:
        if self.erreur() is not None or self.est_vide():
            return [self.origine] if self.origine is not None else []
        return [self.tableau_saisi()]

    def erreurs(self) -> list[str]:
        erreur = self.erreur()
        return [erreur] if erreur is not None else []

    def valeur(self) -> Onglet:
        noms = [grandeur.nom for grandeur in self.grandeurs()]
        return self.onglet.model_copy(
            update={"grandeurs": tuple(noms) + tuple(self._references_conservees)}
        )

    def donnees_tableaux(self) -> dict[str, dict[str, list[str | float]]]:
        if self.erreur() is not None or self.est_vide():
            return {}
        return {self.champ_nom.text().strip(): self.donnees()}

    def tableau_modifie(self) -> bool:
        if self.erreur() is not None or self.est_vide():
            return False
        return self._texte_csv() != self._texte_enregistre

    def enregistre(self, page: Page) -> None:
        super().enregistre(page)
        self.onglet = self.valeur()
        if self.erreur() is None and not self.est_vide():
            self.origine = next(
                g for g in page.mesures if g.nom == self.champ_nom.text().strip()
            )
            self._texte_enregistre = self._texte_csv()

    # --- Import -----------------------------------------------------------------------

    def _choisir_fichier(self) -> str:
        chemin, _filtre = QFileDialog.getOpenFileName(
            self,
            "Importer un tableau",
            "",
            "Tableaux (*.csv *.txt *.tsv *.xlsx);;Tous (*)",
        )
        return chemin

    def _confirmer_remplacement(self) -> bool:
        reponse = QMessageBox.question(
            self, "Importer", "Remplacer le contenu actuel de ce tableau ?"
        )
        return reponse == QMessageBox.StandardButton.Yes

    def _choisir_import(self, noms: Sequence[str]) -> str | None:
        nom, accepte = QInputDialog.getItem(
            self, "Import personnalisé", "Import :", list(noms)
        )
        return nom if accepte else None

    def _signaler(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(True)

    def demander_import(self) -> bool:
        """Choisit un fichier, puis remplit le tableau."""
        chemin = self._choisir_fichier()
        if not chemin:
            return False
        return self.importer_fichier(Path(chemin))

    def importer_fichier(self, chemin: Path) -> bool:
        """Remplit le tableau depuis un fichier. Une erreur est expliquée, sans plantage."""
        try:
            grille = en_grille(lire_fichier(chemin))
        except ImportImpossible as erreur:
            self._signaler(str(erreur))
            return False
        return self.appliquer_grille(grille, chemin.name)

    def demander_import_personnalise(self) -> bool:
        """Propose les fonctions du fichier d'imports de l'utilisateur."""
        fichier = dossier_preferences() / FICHIER_IMPORTS
        try:
            fonctions = charger_imports_personnalises(fichier)
        except ImportImpossible as erreur:
            self._signaler(str(erreur))
            return False
        if not fonctions:
            self._signaler(
                f"Aucun import personnalisé : écrire des fonctions importer_… dans {fichier}."
            )
            return False
        nom = self._choisir_import(sorted(fonctions))
        if nom is None:
            return False
        chemin = self._choisir_fichier()
        if not chemin:
            return False
        try:
            grille = executer_import_personnalise(nom, fonctions[nom], Path(chemin))
        except ImportImpossible as erreur:
            self._signaler(str(erreur))
            return False
        return self.appliquer_grille(grille, Path(chemin).name)

    def appliquer_grille(self, grille: Grille, origine: str) -> bool:
        """Remplace le tableau par une grille importée, en proposant noms, unités et natures.

        Les incertitudes restent à déclarer : aucune n'est inventée.
        """
        if self.lignes_remplies() and not self._confirmer_remplacement():
            return False
        self._chargement = True
        self._vider_colonnes()
        decoupes = [decouper_entete(e, i) for i, e in enumerate(grille.entetes)]
        noms = noms_uniques([nom for nom, _unite in decoupes])
        for j, (nom, (_base, unite)) in enumerate(zip(noms, decoupes)):
            configuration = self.ajouter_colonne(nom, unite)
            valeurs = [ligne[j] if j < len(ligne) else "" for ligne in grille.lignes]
            if deviner_nature(valeurs) == "identifiant":
                configuration.nature.setCurrentIndex(1)
            configuration.actualiser()
        self.grille.clearContents()
        self.grille.setRowCount(len(grille.lignes) + 1)
        self._chargement = False
        self.ecrire_cellules(grille.lignes, 0, 0)
        self._commentaire = f"Importé depuis {origine}"
        self.colonne_modifiee()
        return True

    # --- Mesure répétée depuis une sélection -----------------------------------------

    def _demander_nom_mesure(self, defaut: str) -> str | None:
        nom, accepte = QInputDialog.getText(
            self, "Mesure répétée", "Nom de la nouvelle mesure :", text=defaut
        )
        return nom.strip() if accepte else None

    def _colonne_et_valeurs_selectionnees(
        self,
    ) -> tuple[ConfigurationColonne, list[float]]:
        selection = self.grille.selectedIndexes()
        colonnes = {index.column() for index in selection}
        if len(colonnes) != 1:
            raise ValueError("Sélectionnez des valeurs dans une seule colonne.")
        configuration = self.colonnes[colonnes.pop()]
        if configuration.nature_choisie() != "numerique":
            raise ValueError("Sélectionnez des valeurs dans une colonne numérique.")
        valeurs = [
            lire_nombre(self._texte(index.row(), index.column()))
            for index in sorted(selection, key=lambda x: x.row())
            if self._texte(index.row(), index.column())
        ]
        if len(valeurs) < 2:
            raise ValueError("Sélectionnez au moins deux valeurs répétées.")
        return configuration, valeurs

    def mesure_depuis_selection(self, nom: str) -> Scalaire:
        """Construit une mesure : moyenne, type A et composantes de la colonne."""
        configuration, valeurs = self._colonne_et_valeurs_selectionnees()
        unite = configuration.unite.text().strip()
        moyenne, type_a = repetitions(valeurs, unite)
        return Scalaire(
            nom=nom,
            valeur=moyenne,
            unite=unite,
            composantes=(type_a, *configuration.composantes),
            repetitions=tuple(valeurs),
            commentaire=(
                f"Depuis le tableau {self.champ_nom.text().strip()}, "
                f"colonne {configuration.nom.text().strip()}"
            ),
        )

    def demander_mesure_repetee(self) -> bool:
        """Propose une mesure à la page depuis les cellules sélectionnées."""
        try:
            configuration, _valeurs = self._colonne_et_valeurs_selectionnees()
        except ValueError as erreur:
            self._signaler(message_erreur(erreur))
            return False
        nom = self._demander_nom_mesure(f"{configuration.nom.text().strip()}_moyenne")
        if not nom:
            return False
        try:
            mesure = self.mesure_depuis_selection(nom)
        except ValueError as erreur:
            self._signaler(message_erreur(erreur))
            return False
        self.mesure_proposee.emit(mesure)
        return True


declarer_onglet("tableau", "Tableau", OngletDonnees, EditeurTableau)
