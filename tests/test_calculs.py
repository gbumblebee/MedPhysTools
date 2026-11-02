"""Vérifie les calculs d'une page : formules sûres, unités, erreurs communes et budgets.

Les résultats attendus sont calculés à la main, indépendamment du code.
"""

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from pymphub.calculs import (
    ErreurCalcul,
    FormuleRefusee,
    calculer,
    creer_derivee,
    etat_derivee,
)
from pymphub.incertitudes import Composante
from pymphub.page import (
    ColonneIncertitude,
    ColonneNumerique,
    GrandeurDerivee,
    Identifiant,
    Page,
    Scalaire,
    Tableau,
    depuis_json,
    vers_json,
)

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))


def composante(valeur, unite="MBq", **champs):
    return Composante(
        type="B",
        valeur=valeur,
        unite=None if champs.get("relative") else unite,
        **{"source": "s", **champs},
    )


def scalaire(nom, valeur, unite, *composantes):
    return Scalaire(nom=nom, valeur=valeur, unite=unite, composantes=composantes)


def page_de(*mesures):
    return Page(
        id="2026-11-14_calcul_0001",
        titre="Calcul",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=mesures,
    )


CERTIFICAT = {"relative": True, "source": "certificat", "erreur_commune": "CERT-1"}


# --- Erreurs communes : calculs faits à la main ------------------------------------------


def test_le_rapport_compense_exactement_un_certificat_commun():
    """100 et 50 MBq à 1 % du même certificat, seule source : le rapport est exact."""
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0, **CERTIFICAT)),
        scalaire("b", 50.0, "MBq", composante(1.0, **CERTIFICAT)),
    )
    budget = calculer(page, "a / b")
    assert budget.valeur == pytest.approx(2.0)
    assert budget.unite == "1"
    assert budget.incertitude_type == pytest.approx(0.0, abs=1e-12)


def test_le_rapport_garde_seulement_les_erreurs_propres():
    """u(R)/R = √((0,5/100)² + (0,2/50)²), car le certificat se compense."""
    page = page_de(
        scalaire(
            "a",
            100.0,
            "MBq",
            composante(1.0, **CERTIFICAT),
            composante(0.5, source="résolution"),
        ),
        scalaire(
            "b",
            50.0,
            "MBq",
            composante(1.0, **CERTIFICAT),
            composante(0.2, source="résolution"),
        ),
    )
    budget = calculer(page, "a / b")
    assert budget.incertitude_type == pytest.approx(2 * math.sqrt(0.005**2 + 0.004**2))
    lignes = {ligne.source: ligne for ligne in budget.lignes}
    assert set(lignes) == {"a : résolution", "b : résolution", "CERT-1"}
    assert lignes["CERT-1"].incertitude_type == pytest.approx(0.0, abs=1e-12)
    assert lignes["CERT-1"].contribution_pourcent == pytest.approx(0.0, abs=1e-9)
    assert lignes["a : résolution"].incertitude_type == pytest.approx(0.01)
    assert lignes["b : résolution"].incertitude_type == pytest.approx(0.008)
    total = sum(ligne.contribution_pourcent for ligne in budget.lignes)
    assert total == pytest.approx(100.0)


def test_la_somme_additionne_le_certificat_commun():
    """u² = (1 + 0,5)² + 0,5² + 0,2² = 2,54, plus qu'avec des erreurs indépendantes."""
    page = page_de(
        scalaire(
            "a",
            100.0,
            "MBq",
            composante(1.0, **CERTIFICAT),
            composante(0.5, source="r"),
        ),
        scalaire(
            "b",
            50.0,
            "MBq",
            composante(1.0, **CERTIFICAT),
            composante(0.2, source="r"),
        ),
    )
    assert calculer(page, "a + b").incertitude_type == pytest.approx(math.sqrt(2.54))


