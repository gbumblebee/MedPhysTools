"""Onglet de fichiers : pièces jointes de la page et liens vers des fichiers bruts.

Une pièce jointe est copiée dans le dossier de la page. Un lien vers un fichier brut est une
grandeur de la page : le fichier est soit copié dans `objets/`, soit laissé à sa place et
référencé par son chemin et son empreinte.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QModelIndex, QPoint, Qt
from PySide6.QtGui import QColor, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from medphystools.archive import TAILLE_MAX_VERIFICATION, Archive
from medphystools.edition import Grandeur, nom_libre
from medphystools.fichiers import ETATS_ALERTE
from medphystools.interface.messages import message_erreur
from medphystools.interface.onglets import EditeurOnglet, declarer_onglet
from medphystools.interface.photos import message_etat
from medphystools.interface.pieces import (
    FichierRefuse,
    PiecesDeLaPage,
    ajouter_chemin,
    ajouter_chemin_fichier,
    chemins_deposes,
    ouvrir_avec_le_systeme,
    refuser_dicom,
)
from medphystools.interface.texte_en_grand import afficher_en_grand_colonne
from medphystools.interface.theme import ALERTE
from medphystools.page import (
    Copie,
    Fichier,
    Onglet,
    OngletDonnees,
    Page,
    PieceJointe,
    ReferenceFichier,
)

_COLONNES = ("Nom", "État", "Stockage", "Détail", "Identifiant PACS", "Commentaire")
_NOM, _ETAT, _STOCKAGE, _DETAIL, _PACS, _COMMENTAIRE = range(6)
_MESSAGE_SERIE = (
    "Cette série DICOM se regarde et se retire dans son onglet DICOM : "
    "elle disparaîtra alors d'ici aussi."
)


@dataclass
class LienFichier:
    """Un lien vers un fichier brut, tel qu'il est à l'écran."""

    fichier: Fichier
    nom: str
    commentaire: str
    etat: str

    def nom_valide(self) -> bool:
        return self.nom.isidentifier()

    def grandeur(self) -> Fichier:
        """La grandeur à enregistrer : un nom invalide garde en attendant l'ancien nom."""
        return self.fichier.model_copy(
            update={
                "nom": self.nom if self.nom_valide() else self.fichier.nom,
                "commentaire": self.commentaire,
            }
        )


