"""Vérifie l'import DICOM : pseudonymisation, fantôme, noms neutres, pixels et stockage."""

import hashlib
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pydicom
import pytest
from fabrique_dicom import (
    ID_PATIENT,
    NOM,
    capture_ecran,
    dose_rt,
    ecrire,
    serie_ct,
    structures_rt,
)
from pydicom.uid import JPEG2000Lossless, JPEGLSLossless, RLELossless

from pymphub import Archive
from pymphub.depot import Depot
from pymphub.dicom import (
    BALISES_RETIREES,
    LIMITE_TEXTE_INCRUSTE,
    ErreurDicom,
    balises_patient,
    creer_references,
    empreinte_des_pixels,
    examiner,
    importer_series,
    lire_jeux,
    pseudonymiser,
)
from pymphub.fichiers import empreinte_fichier
from pymphub.page import Copie, Page, ReferenceDicom, depuis_json, vers_json

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MOTS_IDENTIFIANTS = (
    b"DUPONT",
    b"Jean",
    b"IPP-123456",
    b"19600215",
    b"Lilas",
    b"MARTIN",
)


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


def page_avec(arc, fichiers):
    identifiant = arc.nouvel_identifiant(HEURE, "dicom")
    page = Page(
        id=identifiant,
        titre="DICOM",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=tuple(fichiers),
    )
    arc.creer_page(page)
    return page


def octets_du_zip(chemin):
    with zipfile.ZipFile(chemin) as archive_zip:
        return {nom: archive_zip.read(nom) for nom in archive_zip.namelist()}


# --- Pseudonymisation ---------------------------------------------------------------------


