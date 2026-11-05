"""Incertitudes selon le GUM : composantes, budget et incertitude élargie.

On stocke les composantes, pas seulement l'incertitude combinée, pour pouvoir
refaire le budget. La propagation dans les calculs est confiée à GTC.

Le facteur d'élargissement k n'est jamais stocké : il est choisi à l'affichage.
"""

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from GTC import dof, uncertainty, ureal
from GTC.lib import UncertainReal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from medphystools.unites import (
    UniteInvalide,
    UnitesIncompatibles,
    convertir,
    convertir_ecart,
    sont_compatibles,
    verifier_unite,
)

# Diviseur reliant la demi-largeur d'une loi à son incertitude-type.
_DIVISEURS = {"rectangulaire": math.sqrt(3), "triangulaire": math.sqrt(6)}


class Composante(BaseModel):
    """Une composante d'incertitude, telle que déclarée par l'utilisateur.

    Selon la loi, `valeur` désigne :
    - normale : une incertitude, divisée par `k` si elle vient d'un certificat ;
    - rectangulaire ou triangulaire : la demi-largeur de l'intervalle.

    Si `relative` est vrai, `valeur` est un pourcentage de la valeur mesurée et
    `unite` reste vide. Sinon, `unite` est obligatoire. `ddl` à None signifie des
    degrés de liberté infinis.

    `erreur_commune` repère une erreur partagée entre plusieurs mesures, par exemple
    le numéro d'un certificat : voir `InfluencesCommunes` et docs/incertitudes.md.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["A", "B"]
    valeur: float = Field(ge=0, allow_inf_nan=False)
    unite: str | None = None
    relative: bool = False
    loi: Literal["normale", "rectangulaire", "triangulaire"] = "normale"
    k: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    ddl: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    source: str = Field(min_length=1)
    erreur_commune: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _verifier_coherence(self) -> "Composante":
        if self.relative and self.unite is not None:
            raise ValueError("Une composante relative n'a pas d'unité : valeur en %.")
        if not self.relative:
            if self.unite is None:
                raise ValueError("L'unité d'une composante absolue est obligatoire.")
            verification = verifier_unite(self.unite)
            if not verification.valide:
                raise UniteInvalide(verification.message)
        if self.k != 1.0 and self.loi != "normale":
            raise ValueError("Le facteur k ne s'applique qu'à une loi normale.")
        if self.type == "A":
            if self.ddl is None:
                raise ValueError(
                    "Une composante de type A déclare ses degrés de liberté."
                )
            if self.loi != "normale" or self.k != 1.0:
                raise ValueError(
                    "Une composante de type A suit une loi normale, avec k = 1."
                )
        return self

    def incertitude_type(self, valeur_mesuree: float, unite_mesure: str) -> float:
        """Renvoie l'incertitude-type, exprimée dans l'unité de la mesure."""
        diviseur = _DIVISEURS.get(self.loi, self.k)
        if self.relative:
            if valeur_mesuree == 0:
                raise ValueError(
                    "Une incertitude relative n'a pas de sens sur une valeur nulle."
                )
            return abs(valeur_mesuree) * self.valeur / 100 / diviseur
        return convertir_ecart(self.valeur, self.unite, unite_mesure) / diviseur


SOURCE_PAR_DEFAUT = "incertitude nulle par défaut"


def composante_exacte(unite: str, source: str = "valeur exacte") -> Composante:
    """Déclare explicitement une valeur exacte, d'incertitude nulle."""
    return Composante(type="B", valeur=0.0, unite=unite, source=source)


def composante_par_defaut(unite: str) -> Composante:
    """L'incertitude nulle donnée à la saisie quand aucune n'est déclarée.

    Sa source la distingue d'une valeur exacte déclarée, et le cahier la montre.
    """
    return composante_exacte(unite, SOURCE_PAR_DEFAUT)


def sans_composante_par_defaut(
    composantes: tuple[Composante, ...],
) -> tuple[Composante, ...]:
    """Retire le défaut d'une mesure relue : à l'écran, rien n'a été déclaré."""
    if len(composantes) == 1 and composantes[0].source == SOURCE_PAR_DEFAUT:
        return ()
    return composantes


def repetitions(
    valeurs: Sequence[float], unite: str, source: str = "répétabilité"
) -> tuple[float, Composante]:
    """Calcule la moyenne de mesures répétées et leur composante de type A.

    L'incertitude-type est l'écart type de la moyenne, s / √n, avec n − 1 degrés
    de liberté. Les incertitudes de type B de l'appareil restent à ajouter.
    """
    n = len(valeurs)
    if n < 2:
        raise ValueError("Il faut au moins deux valeurs répétées.")
    moyenne = statistics.fmean(valeurs)
    ecart_type_moyenne = statistics.stdev(valeurs) / math.sqrt(n)
    composante = Composante(
        type="A",
        valeur=ecart_type_moyenne,
        unite=unite,
        ddl=n - 1,
        source=f"{source} (n = {n})",
    )
    return moyenne, composante


@dataclass(frozen=True)
class LigneBudget:
    """Une ligne du budget d'incertitude, dans l'unité de la mesure."""

    source: str
    type: str
    loi: str
    incertitude_type: float
    ddl: float | None
    contribution_pourcent: float


