"""Unités : catalogue, vérification de la syntaxe et conversions.

Les unités s'écrivent dans la syntaxe de MetroloPy (`MBq`, `kBq/mL`, `kg/m**3`,
`mA*s`, `um`), mais les calculs passent par pint. Le catalogue ci-dessous limite
la saisie à ce que les deux bibliothèques comprennent de la même façon.

Pour ajouter une unité, il suffit de compléter le catalogue. Si elle est inconnue
de pint ou de MetroloPy, il faut aussi la définir dans `registre` et dans
`tests/test_unites.py`.
"""

import difflib
import re
from dataclasses import dataclass

import pint

# Unités acceptant un préfixe : symbole → préfixes autorisés.
_UNITES_A_PREFIXE: dict[str, tuple[str, ...]] = {
    "Bq": ("k", "M", "G", "T"),
    "Ci": ("u", "m"),
    "Gy": ("u", "m", "c"),
    "Sv": ("u", "m"),
    "cps": ("k",),
    "g": ("u", "m", "k"),
    "m": ("n", "u", "m", "c", "k"),
    "s": ("n", "u", "m"),
    "L": ("u", "m", "d"),
    "A": ("p", "n", "u", "m"),
    "C": ("p", "n", "u", "m"),
    "V": ("u", "m", "k"),
    "Pa": ("h", "k", "M"),
    "bar": ("m",),
    "Hz": ("k", "M", "G"),
    "J": ("m", "k"),
    "W": ("m", "k"),
    "N": ("k",),
    "eV": ("k", "M"),
}

# Unités sans préfixe. L'unité « 1 » désigne une grandeur sans dimension.
_UNITES_SIMPLES: tuple[str, ...] = (
    "1",
    "%",
    "min",
    "h",
    "d",
    "K",
    "degC",
    "°C",
    "deg",
    "rad",
    "mol",
    "mmHg",
    "HU",
    "lp",
)

CATALOGUE: frozenset[str] = frozenset(
    {
        prefixe + symbole
        for symbole, prefixes in _UNITES_A_PREFIXE.items()
        for prefixe in ("", *prefixes)
    }
    | set(_UNITES_SIMPLES)
)

# Écritures courantes qu'une simple correction de casse ne peut pas retrouver.
_ALIAS: dict[str, str] = {
    "mAs": "mA*s",
    "mm Hg": "mmHg",
    "jour": "d",
    "heure": "h",
    "sec": "s",
}

_TERME = r"(?:[A-Za-z%°]+(?:\*\*-?\d+)?|1)"
_EXPRESSION = re.compile(rf"{_TERME}(?:\s*[*/]\s*{_TERME}|\s+{_TERME})*")
_SYMBOLE = re.compile(r"[A-Za-z%°]+")

# Registre unique du logiciel. Doses en sievert, coups par seconde, HU et paires de lignes
# reçoivent leur propre dimension : pint accepterait sinon Gy → Sv et Bq → cps,
# deux conversions sans sens physique malgré des dimensions compatibles.
registre = pint.UnitRegistry(on_redefinition="ignore")
registre.define("dose_efficace_ou_equivalente = [dose_equivalente] = Sv")
registre.define("coup_par_seconde = [taux_de_comptage] = cps")
registre.define("hounsfield = [nombre_ct] = HU")
registre.define("paire_de_lignes = [paire_de_lignes] = lp")


class UniteInvalide(ValueError):
    """L'unité saisie ne respecte pas la syntaxe ou n'est pas au catalogue."""


class UnitesIncompatibles(ValueError):
    """Les deux unités ne mesurent pas la même grandeur physique."""


@dataclass(frozen=True)
class VerificationUnite:
    """Résultat de la vérification d'une unité saisie."""

    valide: bool
    message: str = ""
    suggestion: str | None = None


