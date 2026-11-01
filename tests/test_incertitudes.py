"""Vérifie les composantes, le budget et l'incertitude élargie."""

import math

import pytest
from GTC import uncertainty
from pydantic import ValidationError

from pymphub.incertitudes import (
    Composante,
    composante_exacte,
    etablir_budget,
    repetitions,
)
from pymphub.unites import UniteInvalide, UnitesIncompatibles, convertir_ecart


@pytest.fixture
def budget_activite():
    """Budget calculé à la main : voir test_budget_calcule_a_la_main."""
    return etablir_budget(
        100.0,
        "MBq",
        [
            Composante(type="A", valeur=0.3, unite="MBq", ddl=9, source="répétabilité"),
            Composante(
                type="B",
                valeur=0.52,
                unite="MBq",
                loi="rectangulaire",
                source="résolution",
            ),
            Composante(type="B", valeur=1.0, unite="MBq", k=2.0, source="certificat"),
        ],
    )


def test_budget_calcule_a_la_main(budget_activite):
    # u² = 0,3² + 0,52²/3 + (1/2)² = 0,09 + 0,090133 + 0,25 = 0,430133
    assert budget_activite.incertitude_type == pytest.approx(0.655845, abs=1e-6)
    # Welch-Satterthwaite : seule la composante de type A a des ddl finis.
    # ν = u⁴ / (0,3⁴ / 9) = 0,185015 / 0,0009
    assert budget_activite.ddl == pytest.approx(205.57, abs=0.01)
    assert budget_activite.valeur == 100.0


def test_incertitude_elargie_absolue_et_relative(budget_activite):
    assert budget_activite.elargie() == pytest.approx(1.31169, abs=1e-5)
    assert budget_activite.elargie_relative() == pytest.approx(1.31169, abs=1e-5)


def test_k_change_l_affichage_mais_pas_les_donnees(budget_activite):
    u_avant = budget_activite.incertitude_type
    assert budget_activite.elargie(k=3) == pytest.approx(3 * u_avant)
    assert budget_activite.elargie(k=1) == pytest.approx(u_avant)
    assert budget_activite.incertitude_type == u_avant
    assert budget_activite.elargie() == pytest.approx(2 * u_avant)


@pytest.mark.parametrize("k", [0, -1])
def test_un_k_non_positif_est_refuse(budget_activite, k):
    with pytest.raises(ValueError):
        budget_activite.elargie(k=k)


def test_lignes_du_budget(budget_activite):
    lignes = budget_activite.lignes
    assert [ligne.source for ligne in lignes] == [
        "répétabilité",
        "résolution",
        "certificat",
    ]
    assert lignes[1].incertitude_type == pytest.approx(0.52 / math.sqrt(3))
    assert lignes[2].incertitude_type == pytest.approx(0.5)
    assert sum(ligne.contribution_pourcent for ligne in lignes) == pytest.approx(100)
    assert lignes[2].contribution_pourcent == pytest.approx(
        100 * 0.25 / 0.430133, abs=1e-3
    )


@pytest.mark.parametrize(
    ("loi", "attendu"),
    [
        ("rectangulaire", 1 / math.sqrt(3)),
        ("triangulaire", 1 / math.sqrt(6)),
        ("normale", 1.0),
    ],
)
def test_lois(loi, attendu):
    budget = etablir_budget(
        10.0, "mm", [Composante(type="B", valeur=1.0, unite="mm", loi=loi, source="s")]
    )
    assert budget.incertitude_type == pytest.approx(attendu)


def test_composante_relative():
    composante = Composante(type="B", valeur=2.0, relative=True, source="linéarité")
    assert etablir_budget(-50.0, "MBq", [composante]).incertitude_type == pytest.approx(
        1.0
    )


def test_composante_relative_sur_valeur_nulle_refusee():
    composante = Composante(type="B", valeur=2.0, relative=True, source="linéarité")
    with pytest.raises(ValueError):
        etablir_budget(0.0, "MBq", [composante])


def test_composante_dans_une_autre_unite():
    composante = Composante(type="B", valeur=1.0, unite="mm", source="règle")
    assert etablir_budget(10.0, "cm", [composante]).incertitude_type == pytest.approx(
        0.1
    )


def test_composante_d_une_autre_nature_refusee():
    composante = Composante(type="B", valeur=1.0, unite="mm", source="règle")
    with pytest.raises(UnitesIncompatibles):
        etablir_budget(10.0, "MBq", [composante])


def test_incertitude_obligatoire():
    with pytest.raises(ValueError):
        etablir_budget(10.0, "MBq", [])


def test_unite_de_mesure_obligatoire():
    composante = Composante(type="B", valeur=1.0, relative=True, source="s")
    with pytest.raises(UniteInvalide):
        etablir_budget(10.0, "", [composante])


def test_valeur_exacte():
    budget = etablir_budget(40.0, "1", [composante_exacte("1", "nombre de coupes")])
    assert budget.incertitude_type == 0
    assert budget.elargie() == 0
    assert budget.ddl == math.inf
    assert budget.lignes[0].contribution_pourcent == 0


def test_valeur_nulle_sans_incertitude_relative_affichable():
    budget = etablir_budget(
        0.0, "mm", [Composante(type="B", valeur=1.0, unite="mm", source="s")]
    )
    assert budget.elargie_relative() is None


