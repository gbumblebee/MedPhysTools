"""Vérifie la création des gabarits, leur format TOML et la conformité des pages."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from medphystools.gabarit import (
    Gabarit,
    PageNonConforme,
    colonnes_choisissables,
    creer_gabarit,
    depuis_toml,
    onglets_choisissables,
    page_depuis_gabarit,
    rattacher_gabarit,
    verifier_conformite,
    vers_toml,
)
from medphystools.page import Page, Scalaire, depuis_json, vers_json

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))


def composante(unite):
    return {"type": "B", "valeur": 1.0, "unite": unite, "source": "certificat secret"}


def scalaire(nom="activite", valeur=123.456, unite="MBq"):
    return {
        "nature": "scalaire",
        "nom": nom,
        "valeur": valeur,
        "unite": unite,
        "composantes": [composante(unite)],
    }


def tableau():
    return {
        "nature": "tableau",
        "nom": "decroissance",
        "fichier": "brut/decroissance.csv",
        "colonnes": [
            {"nature": "identifiant", "nom": "lot"},
            {
                "nature": "numerique",
                "nom": "heure",
                "unite": "h",
                "composantes": [composante("h")],
            },
            {"nature": "numerique", "nom": "mesure", "unite": "MBq"},
            {
                "nature": "incertitude",
                "nom": "u_mesure",
                "composante_de": "mesure",
                "type": "B",
                "unite": "MBq",
                "source": "afficheur",
            },
        ],
    }


def page(*mesures, **champs):
    return Page.model_validate(
        {
            "id": "2026-11-14_activimetre-linearite_0007",
            "titre": "Linéarité",
            "date_mesure": HEURE,
            "date_saisie": HEURE,
            "auteur": "G.",
            "mesures": mesures,
        }
        | champs
    )


def page_source():
    return page(
        scalaire(),
        scalaire("temperature", 21.5, "degC"),
        {"nature": "identifiant", "nom": "numero_serie", "valeur": "NS-007"},
        tableau(),
        {
            "nature": "fichier",
            "nom": "serie_ct",
            "stockage": {
                "mode": "copie",
                "empreinte": "a" * 64,
                "nom_origine": "serie.dcm.zip",
            },
        },
    )


VARIABLES = ["activite", "numero_serie", "decroissance", "serie_ct"]


@pytest.fixture
def gabarit():
    return creer_gabarit(page_source(), "activimetre.linearite", VARIABLES)


def test_le_gabarit_contient_les_variables_choisies(gabarit):
    assert gabarit.reference == "activimetre.linearite@1"
    assert [v.nom for v in gabarit.variables] == VARIABLES
    assert [v.nature for v in gabarit.variables] == [
        "scalaire",
        "identifiant",
        "tableau",
        "fichier",
    ]
    assert gabarit.variables[0].unite == "MBq"


def test_le_gabarit_ne_garde_que_les_variables_choisies(gabarit):
    assert "temperature" not in [v.nom for v in gabarit.variables]


def test_le_gabarit_ne_contient_aucune_donnee(gabarit):
    texte = vers_toml(gabarit)
    for donnee in ["123.456", "NS-007", "certificat", "afficheur", "aaaa", "serie.dcm"]:
        assert donnee not in texte


def test_les_colonnes_d_incertitude_ne_sont_pas_reprises(gabarit):
    tableau_attendu = gabarit.variables[2]
    assert [c.nom for c in tableau_attendu.colonnes] == ["lot", "heure", "mesure"]
    assert tableau_attendu.colonnes[0].unite is None
    assert tableau_attendu.colonnes[1].unite == "h"


def test_choix_de_colonnes_d_un_tableau():
    gabarit = creer_gabarit(
        page_source(),
        "activimetre.linearite",
        ["decroissance.heure", "decroissance.mesure"],
    )
    assert len(gabarit.variables) == 1
    assert gabarit.variables[0].nom == "decroissance"
    assert [c.nom for c in gabarit.variables[0].colonnes] == ["heure", "mesure"]


@pytest.mark.parametrize(
    ("variables", "erreur"),
    [
        (["absente"], KeyError),
        (["decroissance.absente"], KeyError),
        (["decroissance.u_mesure"], KeyError),
        (["activite.colonne"], ValueError),
        (["activite", "activite"], ValueError),
        (["decroissance.heure", "decroissance.heure"], ValueError),
        (["decroissance", "decroissance.heure"], ValueError),
        ([], ValueError),
    ],
)
def test_choix_de_variables_invalide_refuse(variables, erreur):
    with pytest.raises(erreur):
        creer_gabarit(page_source(), "activimetre.linearite", variables)


def test_aller_retour_toml_identique(gabarit):
    texte = vers_toml(gabarit)
    assert depuis_toml(texte) == gabarit
    assert vers_toml(depuis_toml(texte)) == texte


def test_le_toml_est_lisible(gabarit):
    texte = vers_toml(gabarit)
    assert 'nom = "activimetre.linearite"' in texte
    assert "[[variables]]" in texte


@pytest.mark.parametrize(
    "texte",
    [
        'nom = "a"\nversion = 1\nvariables = []\n',
        'nom = "A B"\nversion = 1\n[[variables]]\nnature = "identifiant"\nnom = "lot"\n',
        'nom = "a"\nversion = 0\n[[variables]]\nnature = "identifiant"\nnom = "lot"\n',
        'nom = "a"\nversion = 1\n[[variables]]\nnature = "scalaire"\nnom = "x"\n',
        'nom = "a"\nversion = 1\n[[variables]]\nnature = "scalaire"\nnom = "x"\nunite = "Mbq"\n',
        'nom = "a"\nversion = 1\ninconnu = 2\n[[variables]]\nnature = "identifiant"\nnom = "l"\n',
        'nom = "a"\nversion = 1\nversion_schema = 99\n[[variables]]\nnature = "identifiant"\nnom = "l"\n',
    ],
)
def test_toml_invalide_refuse(texte):
    with pytest.raises(ValidationError):
        depuis_toml(texte)


def test_variables_repetees_refusees():
    variable = {"nature": "identifiant", "nom": "lot"}
    with pytest.raises(ValidationError, match="répétées"):
        Gabarit(nom="a", version=1, variables=[variable, variable])


@pytest.mark.parametrize(
    "colonne",
    [
        {"nom": "a", "nature": "numerique"},
        {"nom": "a", "nature": "identifiant", "unite": "mm"},
    ],
)
def test_colonne_attendue_incoherente_refusee(colonne):
    variable = {"nature": "tableau", "nom": "t", "colonnes": [colonne]}
    with pytest.raises(ValidationError):
        Gabarit(nom="a", version=1, variables=[variable])


def test_la_page_source_est_conforme(gabarit):
    assert verifier_conformite(page_source(), gabarit) == []


def test_une_grandeur_en_plus_est_permise(gabarit):
    assert verifier_conformite(page_source(), gabarit) == []
    assert page_source().grandeur("temperature")


def test_une_unite_compatible_est_conforme():
    gabarit = creer_gabarit(page(scalaire()), "g", ["activite"])
    assert verifier_conformite(page(scalaire(unite="mCi")), gabarit) == []


def test_une_variable_absente_est_signalee(gabarit):
    ecarts = verifier_conformite(page(scalaire()), gabarit)
    assert [(e.variable, e.probleme) for e in ecarts] == [
        ("numero_serie", "absente"),
        ("decroissance", "absente"),
        ("serie_ct", "absente"),
    ]


def test_une_unite_incoherente_est_signalee():
    gabarit = creer_gabarit(page(scalaire()), "g", ["activite"])
    ecarts = verifier_conformite(page(scalaire(unite="mm")), gabarit)
    assert [(e.variable, e.probleme) for e in ecarts] == [("activite", "unite")]
    assert "mm" in ecarts[0].message


def test_une_nature_differente_est_signalee():
    gabarit = creer_gabarit(page(scalaire()), "g", ["activite"])
    autre = {"nature": "identifiant", "nom": "activite", "valeur": "x"}
    ecarts = verifier_conformite(page(autre), gabarit)
    assert [(e.variable, e.probleme) for e in ecarts] == [("activite", "nature")]


def test_les_ecarts_d_un_tableau_sont_signales_par_colonne():
    gabarit = creer_gabarit(page(tableau()), "g", ["decroissance"])
    modifie = tableau()
    colonnes = modifie["colonnes"]
    colonnes[0] = {
        "nature": "numerique",
        "nom": "lot",
        "unite": "1",
        "composantes": [composante("1")],
    }
    colonnes[1] = colonnes[1] | {"unite": "mm", "composantes": [composante("mm")]}
    colonnes[3]["nom"] = "u_autre"
    colonnes[2] = colonnes[2] | {"nom": "autre"}
    colonnes[3]["composante_de"] = "autre"

    ecarts = verifier_conformite(page(modifie), gabarit)
    assert [(e.variable, e.probleme) for e in ecarts] == [
        ("decroissance.lot", "nature"),
        ("decroissance.heure", "unite"),
        ("decroissance.mesure", "absente"),
    ]


def test_rattacher_une_page_conforme(gabarit):
    source = page_source()
    rattachee = rattacher_gabarit(source, gabarit)
    assert rattachee.gabarit == "activimetre.linearite@1"
    assert source.gabarit is None
    assert rattachee.mesures == source.mesures


def test_rattacher_une_page_non_conforme_est_refuse(gabarit):
    with pytest.raises(PageNonConforme, match="numero_serie"):
        rattacher_gabarit(page(scalaire()), gabarit)


def test_une_page_creee_depuis_un_gabarit_garde_sa_version_et_liste_ce_qui_reste(
    gabarit,
):
    nouvelle = page_depuis_gabarit(
        gabarit,
        id="2026-12-01_activimetre-linearite_0008",
        titre="Linéarité décembre",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
    )
    assert nouvelle.gabarit == gabarit.reference
    assert nouvelle.mesures == ()
    ecarts = verifier_conformite(nouvelle, gabarit)
    assert {e.probleme for e in ecarts} == {"absente"}
    assert [e.variable for e in ecarts] == VARIABLES


def test_une_page_remplie_selon_le_gabarit_devient_conforme(gabarit):
    remplie = page(
        scalaire(valeur=50.0),
        {"nature": "identifiant", "nom": "numero_serie", "valeur": "NS-008"},
        tableau(),
        {
            "nature": "fichier",
            "nom": "serie_ct",
            "stockage": {
                "mode": "copie",
                "empreinte": "b" * 64,
                "nom_origine": "autre.dcm.zip",
            },
        },
        gabarit=gabarit.reference,
    )
    assert verifier_conformite(remplie, gabarit) == []
    assert isinstance(remplie.grandeur("activite"), Scalaire)
    assert remplie.grandeur("activite").valeur == 50.0


def test_nouvelle_version_d_un_gabarit():
    gabarit = creer_gabarit(page(scalaire()), "g", ["activite"], version=2)
    assert gabarit.reference == "g@2"


# --- Onglets préparés ------------------------------------------------------------------


def colonne_numerique(nom, unite):
    return {
        "nature": "numerique",
        "nom": nom,
        "unite": unite,
        "composantes": [composante(unite)],
    }


def tableau_xy():
    return {
        "nature": "tableau",
        "nom": "t",
        "fichier": "brut/t.csv",
        "colonnes": [
            colonne_numerique("h", "h"),
            colonne_numerique("act", "MBq"),
        ],
    }


def ajustement(modele="Droite", formule="a * x + b"):
    return {
        "nature": "ajustement",
        "nom": "fit",
        "modele": modele,
        "formule": formule,
        "tableau": "t",
        "x": "h",
        "y": "act",
        "unite_x": "h",
        "unite_y": "MBq",
        "x_min": 0.0,
        "x_max": 2.0,
        "points": 3,
        "parametres": [
            {"nom": "a", "valeur": -3.25, "incertitude_type": 0.1, "unite": "MBq/h"},
            {"nom": "b", "valeur": 100.0, "incertitude_type": 0.2, "unite": "MBq"},
        ],
        "correlation": [[1.0, 0.0], [0.0, 1.0]],
        "methode": "moindres carrés",
        "empreinte_donnees": "0" * 64,
        "courbe": "fit_courbe",
    }


def courbe_de_fit():
    return {
        "nature": "tableau",
        "nom": "fit_courbe",
        "fichier": "brut/fit_courbe.csv",
        "colonnes": [
            colonne_numerique("x", "h"),
            colonne_numerique("y", "MBq"),
            colonne_numerique("u_y", "MBq"),
        ],
    }


NOTE_METHODE = "# Méthode\nOn ajuste $A_0 e^{-\\lambda t}$ puis {{fit.a}}."


def page_complete(modele="Droite", formule="a * x + b"):
    return page(
        tableau_xy(),
        ajustement(modele, formule),
        courbe_de_fit(),
        onglets=[
            {"id": "n", "type": "notes", "titre": "Méthode", "texte": NOTE_METHODE},
            {"id": "vide", "type": "notes", "titre": "Vide", "texte": "  "},
            {"id": "tab", "type": "tableau", "titre": "t", "grandeurs": ["t"]},
            {
                "id": "f",
                "type": "ajustements",
                "titre": "Le fit",
                "grandeurs": ["fit", "fit_courbe"],
            },
            {
                "id": "g",
                "type": "graphes",
                "titre": "Courbe",
                "reglages": {
                    "tableau": "t",
                    "x": "h",
                    "y": ["act"],
                    "echelle_y": "log",
                    "courbes": ["fit"],
                },
            },
        ],
    )


def test_seuls_les_onglets_utiles_sont_proposes():
    choix = onglets_choisissables(page_complete())
    assert [(o.id, o.titre, o.type, o.probleme) for o in choix] == [
        ("n", "Méthode", "notes", None),
        ("f", "Le fit", "ajustements", None),
        ("g", "Courbe", "graphes", None),
    ]


def test_un_graphe_d_une_colonne_calculee_ne_peut_pas_etre_repris():
    calculee = {
        "nature": "colonne_calculee",
        "nom": "log_act",
        "formule": "ln(t.act)",
        "unite": "1",
        "tableaux": ["t"],
        "valeurs": [1.0],
        "incertitudes_types": [0.1],
    }
    source = page(
        tableau_xy(),
        calculee,
        onglets=[
            {
                "id": "g",
                "type": "graphes",
                "titre": "Log",
                "reglages": {"tableau": "t", "x": "h", "y": ["log_act"]},
            }
        ],
    )
    assert "log_act" in onglets_choisissables(source)[0].probleme
    with pytest.raises(ValueError, match="log_act"):
        creer_gabarit(source, "g", ["t"], onglets=["g"])


def test_le_gabarit_garde_les_reglages_sans_aucun_resultat():
    gabarit = creer_gabarit(page_complete(), "g", ["t"], onglets=["n", "f", "g"])
    notes, fit, graphe = gabarit.onglets
    assert notes.texte == NOTE_METHODE
    assert graphe.reglages.echelle_y == "log"
    assert graphe.reglages.courbes == ("fit",)
    assert fit.prereglage.modele == "Droite"
    # Un modèle usuel estime seul son départ : rien du résultat source n'est gardé.
    assert fit.prereglage.coefficients == ()
    toml = vers_toml(gabarit)
    assert "-3.25" not in toml
    assert "chi2" not in toml
    assert "empreinte" not in toml
    assert "fit_courbe" not in toml


def test_une_formule_personnalisee_garde_ses_unites_et_son_depart():
    source = page_complete("Personnalisé", "a * x + b")
    gabarit = creer_gabarit(source, "g", ["t"], onglets=["f"])
    coefficients = gabarit.onglets[0].prereglage.coefficients
    assert [(c.nom, c.unite, c.depart) for c in coefficients] == [
        ("a", "MBq/h", -3.25),
        ("b", "MBq", 100.0),
    ]


def test_aller_retour_toml_avec_equation_et_retours_a_la_ligne():
    gabarit = creer_gabarit(page_complete(), "g", ["t"], onglets=["n", "f", "g"])
    assert depuis_toml(vers_toml(gabarit)) == gabarit
    assert depuis_toml(vers_toml(gabarit)).onglets[0].texte == NOTE_METHODE


def test_un_gabarit_de_l_ancien_format_reste_lisible():
    ancien = 'version_schema = 1\nnom = "g"\nversion = 1\n[[variables]]\nnature = "identifiant"\nnom = "lot"\n'
    relu = depuis_toml(ancien)
    assert relu.version_schema == 1
    assert relu.onglets == ()


def test_un_graphe_sur_une_colonne_decochee_est_refuse_en_l_expliquant():
    with pytest.raises(ValueError, match=r"t\.h.*décocher cet onglet"):
        creer_gabarit(page_complete(), "g", ["t.act"], onglets=["g"])
    # Le graphe seul, sans son ajustement : la superposition disparaît.
    gabarit = creer_gabarit(page_complete(), "g", ["t"], onglets=["g"])
    assert gabarit.onglets[0].reglages.courbes == ()


def test_un_ajustement_portant_le_nom_d_une_variable_est_refuse():
    source = page_complete()
    with pytest.raises(ValueError, match="déjà pris"):
        Gabarit(
            nom="g",
            version=1,
            variables=(*creer_gabarit(source, "g", ["t"]).variables,),
            onglets=(
                {
                    "type": "ajustements",
                    "titre": "x",
                    "prereglage": {
                        "nom": "t",
                        "tableau": "t",
                        "x": "h",
                        "y": "act",
                        "modele": "Droite",
                        "formule": "a * x + b",
                    },
                },
            ),
        )


def test_la_courbe_d_un_ajustement_n_est_pas_une_variable():
    source = page_complete()
    assert list(colonnes_choisissables(source)) == ["t"]
    with pytest.raises(ValueError, match="courbe d'un ajustement"):
        creer_gabarit(source, "g", ["fit_courbe"])


def test_une_page_depuis_le_gabarit_a_ses_onglets_et_reste_valide():
    gabarit = creer_gabarit(page_complete(), "g", ["t"], onglets=["n", "f", "g"])
    nouvelle = page_depuis_gabarit(
        gabarit, "2026-11-15_suite_0008", "Suite", HEURE, HEURE, "G."
    )
    assert [o.type for o in nouvelle.onglets] == ["notes", "ajustements", "graphes"]
    assert nouvelle.onglets[0].texte == NOTE_METHODE
    assert nouvelle.onglets[1].prereglage.nom == "fit"
    assert nouvelle.onglets[2].reglages.tableau == "t"
    # Le tableau n'existe pas encore : la page se relit quand même, et reste sans données.
    assert nouvelle.mesures == ()
    assert depuis_json(vers_json(nouvelle)) == nouvelle
