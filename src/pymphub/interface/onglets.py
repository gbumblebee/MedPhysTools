"""Types d'onglets de l'interface : c'est ici qu'on en ajoute un nouveau.

Pour ajouter un type d'onglet, il suffit de :
1. décrire ses données dans une classe dérivée de `Onglet`, avec des valeurs par défaut ;
2. écrire son éditeur, une classe dérivée de `EditeurOnglet` ;
3. appeler `declarer_onglet`.

Un type sans éditeur, ou inconnu de cette version du logiciel, s'affiche en lecture seule
et ses données restent intactes.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import Archive
from pymphub.edition import Grandeur
from pymphub.interface.messages import message_erreur
from pymphub.interface.photos import (
    BandePhotos,
    DialogueAnnotation,
    VignettePhoto,
    est_image,
)
from pymphub.interface.pieces import PiecesDeLaPage, chemins_deposes
from pymphub.interface.texte_en_grand import afficher_en_grand
from pymphub.page import (
    Onglet,
    OngletNotes,
    Page,
    PhotoAnnotee,
    enregistrer_type_onglet,
    retirer_type_onglet,
)


class EditeurOnglet(QWidget):
    """Base d'un éditeur : il affiche un onglet et donne sa nouvelle valeur."""

    modifie = Signal()
    mesure_proposee = Signal(object)

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.onglet = onglet
        self.page = page
        self.pieces: PiecesDeLaPage | None = None

    def attacher_pieces(self, pieces: PiecesDeLaPage) -> None:
        """Donne accès aux pièces jointes de la page, avant `charger`."""
        self.pieces = pieces

    def valeur(self) -> Onglet:
        """Renvoie l'onglet avec les modifications de l'utilisateur."""
        return self.onglet

    def grandeurs_possedees(self) -> list[str]:
        """Noms des grandeurs de la page que cet éditeur peut remplacer ou retirer."""
        return []

    def grandeurs(self) -> list[Grandeur]:
        """Grandeurs valides saisies dans cet onglet."""
        return []

    def erreurs(self) -> list[str]:
        """Ce qu'il manque pour que la saisie de cet onglet soit complète."""
        return []

    def enregistre(self, page: Page) -> None:
        """Prévient l'éditeur que sa saisie vient d'être enregistrée dans cette page."""
        self.page = page

    def charger(self, archive: Archive) -> None:
        """Relit ce qui ne se trouve pas dans `page.json`, comme un fichier CSV."""

    def donnees_tableaux(self) -> dict[str, dict[str, list[str | float]]]:
        """Cellules des tableaux à enregistrer, par nom de tableau."""
        return {}

    def tableau_modifie(self) -> bool:
        """Indique si les cellules diffèrent de la dernière version enregistrée."""
        return False

    def renommer(self, titre: str) -> None:
        """Change le titre de l'onglet, sans toucher à ses données."""
        self.onglet = self.onglet.model_copy(update={"titre": titre})
        self.modifie.emit()


class EditeurLectureSeule(EditeurOnglet):
    """Affiche un onglet sans éditeur. Ses données sont rendues telles quelles."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        message = QLabel(
            f"Le type d'onglet « {onglet.type} » ne se modifie pas encore dans cette "
            "version. Ses données sont conservées."
        )
        message.setObjectName("discret")
        message.setWordWrap(True)
        self.contenu = QPlainTextEdit(
            json.dumps(onglet.model_dump(mode="json"), ensure_ascii=False, indent=2)
        )
        self.contenu.setReadOnly(True)
        disposition = QVBoxLayout(self)
        disposition.addWidget(message)
        disposition.addWidget(self.contenu)


class ZoneNotes(QPlainTextEdit):
    """Le texte des notes : un fichier déposé dessus est joint, au lieu d'écrire son chemin."""

    fichiers_deposes = Signal(list)

    def dragEnterEvent(self, evenement: QDragEnterEvent) -> None:  # noqa: N802
        if chemins_deposes(evenement.mimeData()):
            evenement.acceptProposedAction()
        else:
            super().dragEnterEvent(evenement)

    def dragMoveEvent(self, evenement: QDragMoveEvent) -> None:  # noqa: N802
        if chemins_deposes(evenement.mimeData()):
            evenement.acceptProposedAction()
        else:
            super().dragMoveEvent(evenement)

    def dropEvent(self, evenement: QDropEvent) -> None:  # noqa: N802
        chemins = chemins_deposes(evenement.mimeData())
        if chemins:
            evenement.acceptProposedAction()
            self.fichiers_deposes.emit(chemins)
        else:
            super().dropEvent(evenement)


