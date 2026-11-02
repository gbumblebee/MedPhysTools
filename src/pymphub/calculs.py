"""Calculs sur les grandeurs d'une page : `A0 * exp(-ln(2) * t / T12)`.

La formule n'est jamais exécutée par Python : elle est découpée en arbre, puis seuls les
nombres, les noms de grandeurs, les quatre opérations, la puissance et quelques fonctions
sont interprétés. Tout le reste est refusé. Elle ne peut donc pas exécuter de code.

La propagation est confiée à GTC. Dans un calcul, chaque grandeur est un seul objet, et les
composantes de même repère d'erreur commune partagent une seule erreur.

Un nom `tableau.colonne` sans numéro de ligne fait de la formule un calcul de colonne : elle
s'évalue ligne par ligne, et chaque ligne donne une valeur et son incertitude.
"""

import ast
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import GTC
import pandas as pd
import pint
from GTC.lib import UncertainReal
from GTC.reporting import budget as budget_gtc

from pymphub.incertitudes import (
    Budget,
    Composante,
    ErreurCommuneIncoherente,
    InfluencesCommunes,
    LigneBudget,
    etablir_budget,
)
from pymphub.page import (
    Ajustement,
    ColonneCalculee,
    ColonneIncertitude,
    ColonneNumerique,
    GrandeurDerivee,
    Indicateur,
    LigneDerivee,
    Page,
    Scalaire,
    Tableau,
)
from pymphub.unites import (
    CATALOGUE,
    UniteInvalide,
    convertir,
    registre,
    sont_compatibles,
    verifier_unite,
)

LONGUEUR_MAX_FORMULE = 500
_TOLERANCE_RELATIVE = 1e-9
_SANS_DIMENSION = registre.Unit("dimensionless")


class ErreurCalcul(ValueError):
    """La formule ne peut pas être calculée : le message explique pourquoi."""


class FormuleRefusee(ErreurCalcul):
    """La formule contient autre chose que ce qu'un calcul autorise."""


@dataclass(frozen=True)
class _Valeur:
    """Une valeur en cours de calcul, avec son incertitude et son unité."""

    x: UncertainReal
    unite: pint.Unit


FONCTIONS: dict[str, Callable[[UncertainReal], UncertainReal]] = {
    "exp": GTC.exp,
    "ln": GTC.log,
    "log10": GTC.log10,
    "sin": GTC.sin,
    "cos": GTC.cos,
    "tan": GTC.tan,
}
CONSTANTES: dict[str, float] = {"pi": math.pi}

# Le symbole du catalogue pour chaque unité de pint : le résultat doit rester saisissable.
_SYMBOLES: dict[str, str] = {
    next(iter(registre.Unit(symbole)._units)): symbole
    for symbole in sorted(CATALOGUE, reverse=True)
    if len(registre.Unit(symbole)._units) == 1
}


def _exacte(valeur: float) -> UncertainReal:
    return GTC.ureal(valeur, 0.0)


def _reduire(unite: pint.Unit) -> tuple[float, pint.Unit]:
    """Simplifie une unité : `h/min` donne 60, et `MBq/kBq` donne 1000.

    Seules des unités de même dimension se regroupent. La réduction de pint irait plus
    loin : `MBq*h` deviendrait 3,6e9 sans unité, ce que personne ne reconnaîtrait.
    """
    groupes: dict[object, str] = {}
    exposants: dict[str, float] = {}
    facteur = 1.0
    for nom, exposant in unite._units.items():
        unite_seule = registre.Unit(nom)
        dimension = unite_seule.dimensionality
        reference = groupes.setdefault(dimension, nom)
        if reference != nom:
            rapport = registre.Quantity(1.0, unite_seule).to(reference).magnitude
            facteur *= rapport**exposant
        exposants[reference] = exposants.get(reference, 0.0) + exposant
    resultat = _SANS_DIMENSION
    for nom, exposant in exposants.items():
        if exposant != 0:
            resultat = resultat * registre.Unit(nom) ** exposant
    return facteur, resultat


