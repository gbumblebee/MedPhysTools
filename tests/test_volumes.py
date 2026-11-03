"""Vérifie les valeurs physiques, la géométrie, la 4D et les structures, sur des cas connus."""

import gc
import math
import time
import weakref
from datetime import date

import numpy as np
import pytest
from fabrique_dicom import (
    REPERE,
    capture_ecran,
    dose_rt,
    serie_4d_amelioree,
    serie_4d_classique,
    serie_ct,
    serie_tep,
    structures_rt,
    temp_nm,
    valeurs_ct,
)
from pydicom.uid import JPEG2000Lossless, JPEGLSLossless, RLELossless

from pymphub.dicom import ErreurDicom
from pymphub.volumes import (
    balises_principales,
    construire_volume,
    coupe,
    fenetre_automatique,
    lire_structures,
    nombre_de_coupes,
    pixel_du_voxel,
    preparer_3d,
    projeter_3d,
    reechantillonner_sur_coupe,
    rendre_3d,
    segments_sur_coupe,
    taille_pixel,
    valeur_au_point,
    volume_pour_structures,
    voxel_du_pixel,
)

# --- Valeurs physiques --------------------------------------------------------------------


def test_le_scanner_applique_pente_et_ordonnee():
    volume = construire_volume(serie_ct(pente=2.0, ordonnee=-1024.0))
    assert volume.unite == "HU"
    assert volume.modalite == "CT"
    assert volume.donnees.shape == (1, 4, 8, 10)
    attendu = valeurs_ct(2) * 2.0 - 1024.0
    assert volume.donnees[0, 2] == pytest.approx(attendu)
    # Stockée : 1000 × 3 + 10 × 5 + 7 = 3057, soit 3057 × 2 − 1024 = 5090 HU.
    assert volume.valeur(0, 3, 5, 7) == 5090.0


def test_les_coupes_sont_triees_par_position_et_non_par_ordre_de_fichier():
    jeux = serie_ct(melanger=False)
    jeux[0].InstanceNumber = 99
    volume = construire_volume(jeux[::-1])
    assert volume.vers_patient(0, 0, 0)[2] == 100.0
    assert volume.vers_patient(0, 0, 3)[2] == 115.0
    assert volume.donnees[0, 0, 0, 0] == 0 * 2.0 - 1024.0


def test_chaque_coupe_de_tep_a_sa_propre_pente():
    volume = construire_volume(serie_tep(pentes=(0.5, 2.0)))
    assert volume.unite == "Bq/mL"
    assert volume.donnees[0, 0, 0, 0] == 100 * 0.5
    assert volume.donnees[0, 1, 0, 0] == 200 * 2.0


def test_la_dose_multiplie_les_entiers_par_le_dose_grid_scaling():
    volume = construire_volume([dose_rt(facteur=0.001)])
    assert volume.unite == "Gy"
    assert volume.modalite == "RTDOSE"
    assert volume.donnees.shape == (1, 3, 6, 7)
    # Stockée : 100000 × 2 + 100 × 3 + 4 = 200304, soit 200,304 Gy.
    assert volume.valeur(0, 1, 3, 4) == pytest.approx(200.304)


def test_la_dose_sur_16_bits_utilise_aussi_son_facteur():
    volume = construire_volume([dose_rt(facteur=2.5e-4, bits=16)])
    # Stockée : 1000 × 1 + 10 × 2 + 3 = 1023, soit 0,25575 Gy.
    assert volume.valeur(0, 0, 2, 3) == pytest.approx(0.25575)


def test_un_entier_sur_32_bits_reste_exact_a_la_precision_du_flottant():
    volume = construire_volume([dose_rt(facteur=1e-5)])
    assert volume.valeur(0, 2, 5, 6) == pytest.approx(3.00506, rel=1e-6)


def test_la_dose_sans_facteur_est_refusee_et_non_affichee_en_entiers():
    jeu = dose_rt()
    del jeu.DoseGridScaling
    with pytest.raises(ErreurDicom, match="DoseGridScaling"):
        construire_volume([jeu])


