"""Types d'onglets de l'interface : c'est ici qu'on en ajoute un nouveau.

Pour ajouter un type d'onglet, il suffit de :
1. décrire ses données dans une classe dérivée de `Onglet`, avec des valeurs par défaut ;
2. écrire son éditeur, une classe dérivée de `EditeurOnglet` ;
3. appeler `declarer_onglet`.

Un type sans éditeur, ou inconnu de cette version du logiciel, s'affiche en lecture seule
et ses données restent intactes.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from PySide6.QtCore import Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pymphub.archive import Archive
from pymphub.edition import Grandeur
from pymphub.equations import RESOLUTION_ECRAN
from pymphub.graphes import empreinte_tableau, tableaux_tracables
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
    Ajustement,
    CaptureGraphe,
    GrandeurDerivee,
    Identifiant,
    Indicateur,
    Onglet,
    OngletNotes,
    Page,
    PhotoAnnotee,
    Scalaire,
    enregistrer_type_onglet,
    retirer_type_onglet,
)
from pymphub.tags import TITRE, decouper_note, note_en_html


class EditeurOnglet(QWidget):
    """Base d'un éditeur : il affiche un onglet et donne sa nouvelle valeur."""

    modifie = Signal()
    mesure_proposee = Signal(object)
    # Les valeurs lues par un import, et s'il faut montrer l'onglet qui les reçoit.
    valeurs_importees = Signal(object, bool)
    # La photo d'une capture, à insérer dans un onglet de notes.
    capture_proposee = Signal(object)

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

    def noms_utilises(self) -> list[str]:
        """Noms déjà pris par cet onglet, y compris ceux d'une saisie incomplète."""
        return [grandeur.nom for grandeur in self.grandeurs()]

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

    def noter_saisies_incompletes(self, incompletes: Sequence[str]) -> None:
        """Prévient l'éditeur de ce qui manque dans la page pour l'enregistrer."""

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


def remarque_de_capture(
    page: Page,
    capture: CaptureGraphe,
    lire_tableau: Callable[[str], pd.DataFrame],
) -> str:
    """Dit si le tableau a changé depuis une capture : l'image, elle, ne bouge pas."""
    try:
        empreinte = empreinte_tableau(
            page, capture.tableau, lire_tableau(capture.tableau)
        )
    except (ValueError, KeyError, OSError):
        return f"Capture : le tableau « {capture.tableau} » n'est plus lisible."
    if empreinte != capture.empreinte_donnees:
        return f"Capture ancienne : « {capture.tableau} » a changé depuis."
    return ""


