"""Vérifie le magasin d'objets, les références, les pièces jointes et les tableaux CSV."""

import hashlib
import os
import shutil
import stat
import subprocess
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from pydantic import ValidationError

from medphystools.archive import Archive, PageRefusee
from medphystools.depot import Depot
from medphystools.disque import NomInvalide
from medphystools.fichiers import (
    FichierAltere,
    FichierManquant,
    ReferenceNonResolue,
    empreinte_fichier,
    etat_fichier,
    semble_dicom,
)
from medphystools.historique import Modification, lire_message
from medphystools.page import Fichier, Page, ReferenceDicom, Tableau
from medphystools.tableaux import TableauInvalide, differences_csv, ecrire_csv, lire_csv

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
ID_PAGE = "2026-11-14_activimetre-linearite_0007"
AUTRE_PAGE = "2026-11-15_scanner_0008"


def sha256(contenu):
    return hashlib.sha256(contenu).hexdigest()


def description_tableau(nom="decroissance"):
    return {
        "nature": "tableau",
        "nom": nom,
        "fichier": f"brut/{nom}.csv",
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
            {"nature": "numerique", "nom": "activite", "unite": "MBq"},
            {
                "nature": "incertitude",
                "nom": "u_activite",
                "composante_de": "activite",
                "type": "B",
                "unite": "MBq",
                "source": "afficheur",
            },
        ],
    }


DONNEES = {
    "lot": ["007", "10.50", "1e3"],
    "heure": [0, 6.0, 12.0],
    "activite": [100.0, 50.5, 25.25],
    "u_activite": [1.0, 0.5, 0.25],
}


def page(*mesures, id_page=ID_PAGE, **champs):
    return Page.model_validate(
        {
            "id": id_page,
            "titre": "Linéarité",
            "date_mesure": HEURE,
            "date_saisie": HEURE,
            "auteur": "G.",
            "mesures": mesures,
        }
        | champs
    )


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def source(tmp_path):
    chemin = tmp_path / "serie.dcm"
    chemin.write_bytes(b"pixels" * 1000)
    return chemin


def fichiers_dans_le_magasin(arc):
    return [f for f in (arc.racine / "objets").rglob("*") if f.is_file()]


def etat_de_l_archive(arc):
    return {
        p.relative_to(arc.racine): (p.stat().st_mtime_ns, p.stat().st_size)
        for p in arc.racine.rglob("*")
        if p.is_file()
    }


# --- Magasin d'objets -------------------------------------------------------------


def test_importer_une_copie(arc, source):
    grandeur = arc.importer_copie(source, "serie_ct", type_fichier="DICOM")
    empreinte = sha256(source.read_bytes())
    assert grandeur.stockage.mode == "copie"
    assert grandeur.stockage.empreinte == empreinte
    assert grandeur.stockage.nom_origine == "serie.dcm"
    objet = arc.racine / "objets" / empreinte[:2] / empreinte[2:]
    assert objet.read_bytes() == source.read_bytes()


def test_un_fichier_importe_deux_fois_n_est_stocke_qu_une_fois(arc, source, tmp_path):
    autre = tmp_path / "autre_nom.dcm"
    shutil.copy(source, autre)
    premier = arc.importer_copie(source, "a")
    second = arc.importer_copie(autre, "b")
    assert premier.stockage.empreinte == second.stockage.empreinte
    assert len(fichiers_dans_le_magasin(arc)) == 1


def test_deux_contenus_differents_sont_stockes_a_part(arc, source, tmp_path):
    autre = tmp_path / "autre.dcm"
    autre.write_bytes(b"autre contenu")
    arc.importer_copie(source, "a")
    arc.importer_copie(autre, "b")
    assert len(fichiers_dans_le_magasin(arc)) == 2


def test_l_objet_est_en_lecture_seule(arc, source):
    arc.importer_copie(source, "a")
    objet = fichiers_dans_le_magasin(arc)[0]
    assert not objet.stat().st_mode & stat.S_IWUSR


def test_l_import_ne_modifie_pas_la_source(arc, source):
    avant = source.read_bytes()
    arc.importer_copie(source, "a")
    assert source.read_bytes() == avant


def test_importer_un_fichier_absent(arc, tmp_path):
    with pytest.raises(FichierManquant):
        arc.importer_copie(tmp_path / "absent", "a")
    assert fichiers_dans_le_magasin(arc) == []


