"""Types d'onglets de l'interface : c'est ici qu'on en ajoute un nouveau.

Pour ajouter un type d'onglet, il suffit de :
1. décrire ses données dans une classe dérivée de `Onglet`, avec des valeurs par défaut ;
2. écrire son éditeur, une classe dérivée de `EditeurOnglet` ;
3. appeler `declarer_onglet`.

Un type sans éditeur, ou inconnu de cette version du logiciel, s'affiche en lecture seule
et ses données restent intactes.
"""

import json
from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from pymphub.page import (
    Onglet,
    OngletNotes,
    enregistrer_type_onglet,
    retirer_type_onglet,
)


class EditeurOnglet(QWidget):
    """Base d'un éditeur : il affiche un onglet et donne sa nouvelle valeur."""

    modifie = Signal()

    def __init__(self, onglet: Onglet, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.onglet = onglet

    def valeur(self) -> Onglet:
        """Renvoie l'onglet avec les modifications de l'utilisateur."""
        return self.onglet

    def renommer(self, titre: str) -> None:
        """Change le titre de l'onglet, sans toucher à ses données."""
        self.onglet = self.onglet.model_copy(update={"titre": titre})
        self.modifie.emit()


class EditeurLectureSeule(EditeurOnglet):
    """Affiche un onglet sans éditeur. Ses données sont rendues telles quelles."""

    def __init__(self, onglet: Onglet, parent: QWidget | None = None) -> None:
        super().__init__(onglet, parent)
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


class EditeurNotes(EditeurOnglet):
    """Édite le texte d'un onglet de notes. Ses photos sont conservées."""

    def __init__(self, onglet: Onglet, parent: QWidget | None = None) -> None:
        super().__init__(onglet, parent)
        self.texte = QPlainTextEdit(getattr(onglet, "texte", ""))
        self.texte.setPlaceholderText("Montage, conditions, remarques…")
        self.texte.textChanged.connect(self.modifie)
        disposition = QVBoxLayout(self)
        disposition.addWidget(self.texte)

    def valeur(self) -> Onglet:
        return self.onglet.model_copy(update={"texte": self.texte.toPlainText()})


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


def creer_editeur(onglet: Onglet) -> EditeurOnglet:
    """Crée l'éditeur d'un onglet : en lecture seule si son type n'en a pas."""
    declaration = _DECLARATIONS.get(onglet.type)
    if declaration is None:
        return EditeurLectureSeule(onglet)
    return declaration.editeur(onglet)


declarer_onglet("notes", "Notes", OngletNotes, EditeurNotes)
