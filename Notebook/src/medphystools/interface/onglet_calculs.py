"""Onglet de calculs : des formules sur les grandeurs enregistrées de la page.

Chaque ligne donne un résultat, avec son unité, son incertitude et son budget. Il devient une
grandeur de la page, avec sa formule : les valeurs brutes ne sont jamais remplacées. Le
facteur k ne change que l'affichage.
"""

from collections.abc import Callable, Mapping, Sequence

import pandas as pd
from pydantic import ValidationError
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from medphystools.archive import Archive
from medphystools.calculs import (
    CONSTANTES,
    FONCTIONS,
    ErreurCalcul,
    budget_de_ligne,
    creer_resultat,
    meme_resultat,
    peut_decroitre_avec,
)
from medphystools.dates import afficher_date, ecrire_date_iso, maintenant
from medphystools.edition import Grandeur
from medphystools.graphes import tableaux_tracables
from medphystools.incertitudes import Budget
from medphystools.interface.messages import message_erreur
from medphystools.interface.onglets import EditeurOnglet, declarer_onglet
from medphystools.interface.texte_en_grand import afficher_en_grand
from medphystools.page import (
    Ajustement,
    ColonneCalculee,
    ColonneDate,
    GrandeurDerivee,
    Indicateur,
    Onglet,
    OngletDonnees,
    Page,
    Scalaire,
    Tableau,
)

_LARGEUR_NOM = 120
_LARGEUR_UNITE = 80
_LARGEUR_RESULTAT = 230
_LARGEUR_COMMENTAIRE = 240
_INDICATION = (
    "Exemples : A0 * exp(-ln(2) * t / T12) sur des scalaires ; decroissance.activite / A0 "
    "ligne par ligne, qui donne une colonne dans le tableau ; decroissance.activite[3] pour "
    "une seule cellule ; derivee(t.y, t.x) ou primitive(t.y, t.x) pour une courbe. Le calcul utilise la dernière version enregistrée des grandeurs."
)
_TITRES = ("Nom", "Formule", "Unité", "Résultat", "", "Commentaire", "")
_SANS_RESULTAT = "—"


def ecrire_colonne(colonne: ColonneCalculee) -> str:
    """Résume une colonne calculée : ses lignes, ses tableaux et son unité."""
    nombre = len(colonne.valeurs)
    unite = "" if colonne.unite == "1" else f" en {colonne.unite}"
    return (
        f"{nombre} ligne{'s' if nombre > 1 else ''}{unite}, "
        f"dans {', '.join(colonne.tableaux)}"
    )


def ecrire_resultat(budget: Budget, k: float) -> str:
    """Écrit un résultat avec son incertitude élargie, en absolu et en relatif."""
    unite = "" if budget.unite == "1" else f" {budget.unite}"
    relative = budget.elargie_relative(k)
    suite = "" if relative is None else f" ({relative:.2g} %)"
    return f"{budget.valeur:.6g} ± {budget.elargie(k):.2g}{unite}{suite}"