def test_une_erreur_commune_absolue_s_annule_dans_une_difference():
    """Le même zéro de ±1 MBq sur deux mesures : leur écart n'en dépend pas."""
    commune = {"source": "zéro", "erreur_commune": "ZERO"}
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0, **commune)),
        scalaire("b", 50.0, "MBq", composante(1.0, **commune)),
    )
    assert calculer(page, "a - b").incertitude_type == pytest.approx(0.0, abs=1e-12)


def test_le_rapport_est_partiel_si_l_erreur_commune_est_absolue():
    """R = a/b, a = 100 + e, b = 50 + e : dR/de = 1/50 − 100/50² = −0,02."""
    commune = {"source": "zéro", "erreur_commune": "ZERO"}
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0, **commune)),
        scalaire("b", 50.0, "MBq", composante(1.0, **commune)),
    )
    assert calculer(page, "a / b").incertitude_type == pytest.approx(0.02)


def test_sans_repere_les_mesures_restent_independantes():
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0, source="certificat")),
        scalaire("b", 100.0, "MBq", composante(1.0, source="certificat")),
    )
    assert calculer(page, "a / b").incertitude_type == pytest.approx(math.sqrt(2) / 100)


def test_deux_reperes_differents_restent_independants():
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0, erreur_commune="C1")),
        scalaire("b", 100.0, "MBq", composante(1.0, erreur_commune="C2")),
    )
    assert calculer(page, "a - b").incertitude_type == pytest.approx(math.sqrt(2))


def test_une_meme_grandeur_utilisee_deux_fois_est_correlee():
    page = page_de(scalaire("a", 100.0, "MBq", composante(1.0)))
    assert calculer(page, "a - a").incertitude_type == pytest.approx(0.0, abs=1e-12)
    assert calculer(page, "a + a").incertitude_type == pytest.approx(2.0)


def test_un_repere_contradictoire_s_explique():
    commune = {"source": "c", "erreur_commune": "CERT"}
    page = page_de(
        scalaire("a", 1.0, "MBq", composante(0.1, ddl=4, **commune)),
        scalaire("b", 1.0, "MBq", composante(0.1, ddl=9, **commune)),
    )
    with pytest.raises(ErreurCalcul, match="degrés de liberté"):
        calculer(page, "a / b")


def test_l_api_de_lecture_compense_aussi_le_certificat(tmp_path):
    from datetime import date

    from pymphub import Archive

    arc = Archive.creer(tmp_path / "A", "S", "R", date(2026, 11, 1))
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0, **CERTIFICAT)),
        scalaire("b", 50.0, "MBq", composante(1.0, **CERTIFICAT)),
    )
    arc.creer_page(page)
    lue = arc.page(page.id)
    from GTC import uncertainty

    rapport = lue.mesures["a"].incertitude / lue.mesures["b"].incertitude
    assert uncertainty(rapport) == pytest.approx(0.0, abs=1e-12)
    assert lue.mesures["a"].elargie() == pytest.approx(2.0)


# --- Décroissance : calcul fait à la main -------------------------------------------------


def page_decroissance(unite_t12="h", t12=6.0, u_t12=0.01):
    return page_de(
        scalaire("A0", 100.0, "MBq", composante(1.0, source="certificat")),
        scalaire("t", 2.0, "h", composante(0.1, "h", source="horloge")),
        scalaire("T12", t12, unite_t12, composante(u_t12, unite_t12, source="période")),
    )


def attendu_decroissance():
    """A = A0·2^(−t/T). Dérivées partielles écrites à la main."""
    a0, t, t12 = 100.0, 2.0, 6.0
    lam = math.log(2) / t12
    a = a0 * math.exp(-lam * t)
    u_a0 = (a / a0) * 1.0
    u_t = a * lam * 0.1
    u_t12 = a * lam * t / t12 * 0.01
    return a, math.sqrt(u_a0**2 + u_t**2 + u_t12**2), (u_a0, u_t, u_t12)


