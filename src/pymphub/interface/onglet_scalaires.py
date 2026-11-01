"""Onglet de scalaires : valeur, unité et incertitude, toutes obligatoires.

Une ligne incomplète n'est jamais enregistrée. Elle ne retient pas les lignes complètes,
et une ligne déjà enregistrée garde sa dernière version valide tant qu'on la complète.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pymphub.edition import (
    Grandeur,
    date_saisie_vers_mesure,
    lire_nombre,
    lire_serie,
    suggerer_nom,
)
from pymphub.incertitudes import (
    Budget,
    Composante,
    composante_exacte,
    etablir_budget,
    repetitions,
)
from pymphub.interface.messages import message_erreur
from pymphub.interface.onglets import EditeurOnglet, declarer_onglet
from pymphub.page import Identifiant, Onglet, OngletDonnees, Page, Scalaire
from pymphub.unites import sont_compatibles, verifier_unite

_DATE_VIDE = QDateTime(1900, 1, 1, 0, 0, 0)
_FORMAT_DATE = "dd/MM/yyyy HH:mm"
_SOURCE_REPETABILITE = "répétabilité"
_LOIS = ("normale", "rectangulaire", "triangulaire")


def _nombre_ecrit(valeur: float | None) -> str:
    """Écrit un nombre sans perdre de chiffre : le relire redonne exactement la même valeur."""
    return "" if valeur is None else repr(float(valeur))


def _marquer(champ: QWidget, invalide: bool) -> None:
    """Signale visuellement un champ invalide, grâce à une règle du thème."""
    if champ.property("invalide") != invalide:
        champ.setProperty("invalide", invalide)
        champ.style().unpolish(champ)
        champ.style().polish(champ)


class LigneComposante:
    """Les champs d'une composante d'incertitude, sur une ligne d'une grille."""

    def __init__(
        self,
        grille: QGridLayout,
        rang: int,
        unite: str,
        composante: Composante | None = None,
    ) -> None:
        self.type = QComboBox()
        self.type.addItems(["B", "A"])
        self.valeur = QLineEdit()
        self.valeur.setPlaceholderText("valeur")
        self.unite = QLineEdit(unite)
        self.relative = QCheckBox("%")
        self.relative.setToolTip("Valeur exprimée en % de la mesure")
        self.loi = QComboBox()
        self.loi.addItems(_LOIS)
        self.k = QLineEdit()
        self.k.setPlaceholderText("1")
        self.k.setToolTip(
            "Facteur de la valeur déclarée, par exemple 2 pour un certificat"
        )
        self.ddl = QLineEdit()
        self.ddl.setPlaceholderText("∞")
        self.ddl.setToolTip(
            "Degrés de liberté : obligatoires pour une composante de type A"
        )
        self.source = QLineEdit()
        self.source.setPlaceholderText("Résolution, certificat n°…")
        self.erreur_commune = QLineEdit()
        self.erreur_commune.setPlaceholderText("n° de certificat partagé")
        self.bouton_retirer = QPushButton("×")
        self.bouton_retirer.setToolTip("Retirer cette composante")
        self.bouton_retirer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.champs: list[QWidget] = [
            self.type,
            self.valeur,
            self.unite,
            self.relative,
            self.loi,
            self.k,
            self.ddl,
            self.source,
            self.erreur_commune,
            self.bouton_retirer,
        ]
        for colonne, champ in enumerate(self.champs):
            grille.addWidget(champ, rang, colonne)

        if composante is not None:
            self.type.setCurrentText(composante.type)
            self.valeur.setText(_nombre_ecrit(composante.valeur))
            self.unite.setText(composante.unite or "")
            self.relative.setChecked(composante.relative)
            self.loi.setCurrentText(composante.loi)
            self.k.setText("" if composante.k == 1.0 else _nombre_ecrit(composante.k))
            self.ddl.setText(
                "" if composante.ddl is None else _nombre_ecrit(composante.ddl)
            )
            self.source.setText(composante.source)
            self.erreur_commune.setText(composante.erreur_commune or "")
        self.relative.toggled.connect(self._actualiser)
        self._actualiser()

    def _actualiser(self) -> None:
        self.unite.setEnabled(not self.relative.isChecked())

    def signaler_les_changements(self, rappel: object) -> None:
        """Appelle `rappel` à chaque modification d'un champ."""
        for champ in (
            self.valeur,
            self.unite,
            self.k,
            self.ddl,
            self.source,
            self.erreur_commune,
        ):
            champ.textChanged.connect(rappel)
        self.type.currentIndexChanged.connect(rappel)
        self.loi.currentIndexChanged.connect(rappel)
        self.relative.toggled.connect(rappel)

    def composante(self) -> Composante:
        """Construit la composante. Lève `ValueError` si un champ est invalide."""
        return Composante(
            type=self.type.currentText(),
            valeur=lire_nombre(self.valeur.text()),
            unite=None if self.relative.isChecked() else self.unite.text().strip(),
            relative=self.relative.isChecked(),
            loi=self.loi.currentText(),
            k=lire_nombre(self.k.text()) if self.k.text().strip() else 1.0,
            ddl=lire_nombre(self.ddl.text()) if self.ddl.text().strip() else None,
            source=self.source.text().strip(),
            erreur_commune=self.erreur_commune.text().strip() or None,
        )

    def retirer(self, grille: QGridLayout) -> None:
        """Retire les champs de la grille et les détruit tout de suite."""
        for champ in self.champs:
            grille.removeWidget(champ)
            champ.setParent(None)
            champ.deleteLater()


