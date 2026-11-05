"""Ajustements et indicateurs sur les colonnes d'un tableau, sans interface.

La méthode et ses hypothèses sont décrites dans docs/incertitudes.md. En résumé :
- les points sont pondérés par leur incertitude propre, et la covariance des coefficients
  n'est pas remise à l'échelle du χ² : elle reflète les incertitudes déclarées ;
- l'incertitude sur x passe par la variance effective u² = u(y)² + (f'(x) · u(x))² ;
- une erreur partagée entre plusieurs points ne se combat pas par la pondération : elle est
  propagée à part sur les coefficients, au premier ordre.

La formule d'un modèle n'est jamais exécutée comme du code, comme celle d'un calcul.
"""

import ast
import hashlib
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from medphystools.calculs import (
    CONSTANTES,
    ContexteCalcul,
    ErreurCalcul,
    FormuleRefusee,
    _analyser,
    _ecrire_unite,
    _reduire,
)
from medphystools.page import (
    Ajustement,
    ColonneNumerique,
    Indicateur,
    Page,
    ParametreAjuste,
    Tableau,
)
from medphystools.unites import UniteInvalide, registre, verifier_unite

METHODES_POINTS = (
    "maximum",
    "minimum",
    "moyenne",
    "mediane",
    "percentile",
    "ecart_type",
    "coefficient_variation",
    "moyenne_quadratique",
    "somme",
    "etendue",
    "nombre_points",
    "integrale",
    "derivee",
)
METHODES_AJUSTEMENT = (
    "maximum",
    "minimum",
    "moyenne",
    "integrale",
    "derivee",
    "largeur_mi_hauteur",
    "abscisse_de_valeur",
)
METHODES = tuple(dict.fromkeys((*METHODES_POINTS, *METHODES_AJUSTEMENT)))
# Ces indicateurs se lisent sur l'axe y : un repère peut donc les montrer sur le graphe.
METHODES_EN_Y = ("maximum", "minimum", "moyenne", "mediane", "percentile")
POINTS_COURBE_MIN = 200
_SUR_ECHANTILLONNAGE = 10
_POINTS_RECHERCHE = 2001
_PAS_RELATIF = 1e-6
_FRACTION_DE_U = 0.01
_PAS_MINIMAL = 1e-10
_PASSES_VARIANCE_EFFECTIVE = 3
_CHI2_TROP_GRAND = 3.0
_CONDITIONNEMENT_MAX = 1e13
_METHODE_PONDEREE = (
    "Moindres carrés pondérés par l'incertitude propre de chaque point, variance effective "
    "pour x, erreurs communes propagées à part ; covariance non remise à l'échelle du χ²."
)
_METHODE_NON_PONDEREE = (
    "Moindres carrés sans pondération, car aucun point n'a d'incertitude propre ; "
    "covariance tirée de la dispersion des points, erreurs communes propagées à part."
)

_FONCTIONS: dict[str, Callable[[np.ndarray], np.ndarray]] = {
    "exp": np.exp,
    "ln": np.log,
    "log10": np.log10,
    "sqrt": np.sqrt,
    "sin": np.sin,
    "cos": np.cos,
    "tan": np.tan,
}


class ErreurAjustement(ErreurCalcul):
    """L'ajustement ou l'indicateur est impossible : le message explique pourquoi."""


# --- Modèles ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ModeleUsuel:
    """Un modèle courant. `unites` donne, par coefficient, les puissances de y et de x."""

    libelle: str
    formule: str
    unites: Mapping[str, tuple[int, int]]


MODELES: dict[str, ModeleUsuel] = {
    "droite": ModeleUsuel("Droite", "a * x + b", {"a": (1, -1), "b": (1, 0)}),
    "droite_origine": ModeleUsuel("Droite par l'origine", "a * x", {"a": (1, -1)}),
    "polynome2": ModeleUsuel(
        "Polynôme de degré 2",
        "c * x**2 + b * x + a",
        {"c": (1, -2), "b": (1, -1), "a": (1, 0)},
    ),
    "polynome3": ModeleUsuel(
        "Polynôme de degré 3",
        "d * x**3 + c * x**2 + b * x + a",
        {"d": (1, -3), "c": (1, -2), "b": (1, -1), "a": (1, 0)},
    ),
    "decroissance": ModeleUsuel(
        "Décroissance (période)",
        "A * exp(-ln(2) * x / T12)",
        {"A": (1, 0), "T12": (0, 1)},
    ),
    "exponentielle": ModeleUsuel(
        "Exponentielle",
        "A * exp(-l * x)",
        {"A": (1, 0), "l": (0, -1)},
    ),
    "exponentielle_fond": ModeleUsuel(
        "Exponentielle avec fond",
        "A * exp(-l * x) + C",
        {"A": (1, 0), "l": (0, -1), "C": (1, 0)},
    ),
    "gaussienne": ModeleUsuel(
        "Gaussienne",
        "A * exp(-(x - m)**2 / (2 * s**2))",
        {"A": (1, 0), "m": (0, 1), "s": (0, 1)},
    ),
}