def test_la_formule_de_decroissance_donne_valeur_u_et_budget():
    page = page_decroissance()
    budget = calculer(page, "A0 * exp(-ln(2) * t / T12)")
    a, u, (u_a0, u_t, u_t12) = attendu_decroissance()
    assert budget.valeur == pytest.approx(a)
    assert budget.unite == "MBq"
    assert budget.incertitude_type == pytest.approx(u)
    assert budget.elargie(2.0) == pytest.approx(2 * u)
    lignes = {ligne.source: ligne.incertitude_type for ligne in budget.lignes}
    assert lignes["A0 : certificat"] == pytest.approx(u_a0)
    assert lignes["t : horloge"] == pytest.approx(u_t)
    assert lignes["T12 : période"] == pytest.approx(u_t12)


def test_des_heures_et_des_minutes_se_convertissent_dans_la_formule():
    page = page_decroissance("min", 360.0, 0.6)
    budget = calculer(page, "A0 * exp(-ln(2) * t / T12)")
    a, u, _composantes = attendu_decroissance()
    assert budget.valeur == pytest.approx(a)
    assert budget.incertitude_type == pytest.approx(u)
    assert budget.unite == "MBq"


# --- Unités ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("formule", "valeur", "unite"),
    [
        ("a * t", 200.0, "MBq*h"),
        ("a / v", 10.0, "MBq/mL"),
        ("a + k", 100.5, "MBq"),
        ("k + a", 100500.0, "kBq"),
        ("a / k", 200.0, "1"),
        ("t / m", 2.0, "1"),
        ("v ** 2", 100.0, "mL**2"),
        ("1 / t", 0.5, "1/h"),
        ("2 * a", 200.0, "MBq"),
        ("-a", -100.0, "MBq"),
        ("+a", 100.0, "MBq"),
        ("sqrt(a * a)", 100.0, "MBq"),
        ("1 + t / m", 3.0, "1"),
        ("sin(pi / 2)", 1.0, "1"),
        ("cos(angle)", 0.5, "1"),
        ("log10(1000)", 3.0, "1"),
        ("(a / k) ** 2", 40000.0, "1"),
    ],
)
def test_les_unites_du_resultat(formule, valeur, unite):
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0)),
        scalaire("k", 500.0, "kBq", composante(1.0, "kBq")),
        scalaire("t", 2.0, "h", composante(0.1, "h")),
        scalaire("m", 60.0, "min", composante(1.0, "min")),
        scalaire("v", 10.0, "mL", composante(0.1, "mL")),
        scalaire("angle", 60.0, "deg", composante(1.0, "deg")),
    )
    budget = calculer(page, formule)
    assert budget.valeur == pytest.approx(valeur)
    assert budget.unite == unite


def test_l_unite_demandee_est_appliquee_a_la_valeur_et_a_l_incertitude():
    page = page_de(scalaire("a", 100.0, "MBq", composante(1.0)))
    budget = calculer(page, "a", unite="kBq")
    assert budget.valeur == pytest.approx(100000.0)
    assert budget.incertitude_type == pytest.approx(1000.0)
    assert budget.unite == "kBq"


def test_un_rapport_de_temperatures_se_fait_en_kelvins():
    """293,15 K / 273,15 K : en °C, le rapport 20 / 0 n'aurait aucun sens."""
    page = page_de(
        scalaire("T1", 20.0, "degC", composante(0.5, "degC")),
        scalaire("T0", 0.0, "degC", composante(0.5, "degC")),
    )
    budget = calculer(page, "T1 / T0")
    assert budget.valeur == pytest.approx(293.15 / 273.15)
    assert budget.unite == "1"


