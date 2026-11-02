"""Gabarits : la liste des variables utiles au post-traitement d'un type de CQ.

Un gabarit se crée après coup depuis une page libre, en choisissant ses variables.
Il garde leur nom, leur nature et leur unité attendue, jamais leurs données : celles-ci
sont recherchées dans l'archive au moment de l'analyse.
"""

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pymphub.page import (
    ColonneCalculee,
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Fichier,
    GrandeurDerivee,
    Identifiant,
    Nom,
    Page,
    Scalaire,
    Tableau,
    Unite,
)
from pymphub.unites import sont_compatibles

VERSION_SCHEMA_GABARIT = 1


class _Modele(BaseModel):
    """Base des modèles : immuables, et tout champ inconnu est refusé."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class VariableScalaire(_Modele):
    """Une valeur numérique attendue, dans une unité compatible avec `unite`."""

    nature: Literal["scalaire"] = "scalaire"
    nom: Nom
    unite: Unite


class VariableIdentifiant(_Modele):
    """Un identifiant attendu : numéro de série, de lot…"""

    nature: Literal["identifiant"] = "identifiant"
    nom: Nom


class VariableFichier(_Modele):
    """Un fichier attendu : DICOM, image, export constructeur."""

    nature: Literal["fichier"] = "fichier"
    nom: Nom


class ColonneAttendue(_Modele):
    """Une colonne attendue. `unite` est absente pour une colonne d'identifiants."""

    nom: Nom
    nature: Literal["numerique", "identifiant"]
    unite: Unite | None = None

    @model_validator(mode="after")
    def _verifier_unite(self) -> "ColonneAttendue":
        if (self.nature == "numerique") != (self.unite is not None):
            raise ValueError(
                f"Colonne « {self.nom} » : une unité est requise pour une colonne "
                "numérique, et seulement pour elle."
            )
        return self


class VariableTableau(_Modele):
    """Un tableau attendu, avec les colonnes utiles au post-traitement."""

    nature: Literal["tableau"] = "tableau"
    nom: Nom
    colonnes: tuple[ColonneAttendue, ...] = Field(min_length=1)


Variable = Annotated[
    VariableScalaire | VariableIdentifiant | VariableFichier | VariableTableau,
    Field(discriminator="nature"),
]