def test_les_coupes_de_dose_suivent_le_grid_frame_offset_vector():
    volume = construire_volume([dose_rt(decalages=(0.0, 2.5, 5.0))])
    assert volume.espacement == pytest.approx((3.0, 4.0, 2.5))
    assert volume.vers_patient(0, 0, 2) == pytest.approx([-10.0, -20.0, 105.0])


def test_le_grid_frame_offset_vector_absolu_donne_le_meme_volume():
    relatif = construire_volume([dose_rt(decalages=(0.0, 5.0, 10.0))])
    absolu = construire_volume([dose_rt(decalages=(100.0, 105.0, 110.0))])
    assert absolu.affine == pytest.approx(relatif.affine)
    assert absolu.donnees == pytest.approx(relatif.donnees)


def test_un_grid_frame_offset_vector_decroissant_remet_les_coupes_dans_l_ordre():
    volume = construire_volume([dose_rt(decalages=(10.0, 5.0, 0.0))])
    # La première image du fichier, à 100000 + …, se trouve à l'offset le plus grand.
    assert volume.vers_patient(0, 0, 0)[2] == pytest.approx(90.0)
    assert volume.donnees[0, 2, 0, 0] == pytest.approx(100.0)
    assert volume.donnees[0, 0, 0, 0] == pytest.approx(300.0)


def test_un_vecteur_de_mauvaise_longueur_s_explique():
    jeu = dose_rt()
    jeu.GridFrameOffsetVector = [0.0, 5.0]
    with pytest.raises(ErreurDicom, match="GridFrameOffsetVector"):
        construire_volume([jeu])


def test_une_dose_relative_ne_se_dit_pas_en_gray():
    jeu = dose_rt()
    jeu.DoseUnits = "RELATIVE"
    assert construire_volume([jeu]).unite == "relatif"


def test_la_temp_lit_la_pente_reelle_et_les_positions_du_detecteur():
    volume = construire_volume([temp_nm()])
    assert volume.unite == "Bq/ml"
    assert volume.modalite == "NM"
    assert volume.donnees.shape == (1, 3, 4, 4)
    assert volume.donnees[0, 1, 0, 0] == 20 * 2.5
    assert volume.espacement == pytest.approx((4.0, 4.0, 4.0))
    assert volume.vers_patient(0, 0, 2)[2] == pytest.approx(8.0)


def test_une_capture_sans_position_reste_une_image():
    volume = construire_volume([capture_ecran()])
    assert not volume.geometrie_connue
    assert volume.donnees.shape == (1, 1, 4, 4)
    # Du rouge pur, lu en luminance.
    assert volume.donnees[0, 0, 0, 0] == pytest.approx(255 * 0.299)


def test_monochrome1_est_signale_pour_l_affichage():
    jeu = capture_ecran()
    jeu.SamplesPerPixel = 1
    jeu.PhotometricInterpretation = "MONOCHROME1"
    del jeu.PlanarConfiguration
    jeu.PixelData = bytes(16)
    assert construire_volume([jeu]).inverse_monochrome


@pytest.mark.parametrize(
    "syntaxe", [RLELossless, JPEG2000Lossless, JPEGLSLossless], ids=lambda s: s.name
)
def test_une_serie_compressee_donne_les_memes_valeurs(syntaxe):
    # L'encodeur JPEG 2000 refuse les toutes petites images.
    jeux = serie_ct(coupes=2, melanger=False, lignes=64, colonnes=64)
    for jeu in jeux:
        valeurs = np.frombuffer(jeu.PixelData, dtype=np.int16).reshape(64, 64)
        jeu.PixelData = valeurs.astype(np.uint16).tobytes()
        jeu.PixelRepresentation = 0
        jeu.compress(syntaxe)
    volume = construire_volume(jeux)
    assert volume.donnees[0, 1] == pytest.approx(valeurs_ct(1, 64, 64) * 2.0 - 1024.0)