def test_importer_sur_un_objet_abime_est_signale(arc, source):
    arc.importer_copie(source, "a")
    objet = fichiers_dans_le_magasin(arc)[0]
    objet.chmod(stat.S_IWUSR | stat.S_IRUSR)
    objet.write_bytes(b"abime")
    with pytest.raises(FichierAltere):
        arc.importer_copie(source, "b")
    assert len(fichiers_dans_le_magasin(arc)) == 1


def test_le_magasin_reste_hors_de_git(arc, source):
    arc.creer_page(page(arc.importer_copie(source, "serie_ct").model_dump()))
    assert not any(c.startswith("objets/") for c in Depot(arc.racine).fichiers_suivis())
    assert arc.modifications_non_tracees() == []


# --- Accès vérifié par empreinte --------------------------------------------------


@pytest.fixture
def page_avec_copie(arc, source):
    arc.creer_page(page(arc.importer_copie(source, "serie_ct").model_dump()))
    return arc


@pytest.fixture
def page_avec_reference(arc, source):
    arc.creer_page(page(arc.referencer_fichier(source, "serie_ct").model_dump()))
    return arc


def test_acceder_a_une_copie_intacte(page_avec_copie, source):
    chemin = page_avec_copie.chemin_fichier(ID_PAGE, "serie_ct")
    assert chemin.read_bytes() == source.read_bytes()


def test_une_copie_alteree_est_signalee_sans_rien_ecrire(page_avec_copie):
    objet = fichiers_dans_le_magasin(page_avec_copie)[0]
    objet.chmod(stat.S_IWUSR | stat.S_IRUSR)
    objet.write_bytes(b"abime")
    avant = etat_de_l_archive(page_avec_copie)
    with pytest.raises(FichierAltere):
        page_avec_copie.chemin_fichier(ID_PAGE, "serie_ct")
    assert etat_de_l_archive(page_avec_copie) == avant


def test_une_copie_manquante_est_signalee_sans_rien_ecrire(page_avec_copie):
    fichiers_dans_le_magasin(page_avec_copie)[0].unlink()
    avant = etat_de_l_archive(page_avec_copie)
    with pytest.raises(FichierManquant):
        page_avec_copie.chemin_fichier(ID_PAGE, "serie_ct")
    assert etat_de_l_archive(page_avec_copie) == avant


def test_une_reference_ne_copie_rien(page_avec_reference, source):
    assert fichiers_dans_le_magasin(page_avec_reference) == []
    stockage = page_avec_reference.lire_page(ID_PAGE).grandeur("serie_ct").stockage
    assert stockage.mode == "reference_fichier"
    assert stockage.chemin == str(source)
    assert stockage.taille == source.stat().st_size
    assert stockage.date.tzinfo is not None
    assert stockage.empreinte == sha256(source.read_bytes())


def test_acceder_a_une_reference_intacte(page_avec_reference, source):
    assert page_avec_reference.chemin_fichier(ID_PAGE, "serie_ct") == source


def test_une_reference_alteree_est_signalee_sans_rien_ecrire(
    page_avec_reference, source
):
    source.write_bytes(b"autre")
    avant = etat_de_l_archive(page_avec_reference)
    with pytest.raises(FichierAltere):
        page_avec_reference.chemin_fichier(ID_PAGE, "serie_ct")
    assert etat_de_l_archive(page_avec_reference) == avant


def test_une_reference_introuvable_est_signalee(page_avec_reference, source):
    source.unlink()
    with pytest.raises(FichierManquant):
        page_avec_reference.chemin_fichier(ID_PAGE, "serie_ct")


def test_referencer_un_fichier_absent(arc, tmp_path):
    with pytest.raises(FichierManquant):
        arc.referencer_fichier(tmp_path / "absent", "a")


def test_une_reference_dicom_ne_se_resout_pas_encore(arc):
    reference = Fichier(
        nom="serie",
        stockage=ReferenceDicom(
            study_instance_uid="1.2",
            series_instance_uid="1.2.3",
            sop_instance_uids=("1.2.3.4",),
            noeud="PACS_CHU",
            date=date(2026, 11, 14),
        ),
    )
    arc.creer_page(page(reference.model_dump()))
    with pytest.raises(ReferenceNonResolue, match="1.2.3"):
        arc.chemin_fichier(ID_PAGE, "serie")


@pytest.mark.parametrize("champ", ["patient_id", "nom_patient"])
def test_une_reference_dicom_refuse_un_identifiant_patient(champ):
    with pytest.raises(ValidationError):
        ReferenceDicom(
            study_instance_uid="1.2",
            series_instance_uid="1.2.3",
            sop_instance_uids=("1.2.3.4",),
            noeud="PACS",
            date=date(2026, 11, 14),
            **{champ: "DUPONT"},
        )