class DialogueIncertitudes(QDialog):
    """Déclare les composantes d'incertitude d'une mesure."""

    def __init__(
        self,
        nom: str,
        unite: str,
        composantes: Sequence[Composante],
        autres: Mapping[str, Sequence[Composante]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Incertitudes de {nom or 'la mesure'}")
        self.setMinimumWidth(1100)
        self.unite_mesure = unite
        self.lignes: list[LigneComposante] = []
        self.autres = dict(autres or {})

        self.grille = QGridLayout()
        for colonne, titre in enumerate(
            (
                "Type",
                "Valeur",
                "Unité",
                "",
                "Loi",
                "k",
                "ddl",
                "Source",
                "Erreur commune",
                "",
            )
        ):
            self.grille.addWidget(QLabel(titre), 0, colonne)
        self.grille.setColumnStretch(1, 1)
        self.grille.setColumnStretch(7, 3)
        self.grille.setColumnStretch(8, 1)
        self._prochain_rang = 1

        self.bouton_ajouter = QPushButton("Ajouter une composante")
        self.bouton_ajouter.clicked.connect(lambda: self.ajouter_ligne())
        self.bouton_exacte = QPushButton("Valeur exacte")
        self.bouton_exacte.setToolTip("Déclare explicitement une incertitude nulle")
        self.bouton_exacte.clicked.connect(self.declarer_exacte)
        self.reprendre = QComboBox()
        self.reprendre.addItem("Reprendre depuis…", None)
        for autre in self.autres:
            self.reprendre.addItem(autre, autre)
        self.reprendre.setEnabled(bool(self.autres))
        self.reprendre.activated.connect(self._reprendre_choisie)
        ligne_boutons = QHBoxLayout()
        ligne_boutons.addWidget(self.bouton_ajouter)
        ligne_boutons.addWidget(self.bouton_exacte)
        ligne_boutons.addWidget(self.reprendre)
        ligne_boutons.addStretch()

        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)

        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.bouton_ok = boutons.button(QDialogButtonBox.StandardButton.Ok)
        self.bouton_ok.setObjectName("principal")
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)

        disposition = QVBoxLayout(self)
        disposition.addLayout(self.grille)
        disposition.addLayout(ligne_boutons)
        disposition.addWidget(self.message)
        disposition.addWidget(boutons)

        for composante in composantes:
            self.ajouter_ligne(composante)
        if not self.lignes:
            self.ajouter_ligne()
        self.lignes[0].valeur.setFocus()
        self._actualiser()

    def ajouter_ligne(self, composante: Composante | None = None) -> LigneComposante:
        """Ajoute une ligne, prête à être remplie."""
        ligne = LigneComposante(
            self.grille, self._prochain_rang, self.unite_mesure, composante
        )
        self._prochain_rang += 1
        ligne.signaler_les_changements(self._actualiser)
        ligne.bouton_retirer.clicked.connect(lambda: self.retirer_ligne(ligne))
        self.lignes.append(ligne)
        self._actualiser()
        return ligne

    def retirer_ligne(self, ligne: LigneComposante) -> None:
        """Retire une composante."""
        ligne.retirer(self.grille)
        self.lignes.remove(ligne)
        self._actualiser()

    def declarer_exacte(self) -> None:
        """Remplace toutes les composantes par une incertitude nulle déclarée."""
        for ligne in list(self.lignes):
            self.retirer_ligne(ligne)
        self.ajouter_ligne(
            composante_exacte(self.unite_mesure if self._unite_valide() else "1")
        )

    def reprendre_depuis(self, nom: str) -> None:
        """Ajoute les composantes déclarées pour une autre mesure de la page."""
        for ligne in [x for x in self.lignes if not x.valeur.text().strip()]:
            self.retirer_ligne(ligne)
        for composante in self.autres[nom]:
            self.ajouter_ligne(composante)

    def _reprendre_choisie(self, position: int) -> None:
        nom = self.reprendre.itemData(position)
        if nom is not None:
            self.reprendre_depuis(nom)
        self.reprendre.setCurrentIndex(0)

    def _unite_valide(self) -> bool:
        return verifier_unite(self.unite_mesure).valide

    def erreur(self) -> str | None:
        """Explique ce qui empêche d'accepter la saisie."""
        if not self.lignes:
            return "L'incertitude est obligatoire : déclarer au moins une composante."
        for ligne in self.lignes:
            try:
                composante = ligne.composante()
            except ValueError as erreur:
                return message_erreur(erreur)
            if (
                not composante.relative
                and self._unite_valide()
                and not sont_compatibles(composante.unite, self.unite_mesure)
            ):
                return f"« {composante.unite} » n'est pas compatible avec {self.unite_mesure}."
        return None

    def _actualiser(self) -> None:
        erreur = self.erreur()
        self.message.setText(erreur or "")
        self.bouton_ok.setEnabled(erreur is None)

    def composantes(self) -> tuple[Composante, ...]:
        """Renvoie les composantes saisies."""
        return tuple(ligne.composante() for ligne in self.lignes)