def _ecrire_unite(unite: pint.Unit) -> str:
    """Écrit une unité dans la syntaxe du catalogue : `MBq/mL`, `mm**2`, `1/h`."""
    haut: list[str] = []
    bas: list[str] = []
    for nom, exposant in unite._units.items():
        symbole = _SYMBOLES.get(nom)
        if symbole is None or exposant != round(exposant):
            raise ErreurCalcul(
                "Le résultat n'a pas d'unité utilisable : reformuler le calcul, ou "
                "rendre sans dimension ce qui est sous la racine."
            )
        puissance = abs(int(exposant))
        terme = symbole if puissance == 1 else f"{symbole}**{puissance}"
        (haut if exposant > 0 else bas).append(terme)
    texte = "*".join(haut) or "1"
    for terme in bas:
        texte += f"/{terme}"
    return texte


def _a_un_decalage(unite: str) -> bool:
    return sont_compatibles(unite, "K") and convertir(0.0, unite, "K") != 0.0


class ContexteCalcul:
    """Les grandeurs d'une page pour un calcul, chacune créée une seule fois."""

    def __init__(
        self, page: Page, lire_tableau: Callable[[str], pd.DataFrame] | None = None
    ) -> None:
        self.page = page
        self.lire_tableau = lire_tableau
        self.communes = InfluencesCommunes()
        self.descriptions: dict[str, tuple[str, str]] = {}
        self._valeurs: dict[str, _Valeur] = {}
        self._tableaux: dict[str, pd.DataFrame] = {}
        self._en_cours: list[str] = []
        # Numéro de la ligne en cours, à partir de 1, pour un calcul de colonne.
        self.ligne: int | None = None
        self._coefficients: dict[str, dict[str, _Valeur]] = {}

    def donnees_tableau(self, tableau: str) -> pd.DataFrame:
        """Relit un tableau une seule fois par calcul."""
        if self.lire_tableau is None:
            raise ErreurCalcul(f"Le tableau « {tableau} » n'est pas disponible ici.")
        if tableau not in self._tableaux:
            self._tableaux[tableau] = self.lire_tableau(tableau)
        return self._tableaux[tableau]

    def _noter(self, composantes: tuple[Composante, ...], etiquette: str) -> None:
        for composante in composantes:
            cle = composante.erreur_commune or f"{etiquette}{composante.source}"
            self.descriptions.setdefault(cle, (composante.type, composante.loi))

    def _depuis_budget(
        self,
        valeur: float,
        unite: str,
        composantes: tuple[Composante, ...],
        etiquette: str,
    ) -> _Valeur:
        """Une température avec décalage passe en kelvins : T1 / T2 n'a de sens qu'ainsi."""
        self._noter(composantes, etiquette)
        budget = etablir_budget(valeur, unite, composantes, self.communes, etiquette)
        if _a_un_decalage(unite):
            budget = budget.en("K")
        return _Valeur(budget.gtc, registre.Unit(budget.unite))

    def grandeur(self, nom: str) -> _Valeur:
        """Renvoie une grandeur de la page, toujours le même objet dans un calcul."""
        if nom in self._valeurs:
            return self._valeurs[nom]
        if nom in self._en_cours:
            raise ErreurCalcul(f"Le calcul de « {nom} » dépend de lui-même.")
        trouvee = next((g for g in self.page.mesures if g.nom == nom), None)
        if trouvee is None:
            raise ErreurCalcul(
                f"Aucune grandeur « {nom} » enregistrée dans cette page."
            )
        if isinstance(trouvee, Scalaire):
            valeur = self._depuis_budget(
                trouvee.valeur, trouvee.unite, trouvee.composantes, f"{nom} : "
            )
        elif isinstance(trouvee, GrandeurDerivee):
            # Refaire son calcul garde ses corrélations avec les autres grandeurs.
            ligne, self.ligne = self.ligne, None
            self._en_cours.append(nom)
            try:
                valeur = _evaluer(_analyser(trouvee.formule), self)
            finally:
                self._en_cours.pop()
                self.ligne = ligne
        elif isinstance(trouvee, Indicateur):
            valeur = _Valeur(
                GTC.ureal(trouvee.valeur, trouvee.incertitude_type, label=nom),
                registre.Unit(trouvee.unite),
            )
            self.descriptions[nom] = ("", trouvee.methode)
        elif isinstance(trouvee, ColonneCalculee):
            raise ErreurCalcul(
                f"« {nom} » est une colonne : l'écrire {trouvee.tableaux[0]}.{nom}."
            )
        else:
            raise ErreurCalcul(
                f"« {nom} » n'est pas une valeur numérique : un calcul ne peut pas "
                "l'utiliser."
            )
        self._valeurs[nom] = valeur
        return valeur

    def cellule(self, tableau: str, colonne: str, ligne: int) -> _Valeur:
        """Renvoie une cellule de tableau, numérotée à partir de 1 comme dans la grille."""
        cle = f"{tableau}.{colonne}[{ligne}]"
        if cle in self._valeurs:
            return self._valeurs[cle]
        description = next(
            (
                g
                for g in self.page.mesures
                if isinstance(g, Tableau) and g.nom == tableau
            ),
            None,
        )
        if description is None:
            raise ErreurCalcul(
                f"Aucun tableau « {tableau} » enregistré dans cette page."
            )
        calculee = _colonne_calculee(self.page, tableau, colonne)
        if calculee is not None:
            valeur = self._ligne_de_colonne_calculee(calculee, ligne)
            self._valeurs[cle] = valeur
            return valeur
        cible = next((c for c in description.colonnes if c.nom == colonne), None)
        if not isinstance(cible, ColonneNumerique):
            raise ErreurCalcul(
                f"Aucune colonne numérique « {colonne} » dans « {tableau} »."
            )
        donnees = self.donnees_tableau(tableau)
        if not 1 <= ligne <= len(donnees):
            raise ErreurCalcul(
                f"« {tableau} » a {len(donnees)} ligne(s) : pas de ligne {ligne}."
            )
        rang = ligne - 1
        composantes = tuple(cible.composantes) + tuple(
            c.composante(float(donnees[c.nom].iloc[rang]))
            for c in description.colonnes
            if isinstance(c, ColonneIncertitude) and c.composante_de == colonne
        )
        valeur = self._depuis_budget(
            float(donnees[colonne].iloc[rang]), cible.unite, composantes, f"{cle} : "
        )
        self._valeurs[cle] = valeur
        return valeur

    def abscisse_indicateur(self, indicateur: Indicateur, nom: str) -> _Valeur:
        """Renvoie l'abscisse d'un indicateur : `pic.x` donne la position du maximum."""
        if nom != "x" or indicateur.position is None:
            raise ErreurCalcul(
                f"L'indicateur « {indicateur.nom} » n'a pas d'abscisse « {nom} »."
            )
        cle = f"{indicateur.nom}.x"
        if cle not in self._valeurs:
            self._valeurs[cle] = _Valeur(
                GTC.ureal(
                    indicateur.position,
                    indicateur.incertitude_position or 0.0,
                    label=cle,
                ),
                registre.Unit(indicateur.unite_position or "1"),
            )
            self.descriptions[cle] = ("", indicateur.methode)
        return self._valeurs[cle]

    def coefficient(self, ajustement: Ajustement, nom: str) -> _Valeur:
        """Renvoie un coefficient, corrélé aux autres coefficients du même ajustement."""
        if ajustement.nom not in self._coefficients:
            valeurs = [
                GTC.ureal(
                    p.valeur,
                    p.incertitude_type,
                    independent=False,
                    label=f"{ajustement.nom}.{p.nom}",
                )
                for p in ajustement.parametres
            ]
            for i, ligne in enumerate(ajustement.correlation):
                for j in range(i + 1, len(valeurs)):
                    if (
                        ligne[j] != 0
                        and ajustement.parametres[i].incertitude_type > 0
                        and ajustement.parametres[j].incertitude_type > 0
                    ):
                        GTC.set_correlation(ligne[j], valeurs[i], valeurs[j])
            self._coefficients[ajustement.nom] = {
                p.nom: _Valeur(v, registre.Unit(p.unite))
                for p, v in zip(ajustement.parametres, valeurs)
            }
        coefficients = self._coefficients[ajustement.nom]
        if nom not in coefficients:
            raise ErreurCalcul(
                f"L'ajustement « {ajustement.nom} » n'a pas de coefficient « {nom} »."
            )
        return coefficients[nom]

    def _ligne_de_colonne_calculee(
        self, colonne: ColonneCalculee, ligne: int
    ) -> _Valeur:
        """Refait le calcul d'une ligne : ses corrélations restent ainsi connues."""
        if colonne.nom in self._en_cours:
            raise ErreurCalcul(f"Le calcul de « {colonne.nom} » dépend de lui-même.")
        ligne_en_cours, self.ligne = self.ligne, ligne
        self._en_cours.append(colonne.nom)
        try:
            return _evaluer(_analyser(colonne.formule), self)
        finally:
            self._en_cours.pop()
            self.ligne = ligne_en_cours