def _numerique(noeud: ast.AST, variables: Mapping[str, np.ndarray | float]) -> object:
    """Évalue l'arbre d'une formule sur des tableaux de nombres. Le reste est refusé."""
    if isinstance(noeud, ast.Expression):
        return _numerique(noeud.body, variables)
    if isinstance(noeud, ast.Constant):
        if type(noeud.value) not in (int, float):
            raise FormuleRefusee("Une formule ne contient que des nombres et des noms.")
        return float(noeud.value)
    if isinstance(noeud, ast.Name):
        if noeud.id in variables:
            return variables[noeud.id]
        if noeud.id in CONSTANTES:
            return CONSTANTES[noeud.id]
        raise FormuleRefusee(f"Nom « {noeud.id} » inconnu dans ce modèle.")
    if isinstance(noeud, ast.BinOp):
        gauche = _numerique(noeud.left, variables)
        droite = _numerique(noeud.right, variables)
        if isinstance(noeud.op, ast.Add):
            return gauche + droite
        if isinstance(noeud.op, ast.Sub):
            return gauche - droite
        if isinstance(noeud.op, ast.Mult):
            return gauche * droite
        if isinstance(noeud.op, ast.Div):
            return gauche / droite
        if isinstance(noeud.op, ast.Pow):
            return np.power(np.asarray(gauche, dtype=float), droite)
        if isinstance(noeud.op, ast.BitXor):
            raise FormuleRefusee("Pour une puissance, écrire ** au lieu de ^.")
        raise FormuleRefusee("Seules les opérations + - * / et ** sont autorisées.")
    if isinstance(noeud, ast.UnaryOp) and isinstance(noeud.op, (ast.USub, ast.UAdd)):
        valeur = _numerique(noeud.operand, variables)
        return -valeur if isinstance(noeud.op, ast.USub) else valeur
    if (
        isinstance(noeud, ast.Call)
        and isinstance(noeud.func, ast.Name)
        and noeud.func.id in _FONCTIONS
        and len(noeud.args) == 1
        and not noeud.keywords
        and not isinstance(noeud.args[0], ast.Starred)
    ):
        return _FONCTIONS[noeud.func.id](_numerique(noeud.args[0], variables))
    raise FormuleRefusee(
        "Cette écriture n'est pas autorisée dans un modèle : nombres, x, coefficients, "
        "+ - * / ** et fonctions usuelles seulement."
    )


def coefficients_de(formule: str) -> list[str]:
    """Les coefficients d'un modèle, dans l'ordre où ils apparaissent. x doit y figurer."""
    arbre = _analyser(formule)
    noms: dict[str, None] = {}

    def parcourir(noeud: ast.AST) -> None:
        if isinstance(noeud, ast.Call):
            for argument in noeud.args:
                parcourir(argument)
            return
        if isinstance(noeud, ast.Name):
            noms[noeud.id] = None
        for enfant in ast.iter_child_nodes(noeud):
            parcourir(enfant)

    parcourir(arbre)
    if "x" not in noms:
        raise FormuleRefusee("Un modèle doit dépendre de x.")
    resultat = [nom for nom in noms if nom != "x" and nom not in CONSTANTES]
    if not resultat:
        raise FormuleRefusee("Un modèle doit avoir au moins un coefficient à ajuster.")
    essai = dict.fromkeys(resultat, 1.0) | {"x": np.array([1.0])}
    with np.errstate(all="ignore"):
        _numerique(arbre, essai)
    return resultat


def creer_fonction(
    formule: str, noms: Sequence[str]
) -> Callable[[np.ndarray, Sequence[float]], np.ndarray]:
    """Prépare f(x, coefficients), qui renvoie toujours un tableau de la forme de x."""
    arbre = _analyser(formule)

    def fonction(x: np.ndarray, valeurs: Sequence[float]) -> np.ndarray:
        variables = dict(zip(noms, (float(v) for v in valeurs))) | {"x": x}
        with np.errstate(all="ignore"):
            return np.broadcast_to(
                np.asarray(_numerique(arbre, variables), dtype=float), x.shape
            ).copy()

    return fonction


def unites_des_coefficients(
    modele: ModeleUsuel, unite_x: str, unite_y: str
) -> dict[str, str]:
    """Déduit l'unité des coefficients d'un modèle usuel de celles des deux colonnes."""
    resultat: dict[str, str] = {}
    for nom, (puissance_y, puissance_x) in modele.unites.items():
        unite = (
            registre.Unit(unite_y) ** puissance_y
            * registre.Unit(unite_x) ** puissance_x
        )
        resultat[nom] = _ecrire_unite(_reduire(unite)[1])
    return resultat


# --- Lecture des points -------------------------------------------------------------------


@dataclass(frozen=True)
class Axe:
    """Les valeurs d'une colonne et le détail de leurs incertitudes, par erreur élémentaire."""

    nom: str
    unite: str
    valeurs: np.ndarray
    composantes: tuple[dict[object, float], ...]


@dataclass(frozen=True)
class Points:
    """Les lignes utilisables de deux colonnes, triées par x croissant."""

    tableau: str
    x: Axe
    y: Axe
    u_x: np.ndarray
    u_y: np.ndarray
    communs_x: dict[object, np.ndarray] = field(default_factory=dict)
    communs_y: dict[object, np.ndarray] = field(default_factory=dict)
    gtc_x: tuple[object, ...] = ()
    gtc_y: tuple[object, ...] = ()

    @property
    def empreinte(self) -> str:
        """Résume les points : elle change dès qu'une valeur ou une incertitude change."""
        calcul = hashlib.sha256()
        for tableau in (self.x.valeurs, self.y.valeurs, self.u_x, self.u_y):
            calcul.update(np.round(tableau, 12).tobytes())
        calcul.update(f"{self.x.unite}|{self.y.unite}".encode())
        return calcul.hexdigest()


def _composantes_signees(valeur: object) -> dict[object, float]:
    """La part signée de chaque erreur élémentaire dans une valeur.

    GTC ne la donne qu'en valeur absolue dans ses rapports. Or le signe compte : une même
    erreur peut monter un point et en descendre un autre.
    """
    composantes = {
        feuille.uid: float(u) for feuille, u in valeur._u_components.items() if u != 0
    }
    composantes.update(
        {feuille.uid: float(u) for feuille, u in valeur._d_components.items() if u != 0}
    )
    return composantes


