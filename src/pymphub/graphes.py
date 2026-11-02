"""Graphes d'une page : ce qu'il faut tracer, sans aucune bibliothèque d'affichage.

Les valeurs et les barres d'erreur sont calculées ici, pour pouvoir les vérifier sans écran.
Les barres valent U = k · u, où u combine les composantes de la colonne et celles propres à
chaque cellule. Toutes les séries partagent un seul axe : elles sont converties dans
l'unité de la première, et des unités incompatibles sont refusées.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from pymphub.page import (
    ColonneIncertitude,
    ColonneNumerique,
    OngletGraphes,
    Page,
    ReglagesGraphe,
    Tableau,
)
from pymphub.unites import convertir, convertir_ecart, sont_compatibles

SERIES_MAX = 8


class GrapheImpossible(ValueError):
    """Ces réglages ne permettent pas de tracer un graphe : le message l'explique."""


@dataclass(frozen=True)
class Axe:
    """Une colonne prête à tracer. Une valeur ou un U indéterminés valent `nan`."""

    nom: str
    unite: str
    valeurs: tuple[float, ...]
    elargies: tuple[float, ...]

    @property
    def libelle(self) -> str:
        """Le nom avec son unité, pour un axe ou une légende."""
        return self.nom if self.unite in ("", "1") else f"{self.nom} ({self.unite})"


@dataclass(frozen=True)
class DonneesGraphe:
    """Tout ce qu'il faut pour dessiner, au facteur k choisi."""

    reglages: ReglagesGraphe
    k: float
    x: Axe
    y: tuple[Axe, ...]
    z: Axe | None
    avertissements: tuple[str, ...]


def tableaux_tracables(page: Page) -> dict[str, list[str]]:
    """Les tableaux de la page et leurs colonnes numériques, celles qu'on peut tracer."""
    return {
        grandeur.nom: [
            c.nom for c in grandeur.colonnes if isinstance(c, ColonneNumerique)
        ]
        for grandeur in page.mesures
        if isinstance(grandeur, Tableau)
    }


def reglages_par_defaut(page: Page, onglet: OngletGraphes) -> ReglagesGraphe | None:
    """Propose des réglages : ceux de l'onglet, ses références, ou les premières colonnes.

    Renvoie None si aucun tableau de la page n'a deux colonnes numériques.
    """
    if onglet.reglages is not None:
        return onglet.reglages
    tables = tableaux_tracables(page)
    references = [r.split(".", 1) for r in onglet.grandeurs if "." in r]
    if len(references) >= 2 and references[0][0] in tables:
        table = references[0][0]
        noms = [nom for t, nom in references if t == table and nom in tables[table]]
        if len(noms) >= 2:
            return ReglagesGraphe(tableau=table, x=noms[0], y=tuple(noms[1:]))
    for table, noms in tables.items():
        if len(noms) >= 2:
            return ReglagesGraphe(tableau=table, x=noms[0], y=(noms[1],))
    return None


def incertitudes_types(
    tableau: Tableau, donnees: pd.DataFrame, nom: str
) -> list[float]:
    """Incertitude-type de chaque cellule d'une colonne ; `nan` si elle est indéterminée.

    Une composante relative n'a pas de sens sur une valeur nulle : elle ne doit pas empêcher
    de tracer les autres points.
    """
    colonne = next(c for c in tableau.colonnes if c.nom == nom)
    cellules = [
        c
        for c in tableau.colonnes
        if isinstance(c, ColonneIncertitude) and c.composante_de == nom
    ]
    resultat: list[float] = []
    for ligne, valeur in enumerate(donnees[nom]):
        composantes = list(colonne.composantes) + [
            c.composante(float(donnees[c.nom].iloc[ligne])) for c in cellules
        ]
        try:
            variance = sum(
                c.incertitude_type(float(valeur), colonne.unite) ** 2
                for c in composantes
            )
        except ValueError:
            variance = math.nan
        resultat.append(math.sqrt(variance))
    return resultat


def _axe(
    tableau: Tableau, donnees: pd.DataFrame, nom: str, k: float, unite: str | None
) -> Axe:
    colonne = next(c for c in tableau.colonnes if c.nom == nom)
    origine = colonne.unite
    cible = unite or origine
    if not sont_compatibles(origine, cible):
        raise GrapheImpossible(
            f"« {nom} » est en {origine}, incompatible avec {cible} : un seul axe par "
            "graphe. Tracer cette colonne dans un autre onglet."
        )
    return Axe(
        nom=nom,
        unite=cible,
        valeurs=tuple(convertir(float(v), origine, cible) for v in donnees[nom]),
        elargies=tuple(
            convertir_ecart(k * u, origine, cible) if not math.isnan(u) else math.nan
            for u in incertitudes_types(tableau, donnees, nom)
        ),
    )