def _colonne_calculee(page: Page, tableau: str, nom: str) -> ColonneCalculee | None:
    """Renvoie la colonne calculée de ce nom affichée dans ce tableau, s'il y en a une."""
    for grandeur in page.mesures:
        if (
            isinstance(grandeur, ColonneCalculee)
            and grandeur.nom == nom
            and tableau in grandeur.tableaux
        ):
            return grandeur
    return None


def _indicateur_nomme(page: Page, nom: str) -> Indicateur | None:
    for grandeur in page.mesures:
        if isinstance(grandeur, Indicateur) and grandeur.nom == nom:
            return grandeur
    return None


def _ajustement_nomme(page: Page, nom: str) -> Ajustement | None:
    for grandeur in page.mesures:
        if isinstance(grandeur, Ajustement) and grandeur.nom == nom:
            return grandeur
    return None


def _analyser(formule: str) -> ast.Expression:
    """Découpe la formule sans l'exécuter. Lève `FormuleRefusee` si elle est mal écrite."""
    if not formule.strip():
        raise FormuleRefusee("La formule est vide.")
    if len(formule) > LONGUEUR_MAX_FORMULE:
        raise FormuleRefusee(
            f"Formule trop longue : {LONGUEUR_MAX_FORMULE} caractères au plus."
        )
    try:
        return ast.parse(formule.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError) as erreur:
        raise FormuleRefusee(f"Formule mal écrite : « {formule} ».") from erreur


