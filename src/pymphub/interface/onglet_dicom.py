"""Onglet DICOM : importer des séries, en pseudonymisant par défaut, et les regarder.

Les modules de lecture DICOM ne se chargent qu'à l'ouverture de cet onglet : ils sont lourds et
la plupart des pages n'en ont pas besoin. Lire ou importer se fait dans des fils de travail,
pour que l'interface reste utilisable pendant une grosse série.
"""

import contextlib
import threading
import weakref
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QHideEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QListWidget,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import Archive
from pymphub.edition import Grandeur
from pymphub.fichiers import ETATS_ALERTE
from pymphub.interface.messages import message_erreur
from pymphub.interface.onglet_fichiers import LienFichier
from pymphub.interface.onglets import EditeurOnglet, declarer_onglet
from pymphub.interface.photos import message_etat
from pymphub.interface.pieces import ajouter_chemin_fichier, chemins_deposes
from pymphub.interface.ramasse_miettes import assurer_ramasse_miettes_principal
from pymphub.interface.texte_en_grand import afficher_en_grand_colonne
from pymphub.interface.theme import ALERTE
from pymphub.page import Copie, Fichier, Onglet, OngletDonnees, Page, ReferenceDicom

if TYPE_CHECKING:
    from pymphub.dicom import ExamenFichiers

_COLONNES = ("Nom", "Modalité", "Fichiers", "Traitement", "État", "Commentaire")
_NOM, _MODALITE, _FICHIERS, _TRAITEMENT, _ETAT, _COMMENTAIRE = range(6)
_REFERENCE = "reference"
_MESSAGE_CHOISIR = "Choisissez une série pour l'afficher."
# Revenir vite sur l'onglet évite une relecture. Passé ce délai, la mémoire revient au système.
DELAI_LIBERATION_MS = 30_000
_ERREURS_LECTURE = (
    ValueError,
    OSError,
    KeyError,
    IndexError,
    AttributeError,
    TypeError,
)


class _Signaux(QObject):
    """Résultats renvoyés par les fils de travail vers l'interface."""

    charge = Signal(int, object)
    charge_echec = Signal(int, str)
    examen_fini = Signal(int, object)
    import_fini = Signal(int, object)
    import_echec = Signal(int, str)
    avancement = Signal(int, int, int)
    empreinte = Signal(str, str)
    lecture_finie = Signal()


def _emettre(signal: Signal, *valeurs: object) -> None:
    # La fenêtre a pu se fermer pendant le travail : le résultat n'intéresse plus personne.
    with contextlib.suppress(RuntimeError):
        signal.emit(*valeurs)


def _lancer(fonction: Callable[..., None], *arguments: object) -> None:
    threading.Thread(target=fonction, args=arguments, daemon=True).start()


# Le dernier panneau qui a chargé une série : un seul volume reste en mémoire dans le logiciel.
_panneau_actif: "weakref.ref[PanneauSerie] | None" = None


