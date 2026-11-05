"""Vérifie le modèle de page : page libre, onglets, grandeurs et format JSON."""

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from medphystools import page as page_module
from medphystools.edition import nom_libre, point_normalise
from medphystools.page import (
    VERSION_SCHEMA,
    Annotation,
    Identifiant,
    Onglet,
    OngletDonnees,
    Scalaire,
    Tableau,
    depuis_json,
    enregistrer_type_onglet,
    retirer_type_onglet,
    vers_json,
)

EMPREINTE = "a" * 64
COMPOSANTE_MBQ = {"type": "B", "valeur": 1.0, "unite": "MBq", "source": "certificat"}


def activite():
    return {
        "nature": "scalaire",
        "nom": "activite",
        "valeur": 10.5,
        "unite": "MBq",
        "composantes": [COMPOSANTE_MBQ],
        "horodatage": "2026-11-14T09:30:00+01:00",
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
                "composantes": [
                    {"type": "B", "valeur": 1.0, "unite": "min", "source": "horloge"}
                ],
            },
            {"nature": "numerique", "nom": "mesure", "unite": "MBq"},
            {
                "nature": "incertitude",
                "nom": "u_mesure",
                "composante_de": "mesure",
                "type": "B",
                "unite": "kBq",
                "source": "afficheur",
            },
        ],
    }


def page_libre(**champs):
    donnees = {
        "id": "2026-11-14_activimetre-linearite_0007",
        "titre": "Linéarité Capintec",
        "date_mesure": "2026-11-14T09:30:00+01:00",
        "date_saisie": "2026-11-14T11:05:00+01:00",
        "auteur": "G.",
    }
    return donnees | champs


def page_complete():
    return page_libre(
        machine="chu-x.medecine-nucleaire.activimetre.capintec-crc55.12345",
        tags=["activimetre", "Tc99m"],
        mesures=[
            activite(),
            {"nature": "identifiant", "nom": "numero_serie", "valeur": "007"},
            tableau(),
            {
                "nature": "fichier",
                "nom": "serie_ct",
                "stockage": {
                    "mode": "reference_dicom",
                    "study_instance_uid": "1.2.3",
                    "series_instance_uid": "1.2.3.4",
                    "sop_instance_uids": ["1.2.3.4.5"],
                    "noeud": "PACS_CHU",
                    "date": "2026-11-14",
                },
            },
        ],
        pieces_jointes=[
            {"chemin": "pieces_jointes/montage.jpg", "empreinte": EMPREINTE}
        ],
        onglets=[
            {
                "id": "o1",
                "type": "notes",
                "titre": "Montage",
                "texte": "Source au centre",
                "photos": [
                    {
                        "chemin": "pieces_jointes/montage.jpg",
                        "annotations": [
                            {
                                "forme": "fleche",
                                "points": [[0.1, 0.2], [0.5, 0.5]],
                                "texte": "source",
                            }
                        ],
                    }
                ],
            },
            {
                "id": "o2",
                "type": "scalaires",
                "grandeurs": ["activite", "numero_serie"],
            },
            {"id": "o3", "type": "tableau", "grandeurs": ["decroissance"]},
            {"id": "o4", "type": "dicom", "grandeurs": ["serie_ct"]},
            {
                "id": "o5",
                "type": "graphes",
                "grandeurs": ["decroissance.heure", "decroissance.mesure"],
            },
        ],
    )


def lire(donnees):
    return depuis_json(json.dumps(donnees))


def test_une_page_sans_gabarit_ni_contenu_est_valide():
    page = lire(page_libre())
    assert page.gabarit is None
    assert page.statut == "brouillon"
    assert page.version_schema == VERSION_SCHEMA


def test_aller_retour_json_identique():
    texte = vers_json(lire(page_complete()))
    assert vers_json(depuis_json(texte)) == texte
    assert depuis_json(texte) == lire(page_complete())


def test_le_json_est_lisible_et_conserve_les_accents():
    texte = vers_json(lire(page_complete()))
    assert "Linéarité" in texte
    assert "\n  " in texte
    assert json.loads(texte)["version_schema"] == VERSION_SCHEMA


@pytest.mark.parametrize("texte", ["007", "10.50", "1e3", "00120"])
def test_un_identifiant_reste_exact(texte):
    donnees = page_libre(
        mesures=[{"nature": "identifiant", "nom": "lot", "valeur": texte}]
    )
    relu = depuis_json(vers_json(lire(donnees)))
    assert relu.grandeur("lot").valeur == texte


def test_un_identifiant_numerique_est_refuse_au_lieu_d_etre_converti():
    donnees = page_libre(mesures=[{"nature": "identifiant", "nom": "lot", "valeur": 7}])
    with pytest.raises(ValidationError):
        lire(donnees)


