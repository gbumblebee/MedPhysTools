"""Vérifie les ajustements et les indicateurs sur des résultats connus par ailleurs.

Les valeurs de référence viennent de solutions analytiques, de numpy ou d'un calcul à la main,
jamais du code vérifié.
"""

import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from medphystools.ajustements import (
    MODELES,
    ErreurAjustement,
    ajuster,
    coefficients_de,
    creer_ajustement,
    creer_courbe,
    creer_indicateur,
    etat_resultat,
    evaluer_ajustement,
    lire_points,
)
from medphystools.calculs import ErreurCalcul, FormuleRefusee, calculer
from medphystools.incertitudes import Composante, composante_exacte
from medphystools.page import (
    Ajustement,
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Page,
    Scalaire,
    Tableau,
    depuis_json,
    vers_json,
)

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
EXACT_H = composante_exacte("h")


def tableau(*colonnes):
    return Tableau(nom="t", fichier="brut/t.csv", colonnes=colonnes)


def page_de(*mesures):
    return Page(
        id="2026-11-14_ajustement_0001",
        titre="Ajustement",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=mesures,
    )


def colonne(nom, unite, *composantes):
    return ColonneNumerique(nom=nom, unite=unite, composantes=composantes)


def incertitude(valeur, unite, **champs):
    return Composante(type="B", valeur=valeur, unite=unite, **{"source": "s", **champs})


def points_de(x, y, u_y=0.1, unite_y="MBq", u_x=None, composantes_y=None):
    """Prépare des points avec des incertitudes indépendantes, propres à chaque cellule."""
    donnees = pd.DataFrame(
        {
            "x": np.asarray(x, float),
            "y": np.asarray(y, float),
            "u": np.broadcast_to(u_y, np.shape(x)).astype(float),
        }
    )
    colonnes = [
        colonne("x", "h", incertitude(u_x, "h") if u_x else EXACT_H),
        ColonneNumerique(nom="y", unite=unite_y, composantes=composantes_y or ()),
        ColonneIncertitude(
            nom="u", composante_de="y", type="B", unite=unite_y, source="propre"
        ),
    ]
    page = page_de(tableau(*colonnes))
    return (
        page,
        (lambda _nom: donnees),
        lire_points(page, "t", "x", "y", lambda _n: donnees),
    )


X = np.linspace(0.0, 10.0, 11)


# --- Modèles ------------------------------------------------------------------------------


def test_les_coefficients_se_lisent_dans_la_formule():
    assert coefficients_de("A * exp(-l * x) + C") == ["A", "l", "C"]
    assert coefficients_de("a * sin(2 * pi * x / T)") == ["a", "T"]
    for modele in MODELES.values():
        assert set(coefficients_de(modele.formule)) == set(modele.unites)


@pytest.mark.parametrize(
    "formule",
    [
        "__import__('os').system('touch PIRATE')",
        "open('PIRATE') * x",
        "a.__class__ * x",
        "x[0] * a",
        "a * x if a else 0",
        "(lambda: a)() * x",
        "a * x ^ 2",
        "a * abs(x)",
        "'a' * x",
        "a * x; b",
    ],
)
def test_un_modele_interdit_est_refuse_sans_rien_executer(
    formule, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FormuleRefusee):
        coefficients_de(formule)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("formule", ["a * 2", "x * 2", "x"])
def test_un_modele_doit_avoir_x_et_un_coefficient(formule):
    with pytest.raises(FormuleRefusee):
        coefficients_de(formule)


# --- Paramètres connus --------------------------------------------------------------------


def test_un_ajustement_exponentiel_retrouve_des_parametres_connus():
    """A = 100, T12 = 6 h : des données sans bruit redonnent exactement ces valeurs."""
    y = 100.0 * np.exp(-math.log(2) * X / 6.0)
    _page, _lire, points = points_de(X, y, u_y=0.5)
    modele = MODELES["decroissance"]
    resultat = ajuster(points, modele.formule, "decroissance")
    assert resultat.valeurs == pytest.approx([100.0, 6.0], rel=1e-7)
    assert resultat.chi2_reduit == pytest.approx(0.0, abs=1e-9)


def test_un_ajustement_bruite_tombe_dans_l_incertitude_annoncee():
    """Sur 200 tirages, |écart| < 2u doit arriver environ 95 % du temps."""
    rng = np.random.default_rng(12345)
    modele = MODELES["exponentielle"]
    dedans = 0
    for _tirage in range(200):
        y = 100.0 * np.exp(-0.2 * X) + rng.normal(0.0, 0.5, X.shape)
        _page, _lire, points = points_de(X, y, u_y=0.5)
        resultat = ajuster(points, modele.formule, "exponentielle")
        u_l = math.sqrt(resultat.covariance[1, 1])
        dedans += abs(resultat.valeurs[1] - 0.2) < 2 * u_l
    assert 0.90 <= dedans / 200 <= 0.99


@pytest.mark.parametrize(
    ("modele", "vrais"),
    [
        ("droite", {"a": 2.5, "b": -1.0}),
        ("droite_origine", {"a": 2.5}),
        ("polynome2", {"c": 0.3, "b": -2.0, "a": 5.0}),
        ("polynome3", {"d": 0.02, "c": 0.3, "b": -2.0, "a": 5.0}),
        ("exponentielle", {"A": 50.0, "l": 0.3}),
        ("exponentielle_fond", {"A": 50.0, "l": 0.3, "C": 4.0}),
        ("decroissance", {"A": 50.0, "T12": 3.0}),
        ("gaussienne", {"A": 10.0, "m": 5.0, "s": 1.5}),
    ],
)
def test_chaque_modele_usuel_retrouve_ses_parametres_sans_valeur_de_depart(
    modele, vrais
):
    usuel = MODELES[modele]
    from medphystools.ajustements import creer_fonction

    noms = coefficients_de(usuel.formule)
    y = creer_fonction(usuel.formule, noms)(X, [vrais[n] for n in noms])
    _page, _lire, points = points_de(X, y, u_y=0.1)
    resultat = ajuster(points, usuel.formule, modele)
    trouves = dict(zip(resultat.noms, resultat.valeurs))
    for nom, vrai in vrais.items():
        assert trouves[nom] == pytest.approx(vrai, rel=1e-5, abs=1e-6)