@pytest.mark.parametrize(
    "champs",
    [
        {"type": "B", "valeur": 1.0, "source": "sans unité"},
        {
            "type": "B",
            "valeur": 1.0,
            "unite": "mm",
            "relative": True,
            "source": "les deux",
        },
        {"type": "B", "valeur": 1.0, "unite": "mm"},
        {"type": "B", "valeur": 1.0, "unite": "mm", "source": ""},
        {"type": "B", "valeur": -1.0, "unite": "mm", "source": "négative"},
        {"type": "B", "valeur": math.nan, "unite": "mm", "source": "nan"},
        {"type": "B", "valeur": 1.0, "unite": "mm", "ddl": 0, "source": "ddl nul"},
        {"type": "A", "valeur": 1.0, "unite": "mm", "source": "type A sans ddl"},
        {
            "type": "A",
            "valeur": 1.0,
            "unite": "mm",
            "ddl": 4,
            "loi": "rectangulaire",
            "source": "s",
        },
        {
            "type": "B",
            "valeur": 1.0,
            "unite": "mm",
            "loi": "triangulaire",
            "k": 2,
            "source": "s",
        },
        {"type": "C", "valeur": 1.0, "unite": "mm", "source": "type inconnu"},
    ],
)
def test_composante_incoherente_refusee(champs):
    with pytest.raises(ValidationError):
        Composante(**champs)


def test_unite_de_composante_invalide_refusee_avec_suggestion():
    with pytest.raises(ValidationError, match="MBq"):
        Composante(type="B", valeur=1.0, unite="Mbq", source="s")


def test_repetitions_donnent_moyenne_et_type_a():
    moyenne, composante = repetitions([10.0, 12.0, 14.0, 16.0], "MBq")
    # s = √(20/3) = 2,58199 ; s / √4 = 1,29099
    assert moyenne == pytest.approx(13.0)
    assert composante.type == "A"
    assert composante.valeur == pytest.approx(1.29099, abs=1e-5)
    assert composante.ddl == 3
    assert composante.unite == "MBq"
    assert "n = 4" in composante.source


def test_repetitions_identiques_donnent_une_composante_nulle():
    moyenne, composante = repetitions([5.0, 5.0, 5.0], "mm")
    assert moyenne == 5.0
    assert composante.valeur == 0
    assert composante.ddl == 2


@pytest.mark.parametrize("valeurs", [[], [1.0]])
def test_repetitions_insuffisantes_refusees(valeurs):
    with pytest.raises(ValueError):
        repetitions(valeurs, "mm")


def test_repetitions_se_combinent_avec_le_type_b():
    moyenne, type_a = repetitions([10.0, 12.0, 14.0, 16.0], "MBq")
    type_b = Composante(type="B", valeur=1.0, unite="MBq", source="certificat")
    budget = etablir_budget(moyenne, "MBq", [type_a, type_b])
    assert budget.incertitude_type == pytest.approx(math.sqrt(20 / 12 + 1))


def test_conversion_d_un_ecart_avec_decalage():
    assert convertir_ecart(0.5, "degC", "K") == pytest.approx(0.5)
    assert convertir_ecart(1.0, "cm", "mm") == pytest.approx(10.0)


def test_une_conversion_avec_decalage_ne_decale_pas_l_incertitude():
    budget = etablir_budget(
        20.0, "degC", [Composante(type="B", valeur=0.5, unite="degC", source="s")]
    )
    en_kelvin = budget.en("K")
    assert en_kelvin.valeur == pytest.approx(293.15)
    assert en_kelvin.gtc.x == pytest.approx(293.15)
    assert en_kelvin.incertitude_type == pytest.approx(0.5)
    assert en_kelvin.lignes[0].incertitude_type == pytest.approx(0.5)


def test_conversion_mbq_mci(budget_activite):
    en_mci = budget_activite.en("mCi")
    assert en_mci.unite == "mCi"
    assert en_mci.valeur == pytest.approx(100 / 37)
    assert en_mci.incertitude_type == pytest.approx(0.655845 / 37, abs=1e-6)
    assert en_mci.elargie_relative() == pytest.approx(
        budget_activite.elargie_relative()
    )
    assert en_mci.ddl == pytest.approx(budget_activite.ddl)
    assert budget_activite.unite == "MBq"
    assert budget_activite.valeur == 100.0


def test_conversion_incompatible_refusee(budget_activite):
    with pytest.raises(UnitesIncompatibles):
        budget_activite.en("mm")


def test_une_meme_mesure_reste_correlee_avec_elle_meme(budget_activite):
    difference = budget_activite.gtc - budget_activite.gtc
    assert uncertainty(difference) == pytest.approx(0, abs=1e-12)


def test_la_correlation_survit_a_la_conversion(budget_activite):
    en_kbq = budget_activite.en("kBq")
    difference = en_kbq.gtc / 1000 - budget_activite.gtc
    assert uncertainty(difference) == pytest.approx(0, abs=1e-9)


def test_propagation_d_un_produit_de_deux_mesures():
    # Mesures indépendantes : (u/y)² = (ua/a)² + (ub/b)²
    a = etablir_budget(
        100.0, "MBq", [Composante(type="B", valeur=1.0, unite="MBq", source="a")]
    )
    b = etablir_budget(
        2.0, "1", [Composante(type="B", valeur=0.04, unite="1", source="b")]
    )
    produit = a.gtc * b.gtc
    assert produit.x == pytest.approx(200.0)
    assert uncertainty(produit) == pytest.approx(200 * math.sqrt(0.01**2 + 0.02**2))