def test_un_nombre_peut_perdre_ses_zeros_finaux():
    donnees = page_libre(mesures=[activite() | {"valeur": 10.50}])
    assert '"valeur": 10.5,' in vers_json(lire(donnees))


def test_un_nombre_ecrit_en_texte_est_refuse():
    with pytest.raises(ValidationError):
        lire(page_libre(mesures=[activite() | {"valeur": "10.5"}]))


def test_un_nombre_entier_est_accepte():
    assert (
        lire(page_libre(mesures=[activite() | {"valeur": 10}]))
        .grandeur("activite")
        .valeur
        == 10
    )


def test_un_scalaire_donne_son_budget():
    grandeur = lire(page_libre(mesures=[activite()])).grandeur("activite")
    assert isinstance(grandeur, Scalaire)
    assert grandeur.budget().elargie() == pytest.approx(2.0)


@pytest.mark.parametrize("retrait", ["unite", "composantes"])
def test_unite_et_incertitude_sont_obligatoires(retrait):
    scalaire = activite()
    del scalaire[retrait]
    with pytest.raises(ValidationError):
        lire(page_libre(mesures=[scalaire]))


def test_une_liste_d_incertitudes_vide_est_refusee():
    with pytest.raises(ValidationError, match="obligatoire"):
        lire(page_libre(mesures=[activite() | {"composantes": []}]))


def test_une_valeur_exacte_declare_une_incertitude_nulle():
    scalaire = {
        "nature": "scalaire",
        "nom": "nombre_coupes",
        "valeur": 40,
        "unite": "1",
        "composantes": [
            {"type": "B", "valeur": 0.0, "unite": "1", "source": "valeur exacte"}
        ],
    }
    grandeur = lire(page_libre(mesures=[scalaire])).grandeur("nombre_coupes")
    assert grandeur.budget().elargie() == 0


def test_une_unite_invalide_propose_une_suggestion():
    with pytest.raises(ValidationError, match="MBq"):
        lire(page_libre(mesures=[activite() | {"unite": "Mbq"}]))


def test_une_composante_incompatible_est_refusee():
    composante = {"type": "B", "valeur": 1.0, "unite": "mm", "source": "règle"}
    with pytest.raises(ValidationError):
        lire(page_libre(mesures=[activite() | {"composantes": [composante]}]))


def test_tableau_avec_incertitude_par_colonne_et_par_cellule():
    grandeur = lire(page_libre(mesures=[tableau()])).grandeur("decroissance")
    assert isinstance(grandeur, Tableau)
    colonne = grandeur.colonnes[3]
    assert colonne.composante(5.0).valeur == 5.0
    assert colonne.composante(5.0).unite == "kBq"


def test_colonne_numerique_sans_incertitude_refusee():
    donnees = tableau()
    donnees["colonnes"] = donnees["colonnes"][:3]
    with pytest.raises(ValidationError, match="mesure"):
        lire(page_libre(mesures=[donnees]))


def test_colonne_numerique_sans_unite_refusee():
    donnees = tableau()
    del donnees["colonnes"][1]["unite"]
    with pytest.raises(ValidationError):
        lire(page_libre(mesures=[donnees]))


@pytest.mark.parametrize(
    ("modification", "message"),
    [
        ({"composante_de": "lot"}, "pas une colonne numérique"),
        ({"composante_de": "absente"}, "pas une colonne numérique"),
        ({"unite": "mm"}, "incompatible"),
    ],
)
def test_colonne_d_incertitude_incoherente_refusee(modification, message):
    donnees = tableau()
    donnees["colonnes"][3] |= modification
    with pytest.raises(ValidationError, match=message):
        lire(page_libre(mesures=[donnees]))


def test_composante_de_colonne_incompatible_refusee():
    donnees = tableau()
    donnees["colonnes"][1]["composantes"][0]["unite"] = "MBq"
    with pytest.raises(ValidationError, match="incompatible"):
        lire(page_libre(mesures=[donnees]))


@pytest.mark.parametrize(
    "chemin",
    [
        "decroissance.csv",
        "brut/a.txt",
        "brut/../../a.csv",
        "/brut/a.csv",
        "brut\\a.csv",
        "",
    ],
)
def test_chemin_de_tableau_invalide_refuse(chemin):
    with pytest.raises(ValidationError):
        lire(page_libre(mesures=[tableau() | {"fichier": chemin}]))


@pytest.mark.parametrize(
    "mesures",
    [
        [activite(), activite()],
        [activite(), tableau() | {"nom": "activite"}],
    ],
)
def test_un_nom_repete_dans_la_page_est_refuse(mesures):
    with pytest.raises(ValidationError, match="plusieurs fois"):
        lire(page_libre(mesures=mesures))