def _vers_sans_dimension(valeur: _Valeur, usage: str) -> UncertainReal:
    """Ramène une valeur à un simple nombre : `t / T12` avec des heures et des minutes."""
    quantite = registre.Quantity(1.0, valeur.unite)
    if not quantite.dimensionless:
        raise ErreurCalcul(
            f"{usage} attend une grandeur sans dimension, pas des "
            f"{_ecrire_unite(_reduire(valeur.unite)[1])}."
        )
    return valeur.x * quantite.to(_SANS_DIMENSION).magnitude


def _additionner(a: _Valeur, b: _Valeur, signe: float) -> _Valeur:
    try:
        facteur = registre.Quantity(1.0, b.unite).to(a.unite).magnitude
    except pint.DimensionalityError as erreur:
        raise ErreurCalcul(
            f"Addition ou soustraction impossible : {_ecrire_unite(_reduire(a.unite)[1])} "
            f"et {_ecrire_unite(_reduire(b.unite)[1])} ne sont pas compatibles."
        ) from erreur
    return _Valeur(a.x + signe * facteur * b.x, a.unite)


def _exiger_reel(valeur: UncertainReal) -> UncertainReal:
    """GTC passe en complexes pour la racine d'un négatif : ce n'est jamais voulu ici."""
    if not isinstance(valeur, UncertainReal):
        raise ErreurCalcul(
            "Calcul impossible : le résultat n'est pas un nombre réel, par exemple la "
            "racine d'une valeur négative."
        )
    return valeur


def _puissance(base: _Valeur, exposant: _Valeur) -> _Valeur:
    puissance = _vers_sans_dimension(exposant, "Un exposant")
    if registre.Quantity(1.0, base.unite).dimensionless:
        nombre = _vers_sans_dimension(base, "Une puissance")
        return _Valeur(_exiger_reel(nombre**puissance), _SANS_DIMENSION)
    if GTC.uncertainty(puissance) != 0:
        raise ErreurCalcul(
            "Une grandeur avec une unité ne s'élève qu'à une puissance exacte."
        )
    return _Valeur(
        _exiger_reel(base.x ** GTC.value(puissance)),
        base.unite ** GTC.value(puissance),
    )


def _operation(noeud: ast.BinOp, contexte: ContexteCalcul) -> _Valeur:
    if isinstance(noeud.op, ast.BitXor):
        raise FormuleRefusee("Pour une puissance, écrire ** au lieu de ^.")
    gauche = _evaluer(noeud.left, contexte)
    droite = _evaluer(noeud.right, contexte)
    if isinstance(noeud.op, ast.Add):
        return _additionner(gauche, droite, 1.0)
    if isinstance(noeud.op, ast.Sub):
        return _additionner(gauche, droite, -1.0)
    if isinstance(noeud.op, ast.Mult):
        return _Valeur(gauche.x * droite.x, gauche.unite * droite.unite)
    if isinstance(noeud.op, ast.Div):
        return _Valeur(gauche.x / droite.x, gauche.unite / droite.unite)
    if isinstance(noeud.op, ast.Pow):
        return _puissance(gauche, droite)
    raise FormuleRefusee(
        "Seules les opérations + - * / et ** sont autorisées dans une formule."
    )