def test_une_formule_personnalisee_s_ajuste_avec_ses_valeurs_de_depart():
    y = 3.0 * np.sin(2 * math.pi * X / 8.0) + 1.0
    _page, _lire, points = points_de(X, y, u_y=0.1)
    resultat = ajuster(
        points,
        "a * sin(2 * pi * x / T) + c",
        depart={"a": 2.0, "T": 7.5, "c": 0.0},
    )
    trouves = dict(zip(resultat.noms, resultat.valeurs))
    assert trouves["a"] == pytest.approx(3.0, rel=1e-5)
    assert trouves["T"] == pytest.approx(8.0, rel=1e-5)
    assert trouves["c"] == pytest.approx(1.0, rel=1e-5)


# --- Incertitudes : référence indépendante ------------------------------------------------


def donnees_droite():
    rng = np.random.default_rng(7)
    u = 0.1 + 0.05 * X
    y = 2.0 * X + 1.0 + rng.normal(0.0, u)
    return y, u


def test_la_droite_ponderee_concorde_avec_la_reference_numpy():
    y, u = donnees_droite()
    _page, _lire, points = points_de(X, y, u_y=u)
    resultat = ajuster(points, MODELES["droite"].formule, "droite")
    reference, covariance = np.polyfit(X, y, 1, w=1 / u, cov="unscaled")
    assert resultat.valeurs == pytest.approx(reference, rel=1e-6)
    assert resultat.covariance == pytest.approx(covariance, rel=1e-5)


def test_le_chi2_reduit_est_celui_calcule_a_la_main():
    y, u = donnees_droite()
    _page, _lire, points = points_de(X, y, u_y=u)
    resultat = ajuster(points, MODELES["droite"].formule, "droite")
    a, b = resultat.valeurs
    attendu = np.sum(((y - (a * X + b)) / u) ** 2) / (len(X) - 2)
    assert resultat.chi2_reduit == pytest.approx(attendu)


def test_l_incertitude_de_la_droite_tient_compte_des_covariances():
    """u²(y) = x²·u²(a) + u²(b) + 2x·cov(a, b). Sans le terme croisé, l'écart est net."""
    y, u = donnees_droite()
    _page, _lire, points = points_de(X, y, u_y=u)
    ajustement, _resultat = creer_ajustement(
        points, "fit", MODELES["droite"].formule, "droite"
    )
    _reference, c = np.polyfit(X, y, 1, w=1 / u, cov="unscaled")
    essai = np.array([0.0, 3.3, 10.0])
    attendu = np.sqrt(essai**2 * c[0, 0] + c[1, 1] + 2 * essai * c[0, 1])
    _valeurs, obtenu = evaluer_ajustement(ajustement, essai)
    assert obtenu == pytest.approx(attendu, rel=1e-5)
    sans_covariance = np.sqrt(essai**2 * c[0, 0] + c[1, 1])
    assert not np.allclose(obtenu, sans_covariance, rtol=1e-3)


def test_l_incertitude_de_la_courbe_concorde_avec_un_tirage_monte_carlo():
    """Référence indépendante du calcul analytique, pour un modèle non linéaire."""
    y = 100.0 * np.exp(-0.2 * X)
    _page, _lire, points = points_de(X, y, u_y=0.5)
    ajustement, resultat = creer_ajustement(
        points, "fit", MODELES["exponentielle"].formule, "exponentielle"
    )
    rng = np.random.default_rng(3)
    tirages = rng.multivariate_normal(resultat.valeurs, resultat.covariance, 40000)
    essai = np.array([1.0, 5.0, 9.0])
    courbes = tirages[:, 0, None] * np.exp(-tirages[:, 1, None] * essai)
    _valeurs, obtenu = evaluer_ajustement(ajustement, essai)
    assert obtenu == pytest.approx(courbes.std(axis=0), rel=0.03)


# --- Hypothèses : incertitude sur x et erreurs communes -----------------------------------


def test_l_incertitude_sur_x_passe_par_la_variance_effective():
    """y = 2x + 1 : u²_eff = u(y)² + (2 · u(x))² pour chaque point. Référence : numpy."""
    rng = np.random.default_rng(11)
    u_x, u_y = 0.2, 0.3
    u_eff = math.sqrt(u_y**2 + (2 * u_x) ** 2)
    y = 2.0 * X + 1.0 + rng.normal(0.0, u_eff, X.shape)
    _page, _lire, points = points_de(X, y, u_y=u_y, u_x=u_x)
    # u(x) sans repère est indépendante d'une ligne à l'autre.
    assert points.u_x == pytest.approx(np.full(11, u_x))
    resultat = ajuster(points, MODELES["droite"].formule, "droite")
    # La pente utilisée est la pente ajustée, puisque la vraie n'est pas connue.
    u_eff_ajustee = math.sqrt(u_y**2 + (resultat.valeurs[0] * u_x) ** 2)
    w = np.full_like(X, 1 / u_eff_ajustee)
    _reference, c = np.polyfit(X, y, 1, w=w, cov="unscaled")
    assert resultat.covariance == pytest.approx(c, rel=1e-6)


COMMUN = {"relative": True, "source": "certificat", "erreur_commune": "CERT"}


def rel(valeur, **champs):
    return Composante(type="B", valeur=valeur, **{**COMMUN, **champs})


def test_un_certificat_commun_passe_sur_l_amplitude_et_pas_sur_la_periode():
    """Tous les points multipliés par (1 + ε) : A varie de ε, T12 ne bouge pas."""
    y = 100.0 * np.exp(-math.log(2) * X / 6.0)
    _page, _lire, avec = points_de(X, y, u_y=0.5, composantes_y=(rel(1.0),))
    _page, _lire, sans = points_de(X, y, u_y=0.5)
    assert avec.u_y == pytest.approx(np.full(11, 0.5))
    modele = MODELES["decroissance"]
    r_avec = ajuster(avec, modele.formule, "decroissance")
    r_sans = ajuster(sans, modele.formule, "decroissance")
    assert r_avec.valeurs == pytest.approx(r_sans.valeurs, rel=1e-6)
    # u²(A) = u²(A) propre + (1 % de 100)², et rien de plus sur la période.
    assert r_avec.covariance[0, 0] == pytest.approx(
        r_sans.covariance[0, 0] + 1.0**2, rel=1e-3
    )
    assert r_avec.covariance[1, 1] == pytest.approx(r_sans.covariance[1, 1], rel=1e-3)