@pytest.mark.parametrize("nom", ["Activité (MBq)", "2a", "a b", ""])
def test_un_nom_inutilisable_dans_une_formule_est_refuse(nom):
    with pytest.raises(ValidationError):
        lire(page_libre(mesures=[activite() | {"nom": nom}]))


@pytest.mark.parametrize(
    "champs",
    [
        {"date_mesure": "2026-11-14T09:30:00"},
        {"date_saisie": "2026-11-14T11:05:00"},
        {"machine": "capintec"},
        {"machine": "chu.mn.activimetre.capintec.123"},
        {"machine": "chu.mn.activimetre.capintec-crc55"},
        {"machine": "chu x.mn.activimetre.capintec-crc55.123"},
        {"id": "page 7"},
        {"id": "2026-11-14_Activimetre_0007"},
        {"gabarit": "activimetre"},
        {"statut": "validee"},
        {"titre": ""},
        {"auteur": ""},
        {"champ_inconnu": 1},
    ],
)
def test_champ_de_page_invalide_refuse(champs):
    with pytest.raises(ValidationError):
        lire(page_libre(**champs))


def test_horodatage_de_mesure_sans_fuseau_refuse():
    with pytest.raises(ValidationError):
        lire(page_libre(mesures=[activite() | {"horodatage": "2026-11-14T09:30:00"}]))


def test_format_trop_recent_refuse_avec_un_message_clair():
    with pytest.raises(ValidationError, match="mettre à jour"):
        lire(page_libre(version_schema=VERSION_SCHEMA + 1))


def test_deplacer_une_grandeur_d_onglet_ne_change_pas_sa_relecture():
    avant = lire(
        page_libre(
            mesures=[activite()],
            onglets=[
                {"id": "o1", "type": "scalaires", "grandeurs": ["activite"]},
                {"id": "o2", "type": "scalaires", "grandeurs": []},
            ],
        )
    )
    apres = lire(
        page_libre(
            mesures=[activite()],
            onglets=[
                {"id": "o1", "type": "scalaires", "grandeurs": []},
                {"id": "o2", "type": "scalaires", "grandeurs": ["activite"]},
            ],
        )
    )
    assert avant.grandeur("activite") == apres.grandeur("activite")


def test_une_grandeur_inconnue_leve_une_erreur():
    with pytest.raises(KeyError, match="absente"):
        lire(page_libre()).grandeur("absente")


def test_un_onglet_ne_peut_pas_referencer_une_grandeur_absente():
    onglet = {"id": "o1", "type": "scalaires", "grandeurs": ["absente"]}
    with pytest.raises(ValidationError, match="absente"):
        lire(page_libre(onglets=[onglet]))


def test_un_onglet_peut_referencer_une_colonne():
    references = ["decroissance.heure", "decroissance.mesure"]
    onglet = {"id": "o1", "type": "graphes", "grandeurs": references}
    page = lire(page_libre(mesures=[tableau()], onglets=[onglet]))
    assert page.onglets[0].grandeurs == tuple(references)


def test_un_onglet_ne_designe_pas_une_colonne_sans_son_tableau():
    onglet = {"id": "o1", "type": "graphes", "grandeurs": ["heure"]}
    with pytest.raises(ValidationError, match="heure"):
        lire(page_libre(mesures=[tableau()], onglets=[onglet]))


def test_deux_tableaux_peuvent_avoir_une_colonne_de_meme_nom():
    seconde_serie = tableau() | {"nom": "seconde_serie", "fichier": "brut/seconde.csv"}
    page = lire(page_libre(mesures=[tableau(), seconde_serie]))
    assert depuis_json(vers_json(page)) == page


def test_une_colonne_peut_porter_le_nom_d_une_grandeur():
    page = lire(page_libre(mesures=[activite() | {"nom": "heure"}, tableau()]))
    assert isinstance(page.grandeur("heure"), Scalaire)


def test_une_colonne_repetee_dans_un_tableau_est_refusee():
    donnees = tableau()
    donnees["colonnes"].append(donnees["colonnes"][1])
    with pytest.raises(ValidationError, match="répétées"):
        lire(page_libre(mesures=[donnees]))


def test_l_erreur_commune_est_conservee_dans_une_page():
    composante = COMPOSANTE_MBQ | {"erreur_commune": "certificat-2026-114"}
    donnees = tableau()
    donnees["colonnes"][3]["erreur_commune"] = "afficheur-123"
    page = lire(
        page_libre(mesures=[activite() | {"composantes": [composante]}, donnees])
    )
    relue = depuis_json(vers_json(page))
    assert relue.grandeur("activite").composantes[0].erreur_commune == (
        "certificat-2026-114"
    )
    colonne = relue.grandeur("decroissance").colonnes[3]
    assert colonne.composante(1.0).erreur_commune == "afficheur-123"