class DialogueBudget(QDialog):
    """Montre la part de chaque source dans l'incertitude d'un résultat.

    Pour une colonne calculée, `lire_ligne` donne le budget de chaque ligne.
    """

    def __init__(
        self,
        nom: str,
        budget: Budget | None,
        k: float,
        parent: QWidget | None = None,
        lire_ligne: Callable[[int], Budget] | None = None,
        lignes: int = 0,
    ) -> None:
        super().__init__(parent)
        self.nom = nom
        self.k = k
        self.lire_ligne = lire_ligne
        self.setWindowTitle(f"Budget de {nom}")
        self.setMinimumWidth(640)
        self.resultat = QLabel("")
        self.resultat.setObjectName("titre_page")
        self.resultat.setTextFormat(Qt.TextFormat.PlainText)
        self.champ_ligne = QSpinBox()
        self.champ_ligne.setRange(1, max(lignes, 1))
        self.table = QTableWidget(0, 4)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        explication = QLabel(
            "Une erreur commune compensée dans le calcul y figure avec une part nulle."
        )
        explication.setObjectName("discret")
        boutons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        boutons.button(QDialogButtonBox.StandardButton.Close).setText("Fermer")
        boutons.rejected.connect(self.reject)
        disposition = QVBoxLayout(self)
        disposition.addWidget(self.resultat)
        if lire_ligne is not None:
            ligne_choix = QHBoxLayout()
            ligne_choix.addWidget(QLabel("Ligne du tableau"))
            ligne_choix.addWidget(self.champ_ligne)
            ligne_choix.addStretch()
            disposition.addLayout(ligne_choix)
            self.champ_ligne.valueChanged.connect(self.afficher_ligne)
        disposition.addWidget(self.table)
        disposition.addWidget(explication)
        disposition.addWidget(boutons)
        if budget is not None:
            self._remplir(nom, budget)
        else:
            self.afficher_ligne(1)

    def afficher_ligne(self, ligne: int) -> None:
        """Montre le budget d'une ligne, ou explique pourquoi elle n'a pas de résultat."""
        try:
            self._remplir(f"{self.nom}, ligne {ligne}", self.lire_ligne(ligne))
        except ErreurCalcul as erreur:
            self.table.setRowCount(0)
            self.resultat.setText(f"Ligne {ligne} : {erreur}")

    def _remplir(self, titre: str, budget: Budget) -> None:
        self.resultat.setText(
            f"{titre} = {ecrire_resultat(budget, self.k)}, k = {self.k:g}"
        )
        self.table.setHorizontalHeaderLabels(
            [
                "Source",
                "Type",
                "u" if budget.unite == "1" else f"u ({budget.unite})",
                "Contribution",
            ]
        )
        self.table.setRowCount(len(budget.lignes))
        for rang, ligne in enumerate(budget.lignes):
            for colonne, texte in enumerate(
                (
                    ligne.source,
                    ligne.type,
                    f"{ligne.incertitude_type:.3g}",
                    f"{ligne.contribution_pourcent:.1f} %",
                )
            ):
                self.table.setItem(rang, colonne, QTableWidgetItem(texte))


class DialogueCourbe(QDialog):
    """Choisit une opération et ses deux colonnes : y, et x par rapport auquel calculer."""

    def __init__(
        self, tableaux: Mapping[str, Sequence[str]], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dérivée ou primitive d'une courbe")
        self.tableaux = tableaux
        self.operation = QComboBox()
        self.operation.addItem("Dérivée dy/dx", "derivee")
        self.operation.addItem("Primitive de y dx", "primitive")
        self.tableau = QComboBox()
        self.tableau.addItems(list(tableaux))
        self.y = QComboBox()
        self.x = QComboBox()
        self.tableau.currentTextChanged.connect(self._proposer_colonnes)
        indication = QLabel(
            "La primitive part de 0 à la première ligne du tableau. x doit croître ou "
            "décroître strictement."
            if tableaux
            else "Aucun tableau enregistré dans cette page."
        )
        indication.setObjectName("discret")
        indication.setWordWrap(True)
        formulaire = QFormLayout()
        formulaire.addRow("Opération", self.operation)
        formulaire.addRow("Tableau", self.tableau)
        formulaire.addRow("Colonne y", self.y)
        formulaire.addRow("Colonne x", self.x)
        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.bouton_ok = boutons.button(QDialogButtonBox.StandardButton.Ok)
        self.bouton_ok.setText("Ajouter")
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)
        disposition = QVBoxLayout(self)
        disposition.addLayout(formulaire)
        disposition.addWidget(indication)
        disposition.addWidget(boutons)
        self._proposer_colonnes(self.tableau.currentText())
        self.y.currentTextChanged.connect(self._actualiser)
        self.x.currentTextChanged.connect(self._actualiser)
        self._actualiser()

    def _proposer_colonnes(self, tableau: str) -> None:
        colonnes = list(self.tableaux.get(tableau, ()))
        self.y.clear()
        self.y.addItems(colonnes)
        self.x.clear()
        self.x.addItems(colonnes)
        # Le plus souvent, x est la première colonne et y la suivante.
        if len(colonnes) > 1:
            self.y.setCurrentIndex(1)

    def _actualiser(self) -> None:
        self.bouton_ok.setEnabled(
            bool(self.y.currentText()) and self.y.currentText() != self.x.currentText()
        )

    def choix(self) -> tuple[str, str, str, str]:
        """Renvoie l'opération, le tableau, la colonne y et la colonne x."""
        return (
            self.operation.currentData(),
            self.tableau.currentText(),
            self.y.currentText(),
            self.x.currentText(),
        )