# --- 4D -----------------------------------------------------------------------------------


def test_une_serie_4d_en_fichiers_separes_se_range_par_phase_et_par_coupe():
    volume = construire_volume(serie_4d_classique(phases=3, coupes=2))
    assert volume.donnees.shape == (3, 2, 3, 3)
    assert volume.phases == 3
    assert volume.temps == (1.0, 2.0, 3.0)
    for t in range(3):
        for z in range(2):
            assert volume.donnees[t, z, 0, 0] == 100 * t + z


def test_une_serie_4d_amelioree_lit_tout_image_par_image():
    volume = construire_volume([serie_4d_amelioree(phases=2, coupes=3)])
    assert volume.donnees.shape == (2, 3, 3, 4)
    assert volume.espacement == pytest.approx((0.5, 1.5, 2.5))
    assert volume.vers_patient(0, 0, 2)[2] == pytest.approx(5.0)
    for t in range(2):
        for z in range(3):
            assert volume.donnees[t, z, 0, 0] == (100 * t + 10 * z) * 2.0 - 10.0


def test_une_temp_synchronisee_a_quatre_dimensions():
    volume = construire_volume([temp_nm(coupes=2, temps=3)])
    assert volume.donnees.shape == (3, 2, 4, 4)
    assert volume.donnees[2, 1, 0, 0] == (20 + 2) * 2.5


def test_une_serie_4d_incomplete_s_explique():
    jeux = serie_4d_classique(phases=2, coupes=2)
    with pytest.raises(ErreurDicom, match="4D incomplète"):
        construire_volume(jeux[:-1])


# --- Mémoire ------------------------------------------------------------------------------


def test_un_volume_se_construit_depuis_un_generateur():
    """Chaque fichier est relâché dès que possible : il n'y a donc pas de liste à garder."""
    volume = construire_volume(iter(serie_ct(melanger=False)))
    assert volume.valeur(0, 3, 5, 7) == 5090.0


def test_un_fichier_lu_n_est_plus_retenu(tmp_path):
    jeux = serie_ct()
    observes = []

    def un_a_un():
        while jeux:
            jeu = jeux.pop()
            observes.append(weakref.ref(jeu))
            yield jeu
            del jeu

    volume = construire_volume(un_a_un())
    gc.collect()
    assert volume.donnees.shape == (1, 4, 8, 10)
    assert [o() for o in observes] == [None] * 4


def test_au_dela_du_seuil_le_volume_est_sur_le_disque(monkeypatch):
    """Mêmes valeurs, mais dans un fichier temporaire que le système charge à la demande."""
    en_memoire = construire_volume(serie_ct(melanger=False))
    assert not isinstance(en_memoire.donnees, np.memmap)
    monkeypatch.setattr("pymphub.volumes.SEUIL_SUR_DISQUE_OCTETS", 1)
    sur_disque = construire_volume(serie_ct(melanger=False))
    assert isinstance(sur_disque.donnees, np.memmap)
    assert np.array_equal(sur_disque.donnees, en_memoire.donnees)
    assert coupe(sur_disque, "sagittal", 3) == pytest.approx(
        coupe(en_memoire, "sagittal", 3)
    )


def test_une_4d_sur_le_disque_garde_ses_phases(monkeypatch):
    monkeypatch.setattr("pymphub.volumes.SEUIL_SUR_DISQUE_OCTETS", 1)
    volume = construire_volume(serie_4d_classique(phases=3, coupes=2))
    assert isinstance(volume.donnees, np.memmap)
    assert volume.donnees[2, 1, 0, 0] == 201


def test_le_fichier_temporaire_ne_reste_pas_sur_le_disque(monkeypatch, tmp_path):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setattr("pymphub.volumes.SEUIL_SUR_DISQUE_OCTETS", 1)
    volume = construire_volume(serie_ct())
    assert volume.valeur(0, 0, 0, 0) is not None
    del volume
    gc.collect()
    assert list(tmp_path.iterdir()) == []


