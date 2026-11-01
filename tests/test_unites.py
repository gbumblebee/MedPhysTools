"""Vérifie le catalogue, la syntaxe des unités et les conversions."""

import metrolopy as uc
import pytest

from pymphub.unites import (
    CATALOGUE,
    UniteInvalide,
    UnitesIncompatibles,
    convertir,
    registre,
    sont_compatibles,
    verifier_unite,
)

# MetroloPy ne connaît pas ces unités : on les définit comme le ferait le logiciel
# s'il l'adoptait plus tard. Ces définitions ne servent qu'aux tests.
uc.PrefixedUnit("curie", "Ci", conversion=uc.Conversion("Bq", 3.7e10), add_symbol=True)
uc.PrefixedUnit("coup par seconde", "cps", add_symbol=True)
uc.Unit("hounsfield", "HU", add_symbol=True)
uc.Unit("paire de lignes", "lp", add_symbol=True)


EXPRESSIONS_VALIDES = [
    "MBq",
    "kBq/mL",
    "kg/m**3",
    "g/cm**3",
    "mA*s",
    "mA s",
    "mGy/(mA*s)",
    "cGy/min",
    "1/s",
    "lp/mm",
    "1",
    "%",
]


@pytest.mark.parametrize("unite", sorted(CATALOGUE))
def test_le_catalogue_est_compris_par_pint_et_metrolopy(unite):
    assert verifier_unite(unite).valide
    registre.Quantity(2.0, unite)
    uc.gummy(2.0, unit=unite)


@pytest.mark.parametrize("unite", EXPRESSIONS_VALIDES)
def test_une_expression_valide_est_comprise_par_pint_et_metrolopy(unite):
    assert verifier_unite(unite).valide
    registre.Quantity(2.0, unite)
    uc.gummy(2.0, unit=unite)


@pytest.mark.parametrize(
    ("depuis", "vers"),
    [
        ("MBq", "mCi"),
        ("GBq", "Ci"),
        ("mGy", "cGy"),
        ("mSv", "Sv"),
        ("mm", "cm"),
        ("h", "s"),
        ("mL", "L"),
        ("mbar", "hPa"),
        ("mmHg", "hPa"),
        ("deg", "rad"),
        ("degC", "K"),
        ("kBq/mL", "MBq/L"),
        ("kg/m**3", "g/cm**3"),
    ],
)
def test_pint_et_metrolopy_donnent_la_meme_conversion(depuis, vers):
    attendu = uc.gummy(2.0, unit=depuis).convert(vers).x
    assert convertir(2.0, depuis, vers) == pytest.approx(attendu, rel=1e-6)


def test_conversion_mbq_mci():
    assert convertir(37.0, "MBq", "mCi") == pytest.approx(1.0, rel=1e-12)
    assert convertir(1.0, "mCi", "MBq") == pytest.approx(37.0, rel=1e-12)


def test_conversion_avec_decalage():
    assert convertir(20.0, "degC", "K") == pytest.approx(293.15)
    assert convertir(20.0, "°C", "K") == pytest.approx(293.15)


@pytest.mark.parametrize(
    ("depuis", "vers"),
    [
        ("mm", "MBq"),
        ("Gy", "Sv"),
        ("mGy", "mSv"),
        ("Bq", "cps"),
        ("MBq", "kcps"),
        ("HU", "1"),
        ("HU", "mm"),
    ],
)
def test_une_conversion_sans_sens_physique_est_refusee(depuis, vers):
    with pytest.raises(UnitesIncompatibles):
        convertir(1.0, depuis, vers)
    assert not sont_compatibles(depuis, vers)


@pytest.mark.parametrize(
    ("depuis", "vers"),
    [("MBq", "mCi"), ("mSv", "Sv"), ("kcps", "cps"), ("lp/mm", "lp/cm"), ("1", "%")],
)
def test_des_unites_de_meme_nature_sont_compatibles(depuis, vers):
    assert sont_compatibles(depuis, vers)


@pytest.mark.parametrize(
    ("saisie", "suggestion"),
    [
        ("Mbq", "MBq"),
        ("MBQ", "MBq"),
        ("mbq", "MBq"),
        ("Bq/ml", "Bq/mL"),
        ("mci", "mCi"),
        ("g/cm^3", "g/cm**3"),
        ("g/cm3", "g/cm**3"),
        ("kg/m^3", "kg/m**3"),
        ("µm", "um"),
        ("μm", "um"),
        ("mAs", "mA*s"),
        ("mm Hg", "mmHg"),
        ("jour", "d"),
        (" MBq ", "MBq"),
    ],
)
def test_une_erreur_de_syntaxe_recoit_une_suggestion_valide(saisie, suggestion):
    verification = verifier_unite(saisie)
    assert not verification.valide
    assert verification.suggestion == suggestion
    assert suggestion in verification.message
    assert verifier_unite(suggestion).valide


@pytest.mark.parametrize(
    "saisie", ["pixel", "SUV", "m^^3", "MBq/", "(MBq", "MBq)", "/s", "2MBq"]
)
def test_une_unite_inconnue_est_refusee_sans_suggestion_invalide(saisie):
    verification = verifier_unite(saisie)
    assert not verification.valide
    assert verification.message
    if verification.suggestion is not None:
        assert verifier_unite(verification.suggestion).valide


@pytest.mark.parametrize("saisie", ["", "   "])
def test_une_unite_vide_est_refusee(saisie):
    verification = verifier_unite(saisie)
    assert not verification.valide
    assert verification.suggestion == "1"


@pytest.mark.parametrize("unite", ["Mbq", ""])
def test_convertir_refuse_une_unite_invalide_sans_la_corriger(unite):
    with pytest.raises(UniteInvalide):
        convertir(1.0, unite, "MBq")
    with pytest.raises(UniteInvalide):
        convertir(1.0, "MBq", unite)