def test_un_zero_commun_passe_sur_l_ordonnee_et_pas_sur_la_pente():
    y = 2.0 * X + 1.0
    commune = incertitude(0.5, "MBq", erreur_commune="ZERO", source="zéro")
    _page, _lire, avec = points_de(X, y, u_y=0.2, composantes_y=(commune,))
    _page, _lire, sans = points_de(X, y, u_y=0.2)
    r_avec = ajuster(avec, MODELES["droite"].formule, "droite")
    r_sans = ajuster(sans, MODELES["droite"].formule, "droite")
    assert r_avec.covariance[1, 1] == pytest.approx(
        r_sans.covariance[1, 1] + 0.25, rel=1e-5
    )
    assert r_avec.covariance[0, 0] == pytest.approx(r_sans.covariance[0, 0], rel=1e-5)


def test_un_decalage_d_horloge_commun_passe_par_la_pente():
    """Tous les x décalés de δ : y décalé de −a·δ, donc b varie de −a·δ, a ne bouge pas."""
    y = 2.0 * X + 1.0
    donnees = pd.DataFrame({"x": X, "y": y, "u": 0.2})
    colonnes = (
        colonne("x", "h", incertitude(0.1, "h", erreur_commune="HORLOGE")),
        ColonneNumerique(nom="y", unite="MBq"),
        ColonneIncertitude(
            nom="u", composante_de="y", type="B", unite="MBq", source="propre"
        ),
    )
    page = page_de(tableau(*colonnes))
    points = lire_points(page, "t", "x", "y", lambda _n: donnees)
    assert points.u_x == pytest.approx(np.zeros(11))
    r = ajuster(points, MODELES["droite"].formule, "droite")
    _page, _lire, sans = points_de(X, y, u_y=0.2)
    r_sans = ajuster(sans, MODELES["droite"].formule, "droite")
    assert r.covariance[1, 1] == pytest.approx(
        r_sans.covariance[1, 1] + (2.0 * 0.1) ** 2, rel=1e-5
    )


def test_sans_incertitude_propre_la_dispersion_donne_la_covariance():
    """Seul le certificat est déclaré : on retombe sur la covariance de numpy, remise à l'échelle."""
    rng = np.random.default_rng(5)
    y = 2.0 * X + 1.0 + rng.normal(0.0, 0.3, X.shape)
    donnees = pd.DataFrame({"x": X, "y": y})
    colonnes = (
        colonne("x", "h", EXACT_H),
        colonne("y", "MBq", incertitude(0.0, "MBq", source="exacte")),
    )
    page = page_de(tableau(*colonnes))
    points = lire_points(page, "t", "x", "y", lambda _n: donnees)
    resultat = ajuster(points, MODELES["droite"].formule, "droite")
    _reference, c = np.polyfit(X, y, 1, cov=True)
    assert resultat.covariance == pytest.approx(c, rel=1e-5)
    assert resultat.chi2_reduit is None
    assert "sans pondération" in resultat.methode


def test_un_point_sans_incertitude_parmi_d_autres_est_explique():
    u = np.full(11, 0.1)
    u[3] = 0.0
    _page, _lire, points = points_de(X, 2.0 * X, u_y=u)
    with pytest.raises(ErreurAjustement, match="points 4"):
        ajuster(points, MODELES["droite"].formule, "droite")


def test_un_mauvais_modele_est_signale():
    y = 100.0 * np.exp(-0.5 * X)
    _page, _lire, points = points_de(X, y, u_y=0.1)
    resultat = ajuster(points, MODELES["droite"].formule, "droite")
    assert "χ² réduit" in resultat.avertissements[0]


@pytest.mark.parametrize(
    ("x", "formule", "message"),
    [
        ([1.0, 2.0], "a * x + b", "plus de points que de coefficients"),
        ([2.0, 2.0, 2.0], "a * x + b", "même x"),
        ([0.0, 1.0, 2.0, 3.0], "a * x + b * x", "ne se distinguent pas"),
    ],
)
def test_un_ajustement_impossible_s_explique(x, formule, message):
    y = np.arange(float(len(x)))
    _page, _lire, points = points_de(x, y, u_y=0.1)
    with pytest.raises(ErreurAjustement, match=message):
        ajuster(points, formule, depart={"a": 1.0, "b": 1.0})


# --- Enregistrement -----------------------------------------------------------------------


@pytest.fixture
def ajuste():
    rng = np.random.default_rng(1)
    y = 100.0 * np.exp(-math.log(2) * X / 6.0) + rng.normal(0.0, 0.5, X.shape)
    page, lire, points = points_de(X, y, u_y=0.5)
    ajustement, resultat = creer_ajustement(
        points, "fit", MODELES["decroissance"].formule, "decroissance"
    )
    return page, lire, points, ajustement, resultat


def test_les_unites_des_coefficients_viennent_des_colonnes(ajuste):
    *_debut, ajustement, _resultat = ajuste
    assert {p.nom: p.unite for p in ajustement.parametres} == {"A": "MBq", "T12": "h"}
    assert ajustement.modele == "Décroissance (période)"
    assert ajustement.unite_x == "h"
    assert ajustement.unite_y == "MBq"
    assert ajustement.points == 11


def test_les_unites_d_une_formule_personnalisee_sont_obligatoires():
    _page, _lire, points = points_de(X, 2.0 * X + 1.0, u_y=0.1)
    with pytest.raises(ErreurAjustement, match="unité du coefficient « a »"):
        creer_ajustement(points, "fit", "a * x + b", depart={"a": 1.0, "b": 1.0})
    with pytest.raises(ErreurAjustement, match="non reconnue"):
        creer_ajustement(
            points,
            "fit",
            "a * x + b",
            depart={"a": 1.0, "b": 1.0},
            unites={"a": "pomme", "b": "MBq"},
        )
    ajustement, _r = creer_ajustement(
        points,
        "fit",
        "a * x + b",
        depart={"a": 1.0, "b": 1.0},
        unites={"a": "MBq/h", "b": "MBq"},
    )
    assert ajustement.modele == "Personnalisé"


