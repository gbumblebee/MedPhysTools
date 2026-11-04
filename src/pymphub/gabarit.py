"""Gabarits : la liste des variables utiles au post-traitement d'un type de CQ.

Un gabarit se crée après coup depuis une page libre, en choisissant ses variables.
Il garde leur nom, leur nature et leur unité attendue, jamais leurs données : celles-ci
sont recherchées dans l'archive au moment de l'analyse.

Il peut aussi garder des onglets tout prêts : le texte de notes (méthode, équations), et les
réglages d'un graphe ou d'un ajustement, qui se construisent quand les données arrivent.
"""

import re
import tomllib
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pymphub.graphes import reglages_par_defaut
from pymphub.page import (
    Ajustement,
    CoefficientPrevu,
    ColonneCalculee,
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Fichier,
    GrandeurDerivee,
    Identifiant,
    Indicateur,
    Nom,
    Onglet,
    OngletAjustements,
    OngletGraphes,
    OngletNotes,
    Page,
    PrereglageAjustement,
    ReglagesGraphe,
    Scalaire,
    Tableau,
    Unite,
)
from pymphub.unites import sont_compatibles

# Format 2 : ajout des onglets préparés. Un gabarit au format 1 reste lisible.
VERSION_SCHEMA_GABARIT = 2
_MODELE_PERSONNALISE = "Personnalisé"


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


class NotesPrevues(_Modele):
    """Un onglet de notes prérempli : méthode, équations. Ses photos ne sont pas reprises."""

    type: Literal["notes"] = "notes"
    titre: str = ""
    texte: str = Field(min_length=1)


class GraphePrevu(_Modele):
    """Un onglet de graphes déjà réglé : il se trace dès que son tableau a des données."""

    type: Literal["graphes"] = "graphes"
    titre: str = ""
    reglages: ReglagesGraphe


class AjustementPrevu(_Modele):
    """Un onglet d'ajustement déjà réglé : il s'ajuste dès que son tableau a des données."""

    type: Literal["ajustements"] = "ajustements"
    titre: str = ""
    prereglage: PrereglageAjustement


OngletPrevu = Annotated[
    NotesPrevues | GraphePrevu | AjustementPrevu, Field(discriminator="type")
]


class Gabarit(_Modele):
    """Le contenu d'un fichier de gabarit TOML."""

    version_schema: int = VERSION_SCHEMA_GABARIT
    nom: str = Field(pattern=r"^[a-z0-9_.-]+$")
    version: int = Field(ge=1)
    description: str = ""
    variables: tuple[Variable, ...] = Field(min_length=1)
    onglets: tuple[OngletPrevu, ...] = ()

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
        self._verifier_onglets()
        return self

    def _verifier_onglets(self) -> None:
        """Un onglet préparé ne peut utiliser que des colonnes que le gabarit attend."""
        colonnes = {
            variable.nom: {c.nom for c in variable.colonnes if c.nature == "numerique"}
            for variable in self.variables
            if isinstance(variable, VariableTableau)
        }
        ajustements = [
            o.prereglage.nom for o in self.onglets if isinstance(o, AjustementPrevu)
        ]
        pris = [variable.nom for variable in self.variables]
        repetes = sorted(
            {nom for nom in ajustements if ajustements.count(nom) > 1 or nom in pris}
        )
        if repetes:
            raise ValueError(f"Noms d'ajustement déjà pris : {', '.join(repetes)}.")

        def verifier(nom: str, tableau: str, utilisees: Sequence[str]) -> None:
            for colonne in utilisees:
                if colonne not in colonnes.get(tableau, ()):
                    raise ValueError(
                        f"L'onglet « {nom} » utilise {tableau}.{colonne} : cocher cette "
                        "variable, ou décocher cet onglet."
                    )

        for onglet in self.onglets:
            if isinstance(onglet, GraphePrevu):
                r = onglet.reglages
                verifier(
                    onglet.titre,
                    r.tableau,
                    [r.x, *r.y, *(() if r.z is None else (r.z,))],
                )
                inconnues = sorted(set(r.courbes) - set(ajustements))
                if inconnues:
                    raise ValueError(
                        f"L'onglet « {onglet.titre} » superpose un ajustement absent du "
                        f"gabarit : {', '.join(inconnues)}."
                    )
            elif isinstance(onglet, AjustementPrevu):
                p = onglet.prereglage
                verifier(onglet.titre, p.tableau, [p.x, p.y])

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


def _ajustement_de(page: Page, onglet: Onglet) -> Ajustement | None:
    """L'ajustement que montre un onglet d'ajustement, s'il en a un."""
    for nom in getattr(onglet, "grandeurs", ()):
        grandeur = next((g for g in page.mesures if g.nom == nom), None)
        if isinstance(grandeur, Ajustement):
            return grandeur
    return None


