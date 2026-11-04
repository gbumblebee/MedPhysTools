"""Dialogue « Créer un gabarit » : choisir, dans une page, les variables et onglets à reprendre."""

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
    onglets_choisissables,
    version_suivante,
)
from pymphub.interface.messages import message_erreur
from pymphub.page import Page

_TYPES_ONGLET = {"notes": "notes", "graphes": "graphe", "ajustements": "ajustement"}


class DialogueGabarit(QDialog):
    """Crée un gabarit depuis une page : ses variables et ses onglets, jamais leurs valeurs."""

    def __init__(
        self, page: Page, references: list[str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Créer un gabarit")
        self.setMinimumSize(560, 640)
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
        self.liste_onglets = QTreeWidget()
        self.liste_onglets.setHeaderHidden(True)
        self.liste_onglets.setRootIsDecorated(False)
        for onglet in onglets_choisissables(page):
            libelle = f"{onglet.titre} ({_TYPES_ONGLET[onglet.type]})"
            element = QTreeWidgetItem(self.liste_onglets, [libelle])
            element.setData(0, Qt.ItemDataRole.UserRole, onglet.id)
            if onglet.probleme is None:
                element.setCheckState(0, Qt.CheckState.Checked)
            else:
                # Un gabarit ne garde pas les calculs : cet onglet ne pourrait pas se tracer.
                element.setText(0, f"{libelle} : non repris, {onglet.probleme}")
                element.setCheckState(0, Qt.CheckState.Unchecked)
                element.setFlags(element.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        self.message = QLabel("")
        self.message.setObjectName("discret")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)

        formulaire = QFormLayout()
        formulaire.addRow("Nom", self.champ_nom)
        formulaire.addRow("Description", self.champ_description)
        explication = QLabel(
            "Le gabarit garde le nom, la nature et l'unité des variables cochées, jamais "
            "leurs valeurs ni leurs incertitudes. Des onglets cochés, il garde le texte des "
            "notes, sans leurs photos, et les réglages des graphes et des ajustements, sans "
            "leurs résultats."
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
        disposition.addWidget(QLabel("Variables"))
        disposition.addWidget(self.arbre, stretch=2)
        disposition.addWidget(QLabel("Onglets déjà prêts dans les nouvelles pages"))
        disposition.addWidget(self.liste_onglets, stretch=1)
        disposition.addWidget(self.message)
        disposition.addWidget(boutons)

        self.champ_nom.textChanged.connect(self._actualiser)
        self.arbre.itemChanged.connect(self._actualiser)
        self.liste_onglets.itemChanged.connect(self._actualiser)
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

    def onglets(self) -> list[str]:
        """Les identifiants des onglets cochés."""
        return [
            element.data(0, Qt.ItemDataRole.UserRole)
            for rang in range(self.liste_onglets.topLevelItemCount())
            if (element := self.liste_onglets.topLevelItem(rang)).checkState(0)
            == Qt.CheckState.Checked
        ]

    def gabarit(self) -> Gabarit:
        """Construit le gabarit. Lève `ValueError` si le choix est invalide."""
        nom = self.champ_nom.text().strip()
        return creer_gabarit(
            self.page,
            nom,
            self.variables(),
            version_suivante(self.references, nom),
            self.champ_description.text().strip(),
            self.onglets(),
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
            f"avec {len(gabarit.variables)} variable(s) et "
            f"{len(gabarit.onglets)} onglet(s) déjà prêt(s)."
        )