class DialogueRepetitions(QDialog):
    """Saisie ou collage de mesures répétées : donne la moyenne et le type A."""

    def __init__(
        self,
        unite: str,
        valeurs: Sequence[float] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mesures répétées")
        self.setMinimumWidth(420)
        self.unite = unite
        consigne = QLabel(
            "Saisissez ou collez les valeurs, séparées par des retours à la ligne, "
            "des espaces ou des points-virgules."
        )
        consigne.setWordWrap(True)
        self.texte = QPlainTextEdit("\n".join(_nombre_ecrit(v) for v in valeurs))
        self.texte.setTabChangesFocus(True)
        self.resultat = QLabel("")
        self.resultat.setTextFormat(Qt.TextFormat.PlainText)
        self.resultat.setWordWrap(True)
        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.bouton_ok = boutons.button(QDialogButtonBox.StandardButton.Ok)
        self.bouton_ok.setObjectName("principal")
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)
        disposition = QVBoxLayout(self)
        disposition.addWidget(consigne)
        disposition.addWidget(self.texte)
        disposition.addWidget(self.resultat)
        disposition.addWidget(boutons)
        self.texte.textChanged.connect(self._actualiser)
        self.texte.setFocus()
        self._actualiser()

    def valeurs(self) -> list[float]:
        """Renvoie les valeurs saisies. Lève `ValueError` si l'une n'est pas un nombre."""
        return lire_serie(self.texte.toPlainText())

    def _actualiser(self) -> None:
        try:
            valeurs = self.valeurs()
            moyenne, composante = repetitions(valeurs, self.unite or "1")
        except ValueError as erreur:
            self.resultat.setText(message_erreur(erreur))
            self.bouton_ok.setEnabled(False)
            return
        self.resultat.setText(
            f"n = {len(valeurs)} · moyenne = {moyenne:.10g} · "
            f"u (type A) = {composante.valeur:.3g} · ddl = {composante.ddl:.0f}"
        )
        self.bouton_ok.setEnabled(True)