class Gabarit(_Modele):
    """Le contenu d'un fichier de gabarit TOML."""

    version_schema: int = VERSION_SCHEMA_GABARIT
    nom: str = Field(pattern=r"^[a-z0-9_.-]+$")
    version: int = Field(ge=1)
    description: str = ""
    variables: tuple[Variable, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _verifier(self) -> "Gabarit":
        if not 1 <= self.version_schema <= VERSION_SCHEMA_GABARIT:
            raise ValueError(
                f"Gabarit au format {self.version_schema}, mais ce logiciel lit jusqu'au "
                f"format {VERSION_SCHEMA_GABARIT} : mettre à jour pymphub."
            )
        noms = [variable.nom for variable in self.variables]
        doublons = sorted({nom for nom in noms if noms.count(nom) > 1})
        if doublons:
            raise ValueError(f"Variables répétées : {', '.join(doublons)}.")
        return self

    @property
    def reference(self) -> str:
        """La référence stockée dans une page, par exemple `activimetre.linearite@1`."""
        return f"{self.nom}@{self.version}"


def _colonne_attendue(
    colonne: ColonneNumerique | ColonneIdentifiant,
) -> ColonneAttendue:
    if isinstance(colonne, ColonneNumerique):
        return ColonneAttendue(nom=colonne.nom, nature="numerique", unite=colonne.unite)
    return ColonneAttendue(nom=colonne.nom, nature="identifiant")


def _colonnes_utiles(tableau: Tableau) -> list[ColonneNumerique | ColonneIdentifiant]:
    """Les colonnes de données : les incertitudes restent propres à chaque page."""
    return [c for c in tableau.colonnes if not isinstance(c, ColonneIncertitude)]


def creer_gabarit(
    page: Page,
    nom: str,
    variables: Sequence[str],
    version: int = 1,
    description: str = "",
) -> Gabarit:
    """Crée un gabarit à partir d'une page, en choisissant les variables utiles.

    Chaque variable désigne une grandeur (`activite`), un tableau entier (`decroissance`)
    ou une colonne (`decroissance.heure`). Les colonnes choisies d'un même tableau sont
    regroupées. Aucune valeur mesurée ni incertitude n'est reprise.
    """
    resultat: dict[str, Variable] = {}
    colonnes_choisies: dict[str, list[ColonneAttendue]] = {}

    for choix in variables:
        nom_grandeur, separateur, nom_colonne = choix.partition(".")
        grandeur = page.grandeur(nom_grandeur)

        if separateur:
            if not isinstance(grandeur, Tableau):
                raise ValueError(f"« {nom_grandeur} » n'est pas un tableau.")
            colonnes = {c.nom: c for c in _colonnes_utiles(grandeur)}
            if nom_colonne not in colonnes:
                raise KeyError(
                    f"Aucune colonne de données « {nom_colonne} » dans « {nom_grandeur} »."
                )
            colonnes_choisies.setdefault(nom_grandeur, [])
            attendue = _colonne_attendue(colonnes[nom_colonne])
            if attendue in colonnes_choisies[nom_grandeur]:
                raise ValueError(f"Variable choisie deux fois : « {choix} ».")
            colonnes_choisies[nom_grandeur].append(attendue)
            continue

        if nom_grandeur in resultat:
            raise ValueError(f"Variable choisie deux fois : « {choix} ».")
        if isinstance(grandeur, Scalaire):
            resultat[nom_grandeur] = VariableScalaire(
                nom=grandeur.nom, unite=grandeur.unite
            )
        elif isinstance(grandeur, Identifiant):
            resultat[nom_grandeur] = VariableIdentifiant(nom=grandeur.nom)
        elif isinstance(grandeur, Fichier):
            resultat[nom_grandeur] = VariableFichier(nom=grandeur.nom)
        elif isinstance(grandeur, (GrandeurDerivee, ColonneCalculee)):
            raise ValueError(
                f"« {nom_grandeur} » est un résultat de calcul : choisir plutôt les "
                "grandeurs mesurées dont il dépend."
            )
        else:
            resultat[nom_grandeur] = VariableTableau(
                nom=grandeur.nom,
                colonnes=tuple(
                    _colonne_attendue(c) for c in _colonnes_utiles(grandeur)
                ),
            )

    for nom_tableau, colonnes in colonnes_choisies.items():
        if nom_tableau in resultat:
            raise ValueError(
                f"« {nom_tableau} » est choisi à la fois en entier et par colonne."
            )
        resultat[nom_tableau] = VariableTableau(
            nom=nom_tableau, colonnes=tuple(colonnes)
        )

    return Gabarit(
        nom=nom,
        version=version,
        description=description,
        variables=tuple(resultat.values()),
    )


def vers_toml(gabarit: Gabarit) -> str:
    """Écrit un gabarit en TOML lisible."""
    return tomli_w.dumps(gabarit.model_dump(mode="json", exclude_none=True))


def depuis_toml(texte: str) -> Gabarit:
    """Relit un gabarit depuis son TOML, en vérifiant toutes ses règles."""
    return Gabarit.model_validate(tomllib.loads(texte))


@dataclass(frozen=True)
class Ecart:
    """Une différence entre une page et un gabarit.

    Une variable `absente` reste à remplir, ce qui est normal pour un brouillon. Une
    `nature` ou une `unite` incohérente est une erreur de saisie.
    """

    variable: str
    probleme: Literal["absente", "nature", "unite"]
    message: str


def _ecarts_tableau(variable: VariableTableau, tableau: Tableau) -> list[Ecart]:
    colonnes = {c.nom: c for c in tableau.colonnes}
    ecarts: list[Ecart] = []
    for attendue in variable.colonnes:
        reference = f"{variable.nom}.{attendue.nom}"
        colonne = colonnes.get(attendue.nom)
        if colonne is None:
            ecarts.append(
                Ecart(reference, "absente", f"Colonne « {reference} » absente.")
            )
        elif colonne.nature != attendue.nature:
            ecarts.append(
                Ecart(
                    reference,
                    "nature",
                    f"Colonne « {reference} » : {attendue.nature} attendue, "
                    f"{colonne.nature} trouvée.",
                )
            )
        elif attendue.unite is not None and not sont_compatibles(
            colonne.unite, attendue.unite
        ):
            ecarts.append(
                Ecart(
                    reference,
                    "unite",
                    f"Colonne « {reference} » en {colonne.unite}, "
                    f"incompatible avec {attendue.unite}.",
                )
            )
    return ecarts


def verifier_conformite(page: Page, gabarit: Gabarit) -> list[Ecart]:
    """Liste les écarts entre une page et un gabarit ; une liste vide signifie conforme.

    Une unité différente mais compatible (mCi au lieu de MBq) n'est pas un écart. Les
    grandeurs de la page absentes du gabarit sont permises : la page reste libre.
    """
    presentes = {grandeur.nom: grandeur for grandeur in page.mesures}
    ecarts: list[Ecart] = []
    for variable in gabarit.variables:
        grandeur = presentes.get(variable.nom)
        if grandeur is None:
            ecarts.append(
                Ecart(variable.nom, "absente", f"Variable « {variable.nom} » absente.")
            )
        elif grandeur.nature != variable.nature:
            ecarts.append(
                Ecart(
                    variable.nom,
                    "nature",
                    f"Variable « {variable.nom} » : {variable.nature} attendu, "
                    f"{grandeur.nature} trouvé.",
                )
            )
        elif isinstance(variable, VariableTableau):
            ecarts.extend(_ecarts_tableau(variable, grandeur))
        elif isinstance(variable, VariableScalaire) and not sont_compatibles(
            grandeur.unite, variable.unite
        ):
            ecarts.append(
                Ecart(
                    variable.nom,
                    "unite",
                    f"Variable « {variable.nom} » en {grandeur.unite}, "
                    f"incompatible avec {variable.unite}.",
                )
            )
    return ecarts


class PageNonConforme(ValueError):
    """La page présente des écarts avec le gabarit demandé."""


def rattacher_gabarit(page: Page, gabarit: Gabarit) -> Page:
    """Renvoie une copie de la page rattachée à un gabarit, si elle lui est conforme."""
    ecarts = verifier_conformite(page, gabarit)
    if ecarts:
        details = " ".join(ecart.message for ecart in ecarts)
        raise PageNonConforme(f"Page non conforme à {gabarit.reference} : {details}")
    return page.model_copy(update={"gabarit": gabarit.reference})


def page_depuis_gabarit(
    gabarit: Gabarit,
    id: str,
    titre: str,
    date_mesure: datetime,
    date_saisie: datetime,
    auteur: str,
) -> Page:
    """Crée une page vide rattachée à un gabarit.

    Les variables restent à remplir : `verifier_conformite` les signale comme absentes
    tant qu'elles n'ont pas de valeur, d'unité et d'incertitude.
    """
    return Page(
        id=id,
        titre=titre,
        date_mesure=date_mesure,
        date_saisie=date_saisie,
        auteur=auteur,
        gabarit=gabarit.reference,
    )