def test_sans_place_sur_le_disque_cela_s_explique(monkeypatch):
    monkeypatch.setattr("pymphub.volumes.SEUIL_SUR_DISQUE_OCTETS", 1)
    monkeypatch.setattr("shutil.disk_usage", lambda _d: type("U", (), {"free": 0})())
    with pytest.raises(ErreurDicom, match="place sur le disque"):
        construire_volume(serie_ct())


def test_une_lecture_annulee_s_arrete_au_fichier_suivant(tmp_path):
    from fabrique_dicom import ecrire

    from pymphub import Archive
    from pymphub.dicom import LectureAnnulee, importer_series, lire_serie

    arc = Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))
    fichier = importer_series(arc, ecrire(serie_ct(), tmp_path / "s")).fichiers[0]
    chemin = arc.emplacement_fichier(fichier)
    lus = []
    with pytest.raises(LectureAnnulee):
        for jeu in lire_serie(chemin, annule=lambda: len(lus) >= 2):
            lus.append(jeu)
    assert len(lus) == 2


# --- Erreurs ------------------------------------------------------------------------------


def test_une_serie_sans_image_s_explique():
    with pytest.raises(ErreurDicom, match="pas d'image"):
        construire_volume([structures_rt()])


def test_des_images_de_tailles_differentes_s_expliquent():
    jeux = serie_ct(coupes=2)
    autre = np.zeros((4, 4), dtype=np.int16)
    jeux[0].Rows, jeux[0].Columns = 4, 4
    jeux[0].PixelData = autre.tobytes()
    with pytest.raises(ErreurDicom, match="même taille"):
        construire_volume(jeux)