class LigneScalaire:
    """Une mesure sur une ligne : ses champs vont dans la grille de l'onglet."""

    def __init__(
        self,
        editeur: "EditeurScalaires",
        grandeur: Scalaire | Identifiant | None = None,
    ) -> None:
        self.editeur = editeur
        self.origine = grandeur
        self.composantes: tuple[Composante, ...] = ()
        self.valeurs_repetees: tuple[float, ...] | None = None
        self.horodatage_origine: datetime | None = None

        self.nature = QComboBox()
        self.nature.addItem("Nombre", "scalaire")
        self.nature.addItem("Identifiant", "identifiant")
        self.nom = QLineEdit()
        self.nom.setPlaceholderText("activite")
        self.valeur = QLineEdit()
        self.valeur.setPlaceholderText("valeur")
        self.unite = QLineEdit()
        self.unite.setPlaceholderText("MBq")
        self.bouton_incertitude = QPushButton("Incertitude…")
        self.bouton_incertitude.clicked.connect(self.demander_incertitudes)
        self.bouton_repetitions = QPushButton("Répétitions…")
        self.bouton_repetitions.clicked.connect(self.demander_repetitions)
        self.resultat = QLabel("")
        self.resultat.setTextFormat(Qt.TextFormat.PlainText)
        self.horodatage = QDateTimeEdit()
        self.horodatage.setDisplayFormat(_FORMAT_DATE)
        self.horodatage.setMinimumDateTime(_DATE_VIDE)
        self.horodatage.setSpecialValueText("—")
        self.horodatage.setDateTime(_DATE_VIDE)
        self.horodatage.setToolTip(
            "Heure de cette mesure, si elle diffère de celle de la page"
        )
        # Dans ce champ, la touche Tab parcourt jour, mois, année… : il reste hors du
        # parcours clavier pour ne pas y enfermer une saisie en série.
        self.horodatage.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.bouton_maintenant = QPushButton("Maint.")
        self.bouton_maintenant.setToolTip("Utiliser l'heure actuelle")
        self.bouton_maintenant.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bouton_maintenant.clicked.connect(
            lambda: self.horodatage.setDateTime(QDateTime.currentDateTime())
        )
        self.commentaire = QLineEdit()
        self.commentaire.setPlaceholderText("Commentaire")
        self.bouton_retirer = QPushButton("×")
        self.bouton_retirer.setToolTip("Retirer cette mesure")
        self.bouton_retirer.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bouton_retirer.clicked.connect(lambda: editeur.retirer_ligne(self))

        self.bouton_suggestion = QPushButton("")
        self.bouton_suggestion.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bouton_suggestion.clicked.connect(self.accepter_suggestion)
        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)

        if isinstance(grandeur, Identifiant):
            self.nature.setCurrentIndex(1)
            self.nom.setText(grandeur.nom)
            self.valeur.setText(grandeur.valeur)
            self.commentaire.setText(grandeur.commentaire)
        elif isinstance(grandeur, Scalaire):
            self.nom.setText(grandeur.nom)
            self.valeur.setText(_nombre_ecrit(grandeur.valeur))
            self.unite.setText(grandeur.unite)
            self.composantes = grandeur.composantes
            self.valeurs_repetees = grandeur.repetitions
            self.commentaire.setText(grandeur.commentaire)
            if grandeur.horodatage is not None:
                self.horodatage_origine = grandeur.horodatage
                date = grandeur.horodatage
                self.horodatage.setDateTime(
                    QDateTime(
                        date.year, date.month, date.day, date.hour, date.minute, 0
                    )
                )

        self.nature.currentIndexChanged.connect(self._modifiee)
        self.nom.textChanged.connect(self._modifiee)
        self.valeur.textEdited.connect(self._valeur_tapee)
        self.unite.textChanged.connect(self._modifiee)
        self.unite.returnPressed.connect(self.accepter_suggestion)
        self.commentaire.textChanged.connect(self._modifiee)
        self.horodatage.dateTimeChanged.connect(self._modifiee)

    def champs_grille(self) -> list[QWidget]:
        """Les champs de la première ligne de la grille, dans l'ordre de saisie."""
        return [
            self.nature,
            self.nom,
            self.valeur,
            self.unite,
            self.bouton_incertitude,
            self.bouton_repetitions,
            self.resultat,
            self.horodatage,
            self.bouton_maintenant,
            self.commentaire,
            self.bouton_retirer,
        ]

    def est_identifiant(self) -> bool:
        return self.nature.currentData() == "identifiant"

    def est_vide(self) -> bool:
        """Une ligne jamais remplie n'est pas une erreur : elle est simplement ignorée."""
        return (
            self.origine is None
            and not self.nom.text().strip()
            and not self.valeur.text().strip()
            and not self.unite.text().strip()
            and not self.composantes
            and not self.commentaire.text().strip()
        )

    def _valeur_tapee(self) -> None:
        """Une valeur retapée n'est plus la moyenne des répétitions : on les oublie."""
        if self.valeurs_repetees is not None:
            self.valeurs_repetees = None
            self.composantes = tuple(
                c
                for c in self.composantes
                if not c.source.startswith(_SOURCE_REPETABILITE)
            )
        self._modifiee()

    def _modifiee(self) -> None:
        self.actualiser()
        self.editeur.ligne_modifiee()

    def _horodatage(self) -> datetime | None:
        if self.horodatage.dateTime() == _DATE_VIDE:
            return None
        return date_saisie_vers_mesure(
            self.horodatage.dateTime().toPython(), self.horodatage_origine
        )

    def grandeur(self) -> Scalaire | Identifiant:
        """Construit la grandeur saisie. Lève `ValueError` si elle est incomplète."""
        nom = self.nom.text().strip()
        if not nom:
            raise ValueError("le nom est obligatoire")
        if self.est_identifiant():
            return Identifiant(
                nom=nom,
                valeur=self.valeur.text().strip(),
                commentaire=self.commentaire.text(),
            )
        if not self.valeur.text().strip():
            raise ValueError(f"{nom} : la valeur est obligatoire")
        if not self.unite.text().strip():
            raise ValueError(f"{nom} : l'unité est obligatoire (1 si sans dimension)")
        if not self.composantes:
            raise ValueError(f"{nom} : l'incertitude est obligatoire")
        return Scalaire(
            nom=nom,
            valeur=lire_nombre(self.valeur.text()),
            unite=self.unite.text().strip(),
            composantes=self.composantes,
            horodatage=self._horodatage(),
            commentaire=self.commentaire.text(),
            repetitions=self.valeurs_repetees,
        )

    def erreur(self) -> str | None:
        """Explique pourquoi la ligne est incomplète, sauf si elle est encore vide."""
        if self.est_vide():
            return None
        try:
            self.grandeur()
        except ValueError as erreur:
            return message_erreur(erreur)
        return None

    def actualiser(self) -> None:
        """Met à jour la ligne : unité vérifiée à la frappe, résultat et message."""
        identifiant = self.est_identifiant()
        for champ in (
            self.unite,
            self.bouton_incertitude,
            self.bouton_repetitions,
            self.horodatage,
            self.bouton_maintenant,
        ):
            champ.setEnabled(not identifiant)

        verification = verifier_unite(self.unite.text())
        unite_fausse = (
            not identifiant
            and bool(self.unite.text().strip())
            and not verification.valide
        )
        _marquer(self.unite, unite_fausse)
        suggestion = verification.suggestion if unite_fausse else None
        self.bouton_suggestion.setVisible(suggestion is not None)
        if suggestion is not None:
            self.bouton_suggestion.setText(f"Utiliser {suggestion}")

        texte_nom = self.nom.text().strip()
        _marquer(self.nom, bool(texte_nom) and not texte_nom.isidentifier())

        erreur = self.erreur()
        if unite_fausse:
            erreur = verification.message
        elif texte_nom and not texte_nom.isidentifier():
            proposition = suggerer_nom(texte_nom)
            suite = f" Par exemple : {proposition}." if proposition else ""
            erreur = (
                f"Nom « {texte_nom} » invalide : pas d'espace ni de parenthèse.{suite}"
            )
        self.message.setText(erreur or "")
        self.message.setVisible(erreur is not None)

        self.bouton_incertitude.setText(
            "Incertitude…"
            if identifiant or not self.composantes
            else f"{len(self.composantes)} composante{'s' if len(self.composantes) > 1 else ''}…"
        )
        self.afficher_resultat(self.editeur.champ_k.value())

    def budget(self) -> Budget | None:
        """Calcule le budget si la ligne le permet, sans rien enregistrer."""
        if self.est_identifiant() or not self.composantes:
            return None
        try:
            return etablir_budget(
                lire_nombre(self.valeur.text()),
                self.unite.text().strip(),
                self.composantes,
            )
        except ValueError:
            return None

    def afficher_resultat(self, k: float) -> None:
        """Affiche U en absolu et en relatif, au facteur k choisi."""
        budget = self.budget()
        if budget is None:
            self.resultat.setText("")
            return
        relative = budget.elargie_relative(k)
        suite = "" if relative is None else f" ({relative:.2g} %)"
        self.resultat.setText(f"± {budget.elargie(k):.2g} {budget.unite}{suite}")

    def accepter_suggestion(self) -> bool:
        """Remplace l'unité par la suggestion, en un geste : clic ou touche Entrée."""
        suggestion = verifier_unite(self.unite.text()).suggestion
        if verifier_unite(self.unite.text()).valide or suggestion is None:
            return False
        self.unite.setText(suggestion)
        return True

    def _demander_composantes(self) -> tuple[Composante, ...] | None:
        autres = {
            ligne.nom.text().strip(): ligne.composantes
            for ligne in self.editeur.lignes
            if ligne is not self and ligne.composantes
        }
        dialogue = DialogueIncertitudes(
            self.nom.text().strip(),
            self.unite.text().strip(),
            self.composantes,
            autres,
            self.editeur,
        )
        if dialogue.exec() == QDialog.DialogCode.Accepted:
            return dialogue.composantes()
        return None

    def demander_incertitudes(self) -> None:
        """Ouvre la déclaration des incertitudes."""
        composantes = self._demander_composantes()
        if composantes is not None:
            self.composantes = composantes
            self._modifiee()

    def _demander_repetitions(self) -> list[float] | None:
        dialogue = DialogueRepetitions(
            self.unite.text().strip(), self.valeurs_repetees or (), self.editeur
        )
        if dialogue.exec() == QDialog.DialogCode.Accepted:
            return dialogue.valeurs()
        return None

    def demander_repetitions(self) -> None:
        """Ouvre la saisie de mesures répétées."""
        if not verifier_unite(self.unite.text()).valide:
            self.message.setText("Indiquez d'abord l'unité de ces mesures.")
            self.message.setVisible(True)
            return
        valeurs = self._demander_repetitions()
        if valeurs is not None:
            self.appliquer_repetitions(valeurs)

    def appliquer_repetitions(self, valeurs: Sequence[float]) -> None:
        """Prend la moyenne pour valeur et remplace la composante de répétabilité.

        Les valeurs brutes sont conservées avec la mesure, et les composantes de type B
        déjà déclarées restent à leur place.
        """
        moyenne, composante = repetitions(valeurs, self.unite.text().strip())
        autres = tuple(
            c for c in self.composantes if not c.source.startswith(_SOURCE_REPETABILITE)
        )
        self.composantes = (composante, *autres)
        self.valeurs_repetees = tuple(valeurs)
        self.valeur.setText(_nombre_ecrit(moyenne))
        self._modifiee()