_HEURE_ACTUELLE = "actuelle"
_DATE_CHOISIE = "choisie"


class DialogueDecroissance(QDialog):
    """Compose decroissance(valeur, periode, date_cible) : A0 à une autre date et heure.

    L'heure actuelle s'écrit dans la formule, avec son fuseau : le résultat reste ainsi le même
    à chaque relecture, y compris sur une page validée.
    """

    def __init__(
        self, page: Page, parent: QWidget | None = None, valeur_initiale: str = ""
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Décroissance radioactive")
        self.page = page
        self.scalaires = [g for g in page.mesures if isinstance(g, Scalaire)]
        self.tableaux_dates = {
            g.nom: [c.nom for c in g.colonnes if isinstance(c, ColonneDate)]
            for g in page.mesures
            if isinstance(g, Tableau)
        }
        self.valeur = QComboBox()
        for scalaire in self.scalaires:
            self.valeur.addItem(f"{scalaire.nom} ({scalaire.unite})", scalaire.nom)
        for tableau, colonnes in tableaux_tracables(page).items():
            for colonne in colonnes:
                reference = f"{tableau}.{colonne}"
                self.valeur.addItem(f"{reference} (toute la colonne)", reference)
        if self.valeur.findData(valeur_initiale) >= 0:
            self.valeur.setCurrentIndex(self.valeur.findData(valeur_initiale))
        self.constante = QComboBox()
        self.constante.setToolTip(
            "Scalaire en unité de temps pour une période, en 1/h par exemple pour une constante"
        )
        for grandeur in page.mesures:
            if isinstance(
                grandeur, (Scalaire, GrandeurDerivee)
            ) and peut_decroitre_avec(grandeur.unite):
                self.constante.addItem(
                    f"{grandeur.nom} ({grandeur.unite})", grandeur.nom
                )
        self.reference = QComboBox()
        self.cible = QComboBox()
        self.cible.addItem("Heure actuelle", _HEURE_ACTUELLE)
        self.cible.addItem("Date et heure choisies", _DATE_CHOISIE)
        for tableau, colonnes in self.tableaux_dates.items():
            for colonne in colonnes:
                self.cible.addItem(
                    f"Chaque ligne de {tableau}.{colonne}", f"{tableau}.{colonne}"
                )
        self.date_choisie = QDateTimeEdit(QDateTime.currentDateTime())
        self.date_choisie.setDisplayFormat("dd/MM/yyyy HH:mm:ss")
        self.date_choisie.setCalendarPopup(True)
        indication = QLabel(
            "Le résultat garde l'incertitude de la valeur et de la période. L'heure actuelle "
            "est celle de l'ajout, écrite dans la formule : ajouter un autre calcul pour une "
            "heure plus récente."
            if self.valeur.count() and self.constante.count()
            else "Enregistrer d'abord dans la page la valeur à corriger, et un scalaire pour "
            "la période (en h, min…) ou la constante de décroissance (en 1/h…)."
        )
        indication.setObjectName("discret")
        indication.setWordWrap(True)
        formulaire = QFormLayout()
        formulaire.addRow("Valeur qui décroît", self.valeur)
        formulaire.addRow("Période ou constante", self.constante)
        formulaire.addRow("Valeur mesurée le", self.reference)
        formulaire.addRow("Calculer à", self.cible)
        self.ligne_date = self.date_choisie
        formulaire.addRow("Date et heure", self.date_choisie)
        self.formulaire = formulaire
        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.bouton_ok = boutons.button(QDialogButtonBox.StandardButton.Ok)
        self.bouton_ok.setText("Ajouter")
        self.bouton_ok.setEnabled(bool(self.valeur.count() and self.constante.count()))
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)
        disposition = QVBoxLayout(self)
        disposition.addLayout(formulaire)
        disposition.addWidget(indication)
        disposition.addWidget(boutons)
        self.setMinimumWidth(520)
        self.valeur.currentIndexChanged.connect(self._proposer_references)
        self.cible.currentIndexChanged.connect(self._montrer_date)
        self._proposer_references()
        self._montrer_date()

    def _proposer_references(self) -> None:
        """Un scalaire porte sa date. Une colonne prend une colonne de dates ou celle de la page."""
        self.reference.clear()
        valeur = self.valeur.currentData()
        if valeur is None:
            return
        page = afficher_date(self.page.date_mesure)
        if "." not in valeur:
            scalaire = next(s for s in self.scalaires if s.nom == valeur)
            date = scalaire.horodatage or self.page.date_mesure
            origine = "du scalaire" if scalaire.horodatage else "de la page"
            self.reference.addItem(f"{afficher_date(date)} (date {origine})", None)
            self.reference.setEnabled(False)
            return
        self.reference.setEnabled(True)
        tableau = valeur.split(".")[0]
        for colonne in self.tableaux_dates.get(tableau, ()):
            self.reference.addItem(f"Chaque ligne de {colonne}", f"{tableau}.{colonne}")
        self.reference.addItem(
            f"{page} (date de la page)",
            f'"{ecrire_date_iso(self.page.date_mesure)}"',
        )

    def _montrer_date(self) -> None:
        visible = self.cible.currentData() == _DATE_CHOISIE
        self.date_choisie.setVisible(visible)
        self.formulaire.labelForField(self.date_choisie).setVisible(visible)

    def _cible_ecrite(self) -> str:
        cible = self.cible.currentData()
        if cible == _HEURE_ACTUELLE:
            return f'"{ecrire_date_iso(maintenant())}"'
        if cible == _DATE_CHOISIE:
            return f'"{ecrire_date_iso(self.date_choisie.dateTime().toPython().astimezone())}"'
        return cible

    def formule(self) -> str:
        """Écrit la formule. L'heure actuelle est lue ici, au moment de l'ajout."""
        arguments = [
            self.valeur.currentData(),
            self.constante.currentData(),
            self._cible_ecrite(),
        ]
        if self.reference.currentData() is not None:
            arguments.append(self.reference.currentData())
        return f"decroissance({', '.join(arguments)})"