def lire_points(
    page: Page,
    tableau: str,
    x: str,
    y: str,
    lire_tableau: Callable[[str], pd.DataFrame],
) -> Points:
    """Relit deux colonnes numériques. Une ligne sans résultat calculé est écartée."""
    description = next(
        (g for g in page.mesures if isinstance(g, Tableau) and g.nom == tableau), None
    )
    if description is None:
        raise ErreurAjustement(
            f"Aucun tableau « {tableau} » enregistré dans cette page."
        )
    contexte = ContexteCalcul(page, lire_tableau)
    try:
        nombre = len(contexte.donnees_tableau(tableau))
    except (OSError, KeyError) as erreur:
        raise ErreurAjustement(str(erreur)) from erreur

    lignes: list[tuple[float, float, object, object, float, float]] = []
    for ligne in range(1, nombre + 1):
        try:
            valeur_x = contexte.cellule(tableau, x, ligne)
            valeur_y = contexte.cellule(tableau, y, ligne)
            facteur_x, reduite_x = _reduire(valeur_x.unite)
            facteur_y, reduite_y = _reduire(valeur_y.unite)
        except ErreurCalcul as erreur:
            if "Aucune colonne" in str(erreur):
                raise
            continue
        except (ZeroDivisionError, ValueError, OverflowError):
            continue
        gtc_x = valeur_x.x * facteur_x
        gtc_y = valeur_y.x * facteur_y
        if math.isfinite(gtc_x.x) and math.isfinite(gtc_y.x):
            lignes.append((gtc_x.x, gtc_y.x, gtc_x, gtc_y, reduite_x, reduite_y))
    if not lignes:
        raise ErreurAjustement(
            f"Les colonnes « {x} » et « {y} » n'ont aucune ligne utilisable."
        )
    lignes.sort(key=lambda rang: rang[0])
    unite_x = _ecrire_unite(lignes[0][4])
    unite_y = _ecrire_unite(lignes[0][5])
    detail_x = tuple(_composantes_signees(rang[2]) for rang in lignes)
    detail_y = tuple(_composantes_signees(rang[3]) for rang in lignes)

    # Une erreur vue par plusieurs lignes est commune ; les autres sont propres à leur ligne.
    vues: dict[object, set[int]] = {}
    for rang, (de_x, de_y) in enumerate(zip(detail_x, detail_y)):
        for uid in (*de_x, *de_y):
            vues.setdefault(uid, set()).add(rang)
    communes = [uid for uid, rangs in vues.items() if len(rangs) > 1]

    def propre(detail: tuple[dict[object, float], ...]) -> np.ndarray:
        return np.array(
            [
                math.sqrt(sum(u**2 for uid, u in ligne.items() if uid not in communes))
                for ligne in detail
            ]
        )

    def commun(detail: tuple[dict[object, float], ...]) -> dict[object, np.ndarray]:
        return {
            uid: np.array([ligne.get(uid, 0.0) for ligne in detail]) for uid in communes
        }

    return Points(
        tableau=tableau,
        x=Axe(x, unite_x, np.array([r[0] for r in lignes]), detail_x),
        y=Axe(y, unite_y, np.array([r[1] for r in lignes]), detail_y),
        u_x=propre(detail_x),
        u_y=propre(detail_y),
        communs_x=commun(detail_x),
        communs_y=commun(detail_y),
        gtc_x=tuple(r[2] for r in lignes),
        gtc_y=tuple(r[3] for r in lignes),
    )


# --- Ajustement ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResultatAjustement:
    """Ce que donne un ajustement, avant de l'enregistrer."""

    noms: tuple[str, ...]
    valeurs: np.ndarray
    covariance: np.ndarray
    chi2_reduit: float | None
    methode: str
    avertissements: tuple[str, ...]


def _estimer(
    modele: str | None, x: np.ndarray, y: np.ndarray, noms: Sequence[str]
) -> dict[str, float]:
    """Propose un point de départ : un modèle non linéaire peut sinon ne pas converger."""
    depart = dict.fromkeys(noms, 1.0)
    etendue = float(x.max() - x.min()) or 1.0
    if modele in ("droite", "droite_origine", "polynome2", "polynome3"):
        degre = {"droite": 1, "droite_origine": 1, "polynome2": 2, "polynome3": 3}[
            modele
        ]
        coefficients = np.polyfit(x, y, min(degre, len(x) - 1))[::-1]
        for nom, c in zip("abcd" if modele != "droite" else "ba", coefficients):
            if nom in depart:
                depart[nom] = float(c)
        if modele == "droite_origine":
            depart["a"] = float(np.sum(x * y) / (np.sum(x * x) or 1.0))
    elif modele in ("exponentielle", "decroissance", "exponentielle_fond"):
        fond = float(y.min()) - 0.01 * abs(float(y.min()) or 1.0)
        reference = 0.0 if modele != "exponentielle_fond" else fond
        utiles = (y - reference) > 0
        if utiles.sum() >= 2:
            pente, origine = np.polyfit(x[utiles], np.log(y[utiles] - reference), 1)
        else:
            pente, origine = -1.0 / etendue, math.log(abs(float(y.max())) or 1.0)
        pente = pente or -1.0 / etendue
        depart["A"] = float(math.exp(origine))
        if "l" in depart:
            depart["l"] = float(-pente)
        if "T12" in depart:
            depart["T12"] = float(-math.log(2) / pente)
        if "C" in depart:
            depart["C"] = reference
    elif modele == "gaussienne":
        depart["A"] = float(y.max())
        depart["m"] = float(x[int(np.argmax(y))])
        depart["s"] = etendue / 4
    return depart


def _difference(
    fonction: Callable[[np.ndarray, Sequence[float]], np.ndarray],
    x: np.ndarray,
    valeurs: np.ndarray,
    rang: int,
    pas: float,
) -> np.ndarray:
    haut, bas = valeurs.copy(), valeurs.copy()
    haut[rang] += pas
    bas[rang] -= pas
    return (fonction(x, haut) - fonction(x, bas)) / (2 * pas)


