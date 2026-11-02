"""Espace Saisie : créer une page libre, organiser ses onglets, valider et corriger.

Un brouillon s'enregistre tout seul, peu après chaque modification. Une page validée ne
s'enregistre que par une correction motivée, à la demande de l'utilisateur.
"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import ValidationError
from PySide6.QtCore import QDateTime, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import Archive, PageRefusee
from pymphub.edition import (
    conflits_de_noms,
    date_saisie_vers_mesure,
    decouper_tags,
    fusionner_grandeurs,
    modifier_champs,
    nouvel_id_onglet,
    nouvelle_page,
)
from pymphub.interface import onglet_tableau  # noqa: F401 - déclare son type d'onglet
from pymphub.interface.messages import message_erreur
from pymphub.interface.onglet_scalaires import EditeurScalaires
from pymphub.interface.onglets import (
    EditeurOnglet,
    creer_editeur,
    creer_onglet,
    types_disponibles,
)
from pymphub.interface.texte_en_grand import afficher_en_grand
from pymphub.page import Page, Scalaire

_DELAI_ENREGISTREMENT_MS = 800
_FORMAT_DATE = "dd/MM/yyyy HH:mm"
_ERREURS_ENREGISTREMENT = (PageRefusee, ValueError, KeyError, OSError)


def _en_qdatetime(date: datetime) -> QDateTime:
    """Affiche une date dans son propre fuseau."""
    return QDateTime(date.year, date.month, date.day, date.hour, date.minute, 0)


def _creer_champ_date() -> QDateTimeEdit:
    champ = QDateTimeEdit()
    champ.setDisplayFormat(_FORMAT_DATE)
    champ.setCalendarPopup(True)
    return champ


def _remplir_liste_machines(
    liste: QComboBox, archive: Archive, machine: str | None
) -> None:
    """Propose les machines de l'archive et sélectionne celle de la page."""
    liste.clear()
    liste.addItem("")
    liste.addItems(archive.configuration.machines)
    liste.setCurrentText(machine or "")


def _creer_liste_machines() -> QComboBox:
    liste = QComboBox()
    liste.setEditable(True)
    liste.lineEdit().setPlaceholderText(
        "site.service.modalite.fabricant-modele.numero-serie"
    )
    return liste


@dataclass(frozen=True)
class DescriptionPage:
    """Ce que l'utilisateur renseigne pour créer une page."""

    titre: str
    date_mesure: datetime
    auteur: str
    machine: str | None
    tags: tuple[str, ...]


class DialogueNouvellePage(QDialog):
    """Demande le minimum pour créer une page : tout reste modifiable ensuite."""

    def __init__(self, archive: Archive, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouvelle page")
        self.setMinimumWidth(520)
        self.champ_titre = QLineEdit()
        self.champ_titre.setPlaceholderText("Linéarité de l'activimètre")
        self.champ_date = _creer_champ_date()
        self.champ_date.setDateTime(QDateTime.currentDateTime())
        self.champ_auteur = QLineEdit(archive.configuration.responsable)
        self.champ_machine = _creer_liste_machines()
        _remplir_liste_machines(self.champ_machine, archive, None)
        self.champ_tags = QLineEdit()
        self.champ_tags.setPlaceholderText("activimetre, Tc99m")

        formulaire = QFormLayout()
        formulaire.addRow("Titre", self.champ_titre)
        formulaire.addRow("Date de mesure", self.champ_date)
        formulaire.addRow("Auteur", self.champ_auteur)
        formulaire.addRow("Machine", self.champ_machine)
        formulaire.addRow("Tags", self.champ_tags)

        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.bouton_creer = boutons.button(QDialogButtonBox.StandardButton.Ok)
        self.bouton_creer.setText("Créer")
        self.bouton_creer.setObjectName("principal")
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)

        disposition = QVBoxLayout(self)
        disposition.addLayout(formulaire)
        disposition.addWidget(boutons)

        self.champ_titre.textChanged.connect(self._actualiser)
        self.champ_auteur.textChanged.connect(self._actualiser)
        self._actualiser()

    def _actualiser(self) -> None:
        self.bouton_creer.setEnabled(
            bool(self.champ_titre.text().strip() and self.champ_auteur.text().strip())
        )

    def description(self) -> DescriptionPage:
        """Renvoie les informations saisies."""
        return DescriptionPage(
            titre=self.champ_titre.text().strip(),
            date_mesure=date_saisie_vers_mesure(self.champ_date.dateTime().toPython()),
            auteur=self.champ_auteur.text().strip(),
            machine=self.champ_machine.currentText().strip() or None,
            tags=decouper_tags(self.champ_tags.text()),
        )


