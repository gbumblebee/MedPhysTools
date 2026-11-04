"""Vérifie decroissance() : la valeur, sa date, l'incertitude et le refus des erreurs."""

import math
from datetime import datetime, timedelta, timezone

import GTC
import pandas as pd
import pytest

from pymphub.calculs import (
    ErreurCalcul,
    FormuleRefusee,
    calculer,
    creer_colonne,
    creer_derivee,
    est_calcul_de_colonne,
    incertitudes_de_colonne,
)
from pymphub.page import (
    ColonneDate,
    ColonneIncertitude,
    ColonneNumerique,
    Composante,
    Page,
    Scalaire,
    Tableau,
)

HIVER = timezone(timedelta(hours=1))
ETE = timezone(timedelta(hours=2))
T0 = datetime(2026, 11, 4, 9, 0, tzinfo=HIVER)
SIX_HEURES = '"2026-11-04T15:00:00+01:00"'


def composante(u: float, unite: str) -> Composante:
    return Composante(
        source="balance", type="B", loi="normale", k=1, valeur=u, unite=unite
    )


def scalaire(nom: str, valeur: float, unite: str, u: float = 0.0, **champs) -> Scalaire:
    return Scalaire(
        nom=nom,
        valeur=valeur,
        unite=unite,
        composantes=(composante(u, unite),),
        **champs,
    )


def page(*mesures) -> Page:
    return Page(
        id="2026-11-04_cq_0001",
        titre="CQ",
        date_mesure=T0,
        date_saisie=T0,
        auteur="G.",
        mesures=mesures,
    )


TECHNETIUM = (
    scalaire("A0", 100.0, "MBq", 2.0, horodatage=T0),
    scalaire("T12", 6.0, "h", 0.06),
)


@pytest.mark.parametrize(
    ("constante", "unite"),
    [(6.0, "h"), (360.0, "min"), (21600.0, "s"), (0.25, "d")],
)
def test_une_periode_de_decroissance_divise_par_deux(constante, unite):
    resultat = calculer(
        page(TECHNETIUM[0], scalaire("T12", constante, unite)),
        f"decroissance(A0, T12, {SIX_HEURES})",
    )
    assert resultat.valeur == pytest.approx(50.0, rel=1e-12)
    assert resultat.unite == "MBq"


@pytest.mark.parametrize(
    ("constante", "unite"),
    [(math.log(2) / 6, "1/h"), (math.log(2) / 21600, "1/s")],
)
def test_une_constante_de_decroissance_donne_le_meme_resultat(constante, unite):
    resultat = calculer(
        page(TECHNETIUM[0], scalaire("lam", constante, unite)),
        f"decroissance(A0, lam, {SIX_HEURES})",
    )
    assert resultat.valeur == pytest.approx(50.0, rel=1e-12)


def test_le_resultat_est_celui_de_la_loi_a_une_date_quelconque():
    resultat = calculer(
        page(*TECHNETIUM), 'decroissance(A0, T12, "2026-11-05T02:17:00+01:00")'
    )
    heures = 17 + 17 / 60
    assert resultat.valeur == pytest.approx(100 * math.exp(-math.log(2) * heures / 6))


def test_on_remonte_aussi_dans_le_passe():
    resultat = calculer(
        page(*TECHNETIUM), 'decroissance(A0, T12, "2026-11-04T03:00:00+01:00")'
    )
    assert resultat.valeur == pytest.approx(200.0)


def test_a_la_date_de_la_mesure_rien_n_a_change():
    resultat = calculer(
        page(*TECHNETIUM), 'decroissance(A0, T12, "2026-11-04T09:00:00+01:00")'
    )
    assert resultat.valeur == pytest.approx(100.0)


def test_sans_date_le_scalaire_prend_celle_de_la_page():
    sans_date = (scalaire("A0", 100.0, "MBq"), TECHNETIUM[1])
    assert calculer(
        page(*sans_date), f"decroissance(A0, T12, {SIX_HEURES})"
    ).valeur == pytest.approx(50.0)


def test_la_date_du_scalaire_prime_sur_celle_de_la_page():
    plus_tard = scalaire("A0", 100.0, "MBq", horodatage=T0 + timedelta(hours=6))
    assert calculer(
        page(plus_tard, TECHNETIUM[1]), f"decroissance(A0, T12, {SIX_HEURES})"
    ).valeur == pytest.approx(100.0)