def _jacobienne(
    fonction: Callable[[np.ndarray, Sequence[float]], np.ndarray],
    x: np.ndarray,
    valeurs: np.ndarray,
) -> np.ndarray:
    """Les dérivées de f par rapport à chaque coefficient, par différences centrées.

    Le pas vise une variation de f d'environ 1e-6 de sa valeur. Un pas relatif au seul
    coefficient serait noyé dans le bruit numérique pour un coefficient proche de zéro, et un
    pas absolu serait démesuré pour un taux de 1e-9 par seconde.
    """
    echelle = float(np.nanmax(np.abs(fonction(x, valeurs)))) or 1.0
    colonnes = []
    for rang, valeur in enumerate(valeurs):
        essai = _PAS_RELATIF * max(abs(valeur), 1e-8)
        pente_essai = np.nanmax(np.abs(_difference(fonction, x, valeurs, rang, essai)))
        pas = _PAS_RELATIF * abs(valeur)
        if pente_essai > 0:
            pas = max(pas, _PAS_RELATIF * echelle / pente_essai)
        pas = pas or essai
        colonnes.append(_difference(fonction, x, valeurs, rang, pas))
    return np.column_stack(colonnes)


def _pente(
    fonction: Callable[[np.ndarray, Sequence[float]], np.ndarray],
    x: np.ndarray,
    valeurs: np.ndarray,
) -> np.ndarray:
    pas = _PAS_RELATIF * max(float(np.max(np.abs(x))), 1e-8)
    return (fonction(x + pas, valeurs) - fonction(x - pas, valeurs)) / (2 * pas)


def _minimiser(
    fonction: Callable[[np.ndarray, Sequence[float]], np.ndarray],
    x: np.ndarray,
    y: np.ndarray,
    sigma: np.ndarray,
    depart: Sequence[float],
) -> np.ndarray:
    """Cherche les coefficients avec lmfit, long à charger : il ne s'importe qu'ici."""
    import lmfit  # noqa: PLC0415

    parametres = lmfit.Parameters()
    for rang, valeur in enumerate(depart):
        parametres.add(f"p{rang}", value=float(valeur))

    def residus(courants: "lmfit.Parameters") -> np.ndarray:
        valeurs = [courants[f"p{rang}"].value for rang in range(len(depart))]
        return np.nan_to_num(
            (y - fonction(x, valeurs)) / sigma, nan=1e30, posinf=1e30, neginf=-1e30
        )

    resultat = lmfit.minimize(residus, parametres, scale_covar=False)
    if not resultat.success:
        raise ErreurAjustement(
            "L'ajustement n'a pas convergé : changer le modèle ou ses valeurs de départ."
        )
    return np.array(
        [resultat.params[f"p{rang}"].value for rang in range(len(depart))], dtype=float
    )


def ajuster(
    points: Points,
    formule: str,
    modele: str | None = None,
    depart: Mapping[str, float] | None = None,
) -> ResultatAjustement:
    """Ajuste une formule sur des points. Lève `ErreurAjustement` en l'expliquant."""
    noms = coefficients_de(formule)
    x, y = points.x.valeurs, points.y.valeurs
    if len(x) <= len(noms):
        raise ErreurAjustement(
            f"{len(x)} point(s) pour {len(noms)} coefficient(s) : il faut plus de points "
            "que de coefficients."
        )
    if float(x.max() - x.min()) == 0:
        raise ErreurAjustement("Tous les points ont le même x : ajustement impossible.")
    fonction = creer_fonction(formule, noms)
    initiales = _estimer(modele, x, y, noms) | dict(depart or {})
    valeurs = np.array([initiales[nom] for nom in noms], dtype=float)

    ponderes = bool(np.any(points.u_y > 0) or np.any(points.u_x > 0))
    sigma = np.ones_like(y)
    try:
        for _passe in range(_PASSES_VARIANCE_EFFECTIVE if ponderes else 1):
            if ponderes:
                variance = (
                    points.u_y**2 + (_pente(fonction, x, valeurs) * points.u_x) ** 2
                )
                if np.any(variance <= 0) or not np.all(np.isfinite(variance)):
                    rangs = [str(i + 1) for i in np.flatnonzero(~(variance > 0))]
                    raise ErreurAjustement(
                        f"Aucune incertitude propre sur les points {', '.join(rangs)} : "
                        "ils pèseraient infiniment. Déclarer leur incertitude."
                    )
                sigma = np.sqrt(variance)
            valeurs = _minimiser(fonction, x, y, sigma, valeurs)
    except ErreurCalcul:
        raise
    except (ValueError, TypeError, ZeroDivisionError, OverflowError) as erreur:
        raise ErreurAjustement(f"Ajustement impossible : {erreur}") from erreur

    residus = (y - fonction(x, valeurs)) / sigma
    if not np.all(np.isfinite(residus)) or not np.all(np.isfinite(valeurs)):
        raise ErreurAjustement(
            "Ajustement impossible : le modèle n'est pas défini partout."
        )
    ddl = len(x) - len(noms)
    chi2 = float(np.sum(residus**2)) / ddl

    jacobienne = _jacobienne(fonction, x, valeurs)
    ponderee = jacobienne / sigma[:, None]
    normale = ponderee.T @ ponderee
    diagonale = np.sqrt(np.diag(normale))
    if np.any(diagonale == 0) or (
        np.linalg.cond(normale / np.outer(diagonale, diagonale)) > _CONDITIONNEMENT_MAX
    ):
        raise ErreurAjustement(
            "Des coefficients ne se distinguent pas avec ces points : simplifier le modèle."
        )
    try:
        inverse = np.linalg.inv(normale)
    except np.linalg.LinAlgError as erreur:
        raise ErreurAjustement(
            "Des coefficients ne se distinguent pas avec ces points : simplifier le modèle."
        ) from erreur
    if np.any(np.diag(inverse) < 0) or not np.all(np.isfinite(inverse)):
        raise ErreurAjustement(
            "Des coefficients ne se distinguent pas avec ces points : simplifier le modèle."
        )
    # Sans pondération, seule la dispersion des points renseigne sur leur incertitude.
    covariance = inverse if ponderes else inverse * chi2

    # Une erreur commune déplace tous les points ensemble : δθ = C · Jᵀ · W · δy.
    pente = _pente(fonction, x, valeurs)
    influence = inverse @ (ponderee / sigma[:, None]).T
    for uid in {*points.communs_x, *points.communs_y}:
        deplacement = points.communs_y.get(uid, np.zeros_like(y)) - pente * (
            points.communs_x.get(uid, np.zeros_like(x))
        )
        decalage = influence @ deplacement
        covariance = covariance + np.outer(decalage, decalage)

    avertissements: list[str] = []
    if ponderes and chi2 > _CHI2_TROP_GRAND:
        avertissements.append(
            f"χ² réduit = {chi2:.3g} : les écarts dépassent les incertitudes déclarées. "
            "Le modèle ne convient pas, ou les incertitudes sont sous-estimées."
        )
    return ResultatAjustement(
        tuple(noms),
        valeurs,
        covariance,
        chi2 if ponderes else None,
        _METHODE_PONDEREE if ponderes else _METHODE_NON_PONDEREE,
        tuple(avertissements),
    )