class EditeurNotes(EditeurOnglet):
    """Édite un onglet de notes : son texte et ses photos annotées."""

    def __init__(
        self, onglet: Onglet, page: Page | None = None, parent: QWidget | None = None
    ) -> None:
        super().__init__(onglet, page, parent)
        self.photos: list[PhotoAnnotee] = list(getattr(onglet, "photos", ()))
        self.texte = ZoneNotes(getattr(onglet, "texte", ""))
        self.texte.setPlaceholderText(
            "Montage, conditions, remarques… Déposez ici une photo ou un fichier."
        )
        afficher_en_grand(self.texte, lambda: self.onglet.titre or "Notes")
        self.texte.textChanged.connect(self.modifie)
        self.texte.fichiers_deposes.connect(self.ajouter_fichiers)

        self.bande = BandePhotos()
        self.bande.setAcceptDrops(True)
        self.message = QLabel("")
        self.message.setObjectName("discret")
        self.message.setWordWrap(True)
        self.message.setVisible(False)
        self.bouton_photo = QPushButton("Ajouter une photo ou un fichier…")
        self.bouton_photo.clicked.connect(self.demander_fichiers)
        ligne = QHBoxLayout()
        ligne.addWidget(self.message, stretch=1)
        ligne.addWidget(self.bouton_photo)

        disposition = QVBoxLayout(self)
        disposition.addWidget(self.texte, stretch=1)
        disposition.addWidget(self.bande)
        disposition.addLayout(ligne)
        self.setAcceptDrops(True)

    def charger(self, _archive: Archive) -> None:
        self._afficher_photos()

    def valeur(self) -> Onglet:
        return self.onglet.model_copy(
            update={"texte": self.texte.toPlainText(), "photos": tuple(self.photos)}
        )

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
            self.ajouter_fichiers(chemins)

    # --- Photos ---------------------------------------------------------------------------

    def _signaler(self, message: str) -> None:
        self.message.setText(message)
        self.message.setVisible(bool(message))

    def _choisir_fichiers(self) -> list[str]:
        chemins, _filtre = QFileDialog.getOpenFileNames(self, "Joindre à la page")
        return chemins

    def demander_fichiers(self) -> None:
        """Choisit des fichiers à joindre à la page."""
        self.ajouter_fichiers([Path(c) for c in self._choisir_fichiers()])

    def ajouter_fichiers(self, chemins: Sequence[Path]) -> None:
        """Joint des fichiers à la page : une image s'affiche dans cet onglet."""
        if self.pieces is None:
            return
        messages: list[str] = []
        for chemin in chemins:
            try:
                piece = self.pieces.joindre(chemin)
            except (ValueError, OSError) as erreur:
                messages.append(message_erreur(erreur))
                continue
            if not est_image(chemin):
                messages.append(
                    f"« {chemin.name} » est joint à la page, sans aperçu ici : il "
                    "apparaît dans la fiche et dans un onglet Fichiers."
                )
            elif all(photo.chemin != piece.chemin for photo in self.photos):
                self.photos.append(PhotoAnnotee(chemin=piece.chemin))
        self._signaler(" ".join(messages))
        self._afficher_photos()
        self.modifie.emit()

    def _afficher_photos(self) -> None:
        if self.pieces is None:
            return
        pieces = {piece.chemin: piece for piece in self.pieces.pieces}
        vignettes: list[VignettePhoto] = []
        for rang, photo in enumerate(self.photos):
            vignette = VignettePhoto(
                self.pieces.emplacement(photo.chemin),
                photo,
                self.pieces.etat(pieces[photo.chemin]),
                modifiable=True,
            )
            vignette.ouverture_demandee.connect(
                lambda r=rang: self.demander_annotation(r)
            )
            vignette.retrait_demande.connect(lambda r=rang: self.demander_retrait(r))
            vignettes.append(vignette)
        self.bande.remplacer(vignettes)

    def _annoter(self, photo: PhotoAnnotee) -> PhotoAnnotee | None:
        pieces = {piece.chemin: piece for piece in self.pieces.pieces}
        dialogue = DialogueAnnotation(
            self.pieces.emplacement(photo.chemin),
            photo,
            self.pieces.etat(pieces[photo.chemin]),
            modifiable=True,
            parent=self,
        )
        accepte = dialogue.exec() == QDialog.DialogCode.Accepted
        resultat = dialogue.photo() if accepte else None
        dialogue.deleteLater()
        return resultat

    def demander_annotation(self, rang: int) -> None:
        """Ouvre une photo pour l'annoter. Le fichier de l'image n'est jamais écrit."""
        annotee = self._annoter(self.photos[rang])
        if annotee is not None and annotee != self.photos[rang]:
            self.photos[rang] = annotee
            self._afficher_photos()
            self.modifie.emit()

    def _confirmer_retrait(self, photo: PhotoAnnotee) -> bool:
        reponse = QMessageBox.question(
            self,
            "Retirer la photo",
            f"Retirer « {photo.chemin.removeprefix('pieces_jointes/')} » de la page ? "
            "Le fichier reste dans le dossier de la page et l'historique la garde.",
        )
        return reponse == QMessageBox.StandardButton.Yes

    def demander_retrait(self, rang: int) -> None:
        """Retire une photo de cet onglet et de la page, si aucun autre onglet ne l'affiche."""
        photo = self.photos[rang]
        if not self._confirmer_retrait(photo):
            return
        del self.photos[rang]
        try:
            self.pieces.retirer(photo.chemin)
        except ValueError:
            pass  # Un autre onglet l'affiche encore : elle reste donc jointe à la page.
        self._afficher_photos()
        self.modifie.emit()