def _appel(noeud: ast.Call, contexte: ContexteCalcul) -> _Valeur:
    nom = noeud.func.id if isinstance(noeud.func, ast.Name) else None
    if (
        (nom not in FONCTIONS and nom != "sqrt")
        or noeud.keywords
        or len(noeud.args) != 1
    ):
        permises = ", ".join(sorted((*FONCTIONS, "sqrt")))
        raise FormuleRefusee(
            f"Seules ces fonctions à un argument sont autorisées : {permises}."
        )
    if isinstance(noeud.args[0], ast.Starred):
        raise FormuleRefusee("Cette écriture n'est pas autorisée dans une formule.")
    argument = _evaluer(noeud.args[0], contexte)
    if nom == "sqrt":
        # La racine garde son unité : sqrt(a * a) est en MBq.
        return _puissance(argument, _Valeur(_exacte(0.5), _SANS_DIMENSION))
    x = _vers_sans_dimension(argument, f"La fonction {nom}")
    return _Valeur(FONCTIONS[nom](x), _SANS_DIMENSION)


def _nom_de_cellule(noeud: ast.Subscript) -> tuple[str, str, int]:
    """Lit `tableau.colonne[3]`, seule forme permise avec un point ou des crochets."""
    colonne = noeud.value
    indice = noeud.slice
    if (
        not isinstance(colonne, ast.Attribute)
        or not isinstance(colonne.value, ast.Name)
        or not isinstance(indice, ast.Constant)
        or type(indice.value) is not int
    ):
        raise FormuleRefusee(
            "Une cellule de tableau s'écrit tableau.colonne[numéro de ligne]."
        )
    return colonne.value.id, colonne.attr, indice.value


def _evaluer(noeud: ast.AST, contexte: ContexteCalcul) -> _Valeur:
    """Parcourt l'arbre de la formule. Tout ce qui n'est pas prévu ici est refusé."""
    if isinstance(noeud, ast.Expression):
        return _evaluer(noeud.body, contexte)
    if isinstance(noeud, ast.Constant):
        if type(noeud.value) not in (int, float):
            raise FormuleRefusee("Une formule ne contient que des nombres et des noms.")
        # Tout passe en réels : `9**9**9` déborde aussitôt, sans saturer la mémoire.
        nombre = float(noeud.value)
        if not math.isfinite(nombre):
            raise FormuleRefusee("Ce nombre est trop grand.")
        return _Valeur(_exacte(nombre), _SANS_DIMENSION)
    if isinstance(noeud, ast.Name):
        if noeud.id in CONSTANTES and not any(
            g.nom == noeud.id for g in contexte.page.mesures
        ):
            return _Valeur(_exacte(CONSTANTES[noeud.id]), _SANS_DIMENSION)
        return contexte.grandeur(noeud.id)
    if isinstance(noeud, ast.BinOp):
        return _operation(noeud, contexte)
    if isinstance(noeud, ast.UnaryOp) and isinstance(noeud.op, (ast.USub, ast.UAdd)):
        valeur = _evaluer(noeud.operand, contexte)
        return (
            valeur
            if isinstance(noeud.op, ast.UAdd)
            else _Valeur(-valeur.x, valeur.unite)
        )
    if isinstance(noeud, ast.Call):
        return _appel(noeud, contexte)
    if isinstance(noeud, ast.Subscript):
        return contexte.cellule(*_nom_de_cellule(noeud))
    if isinstance(noeud, ast.Attribute):
        if not isinstance(noeud.value, ast.Name):
            raise FormuleRefusee("Une colonne s'écrit tableau.colonne.")
        ajustement = _ajustement_nomme(contexte.page, noeud.value.id)
        if ajustement is not None:
            return contexte.coefficient(ajustement, noeud.attr)
        indicateur = _indicateur_nomme(contexte.page, noeud.value.id)
        if indicateur is not None:
            return contexte.abscisse_indicateur(indicateur, noeud.attr)
        if contexte.ligne is None:
            raise ErreurCalcul(
                f"« {noeud.value.id}.{noeud.attr} » est une colonne entière : ce calcul "
                "donne une colonne, et non une seule valeur."
            )
        return contexte.cellule(noeud.value.id, noeud.attr, contexte.ligne)
    if isinstance(noeud, ast.Tuple):
        raise FormuleRefusee(
            "Un nombre décimal s'écrit avec un point : 1.5 et non 1,5."
        )
    raise FormuleRefusee(
        "Cette écriture n'est pas autorisée dans une formule : nombres, noms de "
        "grandeurs, + - * / ** et fonctions usuelles seulement."
    )


