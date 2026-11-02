"""Tables de résultats d'un ajustement, communes à la fiche et à l'onglet d'ajustement."""

from collections.abc import Sequence

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from pymphub.page import Ajustement, Indicateur

COLONNES_COEFFICIENTS = ("Coefficient", "Valeur", "U", "U relative", "Unité")
COLONNES_INDICATEURS = (
    "Nom",
    "Indicateur",
    "Source",
    "Intervalle",
    "En x =",
    "Résultat",
    "Commentaire",
)
_LIBELLES_METHODES = {
    "minimum": "Minimum",
    "maximum": "Maximum",
    "moyenne": "Moyenne",
    "integrale": "Intégrale",
    "derivee": "Dérivée",
}
_LIBELLES_SOURCES = {"points": "Points", "ajustement": "Ajustement"}


def libelle_methode(methode: str) -> str:
    return _LIBELLES_METHODES[methode]


def resume_ajustement(ajustement: Ajustement) -> str:
    """Résume un ajustement en une phrase : modèle, données et qualité."""
    chi2 = (
        ""
        if ajustement.chi2_reduit is None
        else f", χ² réduit = {ajustement.chi2_reduit:.3g}"
    )
    return (
        f"{ajustement.modele} : {ajustement.y} = {ajustement.formule}, sur "
        f"{ajustement.tableau}.{ajustement.x}, {ajustement.points} points{chi2}. "
        f"{ajustement.methode}"
    )


def remplir_coefficients(
    table: QTableWidget, ajustement: Ajustement | None, k: float
) -> None:
    """Écrit les coefficients avec leur incertitude élargie U = k · u."""
    parametres = ajustement.parametres if ajustement is not None else ()
    table.setRowCount(len(parametres))
    for rang, parametre in enumerate(parametres):
        elargie = k * parametre.incertitude_type
        relative = (
            "—"
            if parametre.valeur == 0
            else f"{100 * elargie / abs(parametre.valeur):.2g} %"
        )
        for colonne, texte in enumerate(
            (
                parametre.nom,
                f"{parametre.valeur:.6g}",
                f"{elargie:.2g}",
                relative,
                parametre.unite,
            )
        ):
            table.setItem(rang, colonne, QTableWidgetItem(texte))


def remplir_correlation(table: QTableWidget, ajustement: Ajustement | None) -> None:
    """Écrit la matrice de corrélation des coefficients."""
    parametres = ajustement.parametres if ajustement is not None else ()
    noms = [p.nom for p in parametres]
    table.setColumnCount(len(noms) + 1)
    table.setRowCount(len(noms))
    table.setHorizontalHeaderLabels(["Corrélation", *noms])
    for i, nom in enumerate(noms):
        table.setItem(i, 0, QTableWidgetItem(nom))
        for j in range(len(noms)):
            table.setItem(
                i, j + 1, QTableWidgetItem(f"{ajustement.correlation[i][j]:.3f}")
            )


def remplir_indicateurs(
    table: QTableWidget, indicateurs: Sequence[Indicateur], k: float
) -> None:
    """Écrit les indicateurs avec leur méthode, leur intervalle et U = k · u."""
    table.setRowCount(len(indicateurs))
    for rang, indicateur in enumerate(indicateurs):
        unite = "" if indicateur.unite == "1" else f" {indicateur.unite}"
        position = "—" if indicateur.position is None else f"{indicateur.position:.6g}"
        for colonne, texte in enumerate(
            (
                indicateur.nom,
                libelle_methode(indicateur.methode),
                _LIBELLES_SOURCES[indicateur.source],
                f"{indicateur.de:.6g} à {indicateur.a:.6g}",
                position,
                f"{indicateur.valeur:.6g} ± {k * indicateur.incertitude_type:.2g}{unite}",
                indicateur.commentaire,
            )
        ):
            table.setItem(rang, colonne, QTableWidgetItem(texte))