class TitresDeNote(QSyntaxHighlighter):
    """Montre les titres en gras pendant la saisie. Le texte lui-même ne change pas."""

    def highlightBlock(self, texte: str) -> None:  # noqa: N802
        if TITRE.match(texte):
            gras = QTextCharFormat()
            gras.setFontWeight(QFont.Weight.Bold)
            self.setFormat(0, len(texte), gras)


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
        self.archive: Archive | None = None
        self.photos: list[PhotoAnnotee] = list(getattr(onglet, "photos", ()))
        self.texte = ZoneNotes(getattr(onglet, "texte", ""))
        self.texte.setPlaceholderText(
            "Montage, conditions, remarques… Déposez ici une photo ou un fichier. "
            "Une valeur de la page s'insère avec {{nom}}, un titre avec « # Titre », "
            "une équation avec $x^2$."
        )
        self.titres = TitresDeNote(self.texte.document())
        afficher_en_grand(self.texte, lambda: self.onglet.titre or "Notes")
        self.texte.textChanged.connect(self.modifie)
        self.texte.fichiers_deposes.connect(self.ajouter_fichiers)

        self.apercu = QTextEdit()
        self.apercu.setReadOnly(True)
        self.apercu.setVisible(False)
        self.bouton_apercu = QPushButton("Aperçu")
        self.bouton_apercu.setCheckable(True)
        self.bouton_apercu.setToolTip("Montre les valeurs à la place des tags {{nom}}")
        self.bouton_apercu.toggled.connect(self._basculer_apercu)
        self.insertion = QComboBox()
        self.insertion.setToolTip("Insère une valeur de la page, qui se mettra à jour")
        self.insertion.activated.connect(self._inserer_choix)

        self.choix_titre = QComboBox()
        self.choix_titre.setToolTip(
            "Fait de la ligne du curseur un titre ou un sous-titre"
        )
        self.choix_titre.addItem("Titre…")
        for libelle, niveau in (
            ("Titre", 1),
            ("Sous-titre", 2),
            ("Petit titre", 3),
            ("Texte normal", 0),
        ):
            self.choix_titre.addItem(libelle, niveau)
        self.choix_titre.activated.connect(self._appliquer_choix_titre)

        self.bouton_equation = QPushButton("Équation")
        self.bouton_equation.setToolTip(
            "Insère une équation entre $ : syntaxe mathtext, proche de LaTeX, "
            r"par exemple $A_0 e^{-\lambda t}$. À voir dans l'aperçu."
        )
        self.bouton_equation.clicked.connect(self.inserer_equation)

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
        ligne.addWidget(self.choix_titre)
        ligne.addWidget(self.bouton_equation)
        ligne.addWidget(self.insertion)
        ligne.addWidget(self.bouton_apercu)
        ligne.addWidget(self.bouton_photo)

        disposition = QVBoxLayout(self)
        disposition.addWidget(self.texte, stretch=1)
        disposition.addWidget(self.apercu, stretch=1)
        disposition.addWidget(self.bande)
        disposition.addLayout(ligne)
        self.setAcceptDrops(True)

    def charger(self, archive: Archive) -> None:
        self.archive = archive
        self._afficher_photos()
        self._proposer_noms()

    def enregistre(self, page: Page) -> None:
        super().enregistre(page)
        self._proposer_noms()
        self._afficher_photos()
        self._actualiser_apercu()

    # --- Tags -----------------------------------------------------------------------------

    def _lire_tableau(self, nom: str) -> pd.DataFrame:
        return self.archive.lire_tableau(self.page.id, nom)

    def _proposer_noms(self) -> None:
        self.insertion.clear()
        self.insertion.addItem("Insérer une valeur…")
        if self.page is None:
            return
        for grandeur in self.page.mesures:
            if isinstance(grandeur, (Scalaire, Identifiant, GrandeurDerivee)):
                self.insertion.addItem(grandeur.nom)
            elif isinstance(grandeur, Indicateur):
                self.insertion.addItem(grandeur.nom)
                if grandeur.position is not None:
                    self.insertion.addItem(f"{grandeur.nom}.x")
            elif isinstance(grandeur, Ajustement):
                for parametre in grandeur.parametres:
                    self.insertion.addItem(f"{grandeur.nom}.{parametre.nom}")
        for tableau, colonnes in tableaux_tracables(self.page).items():
            for colonne in colonnes:
                self.insertion.addItem(f"{tableau}.{colonne}[1]")

    def _inserer_choix(self, rang: int) -> None:
        if rang > 0:
            self.inserer_tag(self.insertion.itemText(rang))
        self.insertion.setCurrentIndex(0)

    def inserer_tag(self, nom: str) -> None:
        """Insère un tag à l'emplacement du curseur."""
        self.texte.insertPlainText(f"{{{{{nom}}}}}")
        self.texte.setFocus()

    def inserer_equation(self) -> None:
        """Insère une équation d'exemple au curseur, avec sa formule déjà sélectionnée."""
        curseur = self.texte.textCursor()
        curseur.insertText("$x$")
        curseur.movePosition(QTextCursor.MoveOperation.Left)
        curseur.movePosition(
            QTextCursor.MoveOperation.Left, QTextCursor.MoveMode.KeepAnchor
        )
        self.texte.setTextCursor(curseur)
        self.texte.setFocus()

    def _appliquer_choix_titre(self, rang: int) -> None:
        if rang > 0:
            self.appliquer_titre(self.choix_titre.itemData(rang))
        self.choix_titre.setCurrentIndex(0)

    def appliquer_titre(self, niveau: int) -> None:
        """Donne un niveau de titre à la ligne du curseur ; 0 la remet en texte normal."""
        curseur = self.texte.textCursor()
        curseur.movePosition(QTextCursor.MoveOperation.StartOfBlock)
        curseur.movePosition(
            QTextCursor.MoveOperation.EndOfBlock, QTextCursor.MoveMode.KeepAnchor
        )
        ligne = curseur.selectedText()
        titre = TITRE.match(ligne)
        contenu = titre.group(2) if titre else ligne
        curseur.insertText(f"{'#' * niveau} {contenu}" if niveau else contenu)
        self.texte.setTextCursor(curseur)
        self.texte.setFocus()

    def rendu_html(self) -> str:
        """La note telle qu'elle s'affiche, avec les valeurs à la place des tags."""
        if self.page is None or self.archive is None:
            return ""
        morceaux = decouper_note(
            self.texte.toPlainText(), self.page, self._lire_tableau
        )
        return note_en_html(
            morceaux, resolution=RESOLUTION_ECRAN * self.devicePixelRatioF()
        )

    def _actualiser_apercu(self) -> None:
        if self.bouton_apercu.isChecked():
            self.apercu.setHtml(self.rendu_html())

    def _basculer_apercu(self, actif: bool) -> None:
        self.texte.setVisible(not actif)
        self.apercu.setVisible(actif)
        self._actualiser_apercu()

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

    def ajouter_photo(self, photo: PhotoAnnotee) -> None:
        """Ajoute une photo déjà jointe à la page, comme la capture d'un graphe."""
        self.photos.append(photo)
        self._afficher_photos()
        self.modifie.emit()

    def _remarque(self, photo: PhotoAnnotee) -> str:
        """Signale qu'une capture ne correspond plus au tableau dont elle vient."""
        capture = photo.capture
        if capture is None or self.archive is None or self.page is None:
            return ""
        return remarque_de_capture(self.page, capture, self._lire_tableau)

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
                remarque=self._remarque(photo),
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
