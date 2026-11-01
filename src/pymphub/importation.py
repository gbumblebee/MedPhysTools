"""Lecture de tableaux extérieurs : presse-papiers, CSV, Excel et imports personnalisés.

Tout est d'abord lu en texte. Les colonnes ne sont typées qu'ensuite, sur proposition
(`deviner_nature`) que l'utilisateur peut changer : `007` n'est donc jamais transformé.
"""

import csv
import importlib.util
import io
import re
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from pymphub.edition import lire_nombre, suggerer_nom
from pymphub.unites import verifier_unite

_ENTETE_AVEC_UNITE = re.compile(r"^(.*?)\s*[(\[]([^()\[\]]+)[)\]]\s*$")
_ZERO_DE_TETE = re.compile(r"^[+-]?0\d")
_PREFIXE_IMPORT = "importer_"
_ERREURS_DE_LECTURE = (
    OSError,
    ValueError,
    KeyError,
    IndexError,
    csv.Error,
    zipfile.BadZipFile,
    InvalidFileException,
)


class ImportImpossible(Exception):
    """Le fichier ou la fonction d'import ne peut pas fournir de tableau."""


@dataclass(frozen=True)
class Grille:
    """Un tableau lu de l'extérieur, encore entièrement en texte."""

    entetes: list[str]
    lignes: list[list[str]]


def _est_nombre(texte: str) -> bool:
    try:
        lire_nombre(texte)
    except ValueError:
        return False
    return True


def _nettoyer(lignes: Sequence[Sequence[str]]) -> list[list[str]]:
    """Retire les lignes vides de fin et égalise le nombre de colonnes."""
    resultat = [[cellule.strip() for cellule in ligne] for ligne in lignes]
    while resultat and not any(resultat[-1]):
        resultat.pop()
    largeur = max((len(ligne) for ligne in resultat), default=0)
    return [ligne + [""] * (largeur - len(ligne)) for ligne in resultat]


def lire_presse_papiers(texte: str) -> list[list[str]]:
    """Lit un collage depuis Excel : des tabulations entre les cellules."""
    lecteur = csv.reader(io.StringIO(texte, newline=None), delimiter="\t")
    return _nettoyer(list(lecteur))


def _lire_texte(chemin: Path) -> str:
    octets = chemin.read_bytes()
    try:
        return octets.decode("utf-8-sig")
    except UnicodeDecodeError:
        return octets.decode("cp1252")


def _separateur(texte: str) -> str:
    """Choisit le séparateur. La virgule vient en dernier : elle peut être décimale."""
    premiere = next((ligne for ligne in texte.splitlines() if ligne.strip()), "")
    for separateur in ("\t", ";"):
        if separateur in premiere:
            return separateur
    return ","


def _cellule_excel(valeur: object) -> str:
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return str(valeur)
    if isinstance(valeur, float):
        return repr(valeur)
    if isinstance(valeur, (datetime, date, time)):
        return valeur.isoformat()
    return str(valeur)


def _lire_excel(chemin: Path) -> list[list[str]]:
    classeur = openpyxl.load_workbook(chemin, read_only=True, data_only=True)
    try:
        feuille = classeur.worksheets[0]
        return _nettoyer(
            [
                [_cellule_excel(c) for c in ligne]
                for ligne in feuille.iter_rows(values_only=True)
            ]
        )
    finally:
        classeur.close()


def lire_fichier(chemin: Path) -> list[list[str]]:
    """Lit un fichier CSV, texte ou Excel. Lève `ImportImpossible` en cas d'échec."""
    extension = chemin.suffix.lower()
    try:
        if extension == ".xlsx":
            return _lire_excel(chemin)
        if extension in (".csv", ".txt", ".tsv"):
            texte = _lire_texte(chemin)
            lecteur = csv.reader(
                io.StringIO(texte, newline=None), delimiter=_separateur(texte)
            )
            return _nettoyer(list(lecteur))
    except _ERREURS_DE_LECTURE as erreur:
        raise ImportImpossible(
            f"Lecture de « {chemin.name} » impossible : {erreur}"
        ) from erreur
    raise ImportImpossible(
        f"Format « {extension or chemin.name} » non pris en charge : utiliser .csv, .txt, "
        ".tsv ou .xlsx, ou écrire un import personnalisé."
    )


def en_grille(lignes: Sequence[Sequence[str]]) -> Grille:
    """Sépare les en-têtes des données : une première ligne sans aucun nombre en est une."""
    if not lignes:
        raise ImportImpossible("Le tableau est vide.")
    premiere = lignes[0]
    if any(cellule for cellule in premiere) and not any(
        _est_nombre(c) for c in premiere if c
    ):
        return Grille(list(premiere), [list(ligne) for ligne in lignes[1:]])
    return Grille([""] * len(premiere), [list(ligne) for ligne in lignes])