def _lignes_du_budget(
    resultat: UncertainReal, contexte: ContexteCalcul
) -> tuple[LigneBudget, ...]:
    """Décompose l'incertitude du résultat : une erreur compensée y figure à 0."""
    influences = list(budget_gtc(resultat, trim=0))
    variance = sum(i.u**2 for i in influences)
    lignes = []
    for influence in influences:
        type_composante, loi = contexte.descriptions.get(influence.label, ("", ""))
        lignes.append(
            LigneBudget(
                source=influence.label,
                type=type_composante,
                loi=loi,
                incertitude_type=abs(influence.u),
                ddl=None,
                contribution_pourcent=100 * influence.u**2 / variance
                if variance > 0
                else 0.0,
            )
        )
    return tuple(lignes)


def _terminer(
    arbre: ast.Expression, contexte: ContexteCalcul, unite: str
) -> tuple[UncertainReal, str]:
    """Évalue un arbre et donne le résultat dans son unité. Toute erreur est expliquée."""
    try:
        valeur = _evaluer(arbre, contexte)
        facteur, reduite = _reduire(valeur.unite)
        texte = _ecrire_unite(reduite)
        if unite.strip():
            verification = verifier_unite(unite.strip())
            if not verification.valide:
                raise ErreurCalcul(verification.message)
            if _a_un_decalage(unite.strip()):
                raise ErreurCalcul(
                    "Un résultat de calcul reste en K : choisir K pour une température."
                )
            facteur *= registre.Quantity(1.0, reduite).to(unite.strip()).magnitude
            texte = unite.strip()
        resultat = valeur.x * facteur
    except ErreurCalcul:
        raise
    except pint.DimensionalityError as erreur:
        raise ErreurCalcul(
            f"Ce résultat ne peut pas s'exprimer en « {unite.strip()} »."
        ) from erreur
    except (ZeroDivisionError, OverflowError) as erreur:
        raise ErreurCalcul(
            "Calcul impossible : division par zéro ou nombre trop grand."
        ) from erreur
    except RecursionError as erreur:
        raise FormuleRefusee("Formule trop imbriquée.") from erreur
    except (ErreurCommuneIncoherente, UniteInvalide, OSError, KeyError) as erreur:
        raise ErreurCalcul(str(erreur)) from erreur
    except ValueError as erreur:
        raise ErreurCalcul(
            f"Calcul impossible hors du domaine d'une fonction : {erreur}."
        ) from erreur
    if not isinstance(resultat, UncertainReal) or not math.isfinite(resultat.x):
        raise ErreurCalcul(
            "Calcul impossible : le résultat n'est pas un nombre réel, par exemple la "
            "racine d'une valeur négative."
        )
    return resultat, texte


def _references_colonnes(noeud: ast.AST, page: Page) -> list[tuple[str, str]]:
    """Les `tableau.colonne` d'une formule, sans ceux suivis d'un numéro de ligne.

    `ajustement.coefficient` s'écrit pareil, mais désigne une seule valeur.
    """
    if isinstance(noeud, ast.Subscript):
        return []
    if isinstance(noeud, ast.Attribute) and isinstance(noeud.value, ast.Name):
        if (
            _ajustement_nomme(page, noeud.value.id) is not None
            or _indicateur_nomme(page, noeud.value.id) is not None
        ):
            return []
        return [(noeud.value.id, noeud.attr)]
    return [
        reference
        for enfant in ast.iter_child_nodes(noeud)
        for reference in _references_colonnes(enfant, page)
    ]