class PanneauSerie(QWidget):
    """Le visualiseur, et le chargement d'une série sans figer l'interface.

    Pour ménager la mémoire, seule la série affichée est lue, et :
    - changer de série libère l'ancienne avant de lire la nouvelle, et arrête sa lecture ;
    - une série n'est lue que si le panneau est visible ;
    - ouvrir une série ailleurs libère celle-ci, qui sera relue au retour ;
    - un panneau caché libère sa série après un court délai.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from pymphub.interface.visualiseur import VisualiseurVolume  # noqa: PLC0415

        assurer_ramasse_miettes_principal()
        self._generation = 0
        self.en_cours = False
        self._demande: tuple[Archive, Fichier, tuple[Fichier, ...]] | None = None
        self._fil_occupe = False
        self._a_relancer = False
        self._signaux = _Signaux(self)
        self._signaux.charge.connect(self._charge)
        self._signaux.charge_echec.connect(self._charge_echec)
        self._signaux.lecture_finie.connect(self._lecture_finie)
        self._minuteur = QTimer(self)
        self._minuteur.setSingleShot(True)
        self._minuteur.timeout.connect(self.liberer)
        self.etat = QLabel(_MESSAGE_CHOISIR)
        self.etat.setObjectName("discret")
        self.etat.setWordWrap(True)
        self.etat.setTextFormat(Qt.TextFormat.PlainText)
        self.visualiseur = VisualiseurVolume()
        disposition = QVBoxLayout(self)
        disposition.setContentsMargins(0, 0, 0, 0)
        disposition.addWidget(self.etat)
        disposition.addWidget(self.visualiseur, stretch=1)

    def montrer(
        self, archive: Archive, fichier: Fichier | None, voisins: Sequence[Fichier]
    ) -> None:
        """Choisit la série à afficher. L'ancienne quitte la mémoire avant la lecture."""
        self._demande = None
        self.liberer()
        if fichier is None:
            self.etat.setText(_MESSAGE_CHOISIR)
            return
        if fichier.dicom is None:
            self.etat.setText(
                "Cette série est seulement référencée : ses fichiers restent sur le PACS, et "
                "l'archive n'en a aucune image à afficher."
            )
            return
        self._demande = (archive, fichier, tuple(voisins))
        self.recharger()

    def recharger(self) -> None:
        """Relit la série choisie si elle n'est plus en mémoire et que le panneau est visible."""
        if (
            self._demande is None
            or self.en_cours
            or self.visualiseur.volume is not None
            or not self.isVisible()
        ):
            return
        self._prendre_la_place()
        self.en_cours = True
        self.etat.setText(f"Lecture de « {self._demande[1].nom} »…")
        self.etat.setVisible(True)
        if self._fil_occupe:
            # La lecture précédente s'arrête au prochain fichier : deux lectures à la fois
            # additionneraient leur mémoire.
            self._a_relancer = True
            return
        self._demarrer_la_lecture()

    def _demarrer_la_lecture(self) -> None:
        self._fil_occupe = True
        self._a_relancer = False
        _lancer(self._lire, self._generation, *self._demande)

    def _prendre_la_place(self) -> None:
        """Un seul volume en mémoire : celui d'un autre panneau s'en va."""
        global _panneau_actif  # noqa: PLW0603
        autre = _panneau_actif() if _panneau_actif is not None else None
        if autre is not None and autre is not self:
            with contextlib.suppress(RuntimeError):
                autre.liberer()
        _panneau_actif = weakref.ref(self)

    def liberer(self) -> None:
        """Retire la série de la mémoire. Elle reste choisie, et sera relue au besoin."""
        self._generation += 1
        self._minuteur.stop()
        self.en_cours = False
        self._a_relancer = False
        self.visualiseur.vider()
        if self._demande is not None:
            self.etat.setText(f"« {self._demande[1].nom} » sera relue à son affichage.")
        self.etat.setVisible(True)

    def oublier(self) -> None:
        """Libère la mémoire pour de bon : ce panneau ne relira plus rien."""
        self._demande = None
        self.liberer()

    def showEvent(self, evenement: QShowEvent) -> None:  # noqa: N802
        super().showEvent(evenement)
        self._minuteur.stop()
        self.recharger()

    def hideEvent(self, evenement: QHideEvent) -> None:  # noqa: N802
        super().hideEvent(evenement)
        if self.en_cours:
            self.liberer()
        elif self.visualiseur.volume is not None:
            self._minuteur.start(DELAI_LIBERATION_MS)

    def _lire(
        self,
        generation: int,
        archive: Archive,
        fichier: Fichier,
        voisins: tuple[Fichier, ...],
    ) -> None:
        from pymphub.dicom import LectureAnnulee  # noqa: PLC0415
        from pymphub.volumes import assembler_contenu  # noqa: PLC0415

        def annule() -> bool:
            return generation != self._generation

        try:
            contenu = assembler_contenu(
                fichier, voisins, archive.emplacement_fichier, annule
            )
        except LectureAnnulee:
            pass
        except MemoryError:
            _emettre(
                self._signaux.charge_echec,
                generation,
                "Pas assez de mémoire pour afficher cette série.",
            )
        except _ERREURS_LECTURE as erreur:
            _emettre(self._signaux.charge_echec, generation, message_erreur(erreur))
        else:
            _emettre(self._signaux.charge, generation, contenu)
        finally:
            _emettre(self._signaux.lecture_finie)

    def _lecture_finie(self) -> None:
        self._fil_occupe = False
        if self._a_relancer and self._demande is not None:
            self._demarrer_la_lecture()

    def _charge(self, generation: int, contenu: object) -> None:
        if generation != self._generation:
            return
        self.en_cours = False
        self.etat.setVisible(False)
        self.visualiseur.afficher(
            contenu.volume,
            contenu.structures,
            contenu.dose,
            contenu.balises,
            contenu.remarques,
        )
        if not self.isVisible():
            self._minuteur.start(DELAI_LIBERATION_MS)

    def _charge_echec(self, generation: int, message: str) -> None:
        if generation != self._generation:
            return
        self.en_cours = False
        self.etat.setText(message)
        self.etat.setVisible(True)