def choisir_decroissance(
    page: Page | None, parent: QWidget, valeur_initiale: str = ""
) -> str | None:
    """Demande une décroissance et renvoie sa formule, ou None si on annule."""
    dialogue = DialogueDecroissance(page, parent, valeur_initiale)
    accepte = dialogue.exec() == QDialog.DialogCode.Accepted
    formule = dialogue.formule() if accepte else None
    dialogue.deleteLater()
    return formule


class LigneCalcul:
    """Une formule et son résultat."""

    def __init__(
        self,
        editeur: "EditeurCalculs",
        origine: GrandeurDerivee | ColonneCalculee | None = None,
    ) -> None:
        self.editeur = editeur
        self.origine = origine
        self.resultat: GrandeurDerivee | ColonneCalculee | None = origine
        self.erreur: str | None = None
        self.avertissement: str | None = None

        self.nom = QLineEdit(origine.nom if origine else "")
        self.nom.setPlaceholderText("nom")
        self.nom.setMinimumWidth(_LARGEUR_NOM)
        self.formule = QLineEdit(origine.formule if origine else "")
        self.formule.setPlaceholderText("A0 * exp(-ln(2) * t / T12)")
        self.unite = QLineEdit(origine.unite if origine else "")
        self.unite.setPlaceholderText("auto")
        self.unite.setToolTip("Unité du résultat. Vide : celle que donne le calcul.")
        self.unite.setMaximumWidth(_LARGEUR_UNITE)
        self.affichage = QLabel(_SANS_RESULTAT)
        self.affichage.setTextFormat(Qt.TextFormat.PlainText)
        self.affichage.setMinimumWidth(_LARGEUR_RESULTAT)
        self.affichage.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.bouton_budget = QPushButton("Budget…")
        self.bouton_budget.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bouton_budget.clicked.connect(self.demander_budget)
        self.commentaire = QLineEdit(origine.commentaire if origine else "")
        self.commentaire.setPlaceholderText("Commentaire")
        self.commentaire.setMinimumWidth(_LARGEUR_COMMENTAIRE)
        afficher_en_grand(
            self.commentaire, lambda: f"Commentaire de {self.nom.text().strip()}"
        )
        self.bouton_retirer = QPushButton("×")
        self.bouton_retirer.setToolTip("Retirer ce calcul")
        self.bouton_retirer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bouton_retirer.clicked.connect(lambda: editeur.retirer_ligne(self))
        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.message.setVisible(False)

        for champ in (self.nom, self.formule, self.unite, self.commentaire):
            champ.textChanged.connect(self._modifiee)
        self.formule.cursorPositionChanged.connect(
            lambda: editeur.noter_ligne_active(self)
        )

    def champs(self) -> tuple[QWidget, ...]:
        return (
            self.nom,
            self.formule,
            self.unite,
            self.affichage,
            self.bouton_budget,
            self.commentaire,
            self.bouton_retirer,
        )

    def est_vide(self) -> bool:
        """Une ligne jamais remplie n'est pas une erreur : elle est simplement ignorée."""
        return (
            self.origine is None
            and not self.nom.text().strip()
            and not self.formule.text().strip()
            and not self.commentaire.text().strip()
        )

    def revenir_en_auto(self) -> None:
        """Vide l'unité si c'est celle que donne le calcul : elle suivra alors la formule.

        Seule une unité vraiment demandée, comme des kBq, reste écrite dans le champ.
        """
        if self.origine is None:
            return
        try:
            automatique = creer_resultat(
                self.editeur.page,
                self.origine.nom,
                self.origine.formule,
                self.editeur.lire_tableau,
            )
        except (ErreurCalcul, ValidationError):
            return
        if automatique.unite == self.origine.unite:
            self.unite.clear()

    def _modifiee(self) -> None:
        self.calculer()
        self.afficher()
        self.editeur.ligne_modifiee()

    def _depuis_l_origine(
        self, recalculee: GrandeurDerivee | ColonneCalculee
    ) -> GrandeurDerivee | ColonneCalculee:
        """Garde le résultat enregistré s'il n'a pas changé : ouvrir une page ne la modifie pas."""
        origine = self.origine
        if (
            origine is not None
            and origine.formule == recalculee.formule
            and origine.unite == recalculee.unite
            and meme_resultat(origine, recalculee)
        ):
            return origine.model_copy(
                update={
                    "nom": recalculee.nom,
                    "commentaire": recalculee.commentaire,
                }
            )
        return recalculee

    def calculer(self) -> None:
        """Refait le calcul sur la dernière version enregistrée de la page."""
        self.resultat = None
        self.erreur = None
        self.avertissement = None
        if self.est_vide():
            return
        nom = self.nom.text().strip()
        if not nom:
            self.erreur = "le nom est obligatoire"
            return
        if not self.formule.text().strip():
            self.erreur = f"{nom} : la formule est obligatoire"
            return
        try:
            self.resultat = self._depuis_l_origine(
                creer_resultat(
                    self.editeur.page,
                    nom,
                    self.formule.text(),
                    self.editeur.lire_tableau,
                    self.unite.text(),
                    self.commentaire.text(),
                )
            )
        except ValidationError as erreur:
            self.erreur = f"{nom} : {message_erreur(erreur)}"
        except ErreurCalcul as erreur:
            self.erreur = f"{nom} : {erreur}"
        if isinstance(self.resultat, ColonneCalculee):
            sans_resultat = [
                str(rang)
                for rang, valeur in enumerate(self.resultat.valeurs, start=1)
                if valeur is None
            ]
            if sans_resultat:
                self.avertissement = (
                    f"{nom} : pas de résultat aux lignes {', '.join(sans_resultat)} "
                    "(voir leur budget)."
                )

    def afficher(self) -> None:
        k = self.editeur.champ_k.value()
        if self.resultat is None:
            self.affichage.setText(_SANS_RESULTAT)
            self.bouton_budget.setEnabled(False)
        elif isinstance(self.resultat, ColonneCalculee):
            self.affichage.setText(ecrire_colonne(self.resultat))
            self.bouton_budget.setEnabled(True)
        else:
            self.affichage.setText(ecrire_resultat(self.resultat.budget(), k))
            self.bouton_budget.setEnabled(True)
        message = self.erreur or self.avertissement
        self.message.setText(message or "")
        self.message.setVisible(message is not None)

    def demander_budget(self) -> None:
        if self.resultat is not None:
            self.editeur.montrer_budget(self.resultat)


