"""Vérifie la création des gabarits, leur format TOML et la conformité des pages."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from pymphub.gabarit import (
    Gabarit,
    PageNonConforme,
    creer_gabarit,
    depuis_toml,
    page_depuis_gabarit,
    rattacher_gabarit,
    verifier_conformite,
    vers_toml,
)
from pymphub.page import Page, Scalaire

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