def test_acceder_a_une_grandeur_qui_n_est_pas_un_fichier(arc):
    arc.creer_page(page({"nature": "identifiant", "nom": "lot", "valeur": "1"}))
    with pytest.raises(ValueError, match="pas un fichier"):
        arc.chemin_fichier(ID_PAGE, "lot")


# --- Trace des fichiers lourds modifiés -------------------------------------------


def dernier_message(arc):
    return Depot(arc.racine).commits()[-1].message.decode()


def test_le_remplacement_d_un_fichier_lourd_est_trace(page_avec_reference, source):
    ancienne = sha256(source.read_bytes())
    source.write_bytes(b"nouvelle acquisition")
    nouvelle = sha256(b"nouvelle acquisition")

    relue = page_avec_reference.enregistrer_fichier_modifie(
        ID_PAGE, "serie_ct", "Acquisition refaite"
    )
    assert relue.grandeur("serie_ct").stockage.empreinte == nouvelle
    assert page_avec_reference.chemin_fichier(ID_PAGE, "serie_ct") == source

    message = dernier_message(page_avec_reference)
    assert f"mesures.serie_ct.stockage.empreinte : {ancienne} -> {nouvelle}" in message
    assert "Motif: Acquisition refaite" in message
    assert lire_message(message)["Page"] == ID_PAGE


@pytest.mark.parametrize("motif", ["", "   "])
def test_enregistrer_un_fichier_modifie_exige_un_motif(
    page_avec_reference, source, motif
):
    source.write_bytes(b"autre")
    with pytest.raises(PageRefusee, match="motif"):
        page_avec_reference.enregistrer_fichier_modifie(ID_PAGE, "serie_ct", motif)


def test_fichier_modifie_sur_une_page_validee_devient_une_correction(
    page_avec_reference, source
):
    page_avec_reference.valider_page(ID_PAGE)
    source.write_bytes(b"autre")
    relue = page_avec_reference.enregistrer_fichier_modifie(
        ID_PAGE, "serie_ct", "Refait"
    )
    assert relue.statut == "corrigée"
    correction = page_avec_reference.corrections(ID_PAGE)[0]
    assert correction.motif == "Refait"
    assert "mesures.serie_ct.stockage.empreinte" in {
        m.champ for m in correction.modifications
    }


def test_un_fichier_identique_ne_cree_pas_de_commit(page_avec_reference):
    avant = len(Depot(page_avec_reference.racine).commits())
    page_avec_reference.enregistrer_fichier_modifie(ID_PAGE, "serie_ct", "Rien")
    # La date et le chemin sont inchangés : la page reste identique.
    assert len(Depot(page_avec_reference.racine).commits()) == avant


def test_une_reference_disparue_ne_s_enregistre_pas(page_avec_reference, source):
    source.unlink()
    with pytest.raises(FichierManquant):
        page_avec_reference.enregistrer_fichier_modifie(ID_PAGE, "serie_ct", "Disparue")


def test_une_copie_du_magasin_ne_se_remplace_pas(page_avec_copie):
    with pytest.raises(FichierAltere, match="restaurer"):
        page_avec_copie.enregistrer_fichier_modifie(ID_PAGE, "serie_ct", "Remplacer")


def test_remplacer_une_copie_par_une_autre_est_trace(page_avec_copie, source, tmp_path):
    ancienne = sha256(source.read_bytes())
    autre = tmp_path / "refaite.dcm"
    autre.write_bytes(b"refaite")
    grandeur = page_avec_copie.importer_copie(autre, "serie_ct")
    page_avec_copie.modifier_page(page(grandeur.model_dump()))
    assert f"{ancienne} -> {sha256(b'refaite')}" in dernier_message(page_avec_copie)


# --- Pièces jointes ---------------------------------------------------------------


@pytest.fixture
def photo(tmp_path):
    chemin = tmp_path / "montage.jpg"
    chemin.write_bytes(b"photo")
    return chemin


def test_importer_une_piece_jointe(arc, photo):
    arc.creer_page(page())
    piece = arc.importer_piece_jointe(ID_PAGE, photo)
    assert piece.chemin == "pieces_jointes/montage.jpg"
    assert piece.empreinte == sha256(b"photo")
    arc.modifier_page(page(pieces_jointes=[piece.model_dump()]))
    chemin = arc.chemin_piece_jointe(ID_PAGE, piece.chemin)
    assert chemin.read_bytes() == b"photo"
    assert arc.modifications_non_tracees() == []