def test_le_changement_d_heure_ne_fausse_pas_la_duree():
    """Entre 2 h et 2 h 59 le 25 octobre : l'écart réel compte, pas l'heure affichée."""
    ete = scalaire(
        "A0", 100.0, "MBq", horodatage=datetime(2026, 10, 25, 1, 0, tzinfo=ETE)
    )
    resultat = calculer(
        page(ete, TECHNETIUM[1]), 'decroissance(A0, T12, "2026-10-25T06:00:00+01:00")'
    )
    assert resultat.valeur == pytest.approx(50.0)


# --- Incertitude -----------------------------------------------------------------------


def test_l_incertitude_est_celle_de_la_propagation_analytique():
    resultat = calculer(page(*TECHNETIUM), f"decroissance(A0, T12, {SIX_HEURES})")
    # u(A)² = (A/A0 · u_A0)² + (A · ln2 · Δt / T² · u_T)²
    attendue = math.hypot(0.5 * 2.0, 50.0 * math.log(2) * 6.0 / 6.0**2 * 0.06)
    assert GTC.uncertainty(resultat.gtc) == pytest.approx(attendue, rel=1e-9)


def test_le_budget_cite_l_activite_et_la_periode():
    resultat = calculer(page(*TECHNETIUM), f"decroissance(A0, T12, {SIX_HEURES})")
    sources = [ligne.source for ligne in resultat.lignes]
    assert len(sources) == 2
    assert any(source.startswith("A0") for source in sources)
    assert any(source.startswith("T12") for source in sources)


def test_le_resultat_garde_sa_formule_et_se_refait_a_l_identique():
    formule = f"decroissance(A0, T12, {SIX_HEURES})"
    premier = creer_derivee(page(*TECHNETIUM), "A1", formule)
    second = creer_derivee(page(*TECHNETIUM), "A1", formule)
    assert premier.formule == formule
    assert premier.valeur == second.valeur


def test_le_resultat_entre_dans_un_autre_calcul_avec_ses_correlations():
    derivee = creer_derivee(
        page(*TECHNETIUM), "A1", f"decroissance(A0, T12, {SIX_HEURES})"
    )
    resultat = calculer(page(*TECHNETIUM, derivee), "A1 / A0")
    assert resultat.valeur == pytest.approx(0.5)
    # A0 s'y simplifie : seule la période reste.
    attendue = 0.5 * math.log(2) * 6.0 / 6.0**2 * 0.06
    assert GTC.uncertainty(resultat.gtc) == pytest.approx(attendue, rel=1e-9)


# --- Colonnes --------------------------------------------------------------------------


def decroissance_tableau() -> Tableau:
    return Tableau(
        nom="t",
        fichier="brut/t.csv",
        colonnes=(
            ColonneDate(nom="heure"),
            ColonneNumerique(
                nom="A", unite="MBq", composantes=(composante(1.0, "MBq"),)
            ),
        ),
    )


def lire(_nom: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "heure": ["2026-11-04T09:00:00+01:00", "2026-11-04T15:00:00+01:00"],
            "A": [100.0, 80.0],
        }
    )


def test_toute_une_colonne_se_ramene_a_une_meme_date():
    formule = f"decroissance(t.A, T12, {SIX_HEURES}, t.heure)"
    tableau_page = page(decroissance_tableau(), TECHNETIUM[1])
    assert est_calcul_de_colonne(tableau_page, formule)
    colonne = creer_colonne(tableau_page, "A_corrigee", formule, lire)
    assert colonne.valeurs == pytest.approx((50.0, 80.0))
    assert colonne.unite == "MBq"
    assert colonne.tableaux == ("t",)
    assert colonne.incertitudes_types[0] > 0


def test_un_scalaire_se_calcule_a_chaque_date_d_une_colonne():
    tableau_page = page(decroissance_tableau(), *TECHNETIUM)
    colonne = creer_colonne(
        tableau_page, "attendue", "decroissance(A0, T12, t.heure)", lire
    )
    assert colonne.valeurs == pytest.approx((100.0, 50.0))


def test_une_seule_cellule_de_dates_donne_une_seule_valeur():
    tableau_page = page(decroissance_tableau(), *TECHNETIUM)
    formule = "decroissance(A0, T12, t.heure[2])"
    assert not est_calcul_de_colonne(tableau_page, formule)
    assert calculer(tableau_page, formule, lire).valeur == pytest.approx(50.0)