def test_tout_se_retrouve_a_l_identique_apres_relecture(ajuste):
    page, _lire, _points, ajustement, _resultat = ajuste
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    relue = depuis_json(vers_json(avec)).grandeur("fit")
    assert relue == ajustement
    assert isinstance(relue, Ajustement)
    assert relue.correlation == ajustement.correlation
    assert relue.correlation[0][0] == 1.0


def test_la_fonction_se_reevalue_depuis_les_coefficients_avec_la_meme_incertitude(
    ajuste,
):
    page, _lire, _points, ajustement, resultat = ajuste
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    relue = depuis_json(vers_json(avec)).grandeur("fit")
    essai = np.linspace(0.0, 10.0, 7)
    y1, u1 = evaluer_ajustement(ajustement, essai)
    y2, u2 = evaluer_ajustement(relue, essai)
    assert y2 == pytest.approx(y1, rel=1e-14)
    assert u2 == pytest.approx(u1, rel=1e-12)
    assert np.array(relue.covariance()) == pytest.approx(resultat.covariance, rel=1e-9)


def test_la_courbe_est_plus_fine_que_les_donnees_et_reste_dans_leur_intervalle(ajuste):
    *_debut, ajustement, _resultat = ajuste
    tableau_courbe, donnees = creer_courbe(ajustement)
    assert len(donnees["x"]) > ajustement.points
    assert min(donnees["x"]) == X.min()
    assert max(donnees["x"]) == X.max()
    assert tableau_courbe.nom == "fit_courbe"
    assert tableau_courbe.fichier == "brut/fit_courbe.csv"
    y, u = evaluer_ajustement(ajustement, donnees["x"])
    assert donnees["y"] == pytest.approx(list(y))
    assert donnees["u_y"] == pytest.approx(list(u))


def test_un_ajustement_ne_modifie_aucune_valeur_brute(ajuste):
    page, lire, _points, ajustement, _resultat = ajuste
    avant = lire("t").copy()
    creer_courbe(ajustement)
    pd.testing.assert_frame_equal(lire("t"), avant)
    assert page.grandeur("t").nom == "t"


def test_les_points_changes_se_voient(ajuste):
    page, lire, _points, ajustement, _resultat = ajuste
    assert etat_resultat(page, ajustement, lire) is None

    def autre(_nom):
        donnees = lire("t").copy()
        donnees.loc[0, "y"] += 1.0
        return donnees

    assert "À refaire" in etat_resultat(page, ajustement, autre)

    def incertitude_changee(_nom):
        donnees = lire("t").copy()
        donnees.loc[0, "u"] = 9.0
        return donnees

    assert "À refaire" in etat_resultat(page, ajustement, incertitude_changee)