def test_un_ecart_de_temperatures_reste_un_ecart():
    page = page_de(
        scalaire("T1", 20.0, "degC", composante(0.5, "degC")),
        scalaire("T0", 15.0, "degC", composante(0.5, "degC")),
    )
    budget = calculer(page, "T1 - T0")
    assert budget.valeur == pytest.approx(5.0)
    assert budget.unite == "K"
    assert budget.incertitude_type == pytest.approx(0.5 * math.sqrt(2))
    with pytest.raises(ErreurCalcul, match="reste en K"):
        calculer(page, "T1 - T0", unite="degC")


@pytest.mark.parametrize(
    ("formule", "unite", "message"),
    [
        ("a + t", "", "pas compatibles"),
        ("exp(a)", "", "sans dimension"),
        ("2 ** a", "", "sans dimension"),
        ("sin(t)", "", "sans dimension"),
        ("a / 0", "", "division par zéro"),
        ("sqrt(0 - a / a)", "", "pas un nombre réel"),
        ("ln(0 - a / a)", "", "domaine"),
        ("sqrt(a)", "", "unité utilisable"),
        ("a", "mm", "s'exprimer"),
        ("a", "pomme", "non reconnue"),
        ("a ** (t / t2)", "", "puissance exacte"),
        ("lot + 1", "", "n'est pas une valeur numérique"),
        ("absent + 1", "", "Aucune grandeur"),
        ("1e308 * 1e308", "", "pas un nombre réel"),
    ],
)
def test_un_calcul_impossible_s_explique(formule, unite, message):
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0)),
        scalaire("t", 2.0, "h", composante(0.1, "h")),
        scalaire("t2", 4.0, "h", composante(0.1, "h")),
        Identifiant(nom="lot", valeur="007"),
    )
    with pytest.raises(ErreurCalcul) as erreur:
        calculer(page, formule, unite=unite)
    assert message in str(erreur.value)


# --- Une formule ne peut pas exécuter de code --------------------------------------------


