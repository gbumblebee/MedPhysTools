"""Fenêtre principale : accueil, création et ouverture d'une archive, deux espaces."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
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
    QProgressDialog,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from medphystools.archive import (
    Archive,
    ArchiveIntrouvable,
    ArchiveInvalide,
    FormatNonPrisEnCharge,
)
from medphystools.disque import chemin_affichable
from medphystools.export import EstimationExport, estimer_export
from medphystools.interface.espace_archive import EspaceArchive
from medphystools.interface.espace_saisie import EspaceSaisie
from medphystools.interface.preferences import (
    derniere_archive,
    memoriser_derniere_archive,
)

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
        titre = QLabel("MedPhysNotebook")
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


def message_export(estimation: EstimationExport) -> str:
    """Annonce ce que contiendra le PDF, et ce qui n'y sera pas."""
    taille = estimation.octets / 1_000_000
    return (
        f"Le PDF contiendra {estimation.pages} page(s), {estimation.photos} photo(s) et "
        f"{estimation.graphes} graphe(s), pour environ {taille:.0f} Mo.\n\n"
        "Cet ordre de grandeur peut être dépassé, et l'export peut durer plusieurs minutes "
        "pour une grande archive. Il peut être annulé à tout moment.\n\n"
        "Les images DICOM et les pièces jointes autres que les photos n'y sont pas : le PDF "
        "les liste, avec les identifiants PACS des séries DICOM.\n\nContinuer ?"
    )