def test_une_piece_jointe_reste_hors_de_git(arc, photo):
    arc.creer_page(page())
    piece = arc.importer_piece_jointe(ID_PAGE, photo)
    arc.modifier_page(page(pieces_jointes=[piece.model_dump()]))
    assert not any("pieces_jointes/" in c for c in Depot(arc.racine).fichiers_suivis())


def test_une_piece_jointe_ne_se_remplace_pas(arc, photo, tmp_path):
    arc.creer_page(page())
    arc.importer_piece_jointe(ID_PAGE, photo)
    arc.importer_piece_jointe(ID_PAGE, photo)
    autre = tmp_path / "x.jpg"
    autre.write_bytes(b"autre")
    with pytest.raises(FileExistsError):
        arc.importer_piece_jointe(ID_PAGE, autre, "montage.jpg")


def test_piece_jointe_nom_invalide_sous_windows(arc, photo):
    arc.creer_page(page())
    with pytest.raises(ValueError):
        arc.importer_piece_jointe(ID_PAGE, photo, "CON.jpg")


def test_piece_jointe_sur_page_absente(arc, photo):
    with pytest.raises(KeyError):
        arc.importer_piece_jointe(ID_PAGE, photo)


def test_piece_jointe_alteree_signalee_puis_enregistree(arc, photo):
    arc.creer_page(page())
    piece = arc.importer_piece_jointe(ID_PAGE, photo)
    arc.modifier_page(page(pieces_jointes=[piece.model_dump()]))
    (arc.racine / "pages" / ID_PAGE / piece.chemin).write_bytes(b"retouchee")

    with pytest.raises(FichierAltere):
        arc.chemin_piece_jointe(ID_PAGE, piece.chemin)

    arc.enregistrer_fichier_modifie(ID_PAGE, piece.chemin, "Photo recadrée")
    assert arc.chemin_piece_jointe(ID_PAGE, piece.chemin).read_bytes() == b"retouchee"
    assert f"{sha256(b'photo')} -> {sha256(b'retouchee')}" in dernier_message(arc)


def test_piece_jointe_non_declaree(arc):
    arc.creer_page(page())
    with pytest.raises(KeyError):
        arc.chemin_piece_jointe(ID_PAGE, "pieces_jointes/absente.jpg")


# --- CSV ---------------------------------------------------------------------------


@pytest.fixture
def tableau():
    return Tableau.model_validate(description_tableau())


def test_format_du_csv(tableau):
    assert ecrire_csv(tableau, DONNEES) == (
        "lot,heure,activite,u_activite\n"
        "007,0.0,100.0,1.0\n"
        "10.50,6.0,50.5,0.5\n"
        "1e3,12.0,25.25,0.25\n"
    )


def test_les_identifiants_ne_sont_pas_convertis(tableau):
    resultat = lire_csv(tableau, ecrire_csv(tableau, DONNEES))
    assert list(resultat["lot"]) == ["007", "10.50", "1e3"]
    assert all(isinstance(valeur, str) for valeur in resultat["lot"])


@pytest.mark.parametrize("texte", ["NA", "null", "nan", "None", "1,5", 'a"b', " 7 "])
def test_les_identifiants_particuliers_restent_exacts(tableau, texte):
    donnees = {"lot": [texte], "heure": [1.0], "activite": [1.0], "u_activite": [0.1]}
    assert list(lire_csv(tableau, ecrire_csv(tableau, donnees))["lot"]) == [texte]


def test_les_nombres_sont_relus_comme_des_nombres(tableau):
    resultat = lire_csv(tableau, ecrire_csv(tableau, DONNEES))
    assert resultat["activite"].dtype == "float64"
    assert list(resultat["activite"]) == [100.0, 50.5, 25.25]
    assert list(resultat.columns) == ["lot", "heure", "activite", "u_activite"]


def test_aller_retour_exact_des_nombres(tableau):
    valeur = 0.1 + 0.2
    donnees = {
        "lot": ["a"],
        "heure": [valeur],
        "activite": [1e-30],
        "u_activite": [0.0],
    }
    resultat = lire_csv(tableau, ecrire_csv(tableau, donnees))
    assert resultat["heure"][0] == valeur
    assert resultat["activite"][0] == 1e-30


def test_les_unites_accompagnent_le_tableau(tableau):
    resultat = lire_csv(tableau, ecrire_csv(tableau, DONNEES))
    assert resultat.attrs["unites"] == {
        "heure": "h",
        "activite": "MBq",
        "u_activite": "MBq",
    }