def correlation_de(covariance: np.ndarray) -> tuple[tuple[float, ...], ...]:
    """Passe de la covariance à la corrélation. Un coefficient exact n'est corrélé à rien."""
    u = np.sqrt(np.clip(np.diag(covariance), 0.0, None))
    taille = len(u)
    return tuple(
        tuple(
            1.0
            if i == j
            else (
                float(covariance[i, j] / (u[i] * u[j]))
                if u[i] > 0 and u[j] > 0
                else 0.0
            )
            for j in range(taille)
        )
        for i in range(taille)
    )


def incertitude_de_la_fonction(
    formule: str,
    noms: Sequence[str],
    valeurs: np.ndarray,
    covariance: np.ndarray,
    x: np.ndarray,
) -> np.ndarray:
    """u²(f) = gᵀ · C · g : l'incertitude de la courbe, covariances comprises."""
    gradient = _jacobienne(creer_fonction(formule, noms), x, valeurs)
    return np.sqrt(
        np.clip(np.sum((gradient @ covariance) * gradient, axis=1), 0.0, None)
    )


def evaluer_ajustement(
    ajustement: Ajustement, x: Sequence[float] | np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Réévalue la fonction et son incertitude-type, depuis ce qui est enregistré."""
    abscisses = np.asarray(x, dtype=float)
    noms = [p.nom for p in ajustement.parametres]
    valeurs = np.array([p.valeur for p in ajustement.parametres], dtype=float)
    covariance = np.array(ajustement.covariance(), dtype=float)
    y = creer_fonction(ajustement.formule, noms)(abscisses, valeurs)
    return y, incertitude_de_la_fonction(
        ajustement.formule, noms, valeurs, covariance, abscisses
    )


def creer_ajustement(
    points: Points,
    nom: str,
    formule: str,
    modele: str | None = None,
    depart: Mapping[str, float] | None = None,
    unites: Mapping[str, str] | None = None,
    commentaire: str = "",
) -> tuple[Ajustement, ResultatAjustement]:
    """Ajuste et prépare la grandeur à enregistrer. Les unités d'un modèle usuel sont déduites."""
    resultat = ajuster(points, formule, modele, depart)
    if modele in MODELES:
        unites_coefficients = unites_des_coefficients(
            MODELES[modele], points.x.unite, points.y.unite
        )
    else:
        unites_coefficients = dict(unites or {})
        for coefficient in resultat.noms:
            unite = unites_coefficients.get(coefficient, "").strip()
            if not unite:
                raise ErreurAjustement(
                    f"Indiquer l'unité du coefficient « {coefficient} » (1 si sans dimension)."
                )
            verification = verifier_unite(unite)
            if not verification.valide:
                raise ErreurAjustement(verification.message)
            unites_coefficients[coefficient] = unite
    u = np.sqrt(np.clip(np.diag(resultat.covariance), 0.0, None))
    ajustement = Ajustement(
        nom=nom,
        modele=MODELES[modele].libelle if modele in MODELES else "Personnalisé",
        formule=formule.strip(),
        tableau=points.tableau,
        x=points.x.nom,
        y=points.y.nom,
        unite_x=points.x.unite,
        unite_y=points.y.unite,
        x_min=float(points.x.valeurs.min()),
        x_max=float(points.x.valeurs.max()),
        points=len(points.x.valeurs),
        parametres=tuple(
            ParametreAjuste(
                nom=coefficient,
                valeur=float(valeur),
                incertitude_type=float(incertitude),
                unite=unites_coefficients[coefficient],
            )
            for coefficient, valeur, incertitude in zip(
                resultat.noms, resultat.valeurs, u
            )
        ),
        correlation=correlation_de(resultat.covariance),
        chi2_reduit=resultat.chi2_reduit,
        methode=resultat.methode,
        empreinte_donnees=points.empreinte,
        courbe=f"{nom}_courbe",
        commentaire=commentaire,
    )
    return ajustement, resultat


def creer_courbe(ajustement: Ajustement) -> tuple[Tableau, dict[str, list[float]]]:
    """Prépare le tableau qui retrace la fonction, sans jamais sortir des données.

    Il a plus de points que les données, et il sert seulement au tracé : tout calcul ultérieur
    repart des coefficients.
    """
    from medphystools.incertitudes import composante_exacte  # noqa: PLC0415
    from medphystools.page import ColonneIncertitude  # noqa: PLC0415

    nombre = max(POINTS_COURBE_MIN, _SUR_ECHANTILLONNAGE * ajustement.points)
    x = np.linspace(ajustement.x_min, ajustement.x_max, nombre)
    y, u = evaluer_ajustement(ajustement, x)
    tableau = Tableau(
        nom=ajustement.courbe,
        fichier=f"brut/{ajustement.courbe}.csv",
        colonnes=(
            ColonneNumerique(
                nom="x",
                unite=ajustement.unite_x,
                composantes=(
                    composante_exacte(ajustement.unite_x, "abscisse choisie"),
                ),
            ),
            ColonneNumerique(nom="y", unite=ajustement.unite_y),
            ColonneIncertitude(
                nom="u_y",
                composante_de="y",
                type="B",
                unite=ajustement.unite_y,
                source=f"ajustement {ajustement.nom}",
            ),
        ),
        commentaire=f"Courbe de l'ajustement {ajustement.nom}, pour la retracer.",
    )
    return tableau, {"x": x.tolist(), "y": y.tolist(), "u_y": u.tolist()}


# --- Indicateurs --------------------------------------------------------------------------


def _unite_composee(unite_y: str, unite_x: str, puissance_x: int) -> tuple[float, str]:
    """L'unité d'une intégrale (y·x) ou d'une dérivée (y/x), et le facteur de simplification."""
    facteur, unite = _reduire(
        registre.Unit(unite_y) * registre.Unit(unite_x) ** puissance_x
    )
    return facteur, _ecrire_unite(unite)


def _percentile(ys: Sequence[object], rang: float) -> object:
    """Interpolation linéaire entre deux valeurs triées, comme numpy par défaut.

    L'ordre des points est tenu pour fixé : l'incertitude se propage au premier ordre.
    """
    tries = sorted(ys, key=lambda y: y.x)
    position = rang / 100 * (len(tries) - 1)
    bas = math.floor(position)
    haut = min(bas + 1, len(tries) - 1)
    return tries[bas] + (position - bas) * (tries[haut] - tries[bas])


def _indicateur_sur_points(
    points: Points,
    methode: str,
    de: float,
    a: float,
    position: float | None,
    parametre: float | None,
) -> tuple[float, float, str, float | None, float | None, str | None]:
    """Calcule sur les valeurs incertaines : les erreurs communes restent ainsi partagées.

    Renvoie la valeur, u, l'unité, puis l'abscisse du résultat, son u et son unité.
    """
    import GTC  # noqa: PLC0415

    gardes = [
        i for i, valeur in enumerate(points.x.valeurs) if de <= float(valeur) <= a
    ]
    if not gardes:
        raise ErreurAjustement(f"Aucun point entre {de:g} et {a:g}.")
    xs = [points.gtc_x[i] for i in gardes]
    ys = [points.gtc_y[i] for i in gardes]
    unite_x, unite_y = points.x.unite, points.y.unite

    def resultat(valeur: object, unite: str = unite_y) -> tuple:
        return valeur.x, GTC.uncertainty(valeur), unite, None, None, None

    if methode == "nombre_points":
        return float(len(ys)), 0.0, "1", None, None, None
    if methode in ("minimum", "maximum"):
        choix = min if methode == "minimum" else max
        rang = choix(range(len(ys)), key=lambda i: ys[i].x)
        return (
            ys[rang].x,
            GTC.uncertainty(ys[rang]),
            unite_y,
            xs[rang].x,
            GTC.uncertainty(xs[rang]),
            unite_x,
        )
    if methode == "moyenne":
        return resultat(sum(ys[1:], ys[0]) / len(ys))
    if methode == "somme":
        return resultat(sum(ys[1:], ys[0]))
    if methode == "mediane":
        return resultat(_percentile(ys, 50.0))
    if methode == "percentile":
        if parametre is None or not 0 <= parametre <= 100:
            raise ErreurAjustement("Indiquer le rang du percentile, entre 0 et 100.")
        return resultat(_percentile(ys, parametre))
    if methode == "etendue":
        return resultat(_percentile(ys, 100.0) - _percentile(ys, 0.0))
    if methode == "moyenne_quadratique":
        carres = [y * y for y in ys]
        return resultat(GTC.sqrt(sum(carres[1:], carres[0]) / len(ys)))
    if methode in ("ecart_type", "coefficient_variation"):
        if len(ys) < 2:
            raise ErreurAjustement("Il faut au moins deux points pour un écart type.")
        moyenne = sum(ys[1:], ys[0]) / len(ys)
        carres = [(y - moyenne) * (y - moyenne) for y in ys]
        total = sum(carres[1:], carres[0])
        if total.x == 0:
            raise ErreurAjustement(
                "Tous les points sont égaux : l'écart type vaut 0, et son incertitude "
                "n'est pas définie au premier ordre."
            )
        ecart = GTC.sqrt(total / (len(ys) - 1))
        if methode == "ecart_type":
            return resultat(ecart)
        if moyenne.x == 0:
            raise ErreurAjustement(
                "La moyenne est nulle : coefficient de variation impossible."
            )
        return resultat(100 * ecart / moyenne * math.copysign(1.0, moyenne.x), "%")
    if len(ys) < 2:
        raise ErreurAjustement(
            f"Il faut au moins deux points pour une {methode} sur cet intervalle."
        )
    if methode == "integrale":
        facteur, unite = _unite_composee(unite_y, unite_x, 1)
        total = (ys[0] + ys[1]) / 2 * (xs[1] - xs[0])
        for i in range(1, len(ys) - 1):
            total = total + (ys[i] + ys[i + 1]) / 2 * (xs[i + 1] - xs[i])
        return resultat(total * facteur, unite)
    # Dérivée : différence centrée autour du point le plus proche de la position demandée.
    if position is None:
        raise ErreurAjustement("Indiquer l'abscisse où calculer la dérivée.")
    proche = min(range(len(xs)), key=lambda i: abs(xs[i].x - position))
    avant, apres = max(proche - 1, 0), min(proche + 1, len(xs) - 1)
    if xs[apres].x == xs[avant].x:
        raise ErreurAjustement("Deux points ont le même x : dérivée impossible.")
    facteur, unite = _unite_composee(unite_y, unite_x, -1)
    pente = (ys[apres] - ys[avant]) / (xs[apres] - xs[avant]) * facteur
    return (
        pente.x,
        GTC.uncertainty(pente),
        unite,
        xs[proche].x,
        GTC.uncertainty(xs[proche]),
        unite_x,
    )


def _extremum(
    fonction: Callable[[np.ndarray], np.ndarray], de: float, a: float, maximum: bool
) -> float:
    """Cherche l'abscisse d'un extremum : d'abord sur une grille, puis finement."""
    from scipy.optimize import minimize_scalar  # noqa: PLC0415

    signe = -1.0 if maximum else 1.0
    grille = np.linspace(de, a, _POINTS_RECHERCHE)
    valeurs = signe * fonction(grille)
    rang = int(np.nanargmin(valeurs))
    bas = grille[max(rang - 1, 0)]
    haut = grille[min(rang + 1, len(grille) - 1)]
    fine = minimize_scalar(
        lambda t: float(signe * fonction(np.array([t]))[0]),
        bounds=(bas, haut),
        method="bounded",
        options={"xatol": 1e-10 * max(a - de, 1e-30)},
    )
    return float(fine.x) if fine.fun <= valeurs[rang] else float(grille[rang])


def _croisement(
    fonction: Callable[[np.ndarray], np.ndarray], cible: float, de: float, a: float
) -> float:
    """La première abscisse de l'intervalle où la fonction atteint une valeur."""
    from scipy.optimize import brentq  # noqa: PLC0415

    grille = np.linspace(de, a, _POINTS_RECHERCHE)
    ecarts = fonction(grille) - cible
    zero = np.flatnonzero(ecarts == 0)
    if zero.size:
        return float(grille[zero[0]])
    changements = np.flatnonzero(np.sign(ecarts[:-1]) * np.sign(ecarts[1:]) < 0)
    if not changements.size:
        raise ErreurAjustement(
            f"La fonction n'atteint pas {cible:g} entre {de:g} et {a:g}."
        )
    rang = int(changements[0])
    return float(
        brentq(
            lambda t: float(fonction(np.array([t]))[0]) - cible,
            grille[rang],
            grille[rang + 1],
        )
    )


def _largeur_mi_hauteur(
    fonction: Callable[[np.ndarray], np.ndarray], de: float, a: float
) -> tuple[float, float]:
    """La largeur à mi-hauteur du pic le plus haut, comptée depuis y = 0, et son abscisse."""
    sommet = _extremum(fonction, de, a, True)
    moitie = float(fonction(np.array([sommet]))[0]) / 2
    if sommet in (de, a):
        raise ErreurAjustement(
            "Le maximum est au bord : pas de pic dans cet intervalle."
        )
    gauche = _croisement(lambda t: fonction(t[::-1])[::-1], moitie, de, sommet)
    droite = _croisement(fonction, moitie, sommet, a)
    return droite - gauche, sommet


def _indicateur_sur_ajustement(
    ajustement: Ajustement,
    methode: str,
    de: float,
    a: float,
    position: float | None,
    parametre: float | None,
) -> tuple[float, float, str, float | None, float | None, str | None]:
    """Calcule sur la fonction ajustée. u²(I) = gᵀ · C · g, avec g = dI/dθ, numérique.

    Renvoie la valeur, u, l'unité, puis l'abscisse du résultat, son u et son unité.
    """
    from scipy.integrate import quad  # noqa: PLC0415

    if methode not in METHODES_AJUSTEMENT:
        raise ErreurAjustement(
            f"« {methode} » se calcule sur les points, pas sur la fonction ajustée."
        )
    if de < ajustement.x_min or a > ajustement.x_max:
        raise ErreurAjustement(
            f"L'intervalle sort des données ({ajustement.x_min:g} à {ajustement.x_max:g}) : "
            "pas d'extrapolation."
        )
    noms = [p.nom for p in ajustement.parametres]
    valeurs = np.array([p.valeur for p in ajustement.parametres], dtype=float)
    covariance = np.array(ajustement.covariance(), dtype=float)
    formule = creer_fonction(ajustement.formule, noms)
    unite = ajustement.unite_y
    facteur = 1.0
    calcul_position: Callable[[np.ndarray], float] | None = None

    if methode == "derivee":
        if position is None:
            raise ErreurAjustement("Indiquer l'abscisse où calculer la dérivée.")
        if not ajustement.x_min <= position <= ajustement.x_max:
            raise ErreurAjustement(
                f"L'abscisse {position:g} sort des données : pas d'extrapolation."
            )
        facteur, unite = _unite_composee(ajustement.unite_y, ajustement.unite_x, -1)
        pas = 1e-5 * max(ajustement.x_max - ajustement.x_min, 1e-30)

        def calcul(p: np.ndarray) -> float:
            haut = formule(np.array([position + pas]), p)[0]
            bas = formule(np.array([position - pas]), p)[0]
            return float((haut - bas) / (2 * pas))

    elif methode in ("integrale", "moyenne"):
        if methode == "integrale":
            facteur, unite = _unite_composee(ajustement.unite_y, ajustement.unite_x, 1)
        largeur = a - de if methode == "moyenne" else 1.0
        if largeur <= 0:
            raise ErreurAjustement("L'intervalle est vide.")

        def calcul(p: np.ndarray) -> float:
            total, _erreur = quad(
                lambda t: float(formule(np.array([t]), p)[0]), de, a, limit=200
            )
            return float(total / largeur)

    elif methode == "largeur_mi_hauteur":
        unite = ajustement.unite_x

        def calcul(p: np.ndarray) -> float:
            return _largeur_mi_hauteur(lambda z: formule(z, p), de, a)[0]

        def calcul_position(p: np.ndarray) -> float:
            return _largeur_mi_hauteur(lambda z: formule(z, p), de, a)[1]

    elif methode == "abscisse_de_valeur":
        if parametre is None:
            raise ErreurAjustement("Indiquer la valeur de y à atteindre.")
        unite = ajustement.unite_x

        def calcul(p: np.ndarray) -> float:
            return _croisement(lambda z: formule(z, p), parametre, de, a)

    else:
        maximum = methode == "maximum"

        def calcul_position(p: np.ndarray) -> float:
            return _extremum(lambda z: formule(z, p), de, a, maximum)

        def calcul(p: np.ndarray) -> float:
            return float(formule(np.array([calcul_position(p)]), p)[0])

    def propager(fonction: Callable[[np.ndarray], float]) -> float:
        """Le pas suit l'incertitude du coefficient : voir `_jacobienne`."""
        gradient = np.zeros(len(valeurs))
        for rang, v in enumerate(valeurs):
            pas_p = max(
                _FRACTION_DE_U * math.sqrt(max(covariance[rang, rang], 0.0)),
                _PAS_RELATIF * abs(v),
                _PAS_MINIMAL,
            )
            haut_p, bas_p = valeurs.copy(), valeurs.copy()
            haut_p[rang] += pas_p
            bas_p[rang] -= pas_p
            gradient[rang] = (fonction(haut_p) - fonction(bas_p)) / (2 * pas_p)
        return math.sqrt(max(float(gradient @ covariance @ gradient), 0.0))

    valeur = calcul(valeurs)
    u = propager(calcul)
    abscisse: float | None = None
    u_abscisse: float | None = None
    unite_abscisse: str | None = None
    if calcul_position is not None:
        abscisse = calcul_position(valeurs)
        u_abscisse = propager(calcul_position)
        unite_abscisse = ajustement.unite_x
    elif methode == "derivee":
        abscisse, u_abscisse, unite_abscisse = position, 0.0, ajustement.unite_x
    return (
        valeur * facteur,
        u * abs(facteur),
        unite,
        abscisse,
        u_abscisse,
        unite_abscisse,
    )


def creer_indicateur(
    page: Page,
    nom: str,
    methode: str,
    source: str,
    tableau: str,
    x: str,
    y: str,
    lire_tableau: Callable[[str], pd.DataFrame],
    de: float | None = None,
    a: float | None = None,
    position: float | None = None,
    ajustement: Ajustement | None = None,
    commentaire: str = "",
    parametre: float | None = None,
) -> Indicateur:
    """Calcule un indicateur et prépare la grandeur à enregistrer.

    Sans `de` ni `a`, tout l'intervalle des données est utilisé. Lève `ErreurAjustement`.
    """
    if methode not in METHODES:
        raise ErreurAjustement(f"Indicateur « {methode} » inconnu.")
    points = lire_points(page, tableau, x, y, lire_tableau)
    debut = float(points.x.valeurs.min()) if de is None else float(de)
    fin = float(points.x.valeurs.max()) if a is None else float(a)
    if fin < debut:
        raise ErreurAjustement("L'intervalle est à l'envers : de doit précéder à.")
    try:
        if source == "ajustement":
            if ajustement is None:
                raise ErreurAjustement("Faire d'abord l'ajustement de cette courbe.")
            valeur, u, unite, abscisse, u_abscisse, unite_abscisse = (
                _indicateur_sur_ajustement(
                    ajustement, methode, debut, fin, position, parametre
                )
            )
            empreinte = ajustement.empreinte_donnees
        else:
            if methode not in METHODES_POINTS:
                raise ErreurAjustement(
                    f"« {methode} » se calcule sur la fonction ajustée, pas sur les points."
                )
            valeur, u, unite, abscisse, u_abscisse, unite_abscisse = (
                _indicateur_sur_points(points, methode, debut, fin, position, parametre)
            )
            empreinte = points.empreinte
    except ErreurCalcul:
        raise
    except (ValueError, ZeroDivisionError, OverflowError, UniteInvalide) as erreur:
        raise ErreurAjustement(f"Indicateur impossible : {erreur}") from erreur
    if not math.isfinite(valeur) or not math.isfinite(u):
        raise ErreurAjustement("Indicateur impossible : le résultat n'est pas défini.")
    return Indicateur(
        nom=nom,
        methode=methode,
        source=source,
        tableau=tableau,
        x=x,
        y=y,
        ajustement=ajustement.nom if source == "ajustement" else None,
        de=debut,
        a=fin,
        parametre=parametre,
        position=abscisse,
        incertitude_position=u_abscisse,
        unite_position=unite_abscisse,
        valeur=float(valeur),
        incertitude_type=float(u),
        unite=unite,
        empreinte_donnees=empreinte,
        commentaire=commentaire,
    )


def etat_resultat(
    page: Page,
    resultat: Ajustement | Indicateur,
    lire_tableau: Callable[[str], pd.DataFrame],
) -> str | None:
    """Dit si les points ont changé depuis ce résultat ; None s'il est toujours à jour."""
    if isinstance(resultat, Indicateur) and resultat.ajustement is not None:
        source = next(
            (
                g
                for g in page.mesures
                if isinstance(g, Ajustement) and g.nom == resultat.ajustement
            ),
            None,
        )
        if source is None:
            return f"À refaire : l'ajustement « {resultat.ajustement} » a été retiré."
        if source.empreinte_donnees != resultat.empreinte_donnees:
            return "À refaire : l'ajustement a changé depuis ce résultat."
    try:
        points = lire_points(
            page, resultat.tableau, resultat.x, resultat.y, lire_tableau
        )
    except ErreurCalcul as erreur:
        return f"Impossible à vérifier : {erreur}"
    if points.empreinte == resultat.empreinte_donnees:
        return None
    return "À refaire : les points ont changé depuis ce résultat."