@dataclass(frozen=True)
class DeclarationOnglet:
    """Tout ce que l'interface sait d'un type d'onglet."""

    nom_type: str
    libelle: str
    modele: type[Onglet]
    editeur: type[EditeurOnglet]


_DECLARATIONS: dict[str, DeclarationOnglet] = {}


def declarer_onglet(
    nom_type: str,
    libelle: str,
    modele: type[Onglet],
    editeur: type[EditeurOnglet],
) -> None:
    """Déclare un type d'onglet, à la fois pour le cœur et pour l'interface."""
    enregistrer_type_onglet(nom_type, modele)
    _DECLARATIONS[nom_type] = DeclarationOnglet(nom_type, libelle, modele, editeur)


def retirer_onglet_declare(nom_type: str) -> None:
    """Retire un type d'onglet déclaré."""
    del _DECLARATIONS[nom_type]
    retirer_type_onglet(nom_type)


def types_disponibles() -> list[DeclarationOnglet]:
    """Liste les types d'onglets qu'on peut ajouter à une page."""
    return list(_DECLARATIONS.values())


def creer_onglet(nom_type: str, identifiant: str) -> Onglet:
    """Crée un onglet vide d'un type déclaré."""
    declaration = _DECLARATIONS[nom_type]
    return declaration.modele(id=identifiant, type=nom_type, titre=declaration.libelle)


def creer_editeur(onglet: Onglet, page: Page | None = None) -> EditeurOnglet:
    """Crée l'éditeur d'un onglet : en lecture seule si son type n'en a pas."""
    declaration = _DECLARATIONS.get(onglet.type)
    if declaration is None:
        return EditeurLectureSeule(onglet, page)
    return declaration.editeur(onglet, page)


declarer_onglet("notes", "Notes", OngletNotes, EditeurNotes)