def test_des_orientations_differentes_s_expliquent():
    jeux = serie_ct(coupes=2)
    jeux[0].ImageOrientationPatient = [0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    with pytest.raises(ErreurDicom, match="même orientation"):
        construire_volume(jeux)


def test_un_espacement_irregulier_est_signale():
    jeux = serie_ct(coupes=3, melanger=False)
    jeux[2].ImagePositionPatient = [-10.0, -20.0, 130.0]
    volume = construire_volume(jeux)
    assert any("pas régulier" in a for a in volume.avertissements)


def test_une_serie_gigantesque_est_refusee_avant_de_saturer_la_memoire():
    jeu = dose_rt()
    jeu.NumberOfFrames = 2
    jeu.Rows = 65535
    jeu.Columns = 65535
    with pytest.raises(ErreurDicom, match="trop grande"):
        construire_volume([jeu] * 1)


def test_une_syntaxe_impossible_s_explique():
    jeux = serie_ct(coupes=1)
    jeux[0].file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.4.100"
    with pytest.raises(ErreurDicom, match="impossible à décoder"):
        construire_volume(jeux)


# --- Géométrie et coupes ------------------------------------------------------------------


def volume_marque():
    """Un volume dont chaque voxel dit où il est, en position patient."""
    volume = construire_volume(serie_ct(melanger=False))
    return volume


def test_la_coupe_axiale_est_en_convention_radiologique():
    volume = volume_marque()
    assert nombre_de_coupes(volume, "axial") == 4
    image = coupe(volume, "axial", 2)
    assert image.shape == (8, 10)
    # x croît vers la gauche du patient, qui est à droite de l'image : pas d'inversion ici.
    assert image[0, 0] == volume.donnees[0, 2, 0, 0]
    assert image[7, 9] == volume.donnees[0, 2, 7, 9]
    assert taille_pixel(volume, "axial") == pytest.approx((1.0, 2.0))


def test_la_coupe_sagittale_met_la_tete_en_haut():
    volume = volume_marque()
    assert nombre_de_coupes(volume, "sagittal") == 10
    image = coupe(volume, "sagittal", 3)
    assert image.shape == (4, 8)
    # z croît vers la tête : la dernière coupe est sur la première ligne.
    assert image[0, 0] == volume.donnees[0, 3, 0, 3]
    assert image[3, 5] == volume.donnees[0, 0, 5, 3]
    assert taille_pixel(volume, "sagittal") == pytest.approx((5.0, 1.0))


def test_la_coupe_coronale_met_la_tete_en_haut():
    volume = volume_marque()
    assert nombre_de_coupes(volume, "coronal") == 8
    image = coupe(volume, "coronal", 4)
    assert image.shape == (4, 10)
    assert image[0, 6] == volume.donnees[0, 3, 4, 6]
    assert image[3, 0] == volume.donnees[0, 0, 4, 0]
    assert taille_pixel(volume, "coronal") == pytest.approx((5.0, 2.0))


def test_un_patient_acquis_en_sens_inverse_reste_bien_oriente():
    """x décroissant : la droite du patient doit rester à gauche de l'image."""
    jeux = serie_ct(melanger=False)
    for jeu in jeux:
        jeu.ImageOrientationPatient = [-1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    volume = construire_volume(jeux)
    image = coupe(volume, "axial", 0)
    assert volume.vers_patient(9, 0, 0)[0] < volume.vers_patient(0, 0, 0)[0]
    # La colonne de droite de l'image est du côté gauche du patient : x le plus grand.
    assert image[0, 9] == volume.donnees[0, 0, 0, 0]


def test_une_acquisition_sagittale_se_reforme_en_axial():
    jeux = serie_ct(melanger=False)
    for rang, jeu in enumerate(jeux):
        jeu.ImageOrientationPatient = [0.0, 1.0, 0.0, 0.0, 0.0, -1.0]
        jeu.ImagePositionPatient = [rang * 5.0, 0.0, 0.0]
    volume = construire_volume(jeux)
    assert nombre_de_coupes(volume, "sagittal") == 4
    assert coupe(volume, "sagittal", 0).shape == (8, 10)
    assert coupe(volume, "axial", 0).shape == (10, 4)


@pytest.mark.parametrize("plan", ["axial", "sagittal", "coronal"])
def test_pixel_et_voxel_se_correspondent(plan):
    volume = volume_marque()
    indice = 1
    image = coupe(volume, plan, indice)
    ligne, colonne = image.shape[0] - 1, image.shape[1] - 1
    voxel = voxel_du_pixel(volume, plan, indice, ligne, colonne)
    assert image[ligne, colonne] == volume.donnees[0][tuple(int(v) for v in voxel)]
    assert pixel_du_voxel(volume, plan, voxel) == (indice, ligne, colonne)


def test_la_fenetre_automatique_ignore_les_extremes():
    volume = volume_marque()
    bas, haut = fenetre_automatique(volume)
    assert volume.donnees.min() <= bas < haut <= volume.donnees.max()


def test_une_image_uniforme_garde_une_fenetre_valide():
    volume = construire_volume(serie_tep(pentes=(1.0,)))
    bas, haut = fenetre_automatique(volume)
    assert bas < haut


# --- Superposition d'une dose sur une image -----------------------------------------------


def test_la_dose_se_lit_sur_la_grille_du_scanner_par_les_coordonnees_patient():
    """Dose et scanner partagent leur origine, avec des pas différents."""
    scanner = volume_marque()
    dose = construire_volume([dose_rt(origine=(-10.0, -20.0, 100.0))])
    image = reechantillonner_sur_coupe(dose, scanner, "axial", 1)
    assert image.shape == (8, 10)
    # Pixel (0, 0) du scanner : même position que le voxel (0, 0, 1) de la dose.
    assert image[0, 0] == pytest.approx(dose.valeur(0, 1, 0, 0))
    # Colonne 3 du scanner : x = 6 mm, soit le voxel 2 de la dose (pas de 3 mm).
    assert image[0, 3] == pytest.approx(dose.valeur(0, 1, 0, 2))
    # Ligne 2 : y = 2 mm, à mi-chemin entre deux voxels de la dose (pas de 4 mm).
    attendu = (dose.valeur(0, 1, 0, 0) + dose.valeur(0, 1, 1, 0)) / 2
    assert image[2, 0] == pytest.approx(attendu)


def test_hors_de_la_dose_la_valeur_est_vide():
    scanner = volume_marque()
    loin = construire_volume([dose_rt(origine=(500.0, 500.0, 500.0))])
    assert np.all(np.isnan(reechantillonner_sur_coupe(loin, scanner, "axial", 0)))


# --- Structures ---------------------------------------------------------------------------


def volume_autour_de_structures():
    """Un volume centré sur l'origine, avec un voxel de 1 mm et des coupes tous les 5 mm."""
    jeux = serie_ct(coupes=4, origine=(-20.0, -20.0, 100.0), melanger=False)
    for jeu in jeux:
        jeu.PixelSpacing = [1.0, 1.0]
        jeu.Rows = 41
        jeu.Columns = 41
        jeu.PixelData = np.zeros((41, 41), dtype=np.int16).tobytes()
    return construire_volume(jeux)


def test_les_structures_gardent_nom_couleur_et_contours():
    structures = lire_structures(structures_rt())
    assert [s.nom for s in structures] == ["PTV", "Coeur"]
    assert structures[0].couleur == (255, 0, 0)
    assert len(structures[0].contours) == 3
    assert structures[0].contours[0].shape == (64, 3)
    assert structures[0].contours[0][0] == pytest.approx([8.0, 0.0, 100.0])


def test_un_autre_fichier_n_est_pas_un_rtstruct():
    with pytest.raises(ErreurDicom, match="pas un RTSTRUCT"):
        lire_structures(dose_rt())


def test_un_contour_dans_le_plan_de_la_coupe_se_dessine_en_entier():
    volume = volume_autour_de_structures()
    ptv = lire_structures(structures_rt())[0]
    traces = segments_sur_coupe(ptv, volume, "axial", 1)
    assert len(traces) == 1
    # Ligne brisée fermée, de rayon 8 mm autour du pixel central (20, 20).
    trace = traces[0]
    assert trace[0] == pytest.approx(trace[-1])
    rayons = np.hypot(trace[:, 0] - 20.0, trace[:, 1] - 20.0)
    assert rayons == pytest.approx(np.full(len(trace), 8.0))


def test_une_coupe_sans_contour_ne_dessine_rien():
    volume = volume_autour_de_structures()
    coeur = lire_structures(structures_rt())[1]
    assert segments_sur_coupe(coeur, volume, "axial", 0) == []
    assert len(segments_sur_coupe(coeur, volume, "axial", 1)) == 1


def test_en_coronal_un_contour_axial_donne_le_segment_attendu():
    """Cercle de rayon 8 coupé par le plan y = 0 : de x = −8 à x = +8, à la hauteur de z."""
    volume = volume_autour_de_structures()
    ptv = lire_structures(structures_rt())[0]
    traces = segments_sur_coupe(ptv, volume, "coronal", 20)
    assert len(traces) == 3
    extremites = sorted(sorted(t[:, 1].tolist()) for t in traces)
    for gauche, droite in extremites:
        assert gauche == pytest.approx(12.0, abs=1e-6)
        assert droite == pytest.approx(28.0, abs=1e-6)
    # z = 100, 105 et 110 tombent sur les coupes 3, 2 et 1 en partant du haut.
    assert sorted(float(t[0, 0]) for t in traces) == pytest.approx([1.0, 2.0, 3.0])


def test_en_sagittal_la_largeur_diminue_hors_du_centre():
    """Plan x = 6 : la corde du cercle de rayon 8 vaut 2·√(64 − 36)."""
    volume = volume_autour_de_structures()
    ptv = lire_structures(structures_rt())[0]
    traces = segments_sur_coupe(ptv, volume, "sagittal", 26)
    largeur = abs(traces[0][1, 1] - traces[0][0, 1])
    assert largeur == pytest.approx(2 * math.sqrt(64 - 36), abs=0.05)


def test_un_plan_hors_de_la_structure_ne_dessine_rien():
    volume = volume_autour_de_structures()
    ptv = lire_structures(structures_rt())[0]
    assert segments_sur_coupe(ptv, volume, "sagittal", 0) == []


def test_un_rtstruct_sans_image_s_affiche_sur_un_volume_vide():
    structures = lire_structures(structures_rt())
    volume = volume_pour_structures(structures)
    assert volume.donnees.max() == 0.0
    assert volume.modalite == "RTSTRUCT"
    # Les contours tombent bien dans le volume.
    bas = volume.vers_patient(0, 0, 0)
    assert np.all(bas <= np.array([-8.0, -8.0, 100.0]))
    forme = volume.donnees.shape
    haut = volume.vers_patient(forme[3] - 1, forme[2] - 1, forme[1] - 1)
    assert np.all(haut >= np.array([8.0, 8.0, 110.0]))
    assert any(
        segments_sur_coupe(structures[0], volume, "axial", z) for z in range(forme[1])
    )


def test_un_rtstruct_vide_s_explique():
    jeu = structures_rt({"Vide": ((1, 2, 3), {})})
    with pytest.raises(ErreurDicom, match="aucun contour"):
        volume_pour_structures(lire_structures(jeu))


# --- Vue 3D -------------------------------------------------------------------------------


def volume_cube(taille=40, centre=(20, 20, 20), rayon=5):
    """Un volume isotrope de 1 mm, noir, avec une boule brillante à un endroit connu."""
    jeux = serie_ct(coupes=taille, pas=1.0, origine=(0.0, 0.0, 0.0), melanger=False)
    z, y, x = np.mgrid[0:taille, 0:taille, 0:taille]
    boule = (z - centre[0]) ** 2 + (y - centre[1]) ** 2 + (
        x - centre[2]
    ) ** 2 <= rayon**2
    for rang, jeu in enumerate(jeux):
        jeu.PixelSpacing = [1.0, 1.0]
        jeu.Rows = taille
        jeu.Columns = taille
        jeu.PixelData = (boule[rang].astype(np.int16) * 1000).tobytes()
        jeu.RescaleSlope = 1.0
        jeu.RescaleIntercept = 0.0
    return construire_volume(jeux)


def centre_de_masse(image):
    lignes, colonnes = np.indices(image.shape)
    total = image.sum()
    return (lignes * image).sum() / total, (colonnes * image).sum() / total


def test_la_mip_vue_de_face_place_la_boule_au_bon_endroit():
    """Boule à gauche du patient (x grand) et vers la tête (z grand)."""
    volume = volume_cube(centre=(30, 20, 30))
    cube = preparer_3d(volume, taille=40)
    image = rendre_3d(cube, 0.0, 0.0, 0.0, 1000.0, "mip")
    ligne, colonne = centre_de_masse(image)
    # Le cube englobe la diagonale du volume : 40 × √3 mm sur 40 pixels, soit √3 mm par pixel.
    # La boule est à 10,5 mm du centre, en haut (tête) et à droite (gauche du patient).
    assert cube.pas_mm == pytest.approx(math.sqrt(3))
    decalage = 10.5 / math.sqrt(3)
    assert ligne == pytest.approx(19.5 - decalage, abs=1.0)
    assert colonne == pytest.approx(19.5 + decalage, abs=1.0)


def test_tourner_de_180_degres_inverse_la_gauche_et_la_droite():
    volume = volume_cube(centre=(20, 20, 30))
    cube = preparer_3d(volume, taille=40)
    face = rendre_3d(cube, 0.0, 0.0, 0.0, 1000.0, "mip")
    dos = rendre_3d(cube, 180.0, 0.0, 0.0, 1000.0, "mip")
    assert centre_de_masse(face)[1] > 24
    assert centre_de_masse(dos)[1] < 15


@pytest.mark.parametrize("azimut", [0.0, 45.0, 90.0, 135.0])
def test_le_volume_n_est_jamais_coupe_quand_il_tourne(azimut):
    """Une boule dans un coin : à tous les angles, elle reste entière dans l'image."""
    volume = volume_cube(centre=(33, 33, 33), rayon=5)
    cube = preparer_3d(volume, taille=40)
    face = rendre_3d(cube, 0.0, 0.0, 0.0, 1000.0, "mip").sum()
    tournee = rendre_3d(cube, azimut, 0.0, 0.0, 1000.0, "mip").sum()
    assert tournee == pytest.approx(face, rel=0.15)


@pytest.mark.parametrize(
    "angles", [(0.0, 0.0), (40.0, 0.0), (0.0, 70.0), (30.0, 120.0)]
)
def test_un_contour_projete_tombe_sur_la_boule_rendue(angles):
    volume = volume_cube(centre=(30, 12, 28))
    cube = preparer_3d(volume, taille=40)
    image = rendre_3d(cube, *angles, 0.0, 1000.0, "mip")
    point = volume.vers_patient(28, 12, 30)[np.newaxis]
    ligne, colonne = projeter_3d(cube, point, *angles)[0]
    attendu = centre_de_masse(image)
    assert ligne == pytest.approx(attendu[0], abs=2.0)
    assert colonne == pytest.approx(attendu[1], abs=2.0)


def test_la_transparence_laisse_voir_ce_qui_est_derriere():
    """Deux objets l'un devant l'autre : on doit voir plus que le premier."""
    volume = volume_cube(centre=(20, 20, 20))
    cube = preparer_3d(volume, taille=40)
    transparence = rendre_3d(cube, 0.0, 0.0, 0.0, 1000.0, "transparence")
    assert 0.0 < transparence.max() <= 1.0
    assert transparence[0, 0] == 0.0
    assert transparence[19, 19] > 0.1


def test_une_valeur_se_lit_en_un_point_du_patient():
    dose = construire_volume([dose_rt()])
    assert valeur_au_point(dose, dose.vers_patient(4, 3, 1)) == pytest.approx(200.304)
    entre = (dose.vers_patient(0, 0, 0) + dose.vers_patient(1, 0, 0)) / 2
    attendu = (dose.valeur(0, 0, 0, 0) + dose.valeur(0, 0, 0, 1)) / 2
    assert valeur_au_point(dose, entre) == pytest.approx(attendu)
    assert math.isnan(valeur_au_point(dose, np.array([900.0, 900.0, 900.0])))


def test_la_vue_3d_reste_rapide_sans_opengl():
    volume = volume_cube(taille=60, centre=(30, 30, 30), rayon=10)
    debut = time.perf_counter()
    cube = preparer_3d(volume, taille=96)
    preparation = time.perf_counter() - debut
    debut = time.perf_counter()
    rendre_3d(cube, 30.0, 45.0, 0.0, 1000.0, "transparence")
    rendu = time.perf_counter() - debut
    assert cube.donnees.shape == (96, 96, 96)
    assert preparation < 2.0
    assert rendu < 1.0


# --- Balises ------------------------------------------------------------------------------


def test_les_balises_affichees_n_identifient_personne_meme_pour_un_fantome():
    jeu = serie_ct(coupes=1)[0]
    balises = dict(balises_principales(jeu))
    assert balises["Modalité"] == "CT"
    assert float(balises["kV"]) == 120.0
    texte = " ".join(f"{k} {v}" for k, v in balises.items())
    for interdit in (
        "DUPONT",
        "IPP-123456",
        "19600215",
        "Lilas",
        "MARTIN",
        "Nulle-Part",
    ):
        assert interdit not in texte


def test_les_balises_de_dose_donnent_le_facteur():
    balises = dict(balises_principales(dose_rt(facteur=0.002)))
    assert balises["DoseGridScaling"] == "0.002"
    assert balises["Unité de dose"] == "GY"
    assert balises["Type de dose"] == "PHYSICAL"


def test_le_repere_commun_relie_une_dose_a_son_image():
    assert construire_volume([dose_rt()]).cadre_reference == REPERE
    assert construire_volume(serie_ct()).cadre_reference == REPERE