class FenetrePrincipale(QMainWindow):
    """La fenêtre de l'application : elle partage une archive ouverte entre ses espaces."""

    archive_ouverte = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.archive: Archive | None = None
        self.setWindowTitle("MedPhysNotebook")
        self.resize(1100, 720)

        self.accueil = Accueil()
        self.accueil.creation_demandee.connect(self.demander_creation)
        self.accueil.ouverture_demandee.connect(self.demander_ouverture)

        self.espaces = QTabWidget()
        self.espace_archive = EspaceArchive()
        self.espace_saisie = EspaceSaisie()
        self.espace_saisie.page_modifiee.connect(self.espace_archive.page_modifiee)
        self.espace_archive.reprise_effectuee.connect(self.espace_saisie.recharger)
        self.espace_archive.verification_terminee.connect(self._archive_verifiee)
        self.espace_archive.fiche.edition_demandee.connect(self._modifier_page)
        self.espace_archive.fiche.suppression_demandee.connect(self._supprimer_page)
        self.espace_archive.fiche.export_demande.connect(self.demander_export_page)
        self.espace_saisie.fiche_demandee.connect(self._voir_fiche)
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
        self.action_exporter = QAction("&Exporter toute l'archive en PDF…", self)
        self.action_exporter.triggered.connect(self.demander_export_cahier)
        self.action_exporter.setEnabled(False)
        menu.addAction(self.action_exporter)

        menu.addSeparator()
        self.action_quitter = QAction("&Quitter", self)
        self.action_quitter.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_quitter.triggered.connect(self.close)
        menu.addAction(self.action_quitter)

    def afficher_erreur(self, message: str) -> None:
        """Explique une erreur à l'utilisateur."""
        QMessageBox.critical(self, "MedPhysNotebook", message)

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
        if not self.espace_saisie.peut_quitter_la_page():
            return False
        try:
            archive = Archive(chemin)
        except _ERREURS_ARCHIVE as erreur:
            self.afficher_erreur(f"Impossible d'ouvrir cette archive.\n\n{erreur}")
            return False
        self._installer(archive)
        return True

    def creer_archive(self, description: DescriptionArchive) -> bool:
        """Crée une archive puis l'ouvre. En cas d'échec, rien d'autre ne change."""
        if not self.espace_saisie.peut_quitter_la_page():
            return False
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

    def _modifier_page(self, id_page: str) -> None:
        """Ouvre dans l'espace Saisie la page choisie dans l'espace Archive."""
        if self.espace_saisie.isEnabled() and self.espace_saisie.ouvrir_page(id_page):
            self.espaces.setCurrentWidget(self.espace_saisie)

    def _supprimer_page(self, id_page: str) -> None:
        """Supprime une page, sans que l'espace Saisie la réécrive ensuite."""
        ouverte = self.espace_saisie.page
        if ouverte is not None and ouverte.id == id_page:
            # Arrête l'enregistrement automatique : il viserait une page disparue.
            self.espace_saisie.enregistrer_maintenant()
        if self.espace_archive.demander_suppression(id_page):
            self.espace_saisie.page_supprimee(id_page)

    def ouvrir_derniere_archive(self) -> bool:
        """Rouvre la dernière archive. Si elle a disparu, l'accueil s'affiche simplement."""
        chemin = derniere_archive()
        if chemin is None:
            return False
        if not (chemin / "archive.toml").is_file():
            self.statusBar().showMessage(f"Dernière archive introuvable : {chemin}")
            return False
        return self.ouvrir_archive(chemin)

    # --- Export ----------------------------------------------------------------------------

    def _choisir_fichier_pdf(self, nom: str) -> str:
        chemin, _filtre = QFileDialog.getSaveFileName(
            self, "Exporter en PDF", nom, "PDF (*.pdf)"
        )
        return chemin

    def _exporter(self, identifiants: list[str], nom: str, k: float) -> bool:
        """Écrit un PDF, avec une barre d'avancement qui permet d'interrompre."""
        chemin = self._choisir_fichier_pdf(nom)
        if not chemin:
            return False
        fichier = Path(chemin)
        if fichier.suffix.lower() != ".pdf":
            fichier = fichier.with_name(f"{fichier.name}.pdf")
        # L'export se fait sur la dernière version enregistrée : autant la mettre à jour.
        self.espace_saisie.enregistrer_maintenant()
        # Chargé ici : l'export tire matplotlib, long à importer.
        from medphystools.interface.export_pdf import exporter_pdf  # noqa: PLC0415

        avancement = QProgressDialog(
            "Export du cahier…", "Annuler", 0, len(identifiants), self
        )
        avancement.setWindowModality(Qt.WindowModality.WindowModal)
        avancement.setMinimumDuration(500)

        def avancer(rang: int) -> bool:
            avancement.setValue(rang)
            avancement.setLabelText(
                f"Export du cahier : {rang} page(s) sur {len(identifiants)}…"
            )
            QApplication.processEvents()
            return not avancement.wasCanceled()

        try:
            termine = exporter_pdf(self.archive, identifiants, fichier, k, avancer)
        except (OSError, ValueError) as erreur:
            self.afficher_erreur(f"Export impossible.\n\n{erreur}")
            return False
        finally:
            avancement.close()
        if termine:
            self.statusBar().showMessage(f"Cahier exporté : {fichier}")
        return termine

    def _confirmer_export_archive(self, identifiants: list[str]) -> bool:
        """Prévient de la place et du temps que prendra l'export de toute l'archive."""
        return (
            QMessageBox.question(
                self,
                "Exporter toute l'archive",
                message_export(estimer_export(self.archive, identifiants)),
            )
            == QMessageBox.StandardButton.Yes
        )

    def demander_export_cahier(self) -> bool:
        """Exporte toutes les pages de l'archive, de la plus ancienne à la plus récente."""
        if self.archive is None:
            return False
        identifiants = list(reversed(self.archive.chercher_identifiants()))
        if not identifiants:
            self.afficher_erreur("Cette archive n'a encore aucune page.")
            return False
        if not self._confirmer_export_archive(identifiants):
            return False
        return self._exporter(
            identifiants, f"Cahier_{self.archive.configuration.nom}.pdf", 2.0
        )

    def demander_export_page(self, id_page: str, k: float) -> bool:
        """Exporte une seule page, au facteur k de la fiche."""
        return self._exporter([id_page], f"{id_page}.pdf", k)

    def _voir_fiche(self, id_page: str) -> None:
        """Montre dans l'espace Archive la vue d'ensemble de la page."""
        self.espace_archive.afficher_page(id_page)
        self.espaces.setCurrentWidget(self.espace_archive)

    def _installer(self, archive: Archive) -> None:
        self.archive = archive
        self.setWindowTitle(f"{archive.configuration.nom} — MedPhysNotebook")
        self.statusBar().showMessage("Vérification des fichiers de l'archive…")
        self.pile.setCurrentWidget(self.espaces)
        self.action_exporter.setEnabled(True)
        # Une saisie écrit ses fichiers avant son commit : pendant la vérification, elle
        # passerait pour une modification faite hors du logiciel.
        self.espace_saisie.setEnabled(False)
        self.espace_archive.installer(archive, enregistrer_externes=True)
        self.espace_saisie.installer(archive)
        memoriser_derniere_archive(os.path.abspath(chemin_affichable(archive.racine)))
        self.archive_ouverte.emit(archive)

    def _archive_verifiee(self) -> None:
        """Les fichiers sont vérifiés : la saisie peut commencer."""
        self.espace_saisie.setEnabled(True)
        if self.archive is not None:
            self.statusBar().showMessage(chemin_affichable(self.archive.racine))

    def closeEvent(self, evenement: QCloseEvent) -> None:  # noqa: N802
        """Enregistre la page en cours et attend la fin des travaux en arrière-plan."""
        if not self.espace_saisie.peut_quitter_la_page():
            evenement.ignore()
            return
        self.espace_archive.arreter()
        super().closeEvent(evenement)
