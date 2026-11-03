"""Dialogue « Créer un gabarit » : choisir, dans une page, les variables à reprendre."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pymphub.gabarit import (
    Gabarit,
    colonnes_choisissables,
    creer_gabarit,
    nom_de_gabarit,
    version_suivante,
)
from pymphub.interface.messages import message_erreur
from pymphub.page import Page


class DialogueGabarit(QDialog):
    """Crée un gabarit depuis une page : ses variables, jamais leurs valeurs."""

    def __init__(
        self, page: Page, references: list[str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Créer un gabarit")
        self.setMinimumSize(520, 460)
        self.page = page
        self.references = references
        self.champ_nom = QLineEdit(nom_de_gabarit(page.titre))
        self.champ_description = QLineEdit()
        self.arbre = QTreeWidget()
        self.arbre.setHeaderHidden(True)
        for nom, colonnes in colonnes_choisissables(page).items():
            element = QTreeWidgetItem(self.arbre, [nom])
            element.setData(0, Qt.ItemDataRole.UserRole, nom)
            element.setCheckState(0, Qt.CheckState.Checked)
            if colonnes:
                element.setFlags(element.flags() | Qt.ItemFlag.ItemIsAutoTristate)
            for colonne in colonnes:
                enfant = QTreeWidgetItem(element, [colonne])
                enfant.setData(0, Qt.ItemDataRole.UserRole, f"{nom}.{colonne}")
                enfant.setCheckState(0, Qt.CheckState.Checked)
        self.arbre.expandAll()
        self.message = QLabel("")
        self.message.setObjectName("discret")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)

        formulaire = QFormLayout()
        formulaire.addRow("Nom", self.champ_nom)
        formulaire.addRow("Description", self.champ_description)
        explication = QLabel(
            "Le gabarit garde le nom, la nature et l'unité des variables cochées, jamais "
            "leurs valeurs ni leurs incertitudes."
        )
        explication.setObjectName("discret")
        explication.setWordWrap(True)
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
        disposition.addWidget(explication)
        disposition.addWidget(self.arbre, stretch=1)
        disposition.addWidget(self.message)
        disposition.addWidget(boutons)

        self.champ_nom.textChanged.connect(self._actualiser)
        self.arbre.itemChanged.connect(self._actualiser)
        self._actualiser()

    def variables(self) -> list[str]:
        """Les variables cochées : un tableau entier, ou seulement certaines colonnes."""
        choisies: list[str] = []
        for rang in range(self.arbre.topLevelItemCount()):
            element = self.arbre.topLevelItem(rang)
            etat = element.checkState(0)
            if etat == Qt.CheckState.Checked:
                choisies.append(element.data(0, Qt.ItemDataRole.UserRole))
            elif etat == Qt.CheckState.PartiallyChecked:
                choisies.extend(
                    element.child(i).data(0, Qt.ItemDataRole.UserRole)
                    for i in range(element.childCount())
                    if element.child(i).checkState(0) == Qt.CheckState.Checked
                )
        return choisies

    def gabarit(self) -> Gabarit:
        """Construit le gabarit. Lève `ValueError` si le choix est invalide."""
        nom = self.champ_nom.text().strip()
        return creer_gabarit(
            self.page,
            nom,
            self.variables(),
            version_suivante(self.references, nom),
            self.champ_description.text().strip(),
        )

    def _actualiser(self) -> None:
        try:
            gabarit = self.gabarit()
        except ValueError as erreur:
            self.bouton_creer.setEnabled(False)
            self.message.setText(
                "Cochez au moins une variable."
                if not self.variables()
                else "Nom : minuscules, chiffres, tirets et points seulement."
                if self.variables() and not self.champ_nom.text().strip().isascii()
                else message_erreur(erreur)
            )
            return
        self.bouton_creer.setEnabled(True)
        self.message.setText(
            f"Sera enregistré sous {gabarit.reference}, "
            f"avec {len(gabarit.variables)} variable(s)."
        )