@dataclass(frozen=True)
class Budget:
    """Une valeur mesurée, son budget d'incertitude et son objet GTC.

    Utiliser toujours le même attribut `gtc` pour une même mesure : GTC tient ainsi
    compte des corrélations quand elle apparaît plusieurs fois dans un calcul.
    """

    valeur: float
    unite: str
    lignes: tuple[LigneBudget, ...]
    gtc: UncertainReal

    @property
    def incertitude_type(self) -> float:
        """Incertitude-type combinée."""
        return uncertainty(self.gtc)

    @property
    def ddl(self) -> float:
        """Degrés de liberté effectifs (Welch-Satterthwaite) ; infinis si exacte."""
        if self.incertitude_type == 0:
            return math.inf
        return dof(self.gtc)

    def elargie(self, k: float = 2.0) -> float:
        """Incertitude élargie U = k · u, dans l'unité de la mesure."""
        if k <= 0:
            raise ValueError("Le facteur d'élargissement k doit être positif.")
        return k * self.incertitude_type

    def elargie_relative(self, k: float = 2.0) -> float | None:
        """Incertitude élargie en % de la valeur ; None si la valeur est nulle."""
        if self.valeur == 0:
            return None
        return 100 * self.elargie(k) / abs(self.valeur)

    def en(self, unite: str) -> "Budget":
        """Renvoie le même budget dans une autre unité, sans perdre les corrélations."""
        facteur = convertir_ecart(1.0, self.unite, unite)
        decalage = convertir(0.0, self.unite, unite)
        lignes = tuple(
            LigneBudget(
                source=ligne.source,
                type=ligne.type,
                loi=ligne.loi,
                incertitude_type=ligne.incertitude_type * facteur,
                ddl=ligne.ddl,
                contribution_pourcent=ligne.contribution_pourcent,
            )
            for ligne in self.lignes
        )
        return Budget(
            valeur=convertir(self.valeur, self.unite, unite),
            unite=unite,
            lignes=lignes,
            gtc=self.gtc * facteur + decalage,
        )


def budget_fige(
    valeur: float,
    unite: str,
    incertitude_type: float,
    ddl: float | None,
    lignes: Sequence[LigneBudget],
) -> Budget:
    """Reconstruit le budget enregistré d'un résultat de calcul.

    Pour GTC, ce résultat devient une mesure indépendante des grandeurs dont il vient.
    """
    degres = math.inf if ddl is None else ddl
    return Budget(
        valeur=valeur,
        unite=unite,
        lignes=tuple(lignes),
        gtc=ureal(valeur, incertitude_type, degres),
    )


class ErreurCommuneIncoherente(ValueError):
    """Un même repère d'erreur commune est déclaré de façon contradictoire."""


class InfluencesCommunes:
    """Les erreurs partagées entre les mesures d'un même calcul, par repère.

    Chaque repère correspond à une seule variable d'écart type 1. Une mesure en reçoit
    u · e, où u est sa propre incertitude-type pour cette composante. Une erreur relative de
    1 % sur 100 MBq et sur 50 MBq donne donc 1 · e et 0,5 · e : leur rapport ne dépend plus
    de e, et l'erreur se compense exactement. La corrélation est supposée complète et de
    même signe.
    """

    def __init__(self) -> None:
        self._variables: dict[str, tuple[UncertainReal, float]] = {}

    def variable(self, composante: Composante) -> UncertainReal:
        """Renvoie la variable partagée de ce repère, en la créant au premier appel."""
        repere = composante.erreur_commune
        ddl = math.inf if composante.ddl is None else composante.ddl
        if repere not in self._variables:
            self._variables[repere] = (ureal(0.0, 1.0, ddl, label=repere), ddl)
        variable, attendu = self._variables[repere]
        if ddl != attendu:
            raise ErreurCommuneIncoherente(
                f"L'erreur commune « {repere} » est déclarée avec des degrés de liberté "
                f"différents ({attendu} et {ddl}) : c'est une seule erreur, à déclarer "
                "de la même façon partout."
            )
        return variable


def etablir_budget(
    valeur: float,
    unite: str,
    composantes: Sequence[Composante],
    communes: InfluencesCommunes | None = None,
    etiquette: str = "",
) -> Budget:
    """Combine les composantes d'une mesure et renvoie son budget.

    L'incertitude est obligatoire : une valeur exacte le déclare par
    `composante_exacte`. Avec `communes`, les composantes de même repère partagent une seule
    erreur. Sans lui, chaque budget reste indépendant. `etiquette` préfixe le nom des
    composantes, pour les reconnaître dans le budget d'un calcul.
    """
    verification = verifier_unite(unite)
    if not verification.valide:
        raise UniteInvalide(verification.message)
    if not composantes:
        raise ValueError(
            "L'incertitude est obligatoire : déclarer au moins une composante."
        )
    for composante in composantes:
        if not composante.relative and not sont_compatibles(composante.unite, unite):
            raise UnitesIncompatibles(
                f"Composante « {composante.source} » en {composante.unite}, "
                f"incompatible avec une mesure en {unite}."
            )

    incertitudes = [c.incertitude_type(valeur, unite) for c in composantes]
    variance = sum(u**2 for u in incertitudes)

    lignes = tuple(
        LigneBudget(
            source=c.source,
            type=c.type,
            loi=c.loi,
            incertitude_type=u,
            ddl=c.ddl,
            contribution_pourcent=100 * u**2 / variance if variance > 0 else 0.0,
        )
        for c, u in zip(composantes, incertitudes)
    )

    gtc = ureal(valeur, 0.0)
    for composante, u in zip(composantes, incertitudes):
        if u > 0:
            if communes is not None and composante.erreur_commune is not None:
                gtc = gtc + u * communes.variable(composante)
                continue
            ddl = math.inf if composante.ddl is None else composante.ddl
            gtc = gtc + ureal(0.0, u, ddl, label=f"{etiquette}{composante.source}")

    return Budget(valeur=valeur, unite=unite, lignes=lignes, gtc=gtc)
