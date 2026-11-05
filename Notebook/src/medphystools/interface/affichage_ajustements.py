"""Tables de résultats d'un ajustement, communes à la fiche et à l'onglet d'ajustement."""

from collections.abc import Sequence

from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

from medphystools.page import Ajustement, Indicateur

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
    "mediane": "Médiane",
    "percentile": "Percentile",
    "ecart_type": "Écart type",
    "coefficient_variation": "Coefficient de variation",
    "moyenne_quadratique": "Moyenne quadratique",
    "somme": "Somme",
    "etendue": "Étendue",
    "nombre_points": "Nombre de points",
    "integrale": "Intégrale",
    "derivee": "Dérivée",
    "largeur_mi_hauteur": "Largeur à mi-hauteur",
    "abscisse_de_valeur": "Abscisse d'une valeur",
}
_LIBELLES_SOURCES = {"points": "Points", "ajustement": "Ajustement"}


def libelle_methode(methode: str) -> str:
    return _LIBELLES_METHODES[methode]


def ecrire_position(indicateur: Indicateur, k: float) -> str:
    """Écrit l'abscisse d'un indicateur avec son incertitude : 3 ± 0,02 h."""
    if indicateur.position is None:
        return "—"
    unite = indicateur.unite_position
    suite = "" if unite in (None, "1") else f" {unite}"
    u = indicateur.incertitude_position
    if not u:
        return f"{indicateur.position:.6g}{suite}"
    return f"{indicateur.position:.6g} ± {k * u:.2g}{suite}"


def ecrire_coefficients(ajustement: Ajustement, k: float) -> str:
    """Résume les coefficients sur une ligne, pour laisser sa place au graphique."""
    morceaux = []
    for parametre in ajustement.parametres:
        unite = "" if parametre.unite == "1" else f" {parametre.unite}"
        morceaux.append(
            f"{parametre.nom} = {parametre.valeur:.6g} ± "
            f"{k * parametre.incertitude_type:.2g}{unite}"
        )
    if ajustement.chi2_reduit is not None:
        morceaux.append(f"χ² réduit = {ajustement.chi2_reduit:.3g}")
    return " · ".join(morceaux)


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
        libelle = libelle_methode(indicateur.methode)
        if indicateur.parametre is not None:
            libelle = f"{libelle} ({indicateur.parametre:g})"
        for colonne, texte in enumerate(
            (
                indicateur.nom,
                libelle,
                _LIBELLES_SOURCES[indicateur.source],
                f"{indicateur.de:.6g} à {indicateur.a:.6g}",
                ecrire_position(indicateur, k),
                f"{indicateur.valeur:.6g} ± {k * indicateur.incertitude_type:.2g}{unite}",
                indicateur.commentaire,
            )
        ):
            table.setItem(rang, colonne, QTableWidgetItem(texte))
