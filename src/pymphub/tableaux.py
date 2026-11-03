"""Tableaux en CSV : écriture, relecture typée et comparaison cellule par cellule.

Le type de chaque colonne vient de sa description dans `page.json`, jamais du contenu
du fichier. Une colonne d'identifiants reste donc du texte : `007` ne devient pas `7`.
"""

import csv
import io
import math
from collections.abc import Mapping, Sequence
from numbers import Real

import pandas as pd

from pymphub.calculs import ErreurCalcul, incertitudes_de_colonne
from pymphub.historique import Modification
from pymphub.page import (
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Tableau,
)

DonneesTableau = Mapping[str, Sequence[str | float]]


class TableauInvalide(ValueError):
    """Le contenu d'un tableau ne correspond pas à sa description."""


def _verifier_cellule(
    colonne: ColonneNumerique | ColonneIdentifiant | ColonneIncertitude,
    valeur: str | float,
    ligne: int,
) -> None:
    lieu = f"colonne « {colonne.nom} », ligne {ligne}"
    if isinstance(colonne, ColonneIdentifiant):
        if not isinstance(valeur, str) or not valeur:
            raise TableauInvalide(
                f"{lieu} : un texte non vide est attendu, pas {valeur!r}."
            )
        return
    if isinstance(valeur, bool) or not isinstance(valeur, Real):
        raise TableauInvalide(f"{lieu} : un nombre est attendu, pas {valeur!r}.")
    if not math.isfinite(valeur):
        raise TableauInvalide(f"{lieu} : valeur non finie ({valeur}).")
    if isinstance(colonne, ColonneIncertitude) and valeur < 0:
        raise TableauInvalide(f"{lieu} : une incertitude ne peut pas être négative.")


def _colonnes_stockees(
    tableau: Tableau,
) -> list[ColonneNumerique | ColonneIdentifiant | ColonneIncertitude]:
    """Les colonnes du fichier CSV : une incertitude à formule n'y figure pas."""
    return [c for c in tableau.colonnes if getattr(c, "formule", None) is None]


def incertitudes_calculees(
    tableau: Tableau, donnees: Mapping[str, Sequence[str | float]], lignes: int
) -> dict[str, list[float]]:
    """Calcule les colonnes d'incertitude à formule. Lève `TableauInvalide` si l'une échoue."""
    resultat: dict[str, list[float]] = {}
    numeriques = {
        c.nom: donnees[c.nom]
        for c in tableau.colonnes
        if isinstance(c, ColonneNumerique)
    }
    for colonne in tableau.colonnes:
        if isinstance(colonne, ColonneIncertitude) and colonne.formule is not None:
            try:
                resultat[colonne.nom] = incertitudes_de_colonne(
                    tableau, colonne, numeriques, lignes
                )
            except ErreurCalcul as erreur:
                raise TableauInvalide(str(erreur)) from erreur
    return resultat


def _verifier_noms(tableau: Tableau, noms: Sequence[str]) -> None:
    attendus = [colonne.nom for colonne in _colonnes_stockees(tableau)]
    if sorted(noms) != sorted(attendus):
        raise TableauInvalide(
            f"Tableau « {tableau.nom} » : colonnes {attendus} attendues, "
            f"{list(noms)} reçues."
        )


def _lignes_csv(texte: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(texte, newline="")))


def ecrire_csv(tableau: Tableau, donnees: DonneesTableau) -> str:
    """Vérifie les données d'un tableau et renvoie le texte de son fichier CSV.

    `donnees` associe à chaque colonne la liste de ses valeurs. Toute cellule est
    obligatoire. Le format est stable : virgules, point décimal et fins de ligne LF.
    """
    stockees = _colonnes_stockees(tableau)
    calculees = {c.nom for c in tableau.colonnes} - {c.nom for c in stockees}
    donnees = {nom: v for nom, v in donnees.items() if nom not in calculees}
    _verifier_noms(tableau, list(donnees))
    longueurs = {len(valeurs) for valeurs in donnees.values()}
    if len(longueurs) != 1:
        raise TableauInvalide(
            f"Tableau « {tableau.nom} » : les colonnes n'ont pas la même longueur."
        )

    nombre_lignes = longueurs.pop()
    for colonne in stockees:
        for i, valeur in enumerate(donnees[colonne.nom], start=1):
            _verifier_cellule(colonne, valeur, i)
    incertitudes_calculees(tableau, donnees, nombre_lignes)

    sortie = io.StringIO(newline="")
    ecrivain = csv.writer(sortie, lineterminator="\n")
    ecrivain.writerow(colonne.nom for colonne in stockees)
    for i in range(nombre_lignes):
        ecrivain.writerow(
            donnees[colonne.nom][i]
            if isinstance(colonne, ColonneIdentifiant)
            else repr(float(donnees[colonne.nom][i]))
            for colonne in stockees
        )
    return sortie.getvalue()