class EditeurScalaires(EditeurOnglet):
    """Édite les scalaires et les identifiants d'un onglet."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        self.lignes: list[LigneScalaire] = []
        self._chargement = True
        self._prochain_rang = 1

        self.champ_k = QDoubleSpinBox()
        self.champ_k.setRange(0.1, 10.0)
        self.champ_k.setSingleStep(0.5)
        self.champ_k.setValue(2.0)
        self.champ_k.setToolTip(
            "Facteur d'élargissement : il change seulement l'affichage."
        )
        self.champ_k.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.champ_k.valueChanged.connect(self._k_change)
        self.bouton_ajouter = QPushButton("Ajouter une mesure (Ctrl+Entrée)")
        self.bouton_ajouter.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bouton_ajouter.clicked.connect(self.ajouter_et_aller_a_la_ligne)
        ligne_haut = QHBoxLayout()
        ligne_haut.addWidget(self.bouton_ajouter)
        ligne_haut.addStretch()
        ligne_haut.addWidget(QLabel("k ="))
        ligne_haut.addWidget(self.champ_k)

        self.conteneur = QFrame()
        self.grille = QGridLayout(self.conteneur)
        for colonne, titre in enumerate(
            (
                "Nature",
                "Nom",
                "Valeur",
                "Unité",
                "Incertitude",
                "",
                "U",
                "Heure",
                "",
                "Commentaire",
                "",
            )
        ):
            self.grille.addWidget(QLabel(titre), 0, colonne)
        self.grille.setColumnStretch(9, 1)
        self.grille.setRowStretch(9999, 1)
        defilement = QScrollArea()
        defilement.setWidgetResizable(True)
        defilement.setWidget(self.conteneur)

        disposition = QVBoxLayout(self)
        disposition.addLayout(ligne_haut)
        disposition.addWidget(defilement)

        self._references_conservees: list[str] = []
        self._noms_possedes: list[str] = []
        self._charger()
        self._chargement = False

        for touches in ("Ctrl+Return", "Ctrl+Enter"):
            raccourci = QShortcut(QKeySequence(touches), self)
            raccourci.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            raccourci.activated.connect(self.ajouter_et_aller_a_la_ligne)

    def _charger(self) -> None:
        references = getattr(self.onglet, "grandeurs", ())
        for reference in references:
            grandeur = None
            if self.page is not None:
                grandeur = next(
                    (g for g in self.page.mesures if g.nom == reference), None
                )
            if isinstance(grandeur, (Scalaire, Identifiant)):
                self.ajouter_ligne(grandeur)
                self._noms_possedes.append(grandeur.nom)
            else:
                self._references_conservees.append(reference)
        if not self.lignes:
            self.ajouter_ligne()

    def ajouter_ligne(
        self, grandeur: Scalaire | Identifiant | None = None
    ) -> LigneScalaire:
        """Ajoute une ligne à la grille, à la suite des autres."""
        ligne = LigneScalaire(self, grandeur)
        for colonne, champ in enumerate(ligne.champs_grille()):
            self.grille.addWidget(champ, self._prochain_rang, colonne)
            # Sans cela, le champ ne s'affiche qu'au tour suivant : il ne pourrait pas
            # encore recevoir le curseur.
            champ.show()
        pied = QHBoxLayout()
        pied.addWidget(ligne.message, stretch=1)
        pied.addWidget(ligne.bouton_suggestion)
        ligne.pied = pied
        self.grille.addLayout(pied, self._prochain_rang + 1, 1, 1, 10)
        self._prochain_rang += 2
        if self.lignes:
            precedent = self.lignes[-1].commentaire
            QWidget.setTabOrder(precedent, ligne.nature)
        self._ordonner(ligne)
        self.lignes.append(ligne)
        ligne.actualiser()
        return ligne

    @staticmethod
    def _ordonner(ligne: LigneScalaire) -> None:
        """Fixe l'ordre de la touche Tab : celui dans lequel on saisit une mesure."""
        ordre = (
            ligne.nature,
            ligne.nom,
            ligne.valeur,
            ligne.unite,
            ligne.bouton_incertitude,
            ligne.bouton_repetitions,
            ligne.commentaire,
        )
        for avant, apres in zip(ordre, ordre[1:]):
            QWidget.setTabOrder(avant, apres)

    def ajouter_et_aller_a_la_ligne(self) -> LigneScalaire:
        """Ajoute une ligne et y place le curseur, sur le nom de la mesure."""
        ligne = self.ajouter_ligne()
        ligne.nom.setFocus()
        return ligne

    def retirer_ligne(self, ligne: LigneScalaire) -> None:
        """Retire une mesure de l'onglet : elle quittera la page au prochain enregistrement."""
        for champ in (*ligne.champs_grille(), ligne.message, ligne.bouton_suggestion):
            self.grille.removeWidget(champ)
            champ.setParent(None)
            champ.deleteLater()
        self.grille.removeItem(ligne.pied)
        self.lignes.remove(ligne)
        self.ligne_modifiee()

    def ligne_modifiee(self) -> None:
        if not self._chargement:
            self.modifie.emit()

    def _k_change(self, k: float) -> None:
        """Change l'affichage de U : aucune donnée n'est modifiée, donc aucun enregistrement."""
        for ligne in self.lignes:
            ligne.afficher_resultat(k)

    def _noms_repetes(self) -> set[str]:
        noms = [
            ligne.nom.text().strip() for ligne in self.lignes if not ligne.est_vide()
        ]
        return {nom for nom in noms if nom and noms.count(nom) > 1}

    def _grandeurs_par_ligne(self) -> list[Grandeur]:
        """La grandeur de chaque ligne ; une ligne incomplète garde sa dernière version."""
        repetes = self._noms_repetes()
        resultat: list[Grandeur] = []
        for ligne in self.lignes:
            if ligne.est_vide():
                continue
            if ligne.erreur() is None and ligne.nom.text().strip() not in repetes:
                resultat.append(ligne.grandeur())
            elif ligne.origine is not None:
                resultat.append(ligne.origine)
        return resultat

    def grandeurs_possedees(self) -> list[str]:
        return list(self._noms_possedes)

    def grandeurs(self) -> list[Grandeur]:
        return self._grandeurs_par_ligne()

    def valeur(self) -> Onglet:
        noms = [grandeur.nom for grandeur in self._grandeurs_par_ligne()]
        return self.onglet.model_copy(
            update={
                "grandeurs": tuple(dict.fromkeys(noms))
                + tuple(self._references_conservees)
            }
        )

    def erreurs(self) -> list[str]:
        resultat = [e for ligne in self.lignes if (e := ligne.erreur()) is not None]
        resultat += [
            f"nom « {nom} » utilisé deux fois" for nom in sorted(self._noms_repetes())
        ]
        return resultat

    def enregistre(self, page: Page) -> None:
        """La saisie valide devient la nouvelle référence de chaque ligne."""
        super().enregistre(page)
        self.onglet = self.valeur()
        par_nom = {grandeur.nom: grandeur for grandeur in page.mesures}
        repetes = self._noms_repetes()
        for ligne in self.lignes:
            nom = ligne.nom.text().strip()
            if not ligne.est_vide() and ligne.erreur() is None and nom not in repetes:
                ligne.origine = par_nom.get(nom)
                ligne.horodatage_origine = getattr(ligne.origine, "horodatage", None)
        self._noms_possedes = [g.nom for g in self._grandeurs_par_ligne()]


declarer_onglet("scalaires", "Scalaires", OngletDonnees, EditeurScalaires)