def est_calcul_de_colonne(page: Page, formule: str) -> bool:
    """Indique si une formule porte sur des colonnes entières."""
    return bool(_references_colonnes(_analyser(formule), page))


def calculer(
    page: Page,
    formule: str,
    lire_tableau: Callable[[str], pd.DataFrame] | None = None,
    unite: str = "",
) -> Budget:
    """Calcule une formule sur les grandeurs d'une page et renvoie valeur, unité et budget.

    Lève `ErreurCalcul` en l'expliquant. `unite` demande une unité de résultat. Un résultat
    en température reste en kelvins : un écart de T et une température s'y confondraient
    sinon, et le décalage de 273,15 ne s'applique qu'à la seconde.
    """
    contexte = ContexteCalcul(page, lire_tableau)
    resultat, texte = _terminer(_analyser(formule), contexte, unite)
    return Budget(
        valeur=resultat.x,
        unite=texte,
        lignes=_lignes_du_budget(resultat, contexte),
        gtc=resultat,
    )


def creer_derivee(
    page: Page,
    nom: str,
    formule: str,
    lire_tableau: Callable[[str], pd.DataFrame] | None = None,
    unite: str = "",
    commentaire: str = "",
) -> GrandeurDerivee:
    """Calcule une formule et renvoie la grandeur à enregistrer dans la page."""
    budget = calculer(page, formule, lire_tableau, unite)
    ddl = budget.ddl
    return GrandeurDerivee(
        nom=nom,
        formule=formule.strip(),
        valeur=budget.valeur,
        unite=budget.unite,
        incertitude_type=budget.incertitude_type,
        ddl=None if math.isinf(ddl) or math.isnan(ddl) else ddl,
        budget_calcul=tuple(
            LigneDerivee(
                source=ligne.source,
                type=ligne.type,
                loi=ligne.loi,
                incertitude_type=ligne.incertitude_type,
                contribution_pourcent=ligne.contribution_pourcent,
            )
            for ligne in budget.lignes
        ),
        commentaire=commentaire,
    )


def _tableaux_participants(
    page: Page, references: Sequence[tuple[str, str]]
) -> tuple[str, ...]:
    """Les tableaux où la colonne s'affiche, y compris via une autre colonne calculée."""
    tableaux: dict[str, None] = {}
    for tableau, colonne in references:
        tableaux[tableau] = None
        calculee = _colonne_calculee(page, tableau, colonne)
        if calculee is not None:
            tableaux.update(dict.fromkeys(calculee.tableaux))
    return tuple(tableaux)


def creer_colonne(
    page: Page,
    nom: str,
    formule: str,
    lire_tableau: Callable[[str], pd.DataFrame] | None = None,
    unite: str = "",
    commentaire: str = "",
) -> ColonneCalculee:
    """Calcule une formule ligne par ligne et renvoie la colonne à enregistrer.

    Tous les tableaux concernés doivent avoir le même nombre de lignes. Une ligne impossible,
    comme une division par zéro, reste sans résultat et n'empêche pas les autres. Si aucune
    ligne n'aboutit, l'erreur est levée : c'est alors la formule qu'il faut revoir.
    """
    arbre = _analyser(formule)
    references = _references_colonnes(arbre, page)
    if not references:
        raise ErreurCalcul("Cette formule ne porte sur aucune colonne de tableau.")
    tableaux = _tableaux_participants(page, references)
    contexte = ContexteCalcul(page, lire_tableau)
    longueurs: dict[str, int] = {}
    for tableau in tableaux:
        description = next(
            (g for g in page.mesures if isinstance(g, Tableau) and g.nom == tableau),
            None,
        )
        if description is None:
            raise ErreurCalcul(
                f"Aucun tableau « {tableau} » enregistré dans cette page."
            )
        if any(c.nom == nom for c in description.colonnes):
            raise ErreurCalcul(
                f"Le tableau « {tableau} » a déjà une colonne « {nom} »."
            )
        try:
            longueurs[tableau] = len(contexte.donnees_tableau(tableau))
        except (OSError, KeyError, ValueError) as erreur:
            if isinstance(erreur, ErreurCalcul):
                raise
            raise ErreurCalcul(str(erreur)) from erreur
    if len(set(longueurs.values())) > 1:
        detail = ", ".join(f"{t} : {n}" for t, n in longueurs.items())
        raise ErreurCalcul(
            f"Ces tableaux n'ont pas le même nombre de lignes ({detail}) : un calcul "
            "ligne par ligne est impossible."
        )
    nombre = next(iter(longueurs.values()))
    if nombre == 0:
        raise ErreurCalcul("Ces tableaux n'ont pas encore de données.")

    valeurs: list[float | None] = []
    incertitudes: list[float | None] = []
    unite_resultat = ""
    premiere_erreur: ErreurCalcul | None = None
    for ligne in range(1, nombre + 1):
        contexte.ligne = ligne
        try:
            resultat, unite_resultat = _terminer(arbre, contexte, unite)
        except FormuleRefusee:
            raise
        except ErreurCalcul as erreur:
            premiere_erreur = premiere_erreur or erreur
            valeurs.append(None)
            incertitudes.append(None)
            continue
        valeurs.append(resultat.x)
        incertitudes.append(GTC.uncertainty(resultat))
    if not unite_resultat:
        raise premiere_erreur
    return ColonneCalculee(
        nom=nom,
        formule=formule.strip(),
        unite=unite_resultat,
        tableaux=tableaux,
        valeurs=tuple(valeurs),
        incertitudes_types=tuple(incertitudes),
        commentaire=commentaire,
    )