def test_un_import_sans_option_particuliere_est_pseudonymise(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    resultat = importer_series(arc, chemins)
    fichier = resultat.fichiers[0]
    assert fichier.dicom.mode == "pseudonymise"
    for jeu in lire_jeux(arc.emplacement_fichier(fichier)):
        assert str(jeu.PatientName) == "ANONYME"
        assert jeu.PatientID == "ANONYME"
        assert jeu.PatientIdentityRemoved == "YES"
        assert "pymphub" in jeu.DeidentificationMethod


def test_aucune_balise_de_la_liste_ne_reste_dans_la_copie(arc, tmp_path):
    jeux = serie_ct(coupes=1)
    for mot_cle in BALISES_RETIREES:
        if mot_cle.endswith("Sequence"):
            continue
        element = pydicom.datadict.dictionary_VR(mot_cle)
        if element in (
            "LO",
            "SH",
            "ST",
            "LT",
            "UT",
            "CS",
            "DA",
            "TM",
            "AS",
            "DS",
            "IS",
        ):
            valeur = {
                "DA": "19600215",
                "TM": "120000",
                "AS": "066Y",
                "DS": "70",
                "IS": "1",
            }.get(element, "DUPONT")
            setattr(jeux[0], mot_cle, valeur)
    chemins = ecrire(jeux, tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    jeu = lire_jeux(arc.emplacement_fichier(fichier))[0]
    for mot_cle in BALISES_RETIREES:
        assert mot_cle not in jeu, mot_cle


def test_aucun_octet_identifiant_ne_reste_dans_la_copie(arc, tmp_path):
    """On cherche dans les octets eux-mêmes, et non seulement dans les balises connues."""
    chemins = ecrire(serie_ct() + [dose_rt(), structures_rt()], tmp_path / "src")
    resultat = importer_series(arc, chemins)
    assert len(resultat.fichiers) == 3
    for fichier in resultat.fichiers:
        chemin = arc.emplacement_fichier(fichier)
        for contenu in octets_du_zip(chemin).values():
            for mot in MOTS_IDENTIFIANTS:
                assert mot not in contenu, mot


def test_les_personnes_et_les_balises_imbriquees_sont_retirees(tmp_path):
    jeu = structures_rt()
    imbrique = pydicom.Dataset()
    imbrique.PatientName = "CACHE^DANS^UNE^SEQUENCE"
    imbrique.PersonName = "AUTRE^PERSONNE"
    imbrique.PatientBirthDate = "19600215"
    jeu.ROIContourSequence[0].ContourSequence[
        0
    ].ContourImageSequence = pydicom.Sequence([imbrique])
    jeu.add_new(0x60003000, "OW", b"\0\1")
    jeu.add_new(0x50003000, "OW", b"\0\1")
    pseudonymiser(jeu)
    assert "AUTRE" not in str(jeu)
    assert "19600215" not in str(jeu)
    assert "ReviewerName" not in jeu
    assert str(jeu.StructureSetLabel) == "STRUCTURES"
    assert (0x6000, 0x3000) not in jeu
    assert (0x5000, 0x3000) not in jeu
    assert (0x0009, 0x1001) not in jeu


def test_les_champs_de_texte_libre_de_la_radiotherapie_sont_retires():
    jeu = dose_rt()
    jeu.DoseComment = "Plan de DUPONT"
    jeu.RTPlanLabel = "DUPONT"
    jeu.file_meta.SourceApplicationEntityTitle = "DUPONT"
    jeu.file_meta.PrivateInformationCreatorUID = "1.2.3"
    jeu.file_meta.PrivateInformation = b"DUPONT"
    pseudonymiser(jeu)
    assert "DUPONT" not in str(jeu)
    assert "DUPONT" not in str(jeu.file_meta)


def test_les_uid_et_les_donnees_techniques_restent(arc, tmp_path):
    jeux = serie_ct()
    chemins = ecrire(jeux, tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    relus = lire_jeux(arc.emplacement_fichier(fichier))
    assert {j.SOPInstanceUID for j in relus} == {j.SOPInstanceUID for j in jeux}
    assert relus[0].SeriesInstanceUID == jeux[0].SeriesInstanceUID
    assert relus[0].FrameOfReferenceUID == jeux[0].FrameOfReferenceUID
    assert relus[0].KVP == 120
    assert relus[0].Manufacturer == "Fabricant fictif"
    assert relus[0].StudyDate == "20261114"


def test_le_rtstruct_garde_ses_contours_et_les_noms_de_structures(arc, tmp_path):
    jeu = structures_rt()
    chemins = ecrire(jeu, tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    relu = lire_jeux(arc.emplacement_fichier(fichier))[0]
    assert [r.ROIName for r in relu.StructureSetROISequence] == ["PTV", "Coeur"]
    assert (
        list(relu.ROIContourSequence[0].ContourSequence[0].ContourData)[:3]
        == list(jeu.ROIContourSequence[0].ContourSequence[0].ContourData)[:3]
    )
    assert fichier.dicom.empreinte_pixels is None
    assert fichier.dicom.frame_of_reference_uid == str(
        jeu.ReferencedFrameOfReferenceSequence[0].FrameOfReferenceUID
    )


# --- Pixels -------------------------------------------------------------------------------


def test_les_pixels_restent_identiques_empreinte_comprise(arc, tmp_path):
    jeux = serie_ct()
    attendue = empreinte_des_pixels(jeux)
    chemins = ecrire(jeux, tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    relus = lire_jeux(arc.emplacement_fichier(fichier))
    assert fichier.dicom.empreinte_pixels == attendue
    assert empreinte_des_pixels(relus) == attendue
    par_uid = {j.SOPInstanceUID: j for j in jeux}
    for jeu in relus:
        assert jeu.PixelData == par_uid[jeu.SOPInstanceUID].PixelData


def test_l_empreinte_des_pixels_ne_depend_que_des_pixels():
    jeux = serie_ct(coupes=2)
    avant = empreinte_des_pixels(jeux)
    jeux[0].PatientName = "AUTRE"
    assert empreinte_des_pixels(jeux) == avant
    assert empreinte_des_pixels(jeux[::-1]) == avant
    autre = serie_ct(coupes=2, pente=3.0)
    for jeu_autre, jeu in zip(autre, jeux, strict=True):
        jeu_autre.SOPInstanceUID = jeu.SOPInstanceUID
    assert empreinte_des_pixels(autre) == avant
    modifie = np.frombuffer(jeux[0].PixelData, dtype=np.int16).copy()
    modifie[0] += 1
    jeux[0].PixelData = modifie.tobytes()
    assert empreinte_des_pixels(jeux) != avant


def test_l_empreinte_est_calculee_a_la_main():
    jeux = serie_ct(coupes=2)
    calcul = hashlib.sha256()
    for jeu in sorted(jeux, key=lambda j: str(j.SOPInstanceUID)):
        calcul.update(jeu.PixelData)
    assert empreinte_des_pixels(jeux) == calcul.hexdigest()


@pytest.mark.parametrize(
    "syntaxe", [RLELossless, JPEG2000Lossless, JPEGLSLossless], ids=lambda s: s.name
)
def test_une_image_compressee_reste_identique_a_l_import(arc, tmp_path, syntaxe):
    # L'encodeur JPEG 2000 refuse les toutes petites images.
    jeux = serie_ct(coupes=2, lignes=64, colonnes=64)
    for jeu in jeux:
        # Les encodeurs JPEG veulent des valeurs positives.
        valeurs = np.frombuffer(jeu.PixelData, dtype=np.int16).reshape(64, 64)
        jeu.PixelData = valeurs.astype(np.uint16).tobytes()
        jeu.PixelRepresentation = 0
        jeu.compress(syntaxe)
    compressees = {j.SOPInstanceUID: bytes(j.PixelData) for j in jeux}
    chemins = ecrire(jeux, tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    for jeu in lire_jeux(arc.emplacement_fichier(fichier)):
        assert jeu.file_meta.TransferSyntaxUID == syntaxe
        assert bytes(jeu.PixelData) == compressees[jeu.SOPInstanceUID]


# --- Fantôme ------------------------------------------------------------------------------


def test_un_fichier_fantome_reste_identique(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    resultat = importer_series(arc, chemins, mode="fantome")
    fichier = resultat.fichiers[0]
    assert fichier.dicom.mode == "fantome"
    originaux = sorted(c.read_bytes() for c in chemins)
    copies = sorted(octets_du_zip(arc.emplacement_fichier(fichier)).values())
    assert copies == originaux
    assert "patient" in resultat.avertissements[0]


def test_le_logiciel_avertit_si_les_balises_ressemblent_a_un_patient():
    jeu = serie_ct(coupes=1)[0]
    assert "nom du patient" in balises_patient(jeu)
    assert "date de naissance" in balises_patient(jeu)
    # Le message donne des libellés, jamais les valeurs.
    assert NOM not in " ".join(balises_patient(jeu))
    assert ID_PATIENT not in " ".join(balises_patient(jeu))


@pytest.mark.parametrize(
    "nom", ["FANTOME_CATPHAN", "Phantom^Eau", "CQ^SCANNER", "TEST"]
)
def test_un_nom_de_fantome_n_alerte_pas(nom):
    jeu = serie_ct(coupes=1)[0]
    jeu.PatientName = nom
    jeu.PatientID = "CQ-2026"
    del jeu.PatientBirthDate
    del jeu.PatientAddress
    assert balises_patient(jeu) == []


def test_un_fantome_propre_n_avertit_pas(arc, tmp_path):
    jeux = serie_ct(coupes=1)
    jeux[0].PatientName = "FANTOME"
    jeux[0].PatientID = "CQ"
    del jeux[0].PatientBirthDate
    del jeux[0].PatientAddress
    chemins = ecrire(jeux, tmp_path / "src")
    assert importer_series(arc, chemins, mode="fantome").avertissements == []


def test_un_mode_inconnu_est_refuse(arc, tmp_path):
    with pytest.raises(ErreurDicom, match="inconnu"):
        importer_series(arc, ecrire(serie_ct(), tmp_path / "src"), mode="autre")


# --- Noms de fichiers ---------------------------------------------------------------------


def test_aucun_nom_de_fichier_n_atteint_l_archive(arc, tmp_path):
    chemins = ecrire(
        serie_ct(), tmp_path / "DUPONT Jean IRM", nom="DUPONT_Jean_19600215"
    )
    resultat = importer_series(arc, chemins)
    fichier = resultat.fichiers[0]
    page = page_avec(arc, [fichier])
    arc.chercher_identifiants()

    assert isinstance(fichier.stockage, Copie)
    assert fichier.stockage.nom_origine == "CT_4_fichiers.dcm.zip"
    assert fichier.nom == "dicom_CT"
    assert sorted(octets_du_zip(arc.emplacement_fichier(fichier))) == [
        "00001.dcm",
        "00002.dcm",
        "00003.dcm",
        "00004.dcm",
    ]

    json_page = vers_json(page)
    assert "DUPONT" not in json_page
    assert "19600215" not in json_page
    for contenu in Depot(arc.racine).fichiers_suivis().values():
        assert b"DUPONT" not in contenu
    assert b"DUPONT" not in (arc.racine / "index.sqlite").read_bytes()
    for fichier_disque in arc.racine.rglob("*"):
        assert "DUPONT" not in fichier_disque.name
    assert arc.chercher_identifiants(texte="DUPONT") == []


def test_le_commit_ne_cite_aucun_nom(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    page_avec(arc, importer_series(arc, chemins).fichiers)
    for commit in Depot(arc.racine).commits():
        assert b"DUPONT" not in commit.message


# --- Stockage -----------------------------------------------------------------------------


def test_la_serie_est_compressee_sans_perte(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    chemin = arc.emplacement_fichier(fichier)
    with zipfile.ZipFile(chemin) as archive_zip:
        assert archive_zip.testzip() is None
        assert all(
            i.compress_type == zipfile.ZIP_DEFLATED for i in archive_zip.infolist()
        )
    assert fichier.stockage.empreinte == empreinte_fichier(chemin)
    # Hors de Git : seule l'empreinte y est versionnée.
    page_avec(arc, [fichier])
    assert not any(c.startswith("objets/") for c in Depot(arc.racine).fichiers_suivis())


def test_une_meme_serie_n_est_stockee_qu_une_fois(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    un = importer_series(arc, chemins).fichiers[0]
    deux = importer_series(arc, chemins).fichiers[0]
    assert un.stockage.empreinte == deux.stockage.empreinte


def test_plusieurs_series_sont_separees_et_nommees(arc, tmp_path):
    chemins = ecrire(serie_ct() + serie_ct(coupes=2) + [dose_rt()], tmp_path / "src")
    fichiers = importer_series(arc, chemins, noms_pris=["dicom_CT"]).fichiers
    assert sorted(f.nom for f in fichiers) == [
        "dicom_CT_2",
        "dicom_CT_3",
        "dicom_RTDOSE",
    ]
    assert sorted(f.dicom.fichiers for f in fichiers) == [1, 2, 4]


def test_le_choix_est_enregistre_dans_la_page_et_se_relit(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    fichier = importer_series(arc, chemins, mode="fantome").fichiers[0]
    page = page_avec(arc, [fichier])
    relue = depuis_json(vers_json(page)).grandeur("dicom_CT")
    assert relue.dicom.mode == "fantome"
    assert relue.dicom.modalite == "CT"
    assert relue.dicom.fichiers == 4
    assert arc.chemin_fichier(page.id, "dicom_CT").is_file()


def test_les_fichiers_non_dicom_sont_comptes_sans_etre_nommes(arc, tmp_path):
    chemins = ecrire(serie_ct(coupes=1), tmp_path / "src")
    (tmp_path / "src" / "DUPONT_notes.txt").write_text("texte", encoding="utf-8")
    resultat = importer_series(arc, [tmp_path / "src"])
    assert resultat.ignores == 1
    assert len(resultat.fichiers) == 1
    assert chemins


def test_sans_aucun_dicom_l_import_s_explique(arc, tmp_path):
    (tmp_path / "a.txt").write_text("texte", encoding="utf-8")
    with pytest.raises(ErreurDicom, match="Aucun fichier DICOM"):
        importer_series(arc, [tmp_path / "a.txt"])


def test_un_texte_incruste_declare_est_signale_et_la_limite_existe(arc, tmp_path):
    chemins = ecrire(capture_ecran(), tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    assert "texte incrusté" in fichier.dicom.avertissements[0]
    assert "n'est pas détecté" in LIMITE_TEXTE_INCRUSTE


def test_examiner_ne_lit_que_les_entetes_et_n_ecrit_rien(tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    avant = sorted(c.read_bytes() for c in chemins)
    examen = examiner(chemins)
    assert len(examen.series) == 1
    assert examen.series[0].modalite == "CT"
    assert "nom du patient" in examen.balises_patient
    assert sorted(c.read_bytes() for c in chemins) == avant
    assert all(c.parent == tmp_path / "src" for c in chemins)


# --- Référence seule ----------------------------------------------------------------------


def test_une_reference_ne_copie_rien_et_ne_garde_que_les_uid(arc, tmp_path):
    jeux = serie_ct()
    chemins = ecrire(jeux, tmp_path / "src")
    objets_avant = list((arc.racine / "objets").rglob("*"))
    fichier = creer_references(chemins, "PACS_CHU").fichiers[0]
    assert list((arc.racine / "objets").rglob("*")) == objets_avant
    assert isinstance(fichier.stockage, ReferenceDicom)
    assert fichier.stockage.noeud == "PACS_CHU"
    assert fichier.stockage.series_instance_uid == jeux[0].SeriesInstanceUID
    assert set(fichier.stockage.sop_instance_uids) == {j.SOPInstanceUID for j in jeux}
    assert fichier.stockage.date == date(2026, 11, 14)
    assert fichier.stockage.empreinte_pixels == empreinte_des_pixels(jeux)
    assert fichier.stockage.description == ""
    page = page_avec(arc, [fichier])
    assert "DUPONT" not in vers_json(page)


# --- Cache des empreintes -----------------------------------------------------------------


def test_le_cache_evite_de_relire_une_grosse_serie(arc, tmp_path, monkeypatch):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    chemin = arc.emplacement_fichier(fichier)
    assert arc.etat_fichier_brut(fichier, taille_max=1) == "non vérifié"
    assert arc.cache_empreintes.calculer(chemin) == fichier.stockage.empreinte

    def interdit(_chemin):
        pytest.fail("L'empreinte aurait dû venir du cache.")

    monkeypatch.setattr("pymphub.fichiers.empreinte_fichier", interdit)
    assert arc.cache_empreintes.calculer(chemin) == fichier.stockage.empreinte
    # Trop gros pour être calculé à l'affichage, mais déjà vérifié une fois.
    assert arc.etat_fichier_brut(fichier, taille_max=1) == "intact"


def test_le_cache_voit_un_fichier_modifie(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    chemin = arc.emplacement_fichier(fichier)
    arc.cache_empreintes.calculer(chemin)
    chemin.chmod(0o644)
    with open(chemin, "ab") as sortie:
        sortie.write(b"x")
    assert arc.cache_empreintes.connue(chemin) is None
    assert arc.etat_fichier_brut(fichier) == "altéré"


def test_un_cache_abime_se_reconstruit(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    chemin = arc.emplacement_fichier(fichier)
    arc.cache_empreintes.chemin.write_text("{pas du json", encoding="utf-8")
    assert arc.cache_empreintes.calculer(chemin) == fichier.stockage.empreinte
    assert arc.cache_empreintes.connue(chemin) == fichier.stockage.empreinte


def test_le_cache_ne_passe_pas_par_git(arc, tmp_path):
    chemins = ecrire(serie_ct(), tmp_path / "src")
    fichier = importer_series(arc, chemins).fichiers[0]
    arc.cache_empreintes.calculer(arc.emplacement_fichier(fichier))
    page_avec(arc, [fichier])
    assert not any("cache" in c for c in Depot(arc.racine).fichiers_suivis())
    assert arc.modifications_non_tracees() == []


def test_importer_pymphub_ne_charge_pas_pydicom():
    code = (
        "import sys, pymphub, pymphub.archive;"
        "assert not [m for m in sys.modules if m.startswith('pydicom')]"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


def test_lire_une_serie_abimee_s_explique(tmp_path):
    chemin = tmp_path / "abime.zip"
    chemin.write_bytes(b"pas un zip")
    with pytest.raises(ErreurDicom, match="illisible"):
        lire_jeux(chemin)