def test_identifiants_d_onglets_repetes_refuses():
    onglets = [{"id": "o1", "type": "notes"}, {"id": "o1", "type": "notes"}]
    with pytest.raises(ValidationError, match="répétés"):
        lire(page_libre(onglets=onglets))


def test_photo_non_declaree_refusee():
    onglet = {
        "id": "o1",
        "type": "notes",
        "photos": [{"chemin": "pieces_jointes/absente.jpg"}],
    }
    with pytest.raises(ValidationError, match="absente"):
        lire(page_libre(onglets=[onglet]))


@pytest.mark.parametrize(
    "annotation",
    [
        {"forme": "fleche", "points": [[1.5, 0.2]]},
        {"forme": "fleche", "points": []},
        {"forme": "cercle_magique", "points": [[0.1, 0.2]]},
    ],
)
def test_annotation_invalide_refusee(annotation):
    donnees = page_complete()
    donnees["onglets"][0]["photos"][0]["annotations"] = [annotation]
    with pytest.raises(ValidationError):
        lire(donnees)


def test_les_annotations_sont_enregistrees_a_part_de_la_photo():
    page = lire(page_complete())
    photo = page.onglets[0].photos[0]
    assert photo.annotations[0].texte == "source"
    assert page.pieces_jointes[0].empreinte == EMPREINTE


def test_un_type_d_onglet_inconnu_est_conserve_intact():
    futur = {
        "id": "o9",
        "type": "spectre_gamma",
        "titre": "Spectre",
        "pics_keV": [140.5, 159.0],
        "reglages": {"echelle": "log", "canaux": 1024},
    }
    page = lire(page_libre(onglets=[futur]))
    assert type(page.onglets[0]) is Onglet

    relu = json.loads(vers_json(depuis_json(vers_json(page))))
    assert relu["onglets"] == [futur]


def test_un_champ_futur_d_un_type_connu_est_conserve():
    onglet = {"id": "o1", "type": "notes", "texte": "t", "champ_futur": {"a": 1}}
    relu = json.loads(vers_json(lire(page_libre(onglets=[onglet]))))
    assert relu["onglets"][0]["champ_futur"] == {"a": 1}


@pytest.fixture
def type_onglet_de_test():
    class OngletSpectre(OngletDonnees):
        canaux: int

    enregistrer_type_onglet("spectre", OngletSpectre)
    yield OngletSpectre
    retirer_type_onglet("spectre")


def test_ajouter_un_type_d_onglet_ne_demande_qu_une_declaration(type_onglet_de_test):
    onglet = {"id": "o1", "type": "spectre", "canaux": 1024, "grandeurs": ["activite"]}
    page = lire(page_libre(mesures=[activite()], onglets=[onglet]))
    assert isinstance(page.onglets[0], type_onglet_de_test)
    assert page.onglets[0].canaux == 1024
    assert depuis_json(vers_json(page)) == page

    with pytest.raises(ValidationError):
        lire(page_libre(onglets=[onglet | {"canaux": "beaucoup", "grandeurs": []}]))
    with pytest.raises(ValidationError, match="absent"):
        lire(page_libre(onglets=[onglet]))


def test_la_page_ne_modifie_pas_les_donnees_recues():
    donnees = page_complete()
    copie = deepcopy(donnees)
    lire(donnees)
    assert donnees == copie


def test_les_modeles_sont_immuables():
    grandeur = Identifiant(nom="lot", valeur="007")
    with pytest.raises(ValidationError):
        grandeur.valeur = "008"


def test_une_colonne_ecrite_sans_commentaire_se_relit():
    """Un tableau enregistré avant l'arrivée du commentaire de colonne reste lisible."""
    colonne = page_module.ColonneIdentifiant.model_validate(
        {"nature": "identifiant", "nom": "lot"}
    )
    assert colonne.commentaire == ""


@pytest.mark.parametrize(
    ("ecran", "attendu"),
    [
        ((50, 25, 100, 100), (0.5, 0.25)),
        ((0, 100, 100, 100), (0.0, 1.0)),
        ((-5, 130, 100, 100), (0.0, 1.0)),
        ((1, 1, 3, 3), (0.333333, 0.333333)),
    ],
)
def test_un_point_d_annotation_reste_dans_l_image(ecran, attendu):
    assert point_normalise(*ecran) == attendu
    Annotation(forme="point", points=(point_normalise(*ecran),))


def test_le_nom_d_un_fichier_lie_est_utilisable_et_libre():
    assert nom_libre("Export constructeur", []) == "Export_constructeur"
    assert nom_libre("2026 essai", []) == "g_2026_essai"
    assert nom_libre("---", []) == "fichier"
    assert nom_libre("a", ["a", "a_2"]) == "a_3"