def test_une_colonne_sans_date_de_reference_est_expliquee():
    tableau_page = page(decroissance_tableau(), TECHNETIUM[1])
    with pytest.raises(ErreurCalcul, match="date_de_reference"):
        creer_colonne(tableau_page, "x", f"decroissance(t.A, T12, {SIX_HEURES})", lire)


def test_une_colonne_de_nombres_n_est_pas_une_date():
    tableau_page = page(decroissance_tableau(), *TECHNETIUM)
    with pytest.raises(ErreurCalcul, match="n'est pas une colonne de dates"):
        creer_colonne(tableau_page, "x", "decroissance(A0, T12, t.A)", lire)


def test_une_colonne_de_dates_n_est_pas_un_nombre():
    tableau_page = page(decroissance_tableau(), *TECHNETIUM)
    with pytest.raises(ErreurCalcul, match="colonne numérique"):
        creer_colonne(tableau_page, "x", "t.heure * 2", lire)


def test_une_formule_d_incertitude_ne_peut_pas_l_utiliser():
    colonne = ColonneIncertitude(
        nom="u",
        composante_de="A",
        type="B",
        unite="MBq",
        source="calculée",
        formule=f"decroissance(A, A, {SIX_HEURES})",
    )
    tableau_seul = Tableau(
        nom="t",
        fichier="brut/t.csv",
        colonnes=(
            ColonneNumerique(
                nom="A", unite="MBq", composantes=(composante(1.0, "MBq"),)
            ),
            colonne,
        ),
    )
    with pytest.raises(FormuleRefusee, match="formule d'incertitude"):
        incertitudes_de_colonne(tableau_seul, colonne, {"A": [1.0]}, 1)


# --- Erreurs expliquées ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("formule", "message"),
    [
        ("decroissance(A0, T12)", "s'écrit"),
        ('decroissance(A0, T12, "x", "y", "z")', "s'écrit"),
        ("decroissance(A0, T12, cible=1)", "s'écrit"),
        ("decroissance(A0, T12, 3)", "guillemets"),
        ('decroissance(A0, T12, "demain")', "date et une heure"),
        ('decroissance(A0, T12, "2026-11-04T15:00:00")', "fuseau"),
        (f"decroissance(A0, A0, {SIX_HEURES})", "période"),
        (f"decroissance(A0, A0 * 2, {SIX_HEURES})", "période"),
    ],
)
def test_une_erreur_d_ecriture_est_expliquee(formule, message):
    with pytest.raises(ErreurCalcul, match=message):
        calculer(page(*TECHNETIUM), formule)


@pytest.mark.parametrize("valeur", [0.0, -6.0])
def test_une_periode_nulle_ou_negative_est_refusee(valeur):
    with pytest.raises(ErreurCalcul, match="positive"):
        calculer(
            page(TECHNETIUM[0], scalaire("T12", valeur, "h")),
            f"decroissance(A0, T12, {SIX_HEURES})",
        )


def test_un_resultat_de_calcul_doit_indiquer_sa_date():
    derivee = creer_derivee(page(*TECHNETIUM), "double", "A0 * 2")
    mesures = page(*TECHNETIUM, derivee)
    with pytest.raises(ErreurCalcul, match="date_de_reference"):
        calculer(mesures, f"decroissance(double, T12, {SIX_HEURES})")
    assert calculer(
        mesures,
        f'decroissance(double, T12, {SIX_HEURES}, "2026-11-04T09:00:00+01:00")',
    ).valeur == pytest.approx(100.0)


def test_une_grandeur_nommee_lambda_recoit_une_explication():
    """C'est le premier nom auquel on pense pour une constante de décroissance."""
    mesures = page(TECHNETIUM[0], scalaire("lambda", 0.1, "1/h"))
    with pytest.raises(FormuleRefusee, match="« lambda » est un mot réservé"):
        calculer(mesures, f"decroissance(A0, lambda, {SIX_HEURES})")


def test_la_lettre_grecque_lambda_fonctionne():
    mesures = page(TECHNETIUM[0], scalaire("λ", math.log(2) / 6, "1/h"))
    assert calculer(
        mesures, f"decroissance(A0, λ, {SIX_HEURES})"
    ).valeur == pytest.approx(50.0)


def test_du_texte_reste_refuse_ailleurs_que_dans_decroissance():
    with pytest.raises(FormuleRefusee, match="nombres et des noms"):
        calculer(page(*TECHNETIUM), 'A0 * "2"')
