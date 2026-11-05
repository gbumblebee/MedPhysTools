"""Espace Archive : retrouver une page par ses filtres et la consulter.

La recherche tourne dans un fil d'exécution séparé : la construction de l'index, ou une
archive sur un partage réseau lent, ne bloquent donc jamais l'interface. Une seule
recherche tourne à la fois, car l'index peut être écrit pendant sa mise à jour.

Ce sont des fils Python et non des QThread : Qt arrête brutalement le programme si un
QThread est détruit pendant son travail, ce que la fermeture d'une fenêtre peut provoquer.
"""

import contextlib
import sqlite3
import threading
from dataclasses import dataclass

from PySide6.QtCore import (
    QAbstractTableModel,
    QDate,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    Qt,
    QTimer,
    Signal,
    SignalInstance,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from medphystools.archive import Archive, PageRefusee
from medphystools.index import Criteres, PageIllisible, ResumePage, ValeursPossibles
from medphystools.interface.fiche_page import FichePage, formater_date
from medphystools.interface.messages import message_erreur
from medphystools.interface.pieces import ajouter_chemin
from medphystools.interface.ramasse_miettes import assurer_ramasse_miettes_principal
from medphystools.page import Page

_DELAI_FRAPPE_MS = 250
_DATE_VIDE = QDate(1900, 1, 1)
_COLONNES = ("Date", "Titre", "Machine", "Statut", "Auteur")
_ERREURS_RECHERCHE = (OSError, ValueError, sqlite3.Error)


@dataclass(frozen=True)
class ResultatRecherche:
    """Ce que renvoie une recherche en arrière-plan."""

    resumes: list[ResumePage]
    valeurs: ValeursPossibles
    illisibles: list[PageIllisible]


class _Signaux(QObject):
    """Résultats renvoyés par les fils de travail vers l'interface.

    Chaque résultat indique son archive : celui d'une archive refermée entre-temps est ignoré.
    """

    recherche_reussie = Signal(object, object)
    recherche_echouee = Signal(object, str)
    recherche_finie = Signal(object)
    verification_reussie = Signal(object, list, list)
    verification_echouee = Signal(object, str)
    enregistrement_automatique_echoue = Signal(object, str)
    verification_finie = Signal(object)


def _emettre(signal: SignalInstance, *arguments: object) -> None:
    """Renvoie un résultat, sauf si l'interface a été fermée entre-temps."""
    with contextlib.suppress(RuntimeError):
        signal.emit(*arguments)


def _rechercher(signaux: _Signaux, archive: Archive, criteres: Criteres) -> None:
    """Exécute une recherche hors du fil de l'interface."""
    try:
        resultat = ResultatRecherche(
            archive.chercher_resumes(criteres),
            archive.valeurs_possibles(),
            archive.pages_illisibles(),
        )
    except _ERREURS_RECHERCHE as erreur:
        _emettre(signaux.recherche_echouee, archive, str(erreur))
    else:
        _emettre(signaux.recherche_reussie, archive, resultat)
    finally:
        _emettre(signaux.recherche_finie, archive)


def _verifier(signaux: _Signaux, archive: Archive, enregistrer: bool) -> None:
    """Compare les fichiers de l'archive au dernier commit, hors du fil de l'interface.

    À l'ouverture, `enregistrer` crée aussitôt une nouvelle version de ce qui a changé. Si
    c'est impossible, sur un partage en lecture seule par exemple, l'écart reste signalé.
    """
    enregistres: list[str] = []
    try:
        if enregistrer:
            try:
                enregistres = archive.enregistrer_modifications_externes()
            except (OSError, ValueError, KeyError) as erreur:
                _emettre(
                    signaux.enregistrement_automatique_echoue, archive, str(erreur)
                )
        fichiers = archive.modifications_non_tracees()
        archive.precharger_historique()
    except (OSError, ValueError, KeyError) as erreur:
        _emettre(signaux.verification_echouee, archive, str(erreur))
    else:
        _emettre(signaux.verification_reussie, archive, fichiers, enregistres)
    finally:
        _emettre(signaux.verification_finie, archive)


class ModelePages(QAbstractTableModel):
    """Liste des pages. Seules les lignes visibles sont lues : 5 000 pages restent fluides."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.resumes: list[ResumePage] = []

    def remplacer(self, resumes: list[ResumePage]) -> None:
        self.beginResetModel()
        self.resumes = resumes
        self.endResetModel()

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(self.resumes)

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()
    ) -> int:  # noqa: B008, N802
        return 0 if parent.isValid() else len(_COLONNES)

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        resume = self.resumes[index.row()]
        if index.column() == 0:
            return formater_date(resume.date_mesure)
        valeurs = (resume.titre, resume.machine, resume.statut, resume.auteur)
        return valeurs[index.column() - 1] or ""

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return _COLONNES[section]
        return None


class Filtres(QWidget):
    """Les champs de recherche."""

    modifies = Signal()
    frappe = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.texte = QLineEdit()
        self.texte.setPlaceholderText("Titre, notes, identifiant, source…")
        self.tags = QLineEdit()
        self.tags.setPlaceholderText("activimetre, Tc99m")
        self.mode_tags = QComboBox()
        self.mode_tags.addItem("Tous ces tags", "et")
        self.mode_tags.addItem("Au moins un", "ou")
        self.depuis = self._creer_date()
        self.jusqua = self._creer_date()
        self.gabarit = self._creer_liste()
        self.machine = self._creer_liste()
        self.statut = self._creer_liste()
        self.auteur = self._creer_liste()
        self.bouton_reinitialiser = QPushButton("Réinitialiser")
        self.bouton_reinitialiser.clicked.connect(self.reinitialiser)

        ligne_tags = QHBoxLayout()
        ligne_tags.addWidget(self.tags, stretch=1)
        ligne_tags.addWidget(self.mode_tags)
        ligne_dates = QHBoxLayout()
        ligne_dates.addWidget(self.depuis)
        ligne_dates.addWidget(QLabel("au"))
        ligne_dates.addWidget(self.jusqua)

        formulaire = QFormLayout(self)
        formulaire.addRow("Texte", self.texte)
        formulaire.addRow("Tags", ligne_tags)
        formulaire.addRow("Du", ligne_dates)
        formulaire.addRow("Gabarit", self.gabarit)
        formulaire.addRow("Machine", self.machine)
        formulaire.addRow("Statut", self.statut)
        formulaire.addRow("Auteur", self.auteur)
        formulaire.addRow("", self.bouton_reinitialiser)

        self.texte.textChanged.connect(self.frappe)
        self.tags.textChanged.connect(self.frappe)
        self.mode_tags.currentIndexChanged.connect(self.modifies)
        self.depuis.dateChanged.connect(self.modifies)
        self.jusqua.dateChanged.connect(self.modifies)

    def _creer_date(self) -> QDateEdit:
        champ = QDateEdit()
        champ.setDisplayFormat("dd/MM/yyyy")
        champ.setCalendarPopup(True)
        champ.setMinimumDate(_DATE_VIDE)
        champ.setSpecialValueText("—")
        champ.setDate(_DATE_VIDE)
        return champ

    def _creer_liste(self) -> QComboBox:
        liste = QComboBox()
        liste.addItem("Tous", None)
        liste.currentIndexChanged.connect(self.modifies)
        return liste

    @staticmethod
    def _en_date(champ: QDateEdit) -> str | None:
        if champ.date() == _DATE_VIDE:
            return None
        return champ.date().toString("yyyy-MM-dd")

    def criteres(self) -> Criteres:
        """Traduit les champs en critères de recherche."""
        tags = tuple(t.strip() for t in self.tags.text().split(",") if t.strip())
        return Criteres(
            tags=tags,
            mode_tags=self.mode_tags.currentData(),
            depuis=self._en_date(self.depuis),
            jusqua=self._en_date(self.jusqua),
            gabarit=self.gabarit.currentData(),
            machine=self.machine.currentData(),
            statut=self.statut.currentData(),
            auteur=self.auteur.currentData(),
            texte=self.texte.text().strip() or None,
        )

    def proposer(self, valeurs: ValeursPossibles) -> None:
        """Met à jour les listes sans perdre la sélection ni relancer de recherche."""
        for liste, possibles in (
            (self.gabarit, valeurs.gabarits),
            (self.machine, valeurs.machines),
            (self.statut, valeurs.statuts),
            (self.auteur, valeurs.auteurs),
        ):
            actuelle = liste.currentData()
            liste.blockSignals(True)
            liste.clear()
            liste.addItem("Tous", None)
            for valeur in possibles:
                liste.addItem(valeur, valeur)
            if actuelle is not None and (position := liste.findData(actuelle)) >= 0:
                liste.setCurrentIndex(position)
            liste.blockSignals(False)
        self.tags.setToolTip(
            "Tags de l'archive : " + (", ".join(valeurs.tags) or "aucun")
        )

    def reinitialiser(self) -> None:
        """Remet tous les filtres à zéro, en une seule recherche."""
        self.blockSignals(True)
        self.texte.clear()
        self.tags.clear()
        self.mode_tags.setCurrentIndex(0)
        self.depuis.setDate(_DATE_VIDE)
        self.jusqua.setDate(_DATE_VIDE)
        for liste in (self.gabarit, self.machine, self.statut, self.auteur):
            liste.setCurrentIndex(0)
        self.blockSignals(False)
        self.modifies.emit()


class DialogueSuppression(QDialog):
    """Demande un motif et l'identifiant retapé, avant de supprimer une page validée."""

    def __init__(self, page: Page, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.id_page = page.id
        self.setWindowTitle("Supprimer la page")
        self.setMinimumWidth(520)
        explication = QLabel(
            f"La page « {page.titre} » est {page.statut}. Elle quittera l'archive : son "
            "historique reste dans Git et ses pièces jointes vont dans corbeille/."
        )
        explication.setWordWrap(True)
        explication.setTextFormat(Qt.TextFormat.PlainText)
        self.champ_motif = QLineEdit()
        self.champ_motif.setPlaceholderText("Pourquoi supprimer cette page ?")
        self.champ_identifiant = QLineEdit()
        self.champ_identifiant.setPlaceholderText(page.id)

        formulaire = QFormLayout()
        formulaire.addRow("Motif (obligatoire)", self.champ_motif)
        formulaire.addRow("Identifiant à retaper", self.champ_identifiant)

        boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.bouton_supprimer = boutons.button(QDialogButtonBox.StandardButton.Ok)
        self.bouton_supprimer.setText("Supprimer")
        boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        boutons.accepted.connect(self.accept)
        boutons.rejected.connect(self.reject)

        disposition = QVBoxLayout(self)
        disposition.addWidget(explication)
        disposition.addLayout(formulaire)
        disposition.addWidget(boutons)

        self.champ_motif.textChanged.connect(self._actualiser)
        self.champ_identifiant.textChanged.connect(self._actualiser)
        self._actualiser()

    def _actualiser(self) -> None:
        self.bouton_supprimer.setEnabled(
            bool(self.champ_motif.text().strip())
            and self.champ_identifiant.text().strip() == self.id_page
        )

    def motif(self) -> str:
        """Renvoie le motif saisi."""
        return self.champ_motif.text()


class EspaceArchive(QWidget):
    """Filtres et liste de pages à gauche, fiche de la page sélectionnée à droite."""

    recherche_terminee = Signal()
    verification_terminee = Signal()
    reprise_effectuee = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        assurer_ramasse_miettes_principal()
        self.archive: Archive | None = None
        self.illisibles: list[PageIllisible] = []
        self.non_tracees: list[str] = []
        self.versions_automatiques: list[str] = []
        self.erreur_version_automatique: str | None = None
        self._archive_a_enregistrer: Archive | None = None
        self.erreur_recherche: str | None = None
        self._recherche: threading.Thread | None = None
        self._recherche_en_attente = False
        self._verification: threading.Thread | None = None
        self._verification_en_attente = False
        self._signaux = _Signaux(self)
        self._signaux.recherche_reussie.connect(self._recherche_reussie)
        self._signaux.recherche_echouee.connect(self._recherche_echouee)
        self._signaux.recherche_finie.connect(self._recherche_finie)
        self._signaux.verification_reussie.connect(self._verification_reussie)
        self._signaux.verification_echouee.connect(self._verification_echouee)
        self._signaux.verification_finie.connect(self._verification_finie)
        self._signaux.enregistrement_automatique_echoue.connect(
            self._enregistrement_automatique_echoue
        )

        self.filtres = Filtres()
        self.modele = ModelePages(self)
        self.liste = QTableView()
        self.liste.setModel(self.modele)
        self.liste.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.liste.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.liste.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.liste.setAlternatingRowColors(True)
        self.liste.verticalHeader().setVisible(False)
        entete = self.liste.horizontalHeader()
        entete.setResizeContentsPrecision(50)
        for colonne, mode in enumerate(
            (
                QHeaderView.ResizeMode.ResizeToContents,
                QHeaderView.ResizeMode.Stretch,
                QHeaderView.ResizeMode.Interactive,
                QHeaderView.ResizeMode.ResizeToContents,
                QHeaderView.ResizeMode.ResizeToContents,
            )
        ):
            entete.setSectionResizeMode(colonne, mode)
        self.liste.setColumnWidth(2, 110)
        self.liste.selectionModel().currentRowChanged.connect(self._page_selectionnee)
        self.liste.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.liste.customContextMenuRequested.connect(self._ouvrir_menu_page)

        self.compteur = QLabel("")
        self.compteur.setObjectName("discret")
        self.bouton_actualiser = QPushButton("Actualiser")
        self.bouton_actualiser.clicked.connect(self.actualiser)
        ligne_compteur = QHBoxLayout()
        ligne_compteur.addWidget(self.compteur, stretch=1)
        ligne_compteur.addWidget(self.bouton_actualiser)

        self.alerte = QLabel("")
        self.alerte.setObjectName("alerte")
        self.alerte.setWordWrap(True)
        self.alerte.setTextFormat(Qt.TextFormat.PlainText)
        self.bouton_details = QPushButton("Détails…")
        self.bouton_details.clicked.connect(self._afficher_details)
        self.bandeau = QWidget()
        ligne_alerte = QHBoxLayout(self.bandeau)
        ligne_alerte.setContentsMargins(0, 0, 0, 0)
        ligne_alerte.addWidget(self.alerte, stretch=1)
        ligne_alerte.addWidget(self.bouton_details)
        self.bouton_reprise = QPushButton("Enregistrer une reprise…")
        self.bouton_reprise.clicked.connect(self.demander_reprise)
        self.bouton_reprise.setVisible(False)
        ligne_alerte.addWidget(self.bouton_reprise)
        self.bandeau.setVisible(False)

        gauche = QWidget()
        disposition_gauche = QVBoxLayout(gauche)
        disposition_gauche.addWidget(self.filtres)
        disposition_gauche.addLayout(ligne_compteur)
        disposition_gauche.addWidget(self.liste, stretch=1)

        self.fiche = FichePage()
        separateur = QSplitter()
        separateur.addWidget(gauche)
        separateur.addWidget(self.fiche)
        separateur.setStretchFactor(0, 2)
        separateur.setStretchFactor(1, 3)
        separateur.setSizes([500, 600])

        disposition = QVBoxLayout(self)
        disposition.addWidget(self.bandeau)
        disposition.addWidget(separateur, stretch=1)

        self._minuteur = QTimer(self)
        self._minuteur.setSingleShot(True)
        self._minuteur.setInterval(_DELAI_FRAPPE_MS)
        self._minuteur.timeout.connect(self.lancer_recherche)
        self.filtres.frappe.connect(self._minuteur.start)
        self.filtres.modifies.connect(self.lancer_recherche)

    def installer(self, archive: Archive, enregistrer_externes: bool = False) -> None:
        """Affiche une archive : les filtres repartent à zéro.

        Avec `enregistrer_externes`, ce qui a changé hors du logiciel devient d'abord une
        nouvelle version. Aucune saisie ne doit commencer avant `verification_terminee`.
        """
        self.archive = archive
        self.illisibles = []
        self.non_tracees = []
        self.versions_automatiques = []
        self.erreur_version_automatique = None
        self._archive_a_enregistrer = archive if enregistrer_externes else None
        self.fiche.vider()
        self.modele.remplacer([])
        self.filtres.reinitialiser()
        self._minuteur.stop()
        self.lancer_verification()

    def actualiser(self) -> None:
        """Relit l'état de l'archive : modifications externes et nouvelles pages."""
        self.lancer_recherche()
        self.lancer_verification()

    def au_repos(self) -> bool:
        """Indique qu'aucune recherche ni vérification n'est en cours ou prévue."""
        return (
            self._recherche is None
            and self._verification is None
            and not self._recherche_en_attente
            and not self._verification_en_attente
            and not self._minuteur.isActive()
        )

    def lancer_recherche(self) -> None:
        """Lance une recherche, ou la met en attente si une autre est en cours."""
        if self.archive is None:
            return
        if self._recherche is not None:
            self._recherche_en_attente = True
            return
        self.compteur.setText("Recherche…")
        self._recherche = threading.Thread(
            target=_rechercher,
            args=(self._signaux, self.archive, self.filtres.criteres()),
            daemon=True,
        )
        self._recherche.start()

    def _recherche_reussie(self, archive: Archive, resultat: ResultatRecherche) -> None:
        if archive is not self.archive:
            return
        self.erreur_recherche = None
        selection = self.fiche.page.id if self.fiche.page is not None else None
        self.modele.remplacer(resultat.resumes)
        self.filtres.proposer(resultat.valeurs)
        self.illisibles = resultat.illisibles
        total = len(resultat.resumes)
        self.compteur.setText(f"{total} page{'s' if total > 1 else ''}")
        self._restaurer_selection(selection)
        self._actualiser_alerte()

    def _recherche_echouee(self, archive: Archive, message: str) -> None:
        if archive is not self.archive:
            return
        self.erreur_recherche = message
        self.compteur.setText("Recherche impossible")
        self._actualiser_alerte()

    def _recherche_finie(self, archive: Archive) -> None:
        self._recherche = None
        # Une archive changée pendant la recherche, ou de nouveaux filtres : on recommence.
        if self._recherche_en_attente or archive is not self.archive:
            self._recherche_en_attente = False
            self.lancer_recherche()
            return
        self.recherche_terminee.emit()

    def _restaurer_selection(self, id_page: str | None) -> None:
        """Garde la page sélectionnée si elle reste dans les résultats."""
        for ligne, resume in enumerate(self.modele.resumes):
            if resume.id == id_page:
                self.liste.selectRow(ligne)
                return
        if id_page is not None:
            self.fiche.vider()

    def lancer_verification(self) -> None:
        """Cherche en arrière-plan les fichiers modifiés hors du logiciel."""
        if self.archive is None:
            return
        if self._verification is not None:
            self._verification_en_attente = True
            return
        enregistrer = self._archive_a_enregistrer is self.archive
        self._archive_a_enregistrer = None
        self._verification = threading.Thread(
            target=_verifier,
            args=(self._signaux, self.archive, enregistrer),
            daemon=True,
        )
        self._verification.start()

    def _verification_reussie(
        self, archive: Archive, fichiers: list[str], enregistres: list[str]
    ) -> None:
        if archive is not self.archive:
            return
        self.non_tracees = fichiers
        if enregistres:
            self.versions_automatiques = enregistres
            # La recherche a pu partir avant ce commit : la liste doit le refléter.
            self.lancer_recherche()
        self._actualiser_alerte()

    def _enregistrement_automatique_echoue(
        self, archive: Archive, message: str
    ) -> None:
        if archive is self.archive:
            self.erreur_version_automatique = (
                f"Nouvelle version automatique impossible : {message}"
            )
            self._actualiser_alerte()

    def _verification_echouee(self, archive: Archive, message: str) -> None:
        if archive is not self.archive:
            return
        self.erreur_recherche = message
        self._actualiser_alerte()

    def _verification_finie(self, archive: Archive) -> None:
        self._verification = None
        if self._verification_en_attente or archive is not self.archive:
            self._verification_en_attente = False
            self.lancer_verification()
            return
        self.verification_terminee.emit()

    def _actualiser_alerte(self) -> None:
        morceaux: list[str] = []
        if self.versions_automatiques:
            nombre = len(self.versions_automatiques)
            morceaux.append(
                f"{nombre} fichier(s) modifié(s) hors du logiciel : "
                "nouvelle version créée à l'ouverture"
            )
        if self.non_tracees:
            nombre = len(self.non_tracees)
            morceaux.append(f"{nombre} fichier(s) modifié(s) hors du logiciel")
        if self.erreur_version_automatique:
            morceaux.append(self.erreur_version_automatique)
        if self.illisibles:
            morceaux.append(f"{len(self.illisibles)} page(s) illisible(s)")
        if self.erreur_recherche:
            morceaux.append(self.erreur_recherche)
        self.alerte.setText(" · ".join(morceaux))
        self.bandeau.setVisible(bool(morceaux))
        self.bouton_reprise.setVisible(bool(self.non_tracees))

    def texte_details(self) -> str:
        """Décrit les alertes en cours."""
        morceaux: list[str] = []
        if self.versions_automatiques:
            morceaux.append(
                "Ces fichiers avaient changé hors du logiciel, ou un enregistrement a été "
                "interrompu. Une nouvelle version les garde tels quels, et l'ancienne reste "
                "dans l'historique de la page :\n"
                + "\n".join(self.versions_automatiques)
            )
        if self.non_tracees:
            morceaux.append(
                "Fichiers modifiés hors du logiciel, ou enregistrement interrompu :\n"
                + "\n".join(self.non_tracees)
            )
        if self.erreur_version_automatique:
            morceaux.append(self.erreur_version_automatique)
        if self.illisibles:
            morceaux.append(
                "Pages illisibles :\n"
                + "\n".join(f"{p.id} : {p.erreur}" for p in self.illisibles)
            )
        if self.erreur_recherche:
            morceaux.append(self.erreur_recherche)
        return "\n\n".join(morceaux)

    def afficher_erreur(self, message: str) -> None:
        """Explique une erreur à l'utilisateur."""
        QMessageBox.critical(self, "MedPhysNotebook", message)

    def _demander_motif_reprise(self) -> str | None:
        motif, accepte = QInputDialog.getText(
            self,
            "Enregistrer une reprise",
            "Après avoir examiné ces fichiers, indiquez le motif (obligatoire) :\n\n"
            + "\n".join(self.non_tracees),
        )
        return motif if accepte else None

    def demander_reprise(self) -> bool:
        """Enregistre l'état actuel des fichiers modifiés hors du logiciel, avec un motif."""
        if self.archive is None:
            return False
        motif = self._demander_motif_reprise()
        if motif is None:
            return False
        try:
            self.archive.enregistrer_reprise(motif)
        except (PageRefusee, OSError, ValueError) as erreur:
            self.afficher_erreur(str(erreur))
            return False
        self.reprise_effectuee.emit()
        self.actualiser()
        return True

    def _confirmer_suppression_brouillon(self, page: Page) -> bool:
        reponse = QMessageBox.question(
            self,
            "Supprimer le brouillon",
            f"Supprimer le brouillon « {page.titre} » ? Son historique reste dans Git et "
            "ses pièces jointes vont dans corbeille/.",
        )
        return reponse == QMessageBox.StandardButton.Yes

    def _demander_motif_suppression(self, page: Page) -> str | None:
        dialogue = DialogueSuppression(page, self)
        if dialogue.exec() == QDialog.DialogCode.Accepted:
            return dialogue.motif()
        return None

    def demander_suppression(self, id_page: str) -> bool:
        """Supprime une page après confirmation, ou après motif si elle est validée."""
        if self.archive is None:
            return False
        motif = None
        try:
            page = self.archive.lire_page(id_page)
            if page.statut == "brouillon":
                if not self._confirmer_suppression_brouillon(page):
                    return False
            else:
                motif = self._demander_motif_suppression(page)
                if motif is None:
                    return False
            # Une recherche en cours ne doit pas voir le dossier disparaître sous elle.
            self._attendre_travaux()
            self.archive.supprimer_page(id_page, motif)
        except (PageRefusee, OSError, ValueError, KeyError) as erreur:
            self.afficher_erreur(message_erreur(erreur))
            return False
        self.fiche.vider()
        self.actualiser()
        return True

    def afficher_page(self, id_page: str) -> None:
        """Affiche la fiche d'une page, sur son onglet de contenu."""
        if self.archive is None:
            return
        self.fiche.onglets.setCurrentIndex(0)
        self.fiche.afficher(self.archive, id_page)

    def page_modifiee(self, id_page: str) -> None:
        """Met à jour la liste et la fiche après un enregistrement dans l'espace Saisie."""
        if self.archive is None:
            return
        if self.fiche.page is not None and self.fiche.page.id == id_page:
            self.fiche.afficher(self.archive, id_page)
        self.lancer_recherche()

    # --- Menu d'une page -----------------------------------------------------------------

    def creer_menu_page(self, id_page: str) -> QMenu:
        """Le menu du clic droit sur une page de la liste."""
        menu = QMenu(self.liste)
        ajouter_chemin(menu, self.archive.emplacement_page(id_page), self)
        return menu

    def _ouvrir_menu_page(self, position: QPoint) -> None:
        index = self.liste.indexAt(position)
        if self.archive is None or not index.isValid():
            return
        id_page = self.modele.resumes[index.row()].id
        menu = self.creer_menu_page(id_page)
        menu.exec(self.liste.viewport().mapToGlobal(position))
        menu.deleteLater()

    def _afficher_details(self) -> None:
        QMessageBox.warning(self, "Alertes de l'archive", self.texte_details())

    def _page_selectionnee(self, courant: QModelIndex, _precedent: QModelIndex) -> None:
        if self.archive is None or not courant.isValid():
            return
        id_page = self.modele.resumes[courant.row()].id
        if self.fiche.page is None or self.fiche.page.id != id_page:
            self.fiche.afficher(self.archive, id_page)

    def _attendre_travaux(self) -> None:
        for travail in (self._recherche, self._verification):
            if travail is not None:
                travail.join()

    def arreter(self) -> None:
        """Attend la fin des travaux en cours, avant de fermer l'application."""
        self._minuteur.stop()
        self._recherche_en_attente = False
        self._verification_en_attente = False
        self._attendre_travaux()