def test_un_tableau_sans_ligne_est_accepte(tableau):
    vide = {nom: [] for nom in DONNEES}
    assert len(lire_csv(tableau, ecrire_csv(tableau, vide))) == 0


@pytest.mark.parametrize(
    "modification",
    [
        {"lot": [7, "a", "b"]},
        {"lot": ["", "a", "b"]},
        {"heure": ["0", 1.0, 2.0]},
        {"heure": [True, 1.0, 2.0]},
        {"activite": [float("nan"), 1.0, 2.0]},
        {"activite": [float("inf"), 1.0, 2.0]},
        {"u_activite": [-1.0, 1.0, 2.0]},
    ],
)
def test_donnees_invalides_refusees(tableau, modification):
    with pytest.raises(TableauInvalide):
        ecrire_csv(tableau, DONNEES | modification)


def test_colonnes_inattendues_refusees(tableau):
    with pytest.raises(TableauInvalide):
        ecrire_csv(tableau, DONNEES | {"autre": [1, 2, 3]})
    sans_lot = {k: v for k, v in DONNEES.items() if k != "lot"}
    with pytest.raises(TableauInvalide):
        ecrire_csv(tableau, sans_lot)


@pytest.mark.parametrize(
    "texte",
    [
        "",
        "lot,heure\n",
        "lot,heure,activite,u_activite\na,1.0,2.0\n",
        "lot,heure,activite,u_activite\na,abc,2.0,0.1\n",
        "lot,heure,activite,u_activite\na,inf,2.0,0.1\n",
        "lot,heure,activite,u_activite\na,1.0,2.0,-0.1\n",
    ],
)
def test_fichier_csv_invalide_refuse(tableau, texte):
    with pytest.raises(TableauInvalide):
        lire_csv(tableau, texte)


def test_differences_csv_cellule_par_cellule():
    ancien = "a,b\n1,2\n3,4\n"
    nouveau = "a,b\n1,9\n3,4\n"
    assert differences_csv("t", ancien, nouveau) == (
        Modification("mesures.t.b[1]", "2", "9"),
    )
    assert differences_csv("t", ancien, ancien) == ()


def test_differences_csv_changement_de_forme():
    modifications = differences_csv("t", "a\n1\n", "a\n1\n2\n")
    assert len(modifications) == 1
    assert modifications[0].champ == "mesures.t (forme)"
    assert modifications[0].ancien["lignes"] == 1
    assert modifications[0].nouveau["lignes"] == 2


@pytest.mark.parametrize("fichier", ["brut/CON.csv", "brut/a:b.csv"])
def test_un_nom_de_csv_invalide_sous_windows_est_refuse(fichier):
    with pytest.raises(ValidationError):
        Tableau.model_validate(description_tableau() | {"fichier": fichier})


def test_deux_tableaux_ne_partagent_pas_un_fichier():
    autre = description_tableau("autre") | {"fichier": "brut/decroissance.csv"}
    with pytest.raises(ValidationError, match="partagés"):
        page(description_tableau(), autre)


# --- Tableaux dans l'archive -------------------------------------------------------


@pytest.fixture
def avec_tableau(arc):
    arc.creer_page(page(description_tableau()), tableaux={"decroissance": DONNEES})
    return arc


def modifie(colonne, indice, valeur):
    valeurs = list(DONNEES[colonne])
    valeurs[indice] = valeur
    return DONNEES | {colonne: valeurs}


def test_relire_un_tableau_de_l_archive(avec_tableau):
    resultat = avec_tableau.lire_tableau(ID_PAGE, "decroissance")
    assert isinstance(resultat, pd.DataFrame)
    assert list(resultat["lot"]) == ["007", "10.50", "1e3"]
    assert resultat["activite"][2] == 25.25
    assert resultat.attrs["unites"]["activite"] == "MBq"


def test_le_csv_est_dans_git_avec_la_creation(avec_tableau):
    suivis = Depot(avec_tableau.racine).fichiers_suivis()
    assert f"pages/{ID_PAGE}/brut/decroissance.csv" in suivis
    assert avec_tableau.modifications_non_tracees() == []
    assert len(avec_tableau.historique(ID_PAGE)) == 1


def test_un_tableau_declare_doit_avoir_des_donnees(arc):
    with pytest.raises(PageRefusee, match="pas de données"):
        arc.creer_page(page(description_tableau()))
    assert arc.identifiants() == []