def budget_de_ligne(
    page: Page,
    colonne: ColonneCalculee,
    ligne: int,
    lire_tableau: Callable[[str], pd.DataFrame] | None = None,
) -> Budget:
    """Refait le budget d'une ligne d'une colonne calculée, numérotée à partir de 1."""
    contexte = ContexteCalcul(page, lire_tableau)
    contexte.ligne = ligne
    resultat, texte = _terminer(_analyser(colonne.formule), contexte, colonne.unite)
    return Budget(
        valeur=resultat.x,
        unite=texte,
        lignes=_lignes_du_budget(resultat, contexte),
        gtc=resultat,
    )


def creer_resultat(
    page: Page,
    nom: str,
    formule: str,
    lire_tableau: Callable[[str], pd.DataFrame] | None = None,
    unite: str = "",
    commentaire: str = "",
) -> GrandeurDerivee | ColonneCalculee:
    """Crée le résultat d'une formule : une colonne si elle vise des colonnes entières."""
    creer = creer_colonne if est_calcul_de_colonne(page, formule) else creer_derivee
    return creer(page, nom, formule, lire_tableau, unite, commentaire)


def _proches(a: float | None, b: float | None, echelle: float) -> bool:
    if a is None or b is None:
        return a is b
    return math.isclose(a, b, rel_tol=_TOLERANCE_RELATIVE, abs_tol=1e-12 * abs(echelle))


def meme_resultat(
    a: GrandeurDerivee | ColonneCalculee, b: GrandeurDerivee | ColonneCalculee
) -> bool:
    """Compare deux résultats, sans s'arrêter à un écart d'arrondi entre deux versions."""
    if type(a) is not type(b):
        return False
    if isinstance(a, ColonneCalculee):
        return (
            a.tableaux == b.tableaux
            and len(a.valeurs) == len(b.valeurs)
            and all(_proches(x, y, x or 0.0) for x, y in zip(a.valeurs, b.valeurs))
            and all(
                _proches(u, w, v or 0.0)
                for u, w, v in zip(
                    a.incertitudes_types, b.incertitudes_types, a.valeurs
                )
            )
        )
    return _proches(a.valeur, b.valeur, 0.0) and _proches(
        a.incertitude_type, b.incertitude_type, a.valeur
    )


def etat_derivee(
    page: Page,
    derivee: GrandeurDerivee | ColonneCalculee,
    lire_tableau: Callable[[str], pd.DataFrame] | None = None,
) -> str | None:
    """Dit pourquoi un résultat enregistré n'est plus à jour ; None s'il l'est toujours."""
    try:
        recalculee = creer_resultat(
            page, derivee.nom, derivee.formule, lire_tableau, derivee.unite
        )
    except ErreurCalcul as erreur:
        return f"Calcul impossible : {erreur}"
    if meme_resultat(derivee, recalculee):
        return None
    if isinstance(recalculee, ColonneCalculee):
        return "Colonne à recalculer : ses grandeurs ont changé."
    return (
        f"Résultat à recalculer : ses grandeurs ont changé, il vaudrait "
        f"{recalculee.valeur:.6g} {recalculee.unite}."
    )