class EditeurFichiers(EditeurOnglet):
    """Liste les fichiers de la page, et en ajoute par bouton ou par glisser-déposer."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        self.archive: Archive | None = None
        self.liens: list[LienFichier] = []
        self._possedees: list[str] = []
        self._references_conservees: list[str] = []
        self.series_dicom: list[Fichier] = []
        self._remplissage = False

        self.bouton_joindre = QPushButton("Joindre un fichier…")
        self.bouton_joindre.setToolTip("Copie le fichier dans le dossier de la page")
        self.bouton_joindre.clicked.connect(self.demander_jointure)
        self.bouton_copie = QPushButton("Fichier brut en copie…")
        self.bouton_copie.setToolTip(
            "Copie le fichier dans le magasin de l'archive, en lecture seule"
        )
        self.bouton_copie.clicked.connect(self.demander_copie)
        self.bouton_reference = QPushButton("Fichier brut en référence…")
        self.bouton_reference.setToolTip(
            "Laisse le fichier à sa place : la page garde son chemin et son empreinte"
        )
        self.bouton_reference.clicked.connect(self.demander_reference)
        self.bouton_ouvrir = QPushButton("Ouvrir")
        self.bouton_ouvrir.clicked.connect(self.ouvrir_selection)
        self.bouton_retirer = QPushButton("Retirer")
        self.bouton_retirer.clicked.connect(self.demander_retrait)
        ligne = QHBoxLayout()
        for bouton in (
            self.bouton_joindre,
            self.bouton_copie,
            self.bouton_reference,
        ):
            ligne.addWidget(bouton)
        ligne.addStretch()
        ligne.addWidget(self.bouton_ouvrir)
        ligne.addWidget(self.bouton_retirer)

        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setVisible(False)

        self.table = QTableWidget(0, len(_COLONNES))
        self.table.setHorizontalHeaderLabels(list(_COLONNES))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        entete = self.table.horizontalHeader()
        entete.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        entete.setStretchLastSection(True)
        self.table.itemChanged.connect(self._cellule_changee)
        self.table.doubleClicked.connect(self._double_clic)
        afficher_en_grand_colonne(
            self.table, _COMMENTAIRE, "Commentaire"
        ).complement = self._completer_menu

        indication = QLabel("Déposez ici des fichiers pour les joindre à la page.")
        indication.setObjectName("discret")

        disposition = QVBoxLayout(self)
        disposition.addLayout(ligne)
        disposition.addWidget(self.message)
        disposition.addWidget(self.table, stretch=1)
        disposition.addWidget(indication)
        self.setAcceptDrops(True)

    # --- Chargement -----------------------------------------------------------------------

    def attacher_pieces(self, pieces: PiecesDeLaPage) -> None:
        super().attacher_pieces(pieces)
        pieces.changees.connect(self._remplir)

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
                        self._verifier(grandeur),
                    )
                )
                self._possedees.append(grandeur.nom)
            else:
                self._references_conservees.append(reference)
        self.series_dicom = self._series_de_la_page()
        self._remplir()

    def _series_de_la_page(self) -> list[Fichier]:
        """Les séries des onglets DICOM : la page en fait des fichiers bruts comme les autres."""
        miennes = {lien.fichier.nom for lien in self.liens}
        return [
            g
            for g in (self.page.mesures if self.page is not None else ())
            if isinstance(g, Fichier)
            and g.type_fichier == "dicom"
            and g.nom not in miennes
        ]

    def _verifier(self, fichier: Fichier) -> str:
        """Vérifie un fichier une seule fois par ouverture de la page."""
        etat = self.archive.etat_fichier_brut(fichier, TAILLE_MAX_VERIFICATION)
        return etat or "—"

    # --- Contrat d'un éditeur -------------------------------------------------------------

    def grandeurs_possedees(self) -> list[str]:
        return list(self._possedees)

    def grandeurs(self) -> list[Grandeur]:
        return [lien.grandeur() for lien in self.liens]

    def erreurs(self) -> list[str]:
        return [
            f"Fichier : nom « {lien.nom} » invalide (lettres, chiffres et _ seulement)"
            for lien in self.liens
            if not lien.nom_valide()
        ]

    def valeur(self) -> Onglet:
        noms = tuple(lien.grandeur().nom for lien in self.liens)
        return self.onglet.model_copy(
            update={"grandeurs": noms + tuple(self._references_conservees)}
        )

    def enregistre(self, page: Page) -> None:
        super().enregistre(page)
        self.onglet = self.valeur()
        self._possedees = [lien.grandeur().nom for lien in self.liens]
        for lien in self.liens:
            lien.fichier = lien.grandeur()
        series = self._series_de_la_page()
        # Seulement si elles ont changé : réécrire la table fermerait une saisie en cours.
        if series != self.series_dicom:
            self.series_dicom = series
            self._remplir()

    # --- Affichage ------------------------------------------------------------------------

    def _pieces_listees(self) -> tuple[PieceJointe, ...]:
        return self.pieces.pieces if self.pieces is not None else ()

    def _element(self, texte: str, modifiable: bool = False) -> QTableWidgetItem:
        element = QTableWidgetItem(texte)
        if not modifiable:
            element.setFlags(element.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return element

    def _ecrire_ligne(
        self, rang: int, cellules: Sequence[str], nom_modifiable: bool
    ) -> None:
        nom, stockage, detail, etat, commentaire = cellules
        self.table.setItem(rang, _NOM, self._element(nom, nom_modifiable))
        self.table.setItem(rang, _STOCKAGE, self._element(stockage))
        self.table.setItem(rang, _DETAIL, self._element(detail))
        self.table.setItem(rang, _PACS, self._element(""))
        element_etat = self._element(etat)
        if etat in ETATS_ALERTE:
            element_etat.setForeground(QColor(ALERTE))
            element_etat.setToolTip(message_etat(etat))
        self.table.setItem(rang, _ETAT, element_etat)
        self.table.setItem(rang, _COMMENTAIRE, self._element(commentaire, True))

    def _decrire_lien(self, lien: LienFichier) -> tuple[str, str, str, str, str]:
        stockage = lien.fichier.stockage
        if isinstance(stockage, Copie):
            nature, detail = "Copie", stockage.nom_origine
        elif isinstance(stockage, ReferenceFichier):
            nature, detail = "Référence fichier", stockage.chemin
        else:
            nature, detail = "Référence DICOM", stockage.noeud
        return (lien.nom, nature, detail, lien.etat, lien.commentaire)

    def _ecrire_identifiant_pacs(self, rang: int, fichier: Fichier) -> None:
        """Montre l'UID de la série ; le survol donne aussi celui de l'étude."""
        identifiants = fichier.identifiants_pacs
        if identifiants is not None:
            element = self.table.item(rang, _PACS)
            element.setText(identifiants[1])
            element.setToolTip(fichier.texte_identifiants_pacs)

    def _ecrire_serie(self, rang: int, serie: Fichier) -> None:
        """Une série DICOM : en lecture seule ici, car elle appartient à son onglet."""
        if serie.dicom is not None:
            nature, detail = "Copie DICOM", serie.dicom.resume
        else:
            nature, detail = "Référence DICOM", serie.stockage.noeud
        etat = self.archive.etat_fichier_brut(serie, taille_max=0) or "—"
        self._ecrire_ligne(
            rang, (serie.nom, nature, detail, etat, serie.commentaire), False
        )
        self._ecrire_identifiant_pacs(rang, serie)
        self.table.item(rang, _COMMENTAIRE).setFlags(
            self.table.item(rang, _COMMENTAIRE).flags() & ~Qt.ItemFlag.ItemIsEditable
        )

    def _est_serie(self, rang: int) -> bool:
        return rang >= len(self.liens) + len(self._pieces_listees())

    def _remplir(self) -> None:
        """Réécrit la table : liens, puis pièces jointes, puis séries DICOM de la page."""
        if self._remplissage:
            return  # Le changement vient de cette table : elle est déjà à jour.
        self._remplissage = True
        pieces = self._pieces_listees()
        debut_series = len(self.liens) + len(pieces)
        self.table.setRowCount(debut_series + len(self.series_dicom))
        for rang, lien in enumerate(self.liens):
            self._ecrire_ligne(rang, self._decrire_lien(lien), nom_modifiable=True)
            self._ecrire_identifiant_pacs(rang, lien.fichier)
        for rang, piece in enumerate(pieces, start=len(self.liens)):
            self._ecrire_ligne(
                rang,
                (
                    piece.chemin,
                    "Pièce jointe",
                    "",
                    self.pieces.etat(piece),
                    piece.commentaire,
                ),
                nom_modifiable=False,
            )
        for rang, serie in enumerate(self.series_dicom, start=debut_series):
            self._ecrire_serie(rang, serie)
        self._remplissage = False
        self.table.resizeColumnsToContents()

    def _cellule_changee(self, element: QTableWidgetItem) -> None:
        if self._remplissage or self._est_serie(element.row()):
            return
        if element.row() >= len(self.liens):
            piece = self._pieces_listees()[element.row() - len(self.liens)]
            if element.column() == _COMMENTAIRE:
                # Réécrire la table ici détruirait la case en cours de modification.
                self._remplissage = True
                self.pieces.commenter(piece.chemin, element.text())
                self._remplissage = False
            return
        lien = self.liens[element.row()]
        if element.column() == _NOM:
            lien.nom = element.text().strip()
        elif element.column() == _COMMENTAIRE:
            lien.commentaire = element.text()
        self.modifie.emit()

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
            self.joindre(chemins)

    # --- Ajouts ---------------------------------------------------------------------------

    def _signaler(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def _choisir_fichiers(self, titre: str) -> list[str]:
        chemins, _filtre = QFileDialog.getOpenFileNames(self, titre)
        return chemins

    def demander_jointure(self) -> None:
        """Choisit des fichiers à copier dans le dossier de la page."""
        self.joindre([Path(c) for c in self._choisir_fichiers("Joindre à la page")])

    def joindre(self, chemins: Sequence[Path]) -> None:
        """Copie des fichiers dans le dossier de la page. Une erreur est expliquée."""
        messages: list[str] = []
        for chemin in chemins:
            try:
                self.pieces.joindre(chemin)
            except (ValueError, OSError) as erreur:
                messages.append(message_erreur(erreur))
        self._signaler(" ".join(messages))
        self.modifie.emit()

    def _ajouter_lien(self, chemin: Path, copie: bool) -> None:
        pris = [g.nom for g in self.page.mesures] + [lien.nom for lien in self.liens]
        nom = nom_libre(chemin.stem, pris)
        try:
            if not chemin.is_file():
                raise FichierRefuse(f"« {chemin.name} » n'est pas un fichier.")
            refuser_dicom(chemin)
            importer = (
                self.archive.importer_copie
                if copie
                else self.archive.referencer_fichier
            )
            fichier = importer(chemin, nom)
        except (ValueError, ValidationError, OSError) as erreur:
            self._signaler(message_erreur(erreur))
            return
        self._signaler("")
        self.liens.append(LienFichier(fichier, nom, "", self._verifier(fichier)))
        self._remplir()
        self.modifie.emit()

    def demander_copie(self) -> None:
        """Choisit des fichiers bruts à copier dans le magasin `objets/`."""
        for chemin in self._choisir_fichiers("Fichiers bruts à copier dans l'archive"):
            self._ajouter_lien(Path(chemin), copie=True)

    def demander_reference(self) -> None:
        """Choisit des fichiers bruts à référencer sans les copier."""
        for chemin in self._choisir_fichiers("Fichiers bruts à référencer"):
            self._ajouter_lien(Path(chemin), copie=False)

    # --- Actions sur la ligne choisie -----------------------------------------------------

    def _ligne_choisie(self) -> int | None:
        rang = self.table.currentRow()
        return rang if 0 <= rang < self.table.rowCount() else None

    def _emplacement(self, rang: int) -> Path | None:
        if rang < len(self.liens):
            return self.archive.emplacement_fichier(self.liens[rang].fichier)
        piece = self._pieces_listees()[rang - len(self.liens)]
        return self.pieces.emplacement(piece.chemin)

    def _completer_menu(self, menu: QMenu, position: QPoint) -> None:
        """Le clic droit sur une ligne propose de copier le chemin de son fichier."""
        element = self.table.itemAt(position)
        if element is None:
            return
        rang = element.row()
        nombre_liens = len(self.liens)
        if rang < nombre_liens:
            ajouter_chemin_fichier(
                menu, self.archive, self.liens[rang].fichier, self.table
            )
        elif self._est_serie(rang):
            serie = self.series_dicom[rang - nombre_liens - len(self._pieces_listees())]
            ajouter_chemin_fichier(menu, self.archive, serie, self.table)
        else:
            piece = self._pieces_listees()[rang - nombre_liens]
            ajouter_chemin(menu, self.pieces.emplacement(piece.chemin), self.table)

    def _double_clic(self, index: QModelIndex) -> None:
        # Le nom et le commentaire d'un lien s'éditent : seules les autres cases ouvrent.
        if self.table.item(index.row(), index.column()).flags() & (
            Qt.ItemFlag.ItemIsEditable
        ):
            return
        self.ouvrir_selection()

    def _ouvrir(self, chemin: Path) -> bool:
        return ouvrir_avec_le_systeme(chemin)

    def ouvrir_selection(self) -> None:
        """Ouvre le fichier choisi avec le logiciel associé par le système."""
        rang = self._ligne_choisie()
        if rang is None:
            return
        if self._est_serie(rang):
            self._signaler(_MESSAGE_SERIE)
            return
        chemin = self._emplacement(rang)
        if chemin is None or not chemin.is_file():
            self._signaler("Ce fichier n'est pas disponible sur cet ordinateur.")
            return
        self._signaler("")
        if not self._ouvrir(chemin):
            self._signaler(f"Aucun logiciel n'a pu ouvrir « {chemin.name} ».")

    def _confirmer_retrait(self, nom: str) -> bool:
        reponse = QMessageBox.question(
            self,
            "Retirer le fichier",
            f"Retirer « {nom} » de la page ? Le fichier lui-même n'est pas effacé, et "
            "l'historique garde son lien.",
        )
        return reponse == QMessageBox.StandardButton.Yes

    def demander_retrait(self) -> None:
        """Retire de la page le fichier choisi, sans effacer le fichier lui-même."""
        rang = self._ligne_choisie()
        if rang is None:
            return
        if self._est_serie(rang):
            self._signaler(_MESSAGE_SERIE)
            return
        if rang < len(self.liens):
            if self._confirmer_retrait(self.liens[rang].nom):
                del self.liens[rang]
                self._remplir()
                self.modifie.emit()
            return
        piece = self._pieces_listees()[rang - len(self.liens)]
        if not self._confirmer_retrait(piece.chemin):
            return
        try:
            self.pieces.retirer(piece.chemin)
        except ValueError as erreur:
            self._signaler(message_erreur(erreur))
            return
        self._signaler("")


declarer_onglet("fichiers", "Fichiers", OngletDonnees, EditeurFichiers)