def test_des_donnees_invalides_ne_creent_pas_la_page(arc):
    donnees = DONNEES | {"activite": ["x", 1.0, 2.0]}
    with pytest.raises(PageRefusee, match="activite"):
        arc.creer_page(page(description_tableau()), tableaux={"decroissance": donnees})
    assert arc.identifiants() == []
    assert not (arc.racine / "pages" / ID_PAGE).exists()


def test_des_donnees_pour_un_tableau_non_declare_sont_refusees(arc):
    with pytest.raises(PageRefusee, match="non déclarés"):
        arc.creer_page(page(), tableaux={"fantome": DONNEES})


def test_modifier_une_cellule_d_un_brouillon(avec_tableau):
    assert avec_tableau.modifier_page(
        page(description_tableau()),
        tableaux={"decroissance": modifie("activite", 1, 60.0)},
    )
    assert avec_tableau.lire_tableau(ID_PAGE, "decroissance")["activite"][1] == 60.0
    derniere = avec_tableau.historique(ID_PAGE)[-1]
    assert derniere.nature == "enregistrement"
    assert derniere.modifications == (
        Modification("mesures.decroissance.activite[2]", "50.5", "60.0"),
    )


def test_memes_donnees_pas_de_commit(avec_tableau):
    avant = len(Depot(avec_tableau.racine).commits())
    assert not avec_tableau.modifier_page(
        page(description_tableau()), tableaux={"decroissance": DONNEES}
    )
    assert len(Depot(avec_tableau.racine).commits()) == avant


def test_une_cellule_d_une_page_validee_exige_un_motif(avec_tableau):
    avec_tableau.valider_page(ID_PAGE)
    donnees = {"decroissance": modifie("activite", 1, 60.0)}
    validee = page(description_tableau(), statut="validée")
    with pytest.raises(PageRefusee, match="motivée"):
        avec_tableau.modifier_page(validee, tableaux=donnees)
    assert avec_tableau.lire_tableau(ID_PAGE, "decroissance")["activite"][1] == 50.5

    avec_tableau.modifier_page(validee, motif="Erreur de lecture", tableaux=donnees)
    assert avec_tableau.lire_page(ID_PAGE).statut == "corrigée"
    correction = avec_tableau.corrections(ID_PAGE)[0]
    assert correction.motif == "Erreur de lecture"
    assert Modification("mesures.decroissance.activite[2]", "50.5", "60.0") in (
        correction.modifications
    )


def test_l_ancienne_version_d_un_tableau_est_retrouvee(avec_tableau):
    creation = avec_tableau.historique(ID_PAGE)[0].commit
    avec_tableau.modifier_page(
        page(description_tableau()),
        tableaux={"decroissance": modifie("activite", 1, 60.0)},
    )
    ancien = avec_tableau.version_tableau(ID_PAGE, "decroissance", creation)
    assert ancien["activite"][1] == 50.5
    assert list(ancien["lot"]) == ["007", "10.50", "1e3"]


def test_une_description_incompatible_avec_le_csv_est_refusee(avec_tableau):
    description = description_tableau()
    description["colonnes"][0]["nom"] = "autre_nom"
    with pytest.raises(PageRefusee, match="attendues"):
        avec_tableau.modifier_page(page(description))


def test_retirer_un_tableau_le_retire_du_depot_mais_pas_de_l_historique(avec_tableau):
    creation = avec_tableau.historique(ID_PAGE)[0].commit
    chemin = f"pages/{ID_PAGE}/brut/decroissance.csv"
    avec_tableau.modifier_page(page())
    assert chemin not in Depot(avec_tableau.racine).fichiers_suivis()
    assert not (avec_tableau.racine / chemin).exists()
    assert avec_tableau.modifications_non_tracees() == []
    ancien = avec_tableau.version_tableau(ID_PAGE, "decroissance", creation)
    assert len(ancien) == 3


def test_un_csv_modifie_a_la_main_est_signale_et_pas_ecrase(avec_tableau):
    chemin = avec_tableau.racine / "pages" / ID_PAGE / "brut" / "decroissance.csv"
    chemin.write_text(
        chemin.read_text(encoding="utf-8").replace("50.5", "99.0"), encoding="utf-8"
    )
    assert avec_tableau.modifications_non_tracees() == [
        f"pages/{ID_PAGE}/brut/decroissance.csv"
    ]
    with pytest.raises(PageRefusee, match="hors du logiciel"):
        avec_tableau.modifier_page(
            page(description_tableau()),
            tableaux={"decroissance": modifie("activite", 1, 60.0)},
        )
    with pytest.raises(PageRefusee, match="hors du logiciel"):
        avec_tableau.valider_page(ID_PAGE)


