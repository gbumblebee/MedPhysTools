"""Fenêtre principale : accueil, création et ouverture d'une archive, deux espaces."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import (
    Archive,
    ArchiveIntrouvable,
    ArchiveInvalide,
    FormatNonPrisEnCharge,
)
from pymphub.disque import chemin_affichable
from pymphub.interface.espace_archive import EspaceArchive

# Erreurs attendues à l'ouverture ou à la création d'une archive : on les explique à
# l'utilisateur. Les autres sont de vrais défauts du logiciel et doivent rester visibles.
_ERREURS_ARCHIVE = (
    ArchiveIntrouvable,
    ArchiveInvalide,
    FormatNonPrisEnCharge,
    OSError,
    ValueError,
)


@dataclass(frozen=True)
class DescriptionArchive:
    """Ce que l'utilisateur renseigne pour créer une archive."""

    chemin: Path
    site: str
    responsable: str
    periode_debut: date


class DialogueNouvelleArchive(QDialog):
    """Demande les informations d'une nouvelle archive."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Nouvelle archive")
        self.setMinimumWidth(480)

        self.champ_dossier = QLineEdit()
        self.champ_dossier.setPlaceholderText("Dossier où créer l'archive")
        self.bouton_parcourir = QPushButton("Parcourir…")
        self.bouton_parcourir.clicked.connect(self._choisir_dossier)
        ligne_dossier = QHBoxLayout()
        ligne_dossier.addWidget(self.champ_dossier)
        ligne_dossier.addWidget(self.bouton_parcourir)

        self.champ_nom = QLineEdit()
        self.champ_nom.setPlaceholderText("ArchiveStage1")
        self.champ_site = QLineEdit()
        self.champ_responsable = QLineEdit()
        self.champ_debut = QDateEdit(QDate.currentDate())
        self.champ_debut.setCalendarPopup(True)
        self.champ_debut.setDisplayFormat("dd/MM/yyyy")

        formulaire = QFormLayout()
        formulaire.addRow("Emplacement", ligne_dossier)
        formulaire.addRow("Nom de l'archive", self.champ_nom)
        formulaire.addRow("Site", self.champ_site)
        formulaire.addRow("Responsable", self.champ_responsable)
        formulaire.addRow("Début de la période", self.champ_debut)

        self.boutons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.bouton_creer = self.boutons.button(QDialogButtonBox.StandardButton.Ok)
        self.bouton_creer.setText("Créer")
        self.bouton_creer.setObjectName("principal")
        self.boutons.button(QDialogButtonBox.StandardButton.Cancel).setText("Annuler")
        self.boutons.accepted.connect(self.accept)
        self.boutons.rejected.connect(self.reject)

        disposition = QVBoxLayout(self)
        disposition.addLayout(formulaire)
        disposition.addWidget(self.boutons)

        for champ in (
            self.champ_dossier,
            self.champ_nom,
            self.champ_site,
            self.champ_responsable,
        ):
            champ.textChanged.connect(self._actualiser)
        self._actualiser()

    def _choisir_dossier(self) -> None:
        dossier = QFileDialog.getExistingDirectory(self, "Emplacement de l'archive")
        if dossier:
            self.champ_dossier.setText(dossier)

    def _actualiser(self) -> None:
        complet = all(
            champ.text().strip()
            for champ in (
                self.champ_dossier,
                self.champ_nom,
                self.champ_site,
                self.champ_responsable,
            )
        )
        self.bouton_creer.setEnabled(complet)

    def description(self) -> DescriptionArchive:
        """Renvoie les informations saisies."""
        debut = self.champ_debut.date()
        return DescriptionArchive(
            chemin=Path(self.champ_dossier.text().strip())
            / self.champ_nom.text().strip(),
            site=self.champ_site.text().strip(),
            responsable=self.champ_responsable.text().strip(),
            periode_debut=date(debut.year(), debut.month(), debut.day()),
        )


class Accueil(QWidget):
    """Écran affiché tant qu'aucune archive n'est ouverte."""

    creation_demandee = Signal()
    ouverture_demandee = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        titre = QLabel("pymphub")
        titre.setObjectName("titre")
        sous_titre = QLabel(
            "Ouvrez une archive de stage, ou créez-en une pour commencer."
        )
        sous_titre.setObjectName("sous_titre")

        self.bouton_creer = QPushButton("Nouvelle archive…")
        self.bouton_creer.setObjectName("principal")
        self.bouton_creer.clicked.connect(self.creation_demandee)
        self.bouton_ouvrir = QPushButton("Ouvrir une archive…")
        self.bouton_ouvrir.clicked.connect(self.ouverture_demandee)

        boutons = QHBoxLayout()
        boutons.addStretch()
        boutons.addWidget(self.bouton_creer)
        boutons.addWidget(self.bouton_ouvrir)
        boutons.addStretch()

        disposition = QVBoxLayout(self)
        disposition.addStretch()
        disposition.addWidget(titre, alignment=Qt.AlignmentFlag.AlignCenter)
        disposition.addWidget(sous_titre, alignment=Qt.AlignmentFlag.AlignCenter)
        disposition.addSpacing(16)
        disposition.addLayout(boutons)
        disposition.addStretch()


def _espace_a_venir(texte: str) -> QWidget:
    """Espace encore vide, en attendant les prochaines étapes."""
    espace = QWidget()
    etiquette = QLabel(texte)
    etiquette.setObjectName("discret")
    disposition = QVBoxLayout(espace)
    disposition.addWidget(etiquette, alignment=Qt.AlignmentFlag.AlignCenter)
    return espace


class FenetrePrincipale(QMainWindow):
    """La fenêtre de l'application : elle partage une archive ouverte entre ses espaces."""

    archive_ouverte = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.archive: Archive | None = None
        self.setWindowTitle("pymphub")
        self.resize(1100, 720)

        self.accueil = Accueil()
        self.accueil.creation_demandee.connect(self.demander_creation)
        self.accueil.ouverture_demandee.connect(self.demander_ouverture)

        self.espaces = QTabWidget()
        self.espace_archive = EspaceArchive()
        self.espace_saisie = _espace_a_venir(
            "Espace Saisie : création et correction de pages"
        )
        self.espaces.addTab(self.espace_archive, "Archive")
        self.espaces.addTab(self.espace_saisie, "Saisie")

        self.pile = QStackedWidget()
        self.pile.addWidget(self.accueil)
        self.pile.addWidget(self.espaces)
        self.setCentralWidget(self.pile)

        self._creer_menu()
        self.statusBar().showMessage("Aucune archive ouverte")

    def _creer_menu(self) -> None:
        menu = self.menuBar().addMenu("&Fichier")

        self.action_nouvelle = QAction("&Nouvelle archive…", self)
        self.action_nouvelle.setShortcut(QKeySequence.StandardKey.New)
        self.action_nouvelle.triggered.connect(self.demander_creation)
        menu.addAction(self.action_nouvelle)

        self.action_ouvrir = QAction("&Ouvrir une archive…", self)
        self.action_ouvrir.setShortcut(QKeySequence.StandardKey.Open)
        self.action_ouvrir.triggered.connect(self.demander_ouverture)
        menu.addAction(self.action_ouvrir)

        menu.addSeparator()
        self.action_quitter = QAction("&Quitter", self)
        self.action_quitter.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_quitter.triggered.connect(self.close)
        menu.addAction(self.action_quitter)

    def afficher_erreur(self, message: str) -> None:
        """Explique une erreur à l'utilisateur."""
        QMessageBox.critical(self, "pymphub", message)

    def _choisir_archive(self) -> str:
        return QFileDialog.getExistingDirectory(self, "Ouvrir une archive")

    def _decrire_nouvelle_archive(self) -> DescriptionArchive | None:
        dialogue = DialogueNouvelleArchive(self)
        if dialogue.exec() == QDialog.DialogCode.Accepted:
            return dialogue.description()
        return None

    def demander_ouverture(self) -> None:
        """Demande un dossier, puis ouvre l'archive qu'il contient."""
        dossier = self._choisir_archive()
        if dossier:
            self.ouvrir_archive(dossier)

    def demander_creation(self) -> None:
        """Demande les informations d'une archive, puis la crée."""
        description = self._decrire_nouvelle_archive()
        if description is not None:
            self.creer_archive(description)

    def ouvrir_archive(self, chemin: str | Path) -> bool:
        """Ouvre une archive. En cas d'échec, l'archive déjà ouverte reste affichée."""
        try:
            archive = Archive(chemin)
        except _ERREURS_ARCHIVE as erreur:
            self.afficher_erreur(f"Impossible d'ouvrir cette archive.\n\n{erreur}")
            return False
        self._installer(archive)
        return True

    def creer_archive(self, description: DescriptionArchive) -> bool:
        """Crée une archive puis l'ouvre. En cas d'échec, rien d'autre ne change."""
        try:
            archive = Archive.creer(
                description.chemin,
                description.site,
                description.responsable,
                description.periode_debut,
            )
        except _ERREURS_ARCHIVE as erreur:
            self.afficher_erreur(f"Impossible de créer cette archive.\n\n{erreur}")
            return False
        self._installer(archive)
        return True

    def _installer(self, archive: Archive) -> None:
        self.archive = archive
        self.setWindowTitle(f"{archive.configuration.nom} — pymphub")
        self.statusBar().showMessage(chemin_affichable(archive.racine))
        self.pile.setCurrentWidget(self.espaces)
        self.espace_archive.installer(archive)
        self.archive_ouverte.emit(archive)

    def closeEvent(self, evenement: QCloseEvent) -> None:  # noqa: N802
        """Attend la fin des travaux en arrière-plan avant de fermer."""
        self.espace_archive.arreter()
        super().closeEvent(evenement)