@pytest.mark.parametrize(
    "formule",
    [
        "__import__('os').system('touch PIRATE')",
        "open('PIRATE', 'w')",
        "exec('import os')",
        "eval('1+1')",
        "(lambda: 1)()",
        "[x for x in (1, 2)]",
        "a.__class__",
        "a.__class__.__mro__[1].__subclasses__()",
        "().__class__",
        "'texte'",
        "b'octets'",
        "True",
        "None",
        "1 if a else 2",
        "a < 2",
        "a and 2",
        "not a",
        "~1",
        "1 << 2",
        "1 | 2",
        "1 % 2",
        "2 // 3",
        "a[0]",
        "a.b",
        "a.b[1:2]",
        "a.b['x']",
        "a.b[True]",
        "exp(1, 2)",
        "exp()",
        "exp(x=1)",
        "exp(*[1])",
        "exp.__call__(1)",
        "print(1)",
        "abs(1)",
        "(y := 2)",
        "{1: 2}",
        "{1}",
        "[1]",
        "f'{a}'",
        "1j",
        "1,5",
        "2 ^ 3",
        "a = 1",
        "import os",
        "1; 2",
        "",
        "   ",
        "\x00",
        "(" * 500,
        "1 + " * 200 + "1",
        "9" * 600,
    ],
)
def test_une_formule_interdite_est_refusee_sans_rien_executer(
    formule, tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    page = page_de(scalaire("a", 2.0, "MBq", composante(0.1)))
    with pytest.raises(ErreurCalcul):
        calculer(page, formule)
    assert list(Path(tmp_path).iterdir()) == []


def test_une_longue_suite_de_signes_ne_fait_pas_planter():
    assert calculer(page_de(), "-" * 490 + "1").valeur == 1.0


def test_une_puissance_d_une_grandeur_par_elle_meme_est_exacte():
    """t / t vaut exactement 1, sans incertitude : la puissance reste permise."""
    page = page_de(
        scalaire("a", 100.0, "MBq", composante(1.0)),
        scalaire("t", 2.0, "h", composante(0.1, "h")),
    )
    assert calculer(page, "a ** (t / t)").unite == "MBq"


def test_une_tres_grande_puissance_ne_sature_pas_la_memoire():
    page = page_de(scalaire("a", 2.0, "MBq", composante(0.1)))
    with pytest.raises(ErreurCalcul):
        calculer(page, "9 ** 9 ** 9 ** 9")


@pytest.mark.parametrize(
    ("formule", "attendu"),
    [("2 ^ 3", "écrire **"), ("1,5", "point"), ("1 + ", "mal écrite")],
)
def test_une_faute_courante_reçoit_un_conseil(formule, attendu):
    with pytest.raises(FormuleRefusee) as erreur:
        calculer(page_de(), formule)
    assert attendu in str(erreur.value)


def test_une_grandeur_peut_s_appeler_comme_une_constante():
    page = page_de(scalaire("pi", 3.0, "MBq", composante(0.1)))
    assert calculer(page, "pi").unite == "MBq"


# --- Cellules de tableau -----------------------------------------------------------------


def page_tableau():
    return page_de(
        Tableau(
            nom="t",
            fichier="brut/t.csv",
            colonnes=(
                ColonneNumerique(
                    nom="a",
                    unite="MBq",
                    composantes=(composante(1.0, **CERTIFICAT),),
                ),
                ColonneIncertitude(
                    nom="u_a",
                    composante_de="a",
                    type="B",
                    unite="MBq",
                    source="cellule",
                ),
            ),
        )
    )


def lire(_nom):
    return pd.DataFrame({"a": [100.0, 50.0], "u_a": [0.5, 0.2]})


def test_une_cellule_s_utilise_avec_ses_composantes_et_le_certificat_commun():
    budget = calculer(page_tableau(), "t.a[1] / t.a[2]", lire)
    assert budget.valeur == pytest.approx(2.0)
    assert budget.incertitude_type == pytest.approx(2 * math.sqrt(0.005**2 + 0.004**2))
    assert {ligne.source for ligne in budget.lignes} == {
        "t.a[1] : cellule",
        "t.a[2] : cellule",
        "CERT-1",
    }


def test_une_meme_cellule_utilisee_deux_fois_est_correlee():
    budget = calculer(page_tableau(), "t.a[1] - t.a[1]", lire)
    assert budget.incertitude_type == pytest.approx(0.0, abs=1e-12)


@pytest.mark.parametrize(
    ("formule", "message"),
    [
        ("t.a[0]", "pas de ligne 0"),
        ("t.a[3]", "pas de ligne 3"),
        ("t.u_a[1]", "Aucune colonne numérique"),
        ("t.absente[1]", "Aucune colonne numérique"),
        ("z.a[1]", "Aucun tableau"),
    ],
)
def test_une_cellule_absente_s_explique(formule, message):
    with pytest.raises(ErreurCalcul) as erreur:
        calculer(page_tableau(), formule, lire)
    assert message in str(erreur.value)


# --- Grandeur dérivée --------------------------------------------------------------------


@pytest.fixture
def page_avec_derivee():
    base = page_decroissance()
    derivee = creer_derivee(
        base, "A", "A0 * exp(-ln(2) * t / T12)", commentaire="À 11 h 30"
    )
    return base.model_copy(update={"mesures": (*base.mesures, derivee)})


def test_une_derivee_garde_sa_formule_son_resultat_et_son_budget(page_avec_derivee):
    derivee = page_avec_derivee.grandeur("A")
    a, u, _composantes = attendu_decroissance()
    assert isinstance(derivee, GrandeurDerivee)
    assert derivee.formule == "A0 * exp(-ln(2) * t / T12)"
    assert derivee.valeur == pytest.approx(a)
    assert derivee.incertitude_type == pytest.approx(u)
    assert derivee.unite == "MBq"
    assert derivee.commentaire == "À 11 h 30"
    assert {ligne.source for ligne in derivee.budget_calcul} == {
        "A0 : certificat",
        "t : horloge",
        "T12 : période",
    }
    budget = derivee.budget()
    assert budget.elargie(2.0) == pytest.approx(2 * u)
    assert {ligne.source for ligne in budget.lignes} == {
        "A0 : certificat",
        "t : horloge",
        "T12 : période",
    }


def test_une_derivee_ne_remplace_aucune_valeur_brute(page_avec_derivee):
    base = page_decroissance()
    assert page_avec_derivee.mesures[: len(base.mesures)] == base.mesures
    assert page_avec_derivee.grandeur("A0").valeur == 100.0


def test_une_derivee_se_relit_a_l_identique(page_avec_derivee):
    relue = depuis_json(vers_json(page_avec_derivee))
    assert relue == page_avec_derivee
    assert isinstance(relue.grandeur("A"), GrandeurDerivee)


def test_un_nom_deja_pris_est_refuse_par_la_page(page_avec_derivee):
    autre = page_avec_derivee.grandeur("A").model_copy(update={"nom": "A0"})
    with pytest.raises(ValueError, match="plusieurs fois"):
        Page.model_validate(
            page_avec_derivee.model_dump()
            | {"mesures": (*page_avec_derivee.mesures, autre)}
        )


def test_une_derivee_s_utilise_dans_une_autre_formule_sans_perdre_les_correlations(
    page_avec_derivee,
):
    budget = calculer(page_avec_derivee, "A / A0")
    # Le certificat de A0 se compense : seules les erreurs sur t et T12 restent.
    lam = math.log(2) / 6.0
    r = math.exp(-lam * 2.0)
    attendu = r * math.sqrt((lam * 0.1) ** 2 + (lam * 2.0 / 6.0 * 0.01) ** 2)
    assert budget.incertitude_type == pytest.approx(attendu)


def test_un_calcul_circulaire_s_explique():
    base = page_de(scalaire("a", 1.0, "MBq", composante(0.1)))
    x = creer_derivee(base, "x", "a")
    y = x.model_copy(update={"nom": "y", "formule": "x"})
    boucle = x.model_copy(update={"formule": "y"})
    page = page_de(base.mesures[0], boucle, y)
    with pytest.raises(ErreurCalcul, match="dépend de lui-même"):
        calculer(page, "x")


def test_un_resultat_a_jour_n_est_pas_signale(page_avec_derivee):
    assert etat_derivee(page_avec_derivee, page_avec_derivee.grandeur("A")) is None


def test_un_resultat_perime_est_signale(page_avec_derivee):
    mesures = tuple(
        g.model_copy(update={"valeur": 200.0}) if g.nom == "A0" else g
        for g in page_avec_derivee.mesures
    )
    page = page_avec_derivee.model_copy(update={"mesures": mesures})
    message = etat_derivee(page, page.grandeur("A"))
    assert "à recalculer" in message
    a, _u, _composantes = attendu_decroissance()
    assert f"{2 * a:.6g} MBq" in message


def test_un_resultat_dont_une_grandeur_a_disparu_est_signale(page_avec_derivee):
    mesures = tuple(g for g in page_avec_derivee.mesures if g.nom != "A0")
    page = page_avec_derivee.model_copy(update={"mesures": mesures})
    assert "Calcul impossible" in etat_derivee(page, page.grandeur("A"))


def test_une_derivee_ne_s_exporte_pas_dans_un_gabarit(page_avec_derivee):
    from pymphub.gabarit import creer_gabarit

    with pytest.raises(ValueError, match="résultat de calcul"):
        creer_gabarit(page_avec_derivee, "g", ["A"])


def test_un_tableau_illisible_s_explique():
    def refuser(_nom):
        raise OSError("disque absent")

    with pytest.raises(ErreurCalcul, match="disque absent"):
        calculer(page_tableau(), "t.a[1]", refuser)