def test_reprise_d_un_csv_modifie_a_la_main(avec_tableau):
    chemin = avec_tableau.racine / "pages" / ID_PAGE / "brut" / "decroissance.csv"
    chemin.write_text(
        chemin.read_text(encoding="utf-8").replace("50.5", "99.0"), encoding="utf-8"
    )
    avec_tableau.enregistrer_reprise("Faute de frappe corrigée dans le CSV")
    derniere = avec_tableau.historique(ID_PAGE)[-1]
    assert derniere.nature == "reprise"
    assert derniere.modifications == (
        Modification("mesures.decroissance.activite[2]", "50.5", "99.0"),
    )
    assert avec_tableau.modifications_non_tracees() == []


def test_un_csv_supprime_a_la_main_est_signale(avec_tableau):
    (avec_tableau.racine / "pages" / ID_PAGE / "brut" / "decroissance.csv").unlink()
    assert avec_tableau.modifications_non_tracees() == [
        f"pages/{ID_PAGE}/brut/decroissance.csv"
    ]


def test_un_fichier_depose_non_declare_n_est_pas_signale(avec_tableau):
    dossier = avec_tableau.racine / "pages" / ID_PAGE / "brut"
    (dossier / "depose.csv").write_text("a\n1\n", encoding="utf-8")
    (dossier / "serie.dcm").write_bytes(b"lourd")
    assert avec_tableau.modifications_non_tracees() == []


def test_une_coupure_avant_le_commit_est_detectee_pour_le_csv(
    avec_tableau, monkeypatch
):
    def echec(*_, **__):
        raise OSError("coupure")

    monkeypatch.setattr(Depot, "enregistrer", echec)
    with pytest.raises(OSError):
        avec_tableau.modifier_page(
            page(description_tableau()),
            tableaux={"decroissance": modifie("activite", 1, 60.0)},
        )
    monkeypatch.undo()
    assert avec_tableau.modifications_non_tracees() == [
        f"pages/{ID_PAGE}/brut/decroissance.csv"
    ]


def test_le_csv_a_des_fins_de_ligne_lf(avec_tableau):
    chemin = avec_tableau.racine / "pages" / ID_PAGE / "brut" / "decroissance.csv"
    assert b"\r\n" not in chemin.read_bytes()


def test_lire_une_grandeur_qui_n_est_pas_un_tableau(arc):
    arc.creer_page(page({"nature": "identifiant", "nom": "lot", "valeur": "1"}))
    with pytest.raises(ValueError, match="pas un tableau"):
        arc.lire_tableau(ID_PAGE, "lot")