def test_lmfit_ne_se_charge_qu_au_moment_d_ajuster():
    code = (
        "import sys, medphystools, medphystools.archive, medphystools.ajustements;"
        "assert not [m for m in sys.modules if m.startswith('lmfit')]"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


def test_les_coefficients_s_utilisent_dans_un_calcul_avec_leur_correlation(ajuste):
    """A/T12 : u² = (dA·u(A))² + (dT·u(T))² + 2·dA·dT·cov(A, T), terme croisé compris."""
    page, _lire, _points, ajustement, resultat = ajuste
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    budget = calculer(avec, "fit.A / fit.T12")
    a, t = resultat.valeurs
    c = resultat.covariance
    da, dt = 1 / t, -a / t**2
    attendu = math.sqrt(da**2 * c[0, 0] + dt**2 * c[1, 1] + 2 * da * dt * c[0, 1])
    assert budget.valeur == pytest.approx(a / t)
    assert budget.incertitude_type == pytest.approx(attendu, rel=1e-6)
    assert budget.unite == "MBq/h"


def test_un_coefficient_absent_s_explique(ajuste):
    page, _lire, _points, ajustement, _resultat = ajuste
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    with pytest.raises(ErreurCalcul, match="pas de coefficient « z »"):
        calculer(avec, "fit.z")


# --- Indicateurs sur des fonctions au résultat connu --------------------------------------


def parabole():
    """y = 9 − (x − 3)² sur [0, 6] : max 9 en x = 3, min 0, intégrale 36, moyenne 6."""
    x = np.linspace(0.0, 6.0, 601)
    y = 9.0 - (x - 3.0) ** 2
    return points_de(x, y, u_y=0.01)


def indicateur(page, lire, methode, source="points", **champs):
    return creer_indicateur(page, "i", methode, source, "t", "x", "y", lire, **champs)


@pytest.mark.parametrize(
    ("methode", "champs", "valeur", "unite", "position"),
    [
        ("maximum", {}, 9.0, "MBq", 3.0),
        ("minimum", {}, 0.0, "MBq", 0.0),
        ("integrale", {}, 36.0, "MBq*h", None),
        ("derivee", {"position": 1.0}, 4.0, "MBq/h", 1.0),
        ("derivee", {"position": 5.0}, -4.0, "MBq/h", 5.0),
        ("integrale", {"de": 3.0, "a": 6.0}, 18.0, "MBq*h", None),
        ("maximum", {"de": 4.0, "a": 6.0}, 8.0, "MBq", 4.0),
    ],
)
def test_chaque_indicateur_sur_les_points_de_la_parabole(
    methode, champs, valeur, unite, position
):
    page, lire, _points = parabole()
    resultat = indicateur(page, lire, methode, **champs)
    assert resultat.valeur == pytest.approx(valeur, abs=2e-3)
    assert resultat.unite == unite
    assert resultat.source == "points"
    if position is not None:
        assert resultat.position == pytest.approx(position, abs=1e-9)


def test_la_moyenne_des_points_est_leur_moyenne_arithmetique():
    """Elle diffère de la moyenne de la fonction : les 601 points donnent 5,99, et non 6."""
    page, lire, points = parabole()
    resultat = indicateur(page, lire, "moyenne")
    assert resultat.valeur == pytest.approx(np.mean(points.y.valeurs))
    assert resultat.valeur == pytest.approx(5.99, abs=1e-9)


@pytest.fixture
def parabole_ajustee():
    page, lire, points = parabole()
    ajustement, _r = creer_ajustement(
        points, "fit", MODELES["polynome2"].formule, "polynome2"
    )
    return (
        page.model_copy(update={"mesures": (*page.mesures, ajustement)}),
        lire,
        ajustement,
    )


@pytest.mark.parametrize(
    ("methode", "champs", "valeur", "unite", "position"),
    [
        ("maximum", {}, 9.0, "MBq", 3.0),
        ("minimum", {}, 0.0, "MBq", 0.0),
        ("integrale", {}, 36.0, "MBq*h", None),
        ("moyenne", {}, 6.0, "MBq", None),
        ("derivee", {"position": 1.0}, 4.0, "MBq/h", 1.0),
        ("integrale", {"de": 3.0, "a": 6.0}, 18.0, "MBq*h", None),
    ],
)
def test_chaque_indicateur_sur_la_parabole_ajustee(
    parabole_ajustee, methode, champs, valeur, unite, position
):
    page, lire, ajustement = parabole_ajustee
    resultat = indicateur(
        page, lire, methode, "ajustement", ajustement=ajustement, **champs
    )
    assert resultat.valeur == pytest.approx(valeur, abs=1e-5)
    assert resultat.unite == unite
    assert resultat.ajustement == "fit"
    if position is not None:
        assert resultat.position == pytest.approx(position, abs=1e-4)


def droite_incertaine():
    """y = 2x + 1 sur [0, 10], 11 points, u(y) = 0,5 indépendante."""
    return points_de(X, 2.0 * X + 1.0, u_y=0.5)


def test_l_incertitude_de_la_moyenne_des_points_est_celle_calculee_a_la_main():
    page, lire, _points = droite_incertaine()
    resultat = indicateur(page, lire, "moyenne")
    assert resultat.valeur == pytest.approx(11.0)
    assert resultat.incertitude_type == pytest.approx(0.5 / math.sqrt(11))


def test_une_erreur_commune_ne_diminue_pas_avec_le_nombre_de_points():
    """Moyenne de n points à 1 % du même certificat : u = 1 % de la moyenne, sans √n."""
    y = np.full_like(X, 100.0)
    page, lire, _points = points_de(X, y, u_y=0.0, composantes_y=(rel(1.0),))
    resultat = indicateur(page, lire, "moyenne")
    assert resultat.incertitude_type == pytest.approx(1.0)


def test_l_incertitude_de_l_integrale_des_points_est_celle_calculee_a_la_main():
    """Trapèzes de pas 1 : poids 0,5 aux extrémités, 1 ailleurs, sans incertitude sur x."""
    page, lire, _points = droite_incertaine()
    resultat = indicateur(page, lire, "integrale")
    assert resultat.valeur == pytest.approx(110.0)
    attendu = 0.5 * math.sqrt(2 * 0.5**2 + 9 * 1.0**2)
    assert resultat.incertitude_type == pytest.approx(attendu)


def test_l_incertitude_de_la_derivee_des_points_est_celle_calculee_a_la_main():
    """Différence centrée de largeur 2 : u = √2 · 0,5 / 2."""
    page, lire, _points = droite_incertaine()
    resultat = indicateur(page, lire, "derivee", position=5.0)
    assert resultat.valeur == pytest.approx(2.0)
    assert resultat.incertitude_type == pytest.approx(math.sqrt(2) * 0.5 / 2)


def test_l_incertitude_d_un_extremum_des_points_est_celle_du_point():
    page, lire, _points = droite_incertaine()
    resultat = indicateur(page, lire, "maximum")
    assert resultat.valeur == pytest.approx(21.0)
    assert resultat.incertitude_type == pytest.approx(0.5)
    assert resultat.position == pytest.approx(10.0)
    assert resultat.unite_position == "h"
    assert resultat.incertitude_position == 0.0


@pytest.fixture
def droite_ajustee():
    page, lire, points = droite_incertaine()
    ajustement, resultat = creer_ajustement(
        points, "fit", MODELES["droite"].formule, "droite"
    )
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    return avec, lire, ajustement, resultat.covariance


def test_l_incertitude_de_l_integrale_de_la_droite_vient_des_covariances(
    droite_ajustee,
):
    """I = a·(B²−A²)/2 + b·(B−A) : g = (50, 10) sur [0, 10]."""
    page, lire, ajustement, c = droite_ajustee
    resultat = indicateur(page, lire, "integrale", "ajustement", ajustement=ajustement)
    assert resultat.valeur == pytest.approx(110.0, rel=1e-6)
    g = np.array([50.0, 10.0])
    assert resultat.incertitude_type == pytest.approx(math.sqrt(g @ c @ g), rel=1e-5)


def test_l_incertitude_de_la_moyenne_de_la_droite_vient_des_covariances(droite_ajustee):
    page, lire, ajustement, c = droite_ajustee
    resultat = indicateur(page, lire, "moyenne", "ajustement", ajustement=ajustement)
    g = np.array([5.0, 1.0])
    assert resultat.incertitude_type == pytest.approx(math.sqrt(g @ c @ g), rel=1e-5)


def test_la_derivee_de_la_droite_a_l_incertitude_de_sa_pente(droite_ajustee):
    page, lire, ajustement, c = droite_ajustee
    resultat = indicateur(
        page, lire, "derivee", "ajustement", ajustement=ajustement, position=4.0
    )
    assert resultat.valeur == pytest.approx(2.0, rel=1e-6)
    assert resultat.incertitude_type == pytest.approx(math.sqrt(c[0, 0]), rel=1e-4)


def test_l_incertitude_du_sommet_d_une_parabole_vient_de_l_ordonnee():
    """Au sommet, déplacer x ne change rien au premier ordre : l'incertitude reste celle de f."""
    page, lire, points = parabole()
    ajustement, _r = creer_ajustement(
        points, "fit", MODELES["polynome2"].formule, "polynome2"
    )
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    resultat = indicateur(avec, lire, "maximum", "ajustement", ajustement=ajustement)
    _y, u = evaluer_ajustement(ajustement, [3.0])
    assert resultat.incertitude_type == pytest.approx(u[0], rel=1e-4)
    # Référence indépendante : numpy, pour g = (x², x, 1) en x = 3.
    y = points.y.valeurs
    _coefficients, c = np.polyfit(
        points.x.valeurs, y, 2, w=np.full_like(y, 1 / 0.01), cov="unscaled"
    )
    g = np.array([9.0, 3.0, 1.0])
    assert resultat.incertitude_type == pytest.approx(math.sqrt(g @ c @ g), rel=1e-4)


def test_un_coefficient_proche_de_zero_garde_une_covariance_juste():
    """y = 2x² + 3x + 0 : le dernier coefficient vaut 0, ce qui piège un pas relatif."""
    y = 2.0 * X**2 + 3.0 * X
    _page, _lire, points = points_de(X, y, u_y=0.1)
    resultat = ajuster(points, MODELES["polynome2"].formule, "polynome2")
    _reference, c = np.polyfit(X, y, 2, w=np.full_like(X, 10.0), cov="unscaled")
    assert resultat.covariance == pytest.approx(c, rel=1e-4)


def test_un_tres_petit_coefficient_garde_une_covariance_juste():
    """Un taux de 1e-9 par seconde sur 1e9 s : un pas absolu serait bien trop grand."""
    x = np.linspace(0.0, 2e9, 21)
    y = 100.0 * np.exp(-1e-9 * x)
    donnees = pd.DataFrame({"x": x, "y": y, "u": 0.5})
    colonnes = (
        colonne("x", "s", composante_exacte("s")),
        ColonneNumerique(nom="y", unite="MBq"),
        ColonneIncertitude(
            nom="u", composante_de="y", type="B", unite="MBq", source="propre"
        ),
    )
    page = page_de(tableau(*colonnes))
    points = lire_points(page, "t", "x", "y", lambda _n: donnees)
    resultat = ajuster(points, MODELES["exponentielle"].formule, "exponentielle")
    assert resultat.valeurs == pytest.approx([100.0, 1e-9], rel=1e-6)
    # Jacobienne analytique : df/dA = exp(−l·x), df/dl = −A·x·exp(−l·x).
    j = np.column_stack((np.exp(-1e-9 * x), -100.0 * x * np.exp(-1e-9 * x))) / 0.5
    assert resultat.covariance == pytest.approx(np.linalg.inv(j.T @ j), rel=1e-4)


@pytest.mark.parametrize(
    ("methode", "champs", "message"),
    [
        ("integrale", {"de": -1.0, "a": 5.0}, "pas d'extrapolation"),
        ("moyenne", {"de": 2.0, "a": 11.0}, "pas d'extrapolation"),
        ("derivee", {"position": 12.0}, "pas d'extrapolation"),
        ("derivee", {}, "Indiquer l'abscisse"),
        ("moyenne", {"de": 4.0, "a": 4.0}, "vide"),
    ],
)
def test_un_indicateur_ajuste_n_extrapole_jamais(
    droite_ajustee, methode, champs, message
):
    page, lire, ajustement, _c = droite_ajustee
    with pytest.raises(ErreurAjustement, match=message):
        indicateur(page, lire, methode, "ajustement", ajustement=ajustement, **champs)


@pytest.mark.parametrize(
    ("methode", "champs", "message"),
    [
        ("moyenne", {"de": 20.0, "a": 30.0}, "Aucun point"),
        ("integrale", {"de": 4.0, "a": 4.0}, "au moins deux points"),
        ("derivee", {}, "Indiquer l'abscisse"),
        ("moyenne", {"de": 5.0, "a": 1.0}, "à l'envers"),
    ],
)
def test_un_indicateur_impossible_sur_les_points_s_explique(methode, champs, message):
    page, lire, _points = droite_incertaine()
    with pytest.raises(ErreurAjustement, match=message):
        indicateur(page, lire, methode, **champs)


def test_un_indicateur_ajuste_sans_ajustement_s_explique():
    page, lire, _points = droite_incertaine()
    with pytest.raises(ErreurAjustement, match="Faire d'abord"):
        indicateur(page, lire, "moyenne", "ajustement")


def test_un_indicateur_se_relit_avec_sa_methode_ses_donnees_et_son_intervalle():
    page, lire, _points = droite_incertaine()
    resultat = indicateur(page, lire, "integrale", de=2.0, a=8.0, commentaire="Note")
    avec = page.model_copy(update={"mesures": (*page.mesures, resultat)})
    relu = depuis_json(vers_json(avec)).grandeur("i")
    assert relu == resultat
    assert (relu.methode, relu.source, relu.tableau, relu.x, relu.y) == (
        "integrale",
        "points",
        "t",
        "x",
        "y",
    )
    assert (relu.de, relu.a) == (2.0, 8.0)
    assert relu.incertitude_type == resultat.incertitude_type
    assert relu.commentaire == "Note"
    assert relu.budget().elargie(2.0) == pytest.approx(2 * resultat.incertitude_type)


def test_un_indicateur_s_utilise_dans_un_calcul():
    page, lire, _points = droite_incertaine()
    resultat = indicateur(page, lire, "moyenne")
    avec = page.model_copy(update={"mesures": (*page.mesures, resultat)})
    budget = calculer(avec, "i * 2")
    assert budget.valeur == pytest.approx(22.0)
    assert budget.incertitude_type == pytest.approx(2 * resultat.incertitude_type)


def test_un_indicateur_ne_modifie_aucune_valeur_brute():
    page, lire, _points = droite_incertaine()
    avant = lire("t").copy()
    for methode in ("minimum", "maximum", "moyenne", "integrale"):
        indicateur(page, lire, methode)
    pd.testing.assert_frame_equal(lire("t"), avant)


def test_une_colonne_d_identifiants_ne_se_lit_pas_comme_des_points():
    page = page_de(
        tableau(
            ColonneIdentifiant(nom="lot"), colonne("y", "MBq", incertitude(1.0, "MBq"))
        )
    )
    donnees = pd.DataFrame({"lot": ["007"], "y": [1.0]})
    with pytest.raises(ErreurCalcul, match="Aucune colonne numérique « lot »"):
        lire_points(page, "t", "lot", "y", lambda _n: donnees)


def test_une_colonne_calculee_peut_s_ajuster_et_garde_son_scalaire_commun():
    """y = a / R, où R est un scalaire : une erreur de R déplace tous les points ensemble."""
    from medphystools.calculs import creer_colonne

    donnees = pd.DataFrame({"x": X, "a": 2.0 * X + 1.0})
    colonnes = (
        colonne("x", "h", EXACT_H),
        colonne("a", "MBq", incertitude(0.1, "MBq")),
    )
    base = page_de(
        tableau(*colonnes),
        Scalaire(
            nom="R",
            valeur=2.0,
            unite="1",
            composantes=(Composante(type="B", valeur=0.02, unite="1", source="r"),),
        ),
    )
    calculee = creer_colonne(base, "c", "t.a / R", lambda _n: donnees)
    page = base.model_copy(update={"mesures": (*base.mesures, calculee)})
    points = lire_points(page, "t", "x", "c", lambda _n: donnees)
    assert points.u_y == pytest.approx(np.full(11, 0.05))
    assert len(points.communs_y) == 1
    resultat = ajuster(points, MODELES["droite"].formule, "droite")
    # 1 % de R se reporte sur la pente (1) comme sur l'ordonnée (0,5), à 1 %.
    w = np.full_like(X, 1 / 0.05)
    _reference, c = np.polyfit(X, points.y.valeurs, 1, w=w, cov="unscaled")
    assert resultat.covariance[0, 0] == pytest.approx(c[0, 0] + 0.01**2, rel=1e-4)
    assert resultat.covariance[1, 1] == pytest.approx(c[1, 1] + 0.005**2, rel=1e-4)


# --- Nouveaux indicateurs, sur des résultats connus ---------------------------------------


Y_CONNUS = np.array([3.0, 9.0, 1.0, 7.0, 5.0])
X_CONNUS = np.arange(5.0)


def connus(u_y=0.2, **champs):
    return points_de(X_CONNUS, Y_CONNUS, u_y=u_y, **champs)


def test_la_mediane_d_un_nombre_impair_est_le_point_du_milieu():
    page, lire, _points = connus()
    resultat = indicateur(page, lire, "mediane")
    assert resultat.valeur == 5.0
    assert resultat.incertitude_type == pytest.approx(0.2)


def test_la_mediane_d_un_nombre_pair_est_la_moyenne_des_deux_du_milieu():
    x, y = np.arange(4.0), np.array([1.0, 2.0, 4.0, 8.0])
    page, lire, _points = points_de(x, y, u_y=0.2)
    resultat = indicateur(page, lire, "mediane")
    assert resultat.valeur == 3.0
    assert resultat.incertitude_type == pytest.approx(0.2 / math.sqrt(2))


@pytest.mark.parametrize("rang", [0.0, 10.0, 25.0, 50.0, 90.0, 100.0])
def test_le_percentile_concorde_avec_numpy(rang):
    page, lire, _points = connus()
    resultat = indicateur(page, lire, "percentile", parametre=rang)
    assert resultat.valeur == pytest.approx(np.percentile(Y_CONNUS, rang))
    assert resultat.parametre == rang


def test_l_incertitude_d_un_percentile_interpole_est_celle_calculee_a_la_main():
    """P25 de 5 points : rang 1 exact, soit la valeur triée 3 ; P30 : 3 + 0,2 · (5 − 3)."""
    page, lire, _points = connus()
    resultat = indicateur(page, lire, "percentile", parametre=30.0)
    assert resultat.valeur == pytest.approx(3.4)
    assert resultat.incertitude_type == pytest.approx(0.2 * math.hypot(0.8, 0.2))


@pytest.mark.parametrize("rang", [-1.0, 101.0, None])
def test_un_rang_impossible_s_explique(rang):
    page, lire, _points = connus()
    with pytest.raises(ErreurAjustement, match="rang du percentile"):
        indicateur(page, lire, "percentile", parametre=rang)


def test_l_ecart_type_concorde_avec_numpy_et_son_incertitude_avec_le_calcul_a_la_main():
    page, lire, _points = connus()
    resultat = indicateur(page, lire, "ecart_type")
    s = np.std(Y_CONNUS, ddof=1)
    assert resultat.valeur == pytest.approx(s)
    # ds/dy_i = (y_i − ȳ) / ((n − 1)·s) : u² = u² · Σ(y_i − ȳ)² / ((n − 1)·s)² = u² / (n − 1).
    assert resultat.incertitude_type == pytest.approx(0.2 / math.sqrt(4))
    assert resultat.unite == "MBq"


def test_un_ecart_type_nul_s_explique():
    page, lire, _points = points_de(X_CONNUS, np.full(5, 3.0), u_y=0.1)
    with pytest.raises(ErreurAjustement, match="l'écart type vaut 0"):
        indicateur(page, lire, "ecart_type")


def test_un_certificat_commun_n_agit_pas_sur_l_ecart_type_absolu_de_valeurs_egales():
    """Tous multipliés par (1 + ε) : s devient s·(1 + ε), donc u(s) = 1 % de s pour lui seul."""
    page, lire, _points = points_de(
        X_CONNUS, Y_CONNUS, u_y=0.0, composantes_y=(rel(1.0),)
    )
    resultat = indicateur(page, lire, "ecart_type")
    assert resultat.incertitude_type == pytest.approx(0.01 * resultat.valeur)


def test_le_coefficient_de_variation_est_en_pourcent():
    page, lire, _points = connus()
    resultat = indicateur(page, lire, "coefficient_variation")
    attendu = 100 * np.std(Y_CONNUS, ddof=1) / np.mean(Y_CONNUS)
    assert resultat.valeur == pytest.approx(attendu)
    assert resultat.unite == "%"


def test_un_coefficient_de_variation_sur_une_moyenne_nulle_s_explique():
    page, lire, _points = points_de(X_CONNUS, Y_CONNUS - 5.0, u_y=0.1)
    with pytest.raises(ErreurAjustement, match="moyenne est nulle"):
        indicateur(page, lire, "coefficient_variation")


def test_la_moyenne_quadratique_la_somme_l_etendue_et_le_nombre_de_points():
    page, lire, _points = connus()
    rms = indicateur(page, lire, "moyenne_quadratique")
    assert rms.valeur == pytest.approx(math.sqrt(np.mean(Y_CONNUS**2)))
    somme = indicateur(page, lire, "somme")
    assert somme.valeur == 25.0
    assert somme.incertitude_type == pytest.approx(0.2 * math.sqrt(5))
    etendue = indicateur(page, lire, "etendue")
    assert etendue.valeur == 8.0
    assert etendue.incertitude_type == pytest.approx(0.2 * math.sqrt(2))
    nombre = indicateur(page, lire, "nombre_points", de=1.0, a=3.0)
    assert (nombre.valeur, nombre.incertitude_type, nombre.unite) == (3.0, 0.0, "1")


def test_un_certificat_commun_n_agit_pas_sur_l_etendue_d_un_zero_commun():
    commune = incertitude(0.5, "MBq", erreur_commune="ZERO", source="zéro")
    page, lire, _points = points_de(
        X_CONNUS, Y_CONNUS, u_y=0.1, composantes_y=(commune,)
    )
    etendue = indicateur(page, lire, "etendue")
    assert etendue.incertitude_type == pytest.approx(0.1 * math.sqrt(2))


def test_le_max_et_le_min_des_points_donnent_leur_abscisse_et_son_incertitude():
    page, lire, _points = points_de(X_CONNUS, Y_CONNUS, u_y=0.2, u_x=0.05)
    maximum = indicateur(page, lire, "maximum")
    minimum = indicateur(page, lire, "minimum")
    assert (maximum.valeur, maximum.position) == (9.0, 1.0)
    assert (minimum.valeur, minimum.position) == (1.0, 2.0)
    assert maximum.incertitude_position == pytest.approx(0.05)
    assert maximum.unite_position == "h"


def gaussienne_ajustee():
    """A = 10, m = 5, s = 1,5 : largeur à mi-hauteur = 2·√(2·ln 2)·s."""
    x = np.linspace(0.0, 10.0, 41)
    y = 10.0 * np.exp(-((x - 5.0) ** 2) / (2 * 1.5**2))
    page, lire, points = points_de(x, y, u_y=0.05)
    ajustement, resultat = creer_ajustement(
        points, "fit", MODELES["gaussienne"].formule, "gaussienne"
    )
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    return avec, lire, ajustement, resultat


def test_le_maximum_de_la_gaussienne_donne_le_centre_et_son_incertitude():
    page, lire, ajustement, resultat = gaussienne_ajustee()
    maximum = indicateur(page, lire, "maximum", "ajustement", ajustement=ajustement)
    assert maximum.valeur == pytest.approx(10.0, rel=1e-5)
    assert maximum.position == pytest.approx(5.0, abs=1e-4)
    assert maximum.unite_position == "h"
    # L'abscisse du sommet est le coefficient m : même incertitude que lui.
    assert maximum.incertitude_position == pytest.approx(
        math.sqrt(resultat.covariance[1, 1]), rel=1e-3
    )


def test_la_largeur_a_mi_hauteur_de_la_gaussienne_est_celle_de_la_formule():
    page, lire, ajustement, resultat = gaussienne_ajustee()
    largeur = indicateur(
        page, lire, "largeur_mi_hauteur", "ajustement", ajustement=ajustement
    )
    facteur = 2 * math.sqrt(2 * math.log(2))
    assert largeur.valeur == pytest.approx(facteur * 1.5, rel=1e-5)
    assert largeur.unite == "h"
    assert largeur.incertitude_type == pytest.approx(
        facteur * math.sqrt(resultat.covariance[2, 2]), rel=1e-3
    )


def test_une_largeur_sans_pic_s_explique():
    page, lire, points = droite_incertaine()
    ajustement, _r = creer_ajustement(
        points, "fit", MODELES["droite"].formule, "droite"
    )
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    with pytest.raises(ErreurAjustement, match="au bord"):
        indicateur(
            avec, lire, "largeur_mi_hauteur", "ajustement", ajustement=ajustement
        )


def test_l_abscisse_d_une_valeur_sur_une_droite_est_celle_calculee_a_la_main():
    """y = 2x + 1 = 11 en x = 5. x = (y − b) / a, donc g = (−5/2, −1/2) pour (a, b)."""
    page, lire, points = droite_incertaine()
    ajustement, resultat = creer_ajustement(
        points, "fit", MODELES["droite"].formule, "droite"
    )
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    trouve = indicateur(
        avec,
        lire,
        "abscisse_de_valeur",
        "ajustement",
        ajustement=ajustement,
        parametre=11.0,
    )
    assert trouve.valeur == pytest.approx(5.0, rel=1e-6)
    assert trouve.unite == "h"
    g = np.array([-2.5, -0.5])
    assert trouve.incertitude_type == pytest.approx(
        math.sqrt(g @ resultat.covariance @ g), rel=1e-4
    )


def test_une_valeur_jamais_atteinte_s_explique():
    page, lire, points = droite_incertaine()
    ajustement, _r = creer_ajustement(
        points, "fit", MODELES["droite"].formule, "droite"
    )
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    with pytest.raises(ErreurAjustement, match="n'atteint pas 99"):
        indicateur(
            avec,
            lire,
            "abscisse_de_valeur",
            "ajustement",
            ajustement=ajustement,
            parametre=99.0,
        )
    with pytest.raises(ErreurAjustement, match="valeur de y"):
        indicateur(
            avec, lire, "abscisse_de_valeur", "ajustement", ajustement=ajustement
        )


def test_une_methode_propre_aux_points_est_refusee_sur_la_fonction():
    page, lire, points = droite_incertaine()
    ajustement, _r = creer_ajustement(
        points, "fit", MODELES["droite"].formule, "droite"
    )
    avec = page.model_copy(update={"mesures": (*page.mesures, ajustement)})
    with pytest.raises(ErreurAjustement, match="se calcule sur les points"):
        indicateur(avec, lire, "mediane", "ajustement", ajustement=ajustement)
    with pytest.raises(ErreurAjustement, match="sur la fonction ajustée"):
        indicateur(avec, lire, "largeur_mi_hauteur")


def test_l_abscisse_d_un_maximum_s_utilise_dans_un_calcul():
    page, lire, _points = points_de(X_CONNUS, Y_CONNUS, u_y=0.2, u_x=0.05)
    pic = indicateur(page, lire, "maximum")
    avec = page.model_copy(update={"mesures": (*page.mesures, pic)})
    budget = calculer(avec, "i.x * 2")
    assert budget.valeur == 2.0
    assert budget.unite == "h"
    assert budget.incertitude_type == pytest.approx(0.1)
    with pytest.raises(ErreurCalcul, match="pas d'abscisse « z »"):
        calculer(avec, "i.z")