def _prereglage_de(ajustement: Ajustement) -> PrereglageAjustement:
    """Garde le modèle d'un ajustement, sans son résultat.

    Une formule personnalisée a besoin d'un point de départ pour converger, et ses unités
    ne se déduisent pas des colonnes : ses coefficients servent donc de départ.
    """
    personnalise = ajustement.modele == _MODELE_PERSONNALISE
    return PrereglageAjustement(
        nom=ajustement.nom,
        tableau=ajustement.tableau,
        x=ajustement.x,
        y=ajustement.y,
        modele=ajustement.modele,
        formule=ajustement.formule,
        coefficients=tuple(
            CoefficientPrevu(nom=p.nom, unite=p.unite, depart=p.valeur)
            for p in ajustement.parametres
        )
        if personnalise
        else (),
    )


def _colonnes_calculees_de(
    page: Page, tableau: str, utilisees: Sequence[str]
) -> list[str]:
    """Parmi ces colonnes, celles qui sont calculées : un gabarit ne garde pas les calculs."""
    grandeur = next((g for g in page.mesures if g.nom == tableau), None)
    mesurees = (
        {c.nom for c in grandeur.colonnes if isinstance(c, ColonneNumerique)}
        if isinstance(grandeur, Tableau)
        else set()
    )
    return [nom for nom in utilisees if nom not in mesurees]


@dataclass(frozen=True)
class OngletChoisissable:
    """Un onglet qu'un gabarit peut reprendre. `probleme` dit pourquoi il ne le peut pas."""

    id: str
    titre: str
    type: str
    probleme: str | None = None


def onglets_choisissables(page: Page) -> list[OngletChoisissable]:
    """Les onglets de notes, de graphes et d'ajustements qui ont quelque chose à reprendre."""
    resultat: list[OngletChoisissable] = []
    for onglet in page.onglets:
        utilisees: tuple[str, Sequence[str]] | None = None
        if isinstance(onglet, OngletNotes):
            if not onglet.texte.strip():
                continue
        elif isinstance(onglet, OngletGraphes):
            reglages = reglages_par_defaut(page, onglet)
            if reglages is None:
                continue
            utilisees = (
                reglages.tableau,
                [reglages.x, *reglages.y, *((reglages.z,) if reglages.z else ())],
            )
        elif onglet.type == "ajustements":
            ajustement = _ajustement_de(page, onglet)
            if ajustement is None:
                continue
            utilisees = (ajustement.tableau, [ajustement.x, ajustement.y])
        else:
            continue
        calculees = _colonnes_calculees_de(page, *utilisees) if utilisees else []
        resultat.append(
            OngletChoisissable(
                onglet.id,
                onglet.titre or onglet.type,
                onglet.type,
                f"utilise {', '.join(calculees)}, une colonne calculée"
                if calculees
                else None,
            )
        )
    return resultat


def _onglet_prevu(
    page: Page, onglet: Onglet, ajustements: Sequence[str]
) -> NotesPrevues | GraphePrevu | AjustementPrevu:
    titre = onglet.titre or onglet.type
    if isinstance(onglet, OngletNotes):
        return NotesPrevues(titre=titre, texte=onglet.texte)
    if isinstance(onglet, OngletGraphes):
        reglages = reglages_par_defaut(page, onglet)
        if reglages is not None:
            return GraphePrevu(
                titre=titre,
                reglages=reglages.model_copy(
                    update={
                        "courbes": tuple(
                            c for c in reglages.courbes if c in ajustements
                        )
                    }
                ),
            )
    else:
        ajustement = _ajustement_de(page, onglet)
        if ajustement is not None:
            return AjustementPrevu(titre=titre, prereglage=_prereglage_de(ajustement))
    raise ValueError(f"L'onglet « {titre} » n'a rien à reprendre dans un gabarit.")


def _onglets_prevus(
    page: Page, choisis: Sequence[str]
) -> tuple[NotesPrevues | GraphePrevu | AjustementPrevu, ...]:
    """Prépare les onglets choisis. Un graphe ne superpose que les ajustements repris."""
    par_id = {onglet.id: onglet for onglet in page.onglets}
    inconnus = sorted(set(choisis) - set(par_id))
    if inconnus:
        raise KeyError(f"Aucun onglet « {', '.join(inconnus)} » dans cette page.")
    ajustements = [
        a.nom
        for identifiant in choisis
        if (a := _ajustement_de(page, par_id[identifiant])) is not None
    ]
    return tuple(_onglet_prevu(page, par_id[i], ajustements) for i in choisis)