@pytest.mark.skipif(shutil.which("git") is None, reason="Git standard non installé")
def test_git_standard_voit_la_trace_du_fichier_lourd(page_avec_reference, source):
    source.write_bytes(b"autre")
    page_avec_reference.enregistrer_fichier_modifie(ID_PAGE, "serie_ct", "Refait")
    sortie = subprocess.run(
        ["git", "-C", str(page_avec_reference.racine), "log", "-1", "--format=%B"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "Fichier: mesures.serie_ct.stockage.empreinte" in sortie
    subprocess.run(
        ["git", "-C", str(page_avec_reference.racine), "fsck", "--strict"], check=True
    )


def test_chemin_absolu_de_reference_stocke_sans_prefixe(page_avec_reference, source):
    stockage = page_avec_reference.lire_page(ID_PAGE).grandeur("serie_ct").stockage
    assert not stockage.chemin.startswith("\\\\?\\")
    assert os.path.isabs(stockage.chemin)


# --- Pièces jointes de l'interface et états des fichiers ---------------------------------


@pytest.fixture
def page_vide(arc):
    identifiant = arc.nouvel_identifiant(HEURE, "jointures")
    page = Page(
        id=identifiant,
        titre="Jointures",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
    )
    arc.creer_page(page)
    return page


def ecrire(chemin, contenu):
    chemin.write_bytes(contenu)
    return chemin


def test_joindre_deux_fichiers_de_meme_nom_garde_les_deux(arc, page_vide, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    premier = arc.joindre_fichier(page_vide.id, ecrire(tmp_path / "a/i.png", b"un"))
    second = arc.joindre_fichier(page_vide.id, ecrire(tmp_path / "b/i.png", b"deux"))
    troisieme = arc.joindre_fichier(page_vide.id, ecrire(tmp_path / "b/j.tar", b"3"))
    assert premier.chemin == "pieces_jointes/i.png"
    assert second.chemin == "pieces_jointes/i_2.png"
    assert troisieme.chemin == "pieces_jointes/j.tar"
    dossier = arc.racine / "pages" / page_vide.id
    assert (dossier / premier.chemin).read_bytes() == b"un"
    assert (dossier / second.chemin).read_bytes() == b"deux"


def test_joindre_deux_fois_le_meme_fichier_ne_le_copie_qu_une_fois(
    arc, page_vide, tmp_path
):
    source = ecrire(tmp_path / "i.png", b"un")
    assert arc.joindre_fichier(page_vide.id, source) == arc.joindre_fichier(
        page_vide.id, source
    )
    dossier = arc.racine / "pages" / page_vide.id / "pieces_jointes"
    assert sorted(f.name for f in dossier.iterdir()) == ["i.png"]


def test_joindre_un_nom_invalide_sous_windows_est_refuse(arc, page_vide, tmp_path):
    with pytest.raises(NomInvalide):
        arc.joindre_fichier(page_vide.id, ecrire(tmp_path / "a:b.png", b"x"))
    assert not (arc.racine / "pages" / page_vide.id / "pieces_jointes").exists()


def test_etat_d_un_fichier(tmp_path):
    chemin = ecrire(tmp_path / "f", b"contenu")
    empreinte = empreinte_fichier(chemin)
    assert etat_fichier(chemin, empreinte) == "intact"
    assert etat_fichier(chemin, empreinte, taille_max=7) == "intact"
    assert etat_fichier(chemin, empreinte, taille_max=6) == "non vérifié"
    ecrire(chemin, b"change")
    assert etat_fichier(chemin, empreinte) == "altéré"
    chemin.unlink()
    assert etat_fichier(chemin, empreinte) == "manquant"
    assert etat_fichier(tmp_path, empreinte) == "manquant"
    assert etat_fichier(tmp_path / "f" / "dessous", empreinte) == "manquant"


def test_un_fichier_illisible_est_signale_sans_erreur(tmp_path, monkeypatch):
    chemin = ecrire(tmp_path / "f", b"contenu")

    def refuser(_chemin):
        raise PermissionError("accès refusé")

    monkeypatch.setattr("medphystools.fichiers.empreinte_fichier", refuser)
    assert etat_fichier(chemin, "0" * 64) == "illisible"


def test_etats_des_fichiers_d_une_page(arc, page_vide, tmp_path):
    piece = arc.joindre_fichier(page_vide.id, ecrire(tmp_path / "i.png", b"image"))
    copie = arc.importer_copie(ecrire(tmp_path / "c.bin", b"copie"), "copie")
    source = ecrire(tmp_path / "r.bin", b"reference")
    reference = arc.referencer_fichier(source, "reference")
    dicom = Fichier(
        nom="serie",
        stockage=ReferenceDicom(
            study_instance_uid="1.2",
            series_instance_uid="1.2.3",
            sop_instance_uids=("1.2.3.4",),
            noeud="PACS",
            date=HEURE.date(),
        ),
    )
    page = page_vide.model_copy(
        update={"pieces_jointes": (piece,), "mesures": (copie, reference, dicom)}
    )
    arc.modifier_page(page)
    tete = Depot(arc.racine).tete()

    assert arc.etats_fichiers(page) == {
        "pieces_jointes/i.png": "intact",
        "copie": "intact",
        "reference": "intact",
    }

    ecrire(source, b"modifiee")
    (arc.racine / "pages" / page.id / piece.chemin).unlink()
    assert arc.etats_fichiers(page) == {
        "pieces_jointes/i.png": "manquant",
        "copie": "intact",
        "reference": "altéré",
    }
    assert arc.etats_fichiers(page, taille_max=2)["reference"] == "non vérifié"
    assert Depot(arc.racine).tete() == tete


@pytest.mark.parametrize("nom", ["image.dcm", "IMAGE.DCM", "serie.dicom"])
def test_un_dicom_se_reconnait_a_son_extension(tmp_path, nom):
    assert semble_dicom(ecrire(tmp_path / nom, b"court"))


def test_un_dicom_se_reconnait_a_sa_marque(tmp_path):
    assert semble_dicom(ecrire(tmp_path / "IM0001", bytes(128) + b"DICM" + bytes(8)))


def test_un_fichier_ordinaire_n_est_pas_un_dicom(tmp_path):
    assert not semble_dicom(ecrire(tmp_path / "photo.png", bytes(300)))
    assert not semble_dicom(ecrire(tmp_path / "court.txt", b"DICM"))
    assert not semble_dicom(tmp_path / "absent.bin")