class EditeurCalculs(EditeurOnglet):
    """Édite les calculs d'une page."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        self.archive: Archive | None = None
        self.lignes: list[LigneCalcul] = []
        self._ligne_active: LigneCalcul | None = None
        self._prochain_rang = 1
        self._chargement = True
        self._noms_possedes: list[str] = []
        self._references_conservees: list[str] = []

        self.bouton_ajouter = QPushButton("Ajouter un calcul")
        self.bouton_ajouter.clicked.connect(lambda: self.ajouter_ligne().nom.setFocus())
        self.bouton_courbe = QPushButton("Dérivée ou primitive…")
        self.bouton_courbe.setToolTip(
            "Calcule la courbe dérivée ou la courbe primitive d'une colonne, "
            "ligne par ligne : elle s'affiche dans le tableau et se trace dans un graphe."
        )
        self.bouton_courbe.clicked.connect(self.demander_courbe)
        self.bouton_decroissance = QPushButton("Décroissance…")
        self.bouton_decroissance.setToolTip(
            "Calcule une activité, ou toute grandeur qui décroît, à une autre date et heure, "
            "pour un scalaire ou toute une colonne"
        )
        self.bouton_decroissance.clicked.connect(lambda: self.demander_decroissance())
        self.insertion = QComboBox()
        self.insertion.setToolTip("Insère ce nom dans la formule en cours")
        self.insertion.activated.connect(self._inserer_choix)
        self.champ_k = QDoubleSpinBox()
        self.champ_k.setRange(0.1, 10.0)
        self.champ_k.setSingleStep(0.5)
        self.champ_k.setValue(2.0)
        self.champ_k.setToolTip(
            "Facteur d'élargissement : il change seulement l'affichage."
        )
        self.champ_k.valueChanged.connect(self._k_change)
        ligne_haut = QHBoxLayout()
        ligne_haut.addWidget(self.bouton_ajouter)
        ligne_haut.addWidget(self.bouton_courbe)
        ligne_haut.addWidget(self.bouton_decroissance)
        ligne_haut.addWidget(self.insertion)
        ligne_haut.addStretch()
        ligne_haut.addWidget(QLabel("k ="))
        ligne_haut.addWidget(self.champ_k)

        indication = QLabel(_INDICATION)
        indication.setObjectName("discret")
        indication.setWordWrap(True)

        self.conteneur = QFrame()
        self.grille = QGridLayout(self.conteneur)
        for colonne, titre in enumerate(_TITRES):
            self.grille.addWidget(QLabel(titre), 0, colonne)
        self.grille.setColumnStretch(1, 1)
        self.grille.setRowStretch(9999, 1)
        defilement = QScrollArea()
        defilement.setWidgetResizable(True)
        defilement.setWidget(self.conteneur)

        disposition = QVBoxLayout(self)
        disposition.addLayout(ligne_haut)
        disposition.addWidget(indication)
        disposition.addWidget(defilement, stretch=1)
        self._chargement = False

    # --- Chargement -----------------------------------------------------------------------

    def lire_tableau(self, nom: str) -> pd.DataFrame:
        """Relit un tableau enregistré de la page."""
        return self.archive.lire_tableau(self.page.id, nom)

    def charger(self, archive: Archive) -> None:
        self.archive = archive
        self._chargement = True
        for reference in getattr(self.onglet, "grandeurs", ()):
            grandeur = next((g for g in self.page.mesures if g.nom == reference), None)
            if isinstance(grandeur, (GrandeurDerivee, ColonneCalculee)):
                self.ajouter_ligne(grandeur)
                self._noms_possedes.append(grandeur.nom)
            else:
                self._references_conservees.append(reference)
        if not self.lignes:
            self.ajouter_ligne()
        self._chargement = False
        for ligne in self.lignes:
            ligne.revenir_en_auto()
        self.recalculer()
        self._proposer_noms()

    def _proposer_noms(self) -> None:
        self.insertion.clear()
        self.insertion.addItem("Insérer…")
        noms = [
            g.nom
            for g in (self.page.mesures if self.page else ())
            if isinstance(g, (Scalaire, GrandeurDerivee, Indicateur))
        ]
        self.insertion.addItems(noms)
        self.insertion.addItems(
            [
                f"{g.nom}.{parametre.nom}"
                for g in (self.page.mesures if self.page else ())
                if isinstance(g, Ajustement)
                for parametre in g.parametres
            ]
        )
        if self.page:
            self.insertion.addItems(
                [
                    f"{tableau}.{colonne}"
                    for tableau, colonnes in tableaux_tracables(self.page).items()
                    for colonne in colonnes
                ]
            )
        self.insertion.addItems([f"{nom}()" for nom in (*sorted(FONCTIONS), "sqrt")])
        self.insertion.addItems(["derivee()", "primitive()", "decroissance()"])
        self.insertion.addItems(list(CONSTANTES))

    def _inserer_choix(self, rang: int) -> None:
        if rang > 0:
            self.inserer(self.insertion.itemText(rang))
        self.insertion.setCurrentIndex(0)

    def noter_ligne_active(self, ligne: LigneCalcul) -> None:
        self._ligne_active = ligne

    def inserer(self, texte: str) -> None:
        """Insère un nom dans la formule en cours, le curseur dans les parenthèses."""
        ligne = self._ligne_active or (self.lignes[-1] if self.lignes else None)
        if ligne is None:
            return
        ligne.formule.insert(texte)
        if texte.endswith("()"):
            ligne.formule.cursorBackward(False)
        ligne.formule.setFocus()

    # --- Lignes ---------------------------------------------------------------------------

    def ajouter_ligne(
        self, origine: GrandeurDerivee | ColonneCalculee | None = None
    ) -> LigneCalcul:
        ligne = LigneCalcul(self, origine)
        for colonne, champ in enumerate(ligne.champs()):
            self.grille.addWidget(champ, self._prochain_rang, colonne)
            champ.show()
        self.grille.addWidget(ligne.message, self._prochain_rang + 1, 0, 1, 7)
        self._prochain_rang += 2
        self.lignes.append(ligne)
        self._ligne_active = ligne
        ligne.afficher()
        return ligne

    def ajouter_courbe(
        self, operation: str, tableau: str, y: str, x: str
    ) -> LigneCalcul:
        """Ajoute la courbe dérivée ou primitive d'une colonne, avec un nom libre."""
        prefixe = {"derivee": "d", "primitive": "P"}[operation]
        return self._ajouter_avec_nom_libre(
            f"{prefixe}_{y}", f"{operation}({tableau}.{y}, {tableau}.{x})"
        )

    def _ajouter_avec_nom_libre(self, base: str, formule: str) -> LigneCalcul:
        pris = {g.nom for g in (self.page.mesures if self.page else ())}
        pris.update(ligne.nom.text().strip() for ligne in self.lignes)
        nom = base
        rang = 2
        while nom in pris:
            nom = f"{base}_{rang}"
            rang += 1
        vide = next((ligne for ligne in self.lignes if ligne.est_vide()), None)
        ligne = vide or self.ajouter_ligne()
        ligne.nom.setText(nom)
        ligne.formule.setText(formule)
        return ligne

    def ajouter_decroissance(self, formule: str) -> LigneCalcul:
        """Ajoute un calcul de décroissance, nommé d'après la valeur corrigée."""
        valeur = formule.removeprefix("decroissance(").split(",")[0]
        return self._ajouter_avec_nom_libre(f"{valeur.split('.')[-1]}_decru", formule)

    def demander_decroissance(self, valeur_initiale: str = "") -> bool:
        """Demande la valeur, la période et la date, puis ajoute le calcul."""
        formule = choisir_decroissance(self.page, self, valeur_initiale)
        if formule is None:
            return False
        self.ajouter_decroissance(formule)
        return True

    def _choisir_courbe(self) -> tuple[str, str, str, str] | None:
        tableaux = tableaux_tracables(self.page) if self.page else {}
        dialogue = DialogueCourbe(tableaux, self)
        if dialogue.exec() == QDialog.DialogCode.Accepted:
            return dialogue.choix()
        return None

    def demander_courbe(self) -> bool:
        """Demande la colonne à dériver ou à intégrer, puis ajoute son calcul."""
        choix = self._choisir_courbe()
        if choix is None:
            return False
        self.ajouter_courbe(*choix)
        return True

    def retirer_ligne(self, ligne: LigneCalcul) -> None:
        """Retire un calcul : il quittera la page, et l'historique le garde."""
        for champ in (*ligne.champs(), ligne.message):
            self.grille.removeWidget(champ)
            champ.setParent(None)
            champ.deleteLater()
        self.lignes.remove(ligne)
        if self._ligne_active is ligne:
            self._ligne_active = None
        self.ligne_modifiee()

    def ligne_modifiee(self) -> None:
        if not self._chargement:
            self.modifie.emit()

    def recalculer(self) -> None:
        """Refait tous les calculs, sans considérer cela comme une saisie."""
        for ligne in self.lignes:
            ligne.calculer()
            ligne.afficher()

    def _k_change(self) -> None:
        for ligne in self.lignes:
            ligne.afficher()

    def _montrer_dialogue(self, dialogue: QDialog) -> None:
        dialogue.exec()
        dialogue.deleteLater()

    def montrer_budget(self, derivee: GrandeurDerivee | ColonneCalculee) -> None:
        if isinstance(derivee, ColonneCalculee):
            dialogue = DialogueBudget(
                derivee.nom,
                None,
                self.champ_k.value(),
                parent=self,
                lire_ligne=lambda ligne: budget_de_ligne(
                    self.page, derivee, ligne, self.lire_tableau
                ),
                lignes=len(derivee.valeurs),
            )
        else:
            dialogue = DialogueBudget(
                derivee.nom, derivee.budget(), self.champ_k.value(), parent=self
            )
        self._montrer_dialogue(dialogue)

    # --- Contrat d'un éditeur -------------------------------------------------------------

    def _noms_repetes(self) -> set[str]:
        noms = self.noms_utilises()
        return {nom for nom in noms if nom and noms.count(nom) > 1}

    def _grandeurs_par_ligne(self) -> list[Grandeur]:
        """Le résultat de chaque ligne ; une ligne en erreur garde sa dernière version."""
        repetes = self._noms_repetes()
        resultat: list[Grandeur] = []
        for ligne in self.lignes:
            nom = ligne.nom.text().strip()
            if ligne.resultat is not None and nom not in repetes:
                resultat.append(ligne.resultat)
            elif ligne.origine is not None:
                resultat.append(ligne.origine)
        return resultat

    def grandeurs_possedees(self) -> list[str]:
        return list(self._noms_possedes)

    def grandeurs(self) -> list[Grandeur]:
        return self._grandeurs_par_ligne()

    def noms_utilises(self) -> list[str]:
        return [
            ligne.nom.text().strip() for ligne in self.lignes if not ligne.est_vide()
        ]

    def valeur(self) -> Onglet:
        noms = [grandeur.nom for grandeur in self._grandeurs_par_ligne()]
        return self.onglet.model_copy(
            update={
                "grandeurs": tuple(dict.fromkeys(noms))
                + tuple(self._references_conservees)
            }
        )

    def erreurs(self) -> list[str]:
        resultat: list[str] = [
            ligne.erreur for ligne in self.lignes if ligne.erreur is not None
        ]
        resultat += [
            f"nom « {nom} » utilisé deux fois" for nom in sorted(self._noms_repetes())
        ]
        return resultat

    def enregistre(self, page: Page) -> None:
        """Refait les calculs sur la page enregistrée, et dit si un résultat a changé.

        Un brouillon s'enregistre alors à nouveau. Une page validée le propose, à enregistrer
        avec un motif.
        """
        super().enregistre(page)
        self.onglet = self.valeur()
        par_nom = {grandeur.nom: grandeur for grandeur in page.mesures}
        for ligne in self.lignes:
            nom = ligne.nom.text().strip()
            enregistree = par_nom.get(nom)
            if isinstance(enregistree, (GrandeurDerivee, ColonneCalculee)):
                ligne.origine = enregistree
        self._noms_possedes = [g.nom for g in self._grandeurs_par_ligne()]
        self.recalculer()
        self._proposer_noms()
        if any(par_nom.get(g.nom) != g for g in self._grandeurs_par_ligne()):
            self.modifie.emit()


declarer_onglet("calculs", "Calculs", OngletDonnees, EditeurCalculs)