def lire_csv(tableau: Tableau, texte: str) -> pd.DataFrame:
    """Relit un fichier CSV d'après la description du tableau.

    Le DataFrame renvoyé donne les unités des colonnes dans `attrs["unites"]`. Une colonne
    d'incertitude à formule y figure aussi, recalculée à partir des valeurs saisies.
    """
    lignes = _lignes_csv(texte)
    if not lignes:
        raise TableauInvalide(f"Tableau « {tableau.nom} » : fichier vide.")
    en_tete, *cellules = lignes
    _verifier_noms(tableau, en_tete)

    donnees: dict[str, list[str | float]] = {
        colonne.nom: [] for colonne in _colonnes_stockees(tableau)
    }
    colonnes = {colonne.nom: colonne for colonne in tableau.colonnes}
    for numero, ligne in enumerate(cellules, start=1):
        if len(ligne) != len(en_tete):
            raise TableauInvalide(
                f"Tableau « {tableau.nom} », ligne {numero} : {len(ligne)} cellules "
                f"au lieu de {len(en_tete)}."
            )
        for nom, texte_cellule in zip(en_tete, ligne):
            colonne = colonnes[nom]
            if isinstance(colonne, ColonneIdentifiant):
                valeur: str | float = texte_cellule
            else:
                try:
                    valeur = float(texte_cellule)
                except ValueError as erreur:
                    raise TableauInvalide(
                        f"Tableau « {tableau.nom} », colonne « {nom} », ligne {numero} : "
                        f"« {texte_cellule} » n'est pas un nombre."
                    ) from erreur
            _verifier_cellule(colonne, valeur, numero)
            donnees[nom].append(valeur)

    donnees.update(incertitudes_calculees(tableau, donnees, len(cellules)))
    resultat = pd.DataFrame(
        {
            colonne.nom: pd.Series(
                donnees[colonne.nom],
                dtype="object"
                if isinstance(colonne, ColonneIdentifiant)
                else "float64",
            )
            for colonne in tableau.colonnes
        }
    )
    resultat.attrs["unites"] = {
        colonne.nom: colonne.unite
        for colonne in tableau.colonnes
        if not isinstance(colonne, ColonneIdentifiant)
    }
    return resultat


def differences_csv(nom: str, ancien: str, nouveau: str) -> tuple[Modification, ...]:
    """Compare deux versions d'un fichier CSV, cellule par cellule si leur forme est identique.

    Une cellule modifiée se note `mesures.decroissance.activite[2]`, en comptant les
    lignes de données à partir de 1. Si les colonnes ou le nombre de lignes changent,
    seule la forme du tableau est signalée : les valeurs restent lisibles dans Git.
    """
    if ancien == nouveau:
        return ()
    avant, apres = _lignes_csv(ancien), _lignes_csv(nouveau)
    if not avant or not apres or avant[0] != apres[0] or len(avant) != len(apres):
        return (
            Modification(
                f"mesures.{nom} (forme)",
                {
                    "colonnes": avant[0] if avant else [],
                    "lignes": max(len(avant) - 1, 0),
                },
                {
                    "colonnes": apres[0] if apres else [],
                    "lignes": max(len(apres) - 1, 0),
                },
            ),
        )
    return tuple(
        Modification(f"mesures.{nom}.{colonne}[{i}]", a, b)
        for i, (ligne_a, ligne_b) in enumerate(zip(avant[1:], apres[1:]), start=1)
        for colonne, a, b in zip(avant[0], ligne_a, ligne_b)
        if a != b
    )