class EditeurDicom(EditeurOnglet):
    """Liste les séries DICOM d'un onglet, en importe, et montre celle qui est choisie."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        from pymphub.dicom import LIMITE_TEXTE_INCRUSTE  # noqa: PLC0415

        assurer_ramasse_miettes_principal()
        self.archive: Archive | None = None
        self.liens: list[LienFichier] = []
        self._possedees: list[str] = []
        self._conservees: list[str] = []
        self._remplissage = False
        self._generation_import = 0
        self.import_en_cours = False
        self._signaux = _Signaux(self)
        self._signaux.examen_fini.connect(self._examen_fini)
        self._signaux.import_fini.connect(self._import_fini)
        self._signaux.import_echec.connect(self._import_echec)
        self._signaux.avancement.connect(self._avancement)
        self._signaux.empreinte.connect(self._empreinte_calculee)

        self.choix_mode = QComboBox()
        self.choix_mode.addItem("Pseudonymiser (recommandé)", "pseudonymise")
        self.choix_mode.addItem("Fantôme : conserver tel quel", "fantome")
        self.choix_mode.addItem("Référence : UID seuls, sans copie", _REFERENCE)
        self.choix_mode.setToolTip("Ce qui est fait des prochains fichiers importés")
        self.bouton_fichiers = QPushButton("Importer des fichiers…")
        self.bouton_fichiers.clicked.connect(self.demander_fichiers)
        self.bouton_dossier = QPushButton("Importer un dossier…")
        self.bouton_dossier.clicked.connect(self.demander_dossier)
        self.bouton_en_grand = QPushButton("Afficher en grand…")
        self.bouton_en_grand.setToolTip("Ouvre le visualiseur dans sa propre fenêtre")
        self.bouton_en_grand.clicked.connect(self.afficher_en_grand)
        self.bouton_retirer = QPushButton("Retirer")
        self.bouton_retirer.clicked.connect(self.demander_retrait)
        ligne = QHBoxLayout()
        ligne.addWidget(QLabel("Import"))
        ligne.addWidget(self.choix_mode)
        ligne.addWidget(self.bouton_fichiers)
        ligne.addWidget(self.bouton_dossier)
        ligne.addStretch()
        ligne.addWidget(self.bouton_en_grand)
        ligne.addWidget(self.bouton_retirer)

        self.limite = QLabel(LIMITE_TEXTE_INCRUSTE)
        self.limite.setObjectName("discret")
        self.limite.setWordWrap(True)
        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setVisible(False)
        self.avancee = QProgressBar()
        self.avancee.setVisible(False)

        self.table = QTableWidget(0, len(_COLONNES))
        self.table.setHorizontalHeaderLabels(list(_COLONNES))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        entete = self.table.horizontalHeader()
        entete.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        entete.setStretchLastSection(True)
        self.table.itemChanged.connect(self._cellule_changee)
        self.table.itemSelectionChanged.connect(self._selection_changee)
        afficher_en_grand_colonne(
            self.table, _COMMENTAIRE, "Commentaire"
        ).complement = self._completer_menu
        # De quoi voir au moins trois séries : image, dose et structures.
        self.table.setMinimumHeight(130)

        self.panneau = PanneauSerie()
        separation = QSplitter(Qt.Orientation.Vertical)
        separation.addWidget(self.table)
        separation.addWidget(self.panneau)
        separation.setStretchFactor(0, 1)
        separation.setStretchFactor(1, 6)
        separation.setSizes([150, 700])

        disposition = QVBoxLayout(self)
        disposition.addLayout(ligne)
        disposition.addWidget(self.limite)
        disposition.addWidget(self.message)
        disposition.addWidget(self.avancee)
        disposition.addWidget(separation, stretch=1)
        self.setAcceptDrops(True)

    # --- Chargement -----------------------------------------------------------------------

    def charger(self, archive: Archive) -> None:
        self.archive = archive
        for reference in getattr(self.onglet, "grandeurs", ()):
            grandeur = next((g for g in self.page.mesures if g.nom == reference), None)
            if isinstance(grandeur, Fichier):
                self.liens.append(
                    LienFichier(
                        grandeur,
                        grandeur.nom,
                        grandeur.commentaire,
                        self._etat_connu(grandeur),
                    )
                )
                self._possedees.append(grandeur.nom)
            else:
                self._conservees.append(reference)
        self._remplir()
        self._verifier_en_arriere_plan([lien.fichier for lien in self.liens])

    def _etat_connu(self, fichier: Fichier) -> str:
        """L'état sans relire le fichier : une grosse série se vérifie à part."""
        return self.archive.etat_fichier_brut(fichier, taille_max=0) or "—"

    def _verifier_en_arriere_plan(self, fichiers: Sequence[Fichier]) -> None:
        a_verifier = [f for f in fichiers if isinstance(f.stockage, Copie)]
        if a_verifier:
            _lancer(self._calculer_empreintes, self.archive, a_verifier)

    def _calculer_empreintes(
        self, archive: Archive, fichiers: Sequence[Fichier]
    ) -> None:
        for fichier in fichiers:
            chemin = archive.emplacement_fichier(fichier)
            if chemin is None or not chemin.is_file():
                continue
            with contextlib.suppress(OSError):
                calculee = archive.cache_empreintes.calculer(chemin)
                etat = "intact" if calculee == fichier.stockage.empreinte else "altéré"
                _emettre(self._signaux.empreinte, fichier.nom, etat)

    def _empreinte_calculee(self, nom: str, etat: str) -> None:
        for rang, lien in enumerate(self.liens):
            if lien.fichier.nom == nom:
                lien.etat = etat
                # Cette seule case : réécrire la table fermerait une saisie en cours.
                self._remplissage = True
                self.table.setItem(rang, _ETAT, self._element_etat(etat))
                self._remplissage = False

    # --- Contrat d'un éditeur -------------------------------------------------------------

    def grandeurs_possedees(self) -> list[str]:
        return list(self._possedees)

    def grandeurs(self) -> list[Grandeur]:
        return [lien.grandeur() for lien in self.liens]

    def erreurs(self) -> list[str]:
        return [
            f"DICOM : nom « {lien.nom} » invalide (lettres, chiffres et _ seulement)"
            for lien in self.liens
            if not lien.nom_valide()
        ]

    def valeur(self) -> Onglet:
        noms = tuple(lien.grandeur().nom for lien in self.liens)
        return self.onglet.model_copy(
            update={"grandeurs": noms + tuple(self._conservees)}
        )

    def enregistre(self, page: Page) -> None:
        super().enregistre(page)
        self.onglet = self.valeur()
        self._possedees = [lien.grandeur().nom for lien in self.liens]
        for lien in self.liens:
            lien.fichier = lien.grandeur()

    # --- Table ----------------------------------------------------------------------------

    @staticmethod
    def _element(texte: str, modifiable: bool = False) -> QTableWidgetItem:
        element = QTableWidgetItem(texte)
        if not modifiable:
            element.setFlags(element.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return element

    @classmethod
    def _element_etat(cls, etat: str) -> QTableWidgetItem:
        element = cls._element(etat)
        if etat in ETATS_ALERTE:
            element.setForeground(QColor(ALERTE))
            element.setToolTip(message_etat(etat))
        return element

    @staticmethod
    def _decrire(fichier: Fichier) -> tuple[str, str, str]:
        if fichier.dicom is not None:
            return (
                fichier.dicom.modalite,
                str(fichier.dicom.fichiers),
                fichier.dicom.traitement,
            )
        if isinstance(fichier.stockage, ReferenceDicom):
            return (
                "",
                str(len(fichier.stockage.sop_instance_uids)),
                f"Référence : {fichier.stockage.noeud}",
            )
        return ("", "", "Autre fichier")

    def _remplir(self) -> None:
        rang_choisi = self.table.currentRow()
        self._remplissage = True
        self.table.setRowCount(len(self.liens))
        for rang, lien in enumerate(self.liens):
            modalite, nombre, traitement = self._decrire(lien.fichier)
            self.table.setItem(rang, _NOM, self._element(lien.nom, True))
            self.table.setItem(rang, _MODALITE, self._element(modalite))
            self.table.setItem(rang, _FICHIERS, self._element(nombre))
            self.table.setItem(rang, _TRAITEMENT, self._element(traitement))
            self.table.setItem(rang, _ETAT, self._element_etat(lien.etat))
            self.table.setItem(
                rang, _COMMENTAIRE, self._element(lien.commentaire, True)
            )
        if 0 <= rang_choisi < len(self.liens):
            self.table.selectRow(rang_choisi)
        self._remplissage = False
        self.table.resizeColumnsToContents()

    def _completer_menu(self, menu: QMenu, position: QPoint) -> None:
        """Le clic droit sur une ligne propose de copier le chemin de sa série."""
        element = self.table.itemAt(position)
        if element is not None:
            fichier = self.liens[element.row()].fichier
            ajouter_chemin_fichier(menu, self.archive, fichier, self.table)

    def _cellule_changee(self, element: QTableWidgetItem) -> None:
        if self._remplissage:
            return
        lien = self.liens[element.row()]
        if element.column() == _NOM:
            lien.nom = element.text().strip()
        elif element.column() == _COMMENTAIRE:
            lien.commentaire = element.text()
        self.modifie.emit()

    def _voisins(self) -> list[Fichier]:
        """Les séries auxquelles superposer : celles de l'onglet, et du reste de la page."""
        mes_noms = {lien.fichier.nom for lien in self.liens}
        autres = [
            g
            for g in (self.page.mesures if self.page is not None else ())
            if isinstance(g, Fichier) and g.dicom is not None and g.nom not in mes_noms
        ]
        return [lien.fichier for lien in self.liens] + autres

    def _selection_changee(self) -> None:
        if self._remplissage or self.archive is None:
            return
        rang = self.table.currentRow()
        fichier = self.liens[rang].fichier if 0 <= rang < len(self.liens) else None
        self.panneau.montrer(self.archive, fichier, self._voisins())

    # --- Import ---------------------------------------------------------------------------

    def _signaler(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def _choisir_fichiers(self) -> list[str]:
        chemins, _filtre = QFileDialog.getOpenFileNames(
            self, "Fichiers DICOM à importer"
        )
        return chemins

    def _choisir_dossier(self) -> str:
        return QFileDialog.getExistingDirectory(
            self, "Dossier de fichiers DICOM à importer"
        )

    def _demander_noeud(self) -> str | None:
        noeud, accepte = QInputDialog.getText(
            self,
            "Référence DICOM",
            "Nœud où se trouvent ces séries (par exemple le nom du PACS) :",
        )
        return noeud.strip() if accepte and noeud.strip() else None

    def _confirmer_fantome(self, balises: Sequence[str]) -> bool:
        """Un fichier tel quel garde ses balises : on ne le fait pas sans le dire."""
        reponse = QMessageBox.warning(
            self,
            "Ces fichiers ressemblent à ceux d'un patient",
            "Ces balises ressemblent à celles d'un patient : "
            + ", ".join(balises)
            + ".\n\nConservés tels quels, ils entreraient dans l'archive avec ces données. "
            "Les importer quand même comme fantôme ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reponse == QMessageBox.StandardButton.Yes

    def _confirmer_retrait(self, nom: str) -> bool:
        reponse = QMessageBox.question(
            self,
            "Retirer la série",
            f"Retirer « {nom} » de la page ? Le fichier lui-même n'est pas effacé, et "
            "l'historique garde son lien.",
        )
        return reponse == QMessageBox.StandardButton.Yes

    def demander_fichiers(self) -> None:
        chemins = self._choisir_fichiers()
        if chemins:
            self.importer(chemins)

    def demander_dossier(self) -> None:
        dossier = self._choisir_dossier()
        if dossier:
            self.importer([dossier])

    def _occuper(self, occupe: bool) -> None:
        self.import_en_cours = occupe
        self.bouton_fichiers.setEnabled(not occupe)
        self.bouton_dossier.setEnabled(not occupe)
        self.choix_mode.setEnabled(not occupe)
        self.avancee.setVisible(occupe)
        if occupe:
            self.avancee.setRange(0, 0)

    def _noms_pris(self) -> list[str]:
        page = [g.nom for g in self.page.mesures] if self.page is not None else []
        return page + [lien.nom for lien in self.liens]

    def importer(self, chemins: Sequence[str | Path]) -> None:
        """Lance l'import des fichiers choisis, selon le mode en vigueur."""
        if self.import_en_cours or self.archive is None:
            return
        mode = self.choix_mode.currentData()
        noeud = None
        if mode == _REFERENCE:
            noeud = self._demander_noeud()
            if noeud is None:
                return
        self._signaler("")
        self._occuper(True)
        self._generation_import += 1
        generation = self._generation_import
        if mode == _REFERENCE:
            _lancer(
                self._travail_references,
                generation,
                list(chemins),
                noeud,
                self._noms_pris(),
            )
        else:
            _lancer(self._travail_examen, generation, list(chemins), mode)

    def _travail_examen(self, generation: int, chemins: list, mode: str) -> None:
        from pymphub.dicom import examiner  # noqa: PLC0415

        try:
            examen = examiner(chemins)
        except _ERREURS_LECTURE as erreur:
            _emettre(self._signaux.import_echec, generation, message_erreur(erreur))
            return
        _emettre(self._signaux.examen_fini, generation, (examen, mode))

    def _examen_fini(self, generation: int, resultat: tuple) -> None:
        """De retour dans l'interface : c'est le moment de demander une confirmation."""
        if generation != self._generation_import:
            return
        examen, mode = resultat
        if (
            mode == "fantome"
            and examen.balises_patient
            and not self._confirmer_fantome(examen.balises_patient)
        ):
            self._occuper(False)
            self._signaler("Import annulé : rien n'a été copié.")
            return
        _lancer(self._travail_import, generation, examen, mode, self._noms_pris())

    def _travail_import(
        self, generation: int, examen: "ExamenFichiers", mode: str, noms_pris: list[str]
    ) -> None:
        from pymphub.dicom import importer_examen  # noqa: PLC0415

        def avancer(faits: int, total: int) -> None:
            _emettre(self._signaux.avancement, generation, faits, total)

        try:
            resultat = importer_examen(self.archive, examen, mode, noms_pris, avancer)
        except MemoryError:
            _emettre(
                self._signaux.import_echec,
                generation,
                "Pas assez de mémoire pour importer.",
            )
        except _ERREURS_LECTURE as erreur:
            _emettre(self._signaux.import_echec, generation, message_erreur(erreur))
        else:
            _emettre(self._signaux.import_fini, generation, resultat)

    def _travail_references(
        self, generation: int, chemins: list, noeud: str, noms_pris: list[str]
    ) -> None:
        from pymphub.dicom import creer_references  # noqa: PLC0415

        try:
            resultat = creer_references(chemins, noeud, noms_pris)
        except _ERREURS_LECTURE as erreur:
            _emettre(self._signaux.import_echec, generation, message_erreur(erreur))
        else:
            _emettre(self._signaux.import_fini, generation, resultat)

    def _avancement(self, generation: int, faits: int, total: int) -> None:
        if generation == self._generation_import:
            self.avancee.setRange(0, total)
            self.avancee.setValue(faits)

    def _import_echec(self, generation: int, message: str) -> None:
        if generation == self._generation_import:
            self._occuper(False)
            self._signaler(message)

    def _import_fini(self, generation: int, resultat: object) -> None:
        if generation != self._generation_import:
            return
        self._occuper(False)
        premier = len(self.liens)
        for fichier in resultat.fichiers:
            self.liens.append(
                LienFichier(fichier, fichier.nom, "", self._etat_connu(fichier))
            )
        messages = list(resultat.avertissements)
        for fichier in resultat.fichiers:
            if fichier.dicom is not None:
                messages.extend(fichier.dicom.avertissements)
        if resultat.ignores:
            messages.append(
                f"{resultat.ignores} fichier(s) ignoré(s) : ce ne sont pas des DICOM utilisables."
            )
        self._signaler(" ".join(messages))
        self._remplir()
        self.modifie.emit()
        self._verifier_en_arriere_plan(resultat.fichiers)
        if resultat.fichiers:
            self.table.selectRow(premier)

    # --- Grande fenêtre --------------------------------------------------------------------

    def afficher_en_grand(self) -> None:
        """Montre les séries dans une fenêtre entière : les coupes y gagnent toute la place."""
        fichiers = [
            lien.fichier for lien in self.liens if lien.fichier.dicom is not None
        ]
        if not fichiers or self.archive is None:
            self._signaler("Aucune série à afficher : importez d'abord des fichiers.")
            return
        rang = self.table.currentRow()
        choisi = self.liens[rang].fichier if 0 <= rang < len(self.liens) else None
        dialogue = DialogueSeries(self.archive, self.page, self, fichiers, choisi)
        dialogue.exec()
        dialogue.deleteLater()
        # Le dialogue a pris la place en mémoire : la série de l'onglet se relit.
        self.panneau.recharger()

    # --- Retrait --------------------------------------------------------------------------

    def demander_retrait(self) -> None:
        """Retire de la page la série choisie, sans effacer le fichier lui-même."""
        rang = self.table.currentRow()
        if not 0 <= rang < len(self.liens):
            return
        if self._confirmer_retrait(self.liens[rang].nom):
            del self.liens[rang]
            self._remplir()
            self.modifie.emit()
            self._selection_changee()

    # --- Glisser-déposer ------------------------------------------------------------------

    def dragEnterEvent(self, evenement: QDragEnterEvent) -> None:  # noqa: N802
        if chemins_deposes(evenement.mimeData()):
            evenement.acceptProposedAction()

    def dragMoveEvent(self, evenement: QDragMoveEvent) -> None:  # noqa: N802
        if chemins_deposes(evenement.mimeData()):
            evenement.acceptProposedAction()

    def dropEvent(self, evenement: QDropEvent) -> None:  # noqa: N802
        chemins = chemins_deposes(evenement.mimeData())
        if chemins:
            evenement.acceptProposedAction()
            self.importer(chemins)


class DialogueSeries(QDialog):
    """Regarde des séries DICOM dans une grande fenêtre, sans rien pouvoir y changer."""

    def __init__(
        self,
        archive: Archive,
        page: Page,
        parent: QWidget | None = None,
        fichiers: Sequence[Fichier] | None = None,
        choisi: Fichier | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Séries DICOM — {page.titre}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint)
        self.resize(1400, 900)
        self.archive = archive
        self.fichiers = (
            list(fichiers)
            if fichiers is not None
            else [
                g
                for g in page.mesures
                if isinstance(g, Fichier) and g.dicom is not None
            ]
        )
        self.liste = QListWidget()
        self.liste.setWordWrap(True)
        for fichier in self.fichiers:
            self.liste.addItem(f"{fichier.nom} : {fichier.dicom.resume}")
        self.liste.currentRowChanged.connect(self._choisie)
        self.panneau = PanneauSerie()
        separation = QSplitter(Qt.Orientation.Horizontal)
        separation.addWidget(self.liste)
        separation.addWidget(self.panneau)
        separation.setStretchFactor(1, 5)
        separation.setSizes([220, 1180])
        disposition = QVBoxLayout(self)
        disposition.addWidget(separation)
        if self.fichiers:
            noms = [f.nom for f in self.fichiers]
            depart = (
                noms.index(choisi.nom)
                if choisi is not None and choisi.nom in noms
                else 0
            )
            self.liste.setCurrentRow(depart)

    def done(self, resultat: int) -> None:
        # Fermé, le dialogue existe encore un instant : il ne garde pas son volume pour autant.
        self.panneau.oublier()
        super().done(resultat)

    def _choisie(self, rang: int) -> None:
        if 0 <= rang < len(self.fichiers):
            self.panneau.montrer(self.archive, self.fichiers[rang], self.fichiers)


declarer_onglet("dicom", "DICOM", OngletDonnees, EditeurDicom)