def creer_gabarit(
    page: Page,
    nom: str,
    variables: Sequence[str],
    version: int = 1,
    description: str = "",
    onglets: Sequence[str] = (),
) -> Gabarit:
    """Crée un gabarit à partir d'une page, en choisissant les variables utiles.

    Chaque variable désigne une grandeur (`activite`), un tableau entier (`decroissance`)
    ou une colonne (`decroissance.heure`). Les colonnes choisies d'un même tableau sont
    regroupées. Aucune valeur mesurée ni incertitude n'est reprise.

    `onglets` donne les identifiants des onglets à reprendre : le texte des notes, et les
    réglages des graphes et des ajustements, jamais leurs résultats.
    """
    courbes = {g.courbe for g in page.mesures if isinstance(g, Ajustement)}
    resultat: dict[str, Variable] = {}
    colonnes_choisies: dict[str, list[ColonneAttendue]] = {}

    for choix in variables:
        nom_grandeur, separateur, nom_colonne = choix.partition(".")
        grandeur = page.grandeur(nom_grandeur)
        if nom_grandeur in courbes:
            raise ValueError(
                f"« {nom_grandeur} » est la courbe d'un ajustement : reprendre plutôt "
                "l'onglet d'ajustement."
            )

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
        elif isinstance(
            grandeur, (GrandeurDerivee, ColonneCalculee, Ajustement, Indicateur)
        ):
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
        onglets=_onglets_prevus(page, onglets),
    )


def nom_de_gabarit(titre: str) -> str:
    """Propose un nom de gabarit d'après un titre : minuscules, sans accent."""
    sans_accent = (
        unicodedata.normalize("NFKD", titre).encode("ascii", "ignore").decode()
    )
    return re.sub(r"[^a-z0-9]+", "-", sans_accent.lower()).strip("-")


def version_suivante(references: Sequence[str], nom: str) -> int:
    """La version à donner à un gabarit, d'après ceux qui portent déjà ce nom."""
    versions = [
        int(version)
        for reference in references
        for prefixe, _, version in [reference.rpartition("@")]
        if prefixe == nom
    ]
    return max(versions, default=0) + 1


def colonnes_choisissables(page: Page) -> dict[str, list[str]]:
    """Ce qu'un gabarit peut reprendre d'une page : pour un tableau, ses colonnes de données.

    Les résultats de calcul n'y figurent pas : ils se déduisent des grandeurs mesurées.
    """
    resultat: dict[str, list[str]] = {}
    courbes = {g.courbe for g in page.mesures if isinstance(g, Ajustement)}
    for grandeur in page.mesures:
        if grandeur.nom in courbes:
            continue
        if isinstance(grandeur, Tableau):
            resultat[grandeur.nom] = [c.nom for c in _colonnes_utiles(grandeur)]
        elif isinstance(grandeur, (Scalaire, Identifiant, Fichier)):
            resultat[grandeur.nom] = []
    return resultat


def _ecrire_colonne(colonne: ColonneAttendue) -> str:
    return colonne.nom if colonne.unite is None else f"{colonne.nom} en {colonne.unite}"


def decrire_attendu(gabarit: Gabarit, reference: str) -> str:
    """Écrit ce qu'attend une variable, comme `activite (MBq)` ou `t.heure (h)`."""
    nom, separateur, nom_colonne = reference.partition(".")
    variable = next(v for v in gabarit.variables if v.nom == nom)
    if separateur:
        colonne = next(c for c in variable.colonnes if c.nom == nom_colonne)
        return f"{reference} ({colonne.unite or 'identifiant'})"
    if isinstance(variable, VariableScalaire):
        return f"{nom} ({variable.unite})"
    if isinstance(variable, VariableTableau):
        colonnes = ", ".join(_ecrire_colonne(c) for c in variable.colonnes)
        return f"{nom} (tableau : {colonnes})"
    return f"{nom} ({variable.nature})"


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


def onglet_depuis_gabarit(
    prevu: NotesPrevues | GraphePrevu | AjustementPrevu, identifiant: str
) -> Onglet:
    """Crée l'onglet d'une nouvelle page à partir de ce que le gabarit a prévu."""
    if isinstance(prevu, NotesPrevues):
        return OngletNotes(
            id=identifiant, type="notes", titre=prevu.titre, texte=prevu.texte
        )
    if isinstance(prevu, GraphePrevu):
        return OngletGraphes(
            id=identifiant, type="graphes", titre=prevu.titre, reglages=prevu.reglages
        )
    return OngletAjustements(
        id=identifiant,
        type="ajustements",
        titre=prevu.titre,
        prereglage=prevu.prereglage,
    )


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
    tant qu'elles n'ont pas de valeur, d'unité et d'incertitude. Les onglets prévus par le
    gabarit sont déjà là : notes, graphes et ajustements réglés.
    """
    return Page(
        id=id,
        titre=titre,
        date_mesure=date_mesure,
        date_saisie=date_saisie,
        auteur=auteur,
        gabarit=gabarit.reference,
        onglets=tuple(
            onglet_depuis_gabarit(prevu, f"onglet_{rang}")
            for rang, prevu in enumerate(gabarit.onglets, start=1)
        ),
    )