def _est_valide(texte: str) -> bool:
    """Indique si le texte est une expression correcte d'unités du catalogue."""
    profondeur = 0
    for caractere in texte:
        profondeur += {"(": 1, ")": -1}.get(caractere, 0)
        if profondeur < 0:
            return False
    if profondeur != 0:
        return False

    sans_parentheses = texte.replace("(", "").replace(")", "")
    if not _EXPRESSION.fullmatch(sans_parentheses):
        return False
    return all(symbole in CATALOGUE for symbole in _SYMBOLE.findall(texte))


def _corriger_symbole(symbole: str) -> str | None:
    """Renvoie le symbole du catalogue le plus proche, ou None s'il n'y en a pas."""
    if symbole in CATALOGUE:
        return symbole
    if symbole in _ALIAS:
        return _ALIAS[symbole]

    meme_casse = [u for u in CATALOGUE if u.lower() == symbole.lower()]
    candidats = meme_casse or sorted(CATALOGUE)
    seuil = 0.0 if meme_casse else 0.75
    proches = difflib.get_close_matches(symbole, candidats, n=1, cutoff=seuil)
    return proches[0] if proches else None


def _suggerer(texte: str) -> str | None:
    """Cherche une écriture valide proche du texte saisi."""
    candidat = texte.strip()
    if candidat in _ALIAS:
        return _ALIAS[candidat]

    candidat = candidat.replace("^", "**").replace("µ", "u").replace("μ", "u")
    candidat = re.sub(r"([A-Za-z])(-?\d+)", r"\1**\2", candidat)

    symboles_inconnus = False

    def remplacer(correspondance: re.Match[str]) -> str:
        nonlocal symboles_inconnus
        correction = _corriger_symbole(correspondance.group())
        if correction is None:
            symboles_inconnus = True
            return correspondance.group()
        return correction

    candidat = _SYMBOLE.sub(remplacer, candidat)
    if symboles_inconnus or not _est_valide(candidat):
        return None
    return candidat


def verifier_unite(texte: str) -> VerificationUnite:
    """Vérifie une unité saisie, sans jamais la modifier.

    Une suggestion éventuelle est seulement proposée : c'est à l'utilisateur de
    l'accepter. Cette fonction est prévue pour être appelée à chaque frappe.
    """
    if not texte.strip():
        return VerificationUnite(
            valide=False,
            message="L'unité est obligatoire : indiquer 1 pour une grandeur sans dimension.",
            suggestion="1",
        )
    if _est_valide(texte):
        return VerificationUnite(valide=True)

    suggestion = _suggerer(texte)
    if suggestion is not None:
        message = (
            f"Unité « {texte} » non reconnue : vouliez-vous dire « {suggestion} » ?"
        )
    else:
        message = f"Unité « {texte} » non reconnue et absente du catalogue."
    return VerificationUnite(valide=False, message=message, suggestion=suggestion)


def _exiger_valide(texte: str) -> None:
    """Lève UniteInvalide si l'unité n'est pas valide."""
    verification = verifier_unite(texte)
    if not verification.valide:
        raise UniteInvalide(verification.message)


def sont_compatibles(unite_a: str, unite_b: str) -> bool:
    """Indique si deux unités mesurent la même grandeur physique."""
    _exiger_valide(unite_a)
    _exiger_valide(unite_b)
    return registre.Quantity(1.0, unite_a).check(
        registre.Quantity(1.0, unite_b).dimensionality
    )


def convertir(valeur: float, depuis: str, vers: str) -> float:
    """Convertit une valeur d'une unité à une autre et renvoie la valeur convertie."""
    _exiger_valide(depuis)
    _exiger_valide(vers)
    try:
        return float(registre.Quantity(valeur, depuis).to(vers).magnitude)
    except pint.DimensionalityError as erreur:
        raise UnitesIncompatibles(
            f"Conversion impossible de « {depuis} » en « {vers} »."
        ) from erreur


def convertir_ecart(ecart: float, depuis: str, vers: str) -> float:
    """Convertit un écart, comme une incertitude, sans appliquer de décalage.

    Un écart de 0,5 °C vaut 0,5 K, alors qu'une température de 0,5 °C vaut 273,65 K.
    """
    return convertir(ecart, depuis, vers) - convertir(0.0, depuis, vers)