def decouper_entete(entete: str, position: int) -> tuple[str, str]:
    """Tire d'un en-tête un nom utilisable et une unité : `Activité (MBq)` → activité, MBq.

    Une unité mal écrite est corrigée si la suggestion est sûre. Une unité inconnue reste
    dans le nom : mieux vaut une unité à déclarer qu'une unité inventée.
    """
    trouve = _ENTETE_AVEC_UNITE.match(entete)
    unite = ""
    base = entete
    if trouve:
        verification = verifier_unite(trouve.group(2).strip())
        if verification.valide:
            base, unite = trouve.group(1), trouve.group(2).strip()
        elif verification.suggestion is not None:
            base, unite = trouve.group(1), verification.suggestion
    return suggerer_nom(base) or f"colonne_{position + 1}", unite


def noms_uniques(noms: Sequence[str]) -> list[str]:
    """Rend des noms de colonnes uniques : `a`, `a` devient `a`, `a_2`."""
    vus: dict[str, int] = {}
    resultat: list[str] = []
    for nom in noms:
        vus[nom] = vus.get(nom, 0) + 1
        resultat.append(nom if vus[nom] == 1 else f"{nom}_{vus[nom]}")
    return resultat


def deviner_nature(valeurs: Sequence[str]) -> str:
    """Propose « numerique » ou « identifiant » pour une colonne.

    Un zéro de tête (`007`) désigne un identifiant : le convertir en nombre le détruirait.
    """
    remplies = [valeur for valeur in valeurs if valeur]
    if not remplies:
        return "numerique"
    for valeur in remplies:
        if _ZERO_DE_TETE.match(valeur) and not re.match(r"^[+-]?0[.,]", valeur):
            return "identifiant"
        if not _est_nombre(valeur):
            return "identifiant"
    return "numerique"


def _en_texte(valeur: object) -> str:
    if valeur is None:
        return ""
    return repr(valeur) if isinstance(valeur, float) else str(valeur)


def _grille_personnalisee(resultat: object, nom: str) -> Grille:
    """Accepte ce que renvoie un import : `Grille`, dictionnaire de colonnes ou lignes."""
    if isinstance(resultat, Grille):
        return resultat
    if isinstance(resultat, Mapping):
        colonnes = {
            str(k): [_en_texte(v) for v in valeurs] for k, valeurs in resultat.items()
        }
        longueurs = {len(valeurs) for valeurs in colonnes.values()}
        if len(longueurs) != 1:
            raise ImportImpossible(
                f"« {nom} » : les colonnes n'ont pas la même longueur."
            )
        return Grille(
            list(colonnes), [list(ligne) for ligne in zip(*colonnes.values())]
        )
    if isinstance(resultat, Sequence) and not isinstance(resultat, str):
        return en_grille(
            _nettoyer([[_en_texte(c) for c in ligne] for ligne in resultat])
        )
    raise ImportImpossible(
        f"« {nom} » doit renvoyer un dictionnaire de colonnes ou une liste de lignes, "
        f"pas {type(resultat).__name__}."
    )


def charger_imports_personnalises(fichier: Path) -> dict[str, Callable[[Path], Any]]:
    """Charge les fonctions `importer_…` du fichier de l'utilisateur.

    Ce fichier exécute du code : il vient de son profil, jamais d'une archive, pour qu'ouvrir
    une archive reçue de quelqu'un d'autre n'exécute rien. Une erreur dans ce fichier est
    expliquée et ne fait pas planter l'application.
    """
    if not fichier.is_file():
        return {}
    try:
        specification = importlib.util.spec_from_file_location(
            "pymphub_imports_personnalises", fichier
        )
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
    except (Exception, SystemExit) as erreur:
        raise ImportImpossible(
            f"Erreur dans « {fichier.name} » : {type(erreur).__name__} : {erreur}"
        ) from erreur
    return {
        nom.removeprefix(_PREFIXE_IMPORT): fonction
        for nom, fonction in vars(module).items()
        if nom.startswith(_PREFIXE_IMPORT) and callable(fonction)
    }


def executer_import_personnalise(
    nom: str, fonction: Callable[[Path], Any], chemin: Path
) -> Grille:
    """Exécute un import personnalisé. Son erreur est expliquée, sans plantage."""
    try:
        return _grille_personnalisee(fonction(chemin), nom)
    except ImportImpossible:
        raise
    except (Exception, SystemExit) as erreur:
        raise ImportImpossible(
            f"Erreur dans l'import « {nom} » : {type(erreur).__name__} : {erreur}"
        ) from erreur
