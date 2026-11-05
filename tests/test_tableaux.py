"""Vérifie les colonnes d'incertitude à formule : hors du CSV, recalculées à la relecture."""

import pytest

from medphystools.page import ColonneIncertitude, ColonneNumerique, Tableau
from medphystools.tableaux import TableauInvalide, ecrire_csv, lire_csv

# --- Incertitude calculée -------------------------------------------------------------


def _comptage(formule="sqrt(N)", unite="1"):
    return Tableau(
        nom="comptage",
        fichier="brut/comptage.csv",
        colonnes=(
            ColonneNumerique(nom="N", unite="1"),
            ColonneIncertitude(
                nom="u_N",
                composante_de="N",
                type="B",
                unite=unite,
                source=f"Calculée : {formule}",
                formule=formule,
            ),
        ),
    )


def test_une_incertitude_calculee_n_entre_pas_dans_le_csv():
    texte = ecrire_csv(_comptage(), {"N": [100.0, 0.0, 400.0]})
    assert texte == "N\n100.0\n0.0\n400.0\n"


def test_une_incertitude_calculee_se_relit_sans_incertitude_propre():
    relu = lire_csv(_comptage(), "N\n100.0\n0.0\n400.0\n")
    assert list(relu.columns) == ["N", "u_N"]
    assert list(relu["u_N"]) == [10.0, 0.0, 20.0]


def test_ecrire_ignore_une_colonne_calculee_fournie():
    texte = ecrire_csv(_comptage(), {"N": [4.0], "u_N": [999.0]})
    assert texte == "N\n4.0\n"


@pytest.mark.parametrize(
    ("formule", "morceau"),
    [
        ("sqrt(M)", "« M »"),
        ("sqrt(u_N)", "« u_N »"),
        ("N - 10", "négative"),
        ("sqrt(N - 10)", "ligne 1"),
        ("__import__('os')", "fonctions"),
        ("1 / (N - 4)", "ligne 1"),
    ],
)
def test_une_formule_fausse_est_refusee_en_l_expliquant(formule, morceau):
    with pytest.raises(TableauInvalide, match=morceau):
        ecrire_csv(_comptage(formule), {"N": [4.0]})


def test_une_unite_incompatible_est_refusee_a_la_description():
    with pytest.raises(ValueError, match="incompatible"):
        _comptage(unite="MBq")


def test_une_formule_qui_ne_donne_pas_l_unite_est_refusee():
    tableau = Tableau(
        nom="taux",
        fichier="brut/taux.csv",
        colonnes=(
            ColonneNumerique(nom="R", unite="cps"),
            ColonneIncertitude(
                nom="u_R",
                composante_de="R",
                type="B",
                unite="cps",
                source="Calculée",
                formule="sqrt(R)",
            ),
        ),
    )
    with pytest.raises(TableauInvalide, match="u_R"):
        ecrire_csv(tableau, {"R": [4.0]})


def test_une_formule_est_forcement_une_incertitude_type_exacte():
    with pytest.raises(ValueError, match="exacte"):
        ColonneIncertitude(
            nom="u",
            composante_de="N",
            type="A",
            ddl=3,
            unite="1",
            source="s",
            formule="sqrt(N)",
        )