class EspaceSaisie(QWidget):
    """Édite une page : ses informations et ses onglets."""

    page_modifiee = Signal(str)
    fiche_demandee = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.archive: Archive | None = None
        self.page: Page | None = None
        self._chargement = False

        self._minuteur = QTimer(self)
        self._minuteur.setSingleShot(True)
        self._minuteur.setInterval(_DELAI_ENREGISTREMENT_MS)
        self._minuteur.timeout.connect(self.enregistrer_maintenant)

        self.bouton_nouvelle_vide = QPushButton("Nouvelle page…")
        self.bouton_nouvelle_vide.setObjectName("principal")
        self.bouton_nouvelle_vide.clicked.connect(self.demander_nouvelle_page)
        message_vide = QLabel(
            "Créez une page, ou choisissez « Modifier » sur une page de l'espace Archive."
        )
        message_vide.setObjectName("discret")
        self.vide = QWidget()
        disposition_vide = QVBoxLayout(self.vide)
        disposition_vide.addStretch()
        disposition_vide.addWidget(message_vide, alignment=Qt.AlignmentFlag.AlignCenter)
        disposition_vide.addWidget(
            self.bouton_nouvelle_vide, alignment=Qt.AlignmentFlag.AlignCenter
        )
        disposition_vide.addStretch()

        self.edition = self._creer_edition()
        self.pile = QStackedWidget()
        self.pile.addWidget(self.vide)
        self.pile.addWidget(self.edition)
        disposition = QVBoxLayout(self)
        disposition.addWidget(self.pile)

    def _creer_edition(self) -> QWidget:
        self.identifiant = QLabel("")
        self.identifiant.setObjectName("titre_page")
        self.identifiant.setTextFormat(Qt.TextFormat.PlainText)
        self.etat = QLabel("")
        self.etat.setObjectName("discret")
        self.etat.setTextFormat(Qt.TextFormat.PlainText)
        self.etat.setWordWrap(True)

        self.bouton_nouvelle = QPushButton("Nouvelle page…")
        self.bouton_nouvelle.clicked.connect(self.demander_nouvelle_page)
        self.bouton_valider = QPushButton("Valider la page…")
        self.bouton_valider.setObjectName("principal")
        self.bouton_valider.clicked.connect(self.demander_validation)
        self.bouton_corriger = QPushButton("Enregistrer la correction…")
        self.bouton_corriger.setObjectName("principal")
        self.bouton_corriger.clicked.connect(self.demander_correction)
        self.bouton_annuler = QPushButton("Annuler les modifications")
        self.bouton_annuler.clicked.connect(self.annuler_modifications)

        ligne_haut = QHBoxLayout()
        self.bouton_fiche = QPushButton("Voir la fiche")
        self.bouton_fiche.setToolTip("Vue d'ensemble de la page, dans l'espace Archive")
        self.bouton_fiche.clicked.connect(self.demander_fiche)
        ligne_haut.addWidget(self.identifiant, stretch=1)
        ligne_haut.addWidget(self.bouton_fiche)
        ligne_haut.addWidget(self.bouton_annuler)
        ligne_haut.addWidget(self.bouton_corriger)
        ligne_haut.addWidget(self.bouton_valider)
        ligne_haut.addWidget(self.bouton_nouvelle)

        self.champ_titre = QLineEdit()
        self.champ_description = QLineEdit()
        afficher_en_grand(self.champ_description, "Description de la page")
        self.champ_tags = QLineEdit()
        self.champ_tags.setPlaceholderText("activimetre, Tc99m")
        self.champ_date = _creer_champ_date()
        self.bouton_maintenant = QPushButton("Maintenant")
        self.bouton_maintenant.clicked.connect(
            lambda: self.champ_date.setDateTime(QDateTime.currentDateTime())
        )
        self.champ_machine = _creer_liste_machines()
        ligne_date = QHBoxLayout()
        ligne_date.addWidget(self.champ_date, stretch=1)
        ligne_date.addWidget(self.bouton_maintenant)

        self.formulaire = QFormLayout()
        self.formulaire.addRow("Titre", self.champ_titre)
        self.formulaire.addRow("Description", self.champ_description)
        self.formulaire.addRow("Date de mesure", ligne_date)
        self.formulaire.addRow("Machine", self.champ_machine)
        self.formulaire.addRow("Tags", self.champ_tags)

        self.bloc_informations = QWidget()
        self.bloc_informations.setLayout(self.formulaire)
        self.bouton_informations = QPushButton("Masquer les informations")
        self.bouton_informations.setCheckable(True)
        self.bouton_informations.toggled.connect(self._replier_informations)

        self.onglets = QTabWidget()
        self.onglets.setMovable(True)
        self.onglets.setTabsClosable(True)
        self.onglets.tabBar().tabMoved.connect(self._sur_modification)
        self.onglets.tabCloseRequested.connect(self.demander_retrait_onglet)
        self.onglets.tabBarDoubleClicked.connect(self.demander_renommage_onglet)
        self.bouton_ajouter = QPushButton("Ajouter un onglet")
        self.menu_ajouter = QMenu(self.bouton_ajouter)
        self.menu_ajouter.aboutToShow.connect(self._remplir_menu_ajouter)
        self.bouton_ajouter.setMenu(self.menu_ajouter)
        self.onglets.setCornerWidget(self.bouton_ajouter)

        self.champ_titre.textEdited.connect(self._sur_modification)
        self.champ_description.textEdited.connect(self._sur_modification)
        self.champ_tags.textEdited.connect(self._sur_modification)
        self.champ_date.dateTimeChanged.connect(self._sur_modification)
        self.champ_machine.currentTextChanged.connect(self._sur_modification)

        ligne_haut.insertWidget(1, self.bouton_informations)

        edition = QWidget()
        disposition = QVBoxLayout(edition)
        disposition.addLayout(ligne_haut)
        disposition.addWidget(self.etat)
        disposition.addWidget(self.bloc_informations)
        disposition.addWidget(self.onglets, stretch=1)
        return edition

    def _replier_informations(self, replie: bool) -> None:
        """Laisse toute la hauteur aux onglets, par exemple pour un grand tableau."""
        self.bloc_informations.setVisible(not replie)
        self.bouton_informations.setText(
            "Afficher les informations" if replie else "Masquer les informations"
        )

    # --- Dialogues : isolés ici pour pouvoir les remplacer dans les tests --------------

    def afficher_erreur(self, message: str) -> None:
        """Explique une erreur à l'utilisateur."""
        QMessageBox.critical(self, "pymphub", message)

    def _decrire_nouvelle_page(self) -> DescriptionPage | None:
        dialogue = DialogueNouvellePage(self.archive, self)
        if dialogue.exec() == QDialog.DialogCode.Accepted:
            return dialogue.description()
        return None

    def _demander_motif(self) -> str | None:
        motif, accepte = QInputDialog.getText(
            self, "Correction", "Motif de la correction (obligatoire) :"
        )
        return motif if accepte else None

    def _demander_titre_onglet(self, actuel: str) -> str | None:
        titre, accepte = QInputDialog.getText(
            self, "Renommer l'onglet", "Titre de l'onglet :", text=actuel
        )
        return titre if accepte else None

    def _confirmer(self, titre: str, question: str) -> bool:
        reponse = QMessageBox.question(self, titre, question)
        return reponse == QMessageBox.StandardButton.Yes

    # --- Archive et page ouvertes -------------------------------------------------------

    def installer(self, archive: Archive) -> None:
        """Change d'archive : aucune page n'est ouverte."""
        self.archive = archive
        self.page = None
        self._minuteur.stop()
        self.pile.setCurrentWidget(self.vide)

    def demander_nouvelle_page(self) -> None:
        """Demande les informations d'une page, puis la crée et l'ouvre."""
        if self.archive is None or not self.peut_quitter_la_page():
            return
        description = self._decrire_nouvelle_page()
        if description is not None:
            self.creer_page(description)

    def creer_page(self, description: DescriptionPage) -> bool:
        """Crée une page libre avec un premier onglet de notes, puis l'ouvre."""
        try:
            if description.machine is not None:
                self.archive.declarer_machine(description.machine)
            page = nouvelle_page(
                self.archive,
                description.titre,
                description.date_mesure,
                description.auteur,
                description.machine,
                description.tags,
                onglets=[creer_onglet("notes", nouvel_id_onglet(()))],
            )
            self.archive.creer_page(page)
        except (*_ERREURS_ENREGISTREMENT, FileExistsError) as erreur:
            self.afficher_erreur(message_erreur(erreur))
            return False
        self.page_modifiee.emit(page.id)
        self._charger(self.archive.lire_page(page.id))
        return True

    def ouvrir_page(self, id_page: str) -> bool:
        """Ouvre une page existante, après avoir enregistré la page en cours."""
        if self.archive is None or not self.peut_quitter_la_page():
            return False
        try:
            self._charger(self.archive.lire_page(id_page))
        except _ERREURS_ENREGISTREMENT as erreur:
            self.afficher_erreur(
                f"Page « {id_page} » illisible : {message_erreur(erreur)}"
            )
            return False
        return True

    def recharger(self) -> None:
        """Relit la page ouverte, après une reprise qui a pu la changer sur le disque."""
        if self.page is None:
            return
        try:
            self._charger(self.archive.lire_page(self.page.id))
        except _ERREURS_ENREGISTREMENT:
            self.installer(self.archive)

    def page_supprimee(self, id_page: str) -> None:
        """Referme la page si c'est celle qui vient d'être supprimée."""
        if self.page is not None and self.page.id == id_page:
            self.installer(self.archive)

    def _charger(self, page: Page) -> None:
        """Affiche une page, sans considérer cet affichage comme une modification."""
        self._minuteur.stop()
        self._chargement = True
        self.page = page
        self.identifiant.setText(page.id)
        self.champ_titre.setText(page.titre)
        self.champ_description.setText(page.description)
        self.champ_tags.setText(", ".join(page.tags))
        self.champ_date.setDateTime(_en_qdatetime(page.date_mesure))

        _remplir_liste_machines(self.champ_machine, self.archive, page.machine)

        while self.onglets.count():
            editeur = self.onglets.widget(0)
            self.onglets.removeTab(0)
            editeur.deleteLater()
        for onglet in page.onglets:
            self._ajouter_editeur(onglet)
        self.pile.setCurrentWidget(self.edition)
        self._chargement = False
        self._actualiser_etat()

    def _ajouter_editeur(self, onglet: object) -> EditeurOnglet:
        editeur = creer_editeur(onglet, self.page)
        editeur.charger(self.archive)
        editeur.modifie.connect(self._sur_modification)
        editeur.mesure_proposee.connect(self.ajouter_mesure_proposee)
        self.onglets.addTab(editeur, onglet.titre or onglet.type)
        return editeur

    def editeurs(self) -> list[EditeurOnglet]:
        """Les éditeurs d'onglets, dans l'ordre affiché."""
        return [self.onglets.widget(i) for i in range(self.onglets.count())]

    def _page_saisie(self) -> Page:
        """Construit la page telle qu'elle est à l'écran, en vérifiant ses règles."""
        date_mesure = date_saisie_vers_mesure(
            self.champ_date.dateTime().toPython(), self.page.date_mesure
        )
        return modifier_champs(
            self.page,
            titre=self.champ_titre.text().strip(),
            description=self.champ_description.text(),
            tags=decouper_tags(self.champ_tags.text()),
            date_mesure=date_mesure,
            machine=self.champ_machine.currentText().strip() or None,
            onglets=[editeur.valeur() for editeur in self.editeurs()],
            mesures=fusionner_grandeurs(
                self.page.mesures,
                [nom for e in self.editeurs() for nom in e.grandeurs_possedees()],
                [grandeur for e in self.editeurs() for grandeur in e.grandeurs()],
            ),
        )

    def saisies_incompletes(self) -> list[str]:
        """Ce qu'il manque dans les onglets : ces saisies ne sont pas enregistrées."""
        conflits = [
            f"le nom « {nom} » est déjà utilisé dans cette page"
            for nom in self._conflits()
        ]
        return conflits + [
            erreur for editeur in self.editeurs() for erreur in editeur.erreurs()
        ]

    def _conflits(self) -> list[str]:
        """Noms qui écraseraient une autre grandeur : rien n'est enregistré dans ce cas."""
        if self.page is None:
            return []
        return conflits_de_noms(
            self.page.mesures,
            [nom for e in self.editeurs() for nom in e.grandeurs_possedees()],
            [grandeur for e in self.editeurs() for grandeur in e.grandeurs()],
        )

    def modifications_en_attente(self) -> bool:
        """Indique si l'écran diffère de la page enregistrée, ou s'il est invalide."""
        if self.page is None:
            return False
        if self.saisies_incompletes():
            return True
        if any(editeur.tableau_modifie() for editeur in self.editeurs()):
            return True
        try:
            return self._page_saisie() != self.page
        except ValidationError:
            return True

    def _sur_modification(self) -> None:
        if self._chargement or self.page is None:
            return
        self._actualiser_etat()
        if self.page.statut == "brouillon" and self._erreur_de_saisie() is None:
            # Une ligne incomplète ne retarde pas l'enregistrement des autres.
            self._minuteur.start()
        else:
            self._minuteur.stop()

    def _erreur_de_saisie(self) -> str | None:
        try:
            self._page_saisie()
        except ValidationError as erreur:
            return message_erreur(erreur)
        return None

    def _actualiser_etat(self) -> None:
        if self.page is None:
            return
        brouillon = self.page.statut == "brouillon"
        attente = self.modifications_en_attente()
        erreur = self._erreur_de_saisie()
        incompletes = self.saisies_incompletes()
        self.bouton_valider.setVisible(brouillon)
        self.bouton_corriger.setVisible(not brouillon)
        self.bouton_annuler.setVisible(not brouillon)
        self.bouton_corriger.setEnabled(attente and erreur is None and not incompletes)
        self.bouton_annuler.setEnabled(attente)
        self.bouton_valider.setEnabled(erreur is None and not incompletes)

        statut = f"Statut : {self.page.statut}"
        if erreur is not None:
            self.etat.setText(f"{statut} · Non enregistré : {erreur}")
        elif incompletes:
            self.etat.setText(f"{statut} · À compléter : {incompletes[0]}")
        elif brouillon:
            suite = "enregistrement en attente…" if attente else "enregistré"
            self.etat.setText(f"{statut} · {suite}")
        elif attente:
            self.etat.setText(f"{statut} · modifications à enregistrer avec un motif")
        else:
            self.etat.setText(f"{statut} · toute modification demandera un motif")

    # --- Enregistrement -----------------------------------------------------------------

    def _ecrire(self, page: Page, motif: str | None = None) -> bool:
        """Écrit la page. Une erreur est expliquée, et l'écran garde la saisie."""
        try:
            if page.machine is not None:
                self.archive.declarer_machine(page.machine)
            tableaux = {
                nom: donnees
                for editeur in self.editeurs()
                for nom, donnees in editeur.donnees_tableaux().items()
            }
            self.archive.modifier_page(page, motif=motif, tableaux=tableaux)
            self.page = self.archive.lire_page(page.id)
        except _ERREURS_ENREGISTREMENT as erreur:
            self.afficher_erreur(message_erreur(erreur))
            self._actualiser_etat()
            return False
        for editeur in self.editeurs():
            editeur.enregistre(self.page)
        self._actualiser_etat()
        self.page_modifiee.emit(page.id)
        return True

    def enregistrer_maintenant(self) -> bool:
        """Enregistre un brouillon sans attendre. Une page validée n'est pas concernée."""
        self._minuteur.stop()
        if self.page is None or self.page.statut != "brouillon":
            return True
        if self._conflits():
            self._actualiser_etat()
            return False
        try:
            page = self._page_saisie()
        except ValidationError:
            return False
        if page == self.page and not any(e.tableau_modifie() for e in self.editeurs()):
            return True
        return self._ecrire(page)

    def demander_correction(self) -> bool:
        """Demande un motif, puis enregistre la correction d'une page validée."""
        if self.saisies_incompletes():
            self.afficher_erreur(self.saisies_incompletes()[0])
            return False
        try:
            page = self._page_saisie()
        except ValidationError as erreur:
            self.afficher_erreur(message_erreur(erreur))
            return False
        motif = self._demander_motif()
        if motif is None:
            return False
        if not self._ecrire(page, motif):
            return False
        self._charger(self.page)
        return True

    def demander_fiche(self) -> None:
        """Enregistre un brouillon, puis demande la vue d'ensemble de la page."""
        if self.page is not None:
            self.enregistrer_maintenant()
            self.fiche_demandee.emit(self.page.id)

    def annuler_modifications(self) -> None:
        """Revient à la dernière version enregistrée."""
        if self.page is not None:
            self._charger(self.page)

    def demander_validation(self) -> bool:
        """Enregistre le brouillon, demande confirmation, puis valide la page."""
        if self.page is None or not self.enregistrer_maintenant():
            return False
        question = (
            "Une fois validée, cette page ne se modifiera plus que par une correction "
            "motivée. Valider ?"
        )
        if not self._confirmer("Valider la page", question):
            return False
        try:
            page = self.archive.valider_page(self.page.id)
        except _ERREURS_ENREGISTREMENT as erreur:
            self.afficher_erreur(message_erreur(erreur))
            return False
        self._charger(page)
        self.page_modifiee.emit(page.id)
        return True

    def peut_quitter_la_page(self) -> bool:
        """Enregistre un brouillon, ou demande quoi faire des modifications en attente."""
        if self.page is None:
            return True
        if self.page.statut == "brouillon":
            self.enregistrer_maintenant()
        if not self.modifications_en_attente():
            return True
        return self._confirmer(
            "Modifications non enregistrées",
            "Cette page a des modifications qui ne sont pas enregistrées. Les abandonner ?",
        )

    # --- Onglets ------------------------------------------------------------------------

    def _remplir_menu_ajouter(self) -> None:
        self.menu_ajouter.clear()
        for declaration in types_disponibles():
            action = self.menu_ajouter.addAction(declaration.libelle)
            action.triggered.connect(
                lambda _coche=False, nom=declaration.nom_type: self.ajouter_onglet(nom)
            )

    def ajouter_mesure_proposee(self, mesure: Scalaire) -> None:
        """Ajoute une mesure à l'onglet de scalaires, en le créant s'il n'existe pas."""
        scalaires = next(
            (e for e in self.editeurs() if isinstance(e, EditeurScalaires)), None
        )
        if scalaires is None:
            scalaires = self.ajouter_onglet("scalaires")
        scalaires.ajouter_mesure(mesure)
        self.onglets.setCurrentWidget(scalaires)

    def ajouter_onglet(self, nom_type: str) -> EditeurOnglet:
        """Ajoute un onglet vide à la page et l'affiche."""
        onglets = [editeur.valeur() for editeur in self.editeurs()]
        editeur = self._ajouter_editeur(
            creer_onglet(nom_type, nouvel_id_onglet(onglets))
        )
        self.onglets.setCurrentWidget(editeur)
        self._sur_modification()
        return editeur

    def demander_retrait_onglet(self, position: int) -> bool:
        """Retire un onglet après confirmation. Les grandeurs de la page restent."""
        editeur = self.onglets.widget(position)
        question = (
            f"Retirer l'onglet « {self.onglets.tabText(position)} » ? Son texte sera retiré "
            "de la page mais restera lisible dans l'historique. Les mesures restent."
        )
        if not self._confirmer("Retirer l'onglet", question):
            return False
        self.onglets.removeTab(position)
        editeur.deleteLater()
        self._sur_modification()
        return True

    def demander_renommage_onglet(self, position: int) -> bool:
        """Renomme un onglet."""
        if position < 0:
            return False
        titre = self._demander_titre_onglet(self.onglets.tabText(position))
        if titre is None or not titre.strip():
            return False
        self.onglets.setTabText(position, titre.strip())
        self.onglets.widget(position).renommer(titre.strip())
        return True