def _masquer(axe: Axe, masque: list[bool]) -> Axe:
    return Axe(
        axe.nom,
        axe.unite,
        tuple(math.nan if m else v for v, m in zip(axe.valeurs, masque)),
        axe.elargies,
    )


def _appliquer_log(
    x: Axe, y: list[Axe], z: Axe | None, reglages: ReglagesGraphe
) -> tuple[Axe, list[Axe], Axe | None, list[str]]:
    """Retire les points négatifs ou nuls d'un axe log, et le dit : ils ne s'y placent pas."""
    avertissements: list[str] = []
    lignes = range(len(x.valeurs))

    def masque_de(axes: list[Axe]) -> list[bool]:
        return [any(a.valeurs[i] <= 0 for a in axes) for i in lignes]

    if reglages.echelle_x == "log":
        masque = masque_de([x])
        y = [_masquer(a, masque) for a in y]
        z = _masquer(z, masque) if z is not None else None
        x = _masquer(x, masque)
        if any(masque):
            avertissements.append(
                f"{sum(masque)} point(s) à x ≤ 0 non affiché(s) en échelle log."
            )
    if reglages.echelle_y == "log":
        nombre = sum(a.valeurs[i] <= 0 for a in y for i in lignes)
        y = [_masquer(a, [v <= 0 for v in a.valeurs]) for a in y]
        if nombre:
            avertissements.append(
                f"{nombre} point(s) à y ≤ 0 non affiché(s) en échelle log."
            )
    if z is not None and reglages.echelle_z == "log":
        masque = [v <= 0 for v in z.valeurs]
        z = _masquer(z, masque)
        if any(masque):
            avertissements.append(
                f"{sum(masque)} point(s) à z ≤ 0 non affiché(s) en échelle log."
            )
    return x, y, z, avertissements


def preparer_graphe(
    page: Page,
    tableaux: Mapping[str, pd.DataFrame],
    reglages: ReglagesGraphe,
    k: float = 2.0,
) -> DonneesGraphe:
    """Calcule les valeurs et les barres à tracer. Lève `GrapheImpossible` en l'expliquant.

    `tableaux` donne les données du tableau désigné par les réglages.
    """
    if k <= 0:
        raise GrapheImpossible("Le facteur d'élargissement k doit être positif.")
    tableau = next(
        (
            g
            for g in page.mesures
            if isinstance(g, Tableau) and g.nom == reglages.tableau
        ),
        None,
    )
    if tableau is None:
        raise GrapheImpossible(
            f"Le tableau « {reglages.tableau} » n'existe plus : en choisir un autre."
        )
    numeriques = {c.nom for c in tableau.colonnes if isinstance(c, ColonneNumerique)}
    y_noms = reglages.y if reglages.mode == "2d" else reglages.y[:1]
    demandees = [reglages.x, *y_noms]
    if reglages.mode == "3d":
        if reglages.z is None:
            raise GrapheImpossible("Un graphe 3D demande une colonne pour l'axe z.")
        demandees.append(reglages.z)
    for nom in demandees:
        if nom not in numeriques:
            raise GrapheImpossible(
                f"La colonne numérique « {nom} » n'existe plus dans « {tableau.nom} » : "
                "en choisir une autre."
            )
    if len(y_noms) > SERIES_MAX:
        raise GrapheImpossible(
            f"{len(y_noms)} séries : {SERIES_MAX} au plus restent lisibles sur un graphe."
        )
    donnees = tableaux[tableau.nom]
    if donnees.empty:
        raise GrapheImpossible(
            f"Le tableau « {tableau.nom} » n'a pas encore de données."
        )

    x = _axe(tableau, donnees, reglages.x, k, None)
    premiere = next(c.unite for c in tableau.colonnes if c.nom == y_noms[0])
    y = [_axe(tableau, donnees, nom, k, premiere) for nom in y_noms]
    z = _axe(tableau, donnees, reglages.z, k, None) if reglages.mode == "3d" else None
    x, y, z, avertissements = _appliquer_log(x, y, z, reglages)

    indeterminees = sum(
        math.isnan(u) for a in (x, *y, *(() if z is None else (z,))) for u in a.elargies
    )
    if indeterminees:
        avertissements.append(
            f"{indeterminees} barre(s) d'erreur indéterminée(s) : incertitude relative "
            "sur une valeur nulle."
        )
    return DonneesGraphe(reglages, k, x, tuple(y), z, tuple(avertissements))
