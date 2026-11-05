"""Vérifie le visualiseur et l'onglet DICOM, dans de vrais widgets, avec le thème appliqué."""

import gc
import time
import weakref
from datetime import date as date_
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fabrique_dicom import (
    capture_ecran,
    dose_rt,
    ecrire,
    serie_4d_classique,
    serie_ct,
    structures_rt,
)
from pydicom.encaps import encapsulate
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMessageBox,
    QTableWidget,
)

from medphystools import Archive
from medphystools.depot import Depot
from medphystools.interface.espace_saisie import DescriptionPage, EspaceSaisie
from medphystools.interface.fiche_page import FichePage
from medphystools.interface.onglet_dicom import (
    DialogueSeries,
    EditeurDicom,
    PanneauSerie,
)
from medphystools.interface.onglets import types_disponibles
from medphystools.interface.theme import FEUILLE_DE_STYLE
from medphystools.interface.visualiseur import (
    VisualiseurVolume,
    VueCoupe,
    image_dose,
    image_grise,
)
from medphystools.page import Fichier, ReferenceDicom, vers_json
from medphystools.volumes import (
    construire_volume,
    coupe,
    lire_structures,
    volume_pour_structures,
)

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
INTERDITS = (
    "DUPONT",
    "IPP-123456",
    "19600215",
    "Lilas",
    "MARTIN",
    "DURAND",
    "Nulle-Part",
)


@pytest.fixture
def theme_applique(qapp):
    avant = qapp.styleSheet()
    qapp.setStyleSheet(FEUILLE_DE_STYLE)
    yield
    qapp.setStyleSheet(avant)


@pytest.fixture
def visu(qtbot, theme_applique):
    resultat = VisualiseurVolume()
    resultat.resize(1100, 800)
    qtbot.addWidget(resultat)
    resultat.show()
    qtbot.waitExposed(resultat)
    return resultat


@pytest.fixture
def scanner():
    return construire_volume(serie_ct(lignes=32, colonnes=32, coupes=8, melanger=False))


def textes_visibles(widget):
    """Tout ce qu'un utilisateur peut lire dans un widget."""
    morceaux = [label.text() for label in widget.findChildren(QLabel)]
    for table in widget.findChildren(QTableWidget):
        for i in range(table.rowCount()):
            for j in range(table.columnCount()):
                element = table.item(i, j)
                if element is not None:
                    morceaux.append(element.text())
    return " ".join(morceaux)


# --- Images ------------------------------------------------------------------------------


def test_la_fenetre_va_du_noir_au_blanc():
    image = image_grise(np.array([[0.0, 50.0, 100.0, 200.0]]), 0.0, 100.0)
    assert [image.pixelColor(k, 0).red() for k in range(4)] == [0, 128, 255, 255]


def test_monochrome1_inverse_l_affichage():
    image = image_grise(np.array([[0.0, 100.0]]), 0.0, 100.0, inverser=True)
    assert [image.pixelColor(k, 0).red() for k in range(2)] == [255, 0]


def test_la_dose_est_transparente_sous_le_seuil_et_hors_de_la_grille():
    image = image_dose(np.array([[1.0, 5.0, 10.0, np.nan]]), 5.0, 10.0, 0.5)
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(1, 0).alpha() == 128
    assert image.pixelColor(2, 0).alpha() == 128
    assert image.pixelColor(3, 0).alpha() == 0


def test_la_dose_la_plus_forte_est_la_plus_claire():
    image = image_dose(np.array([[0.0, 5.0, 10.0]]), 0.0, 10.0, 1.0)
    clartes = [image.pixelColor(k, 0).lightnessF() for k in range(3)]
    assert clartes[0] < clartes[1] < clartes[2]


# --- Géométrie à l'écran -----------------------------------------------------------------


def test_les_proportions_de_l_image_sont_celles_du_patient(visu):
    """Pixels de 1 × 2 mm et coupes de 5 mm : l'écran doit le respecter, pas la grille."""
    volume = construire_volume(serie_ct(melanger=False))
    visu.afficher(volume)
    axiale = visu.vues["axial"].zone_image()
    # 8 lignes de 1 mm et 10 colonnes de 2 mm.
    assert axiale.width() / axiale.height() == pytest.approx(20 / 8, rel=0.01)
    sagittale = visu.vues["sagittal"].zone_image()
    # 4 coupes de 5 mm de haut et 8 lignes de 1 mm de large.
    assert sagittale.width() / sagittale.height() == pytest.approx(8 / 20, rel=0.01)


def test_ecran_et_pixel_se_correspondent(visu, scanner):
    visu.afficher(scanner)
    vue = visu.vues["axial"]
    for ligne, colonne in ((0, 0), (31, 31), (5, 20)):
        assert vue.pixel_de_l_ecran(vue.ecran_du_pixel(ligne, colonne)) == (
            ligne,
            colonne,
        )


def test_hors_de_l_image_il_n_y_a_pas_de_pixel(visu):
    visu.afficher(construire_volume(serie_ct(melanger=False)))
    vue = visu.vues["sagittal"]
    assert vue.pixel_de_l_ecran(QPointF(0.0, 0.0)) is None


def test_les_vues_ont_vraiment_de_la_place_dans_la_fenetre(visu, scanner):
    visu.afficher(scanner)
    for vue in (*visu.vues.values(), visu.vue_3d):
        assert vue.isVisible()
        assert vue.width() >= 250
        assert vue.height() >= 200


# --- Navigation --------------------------------------------------------------------------


def test_la_molette_fait_defiler_les_coupes_et_s_arrete_aux_bords(visu, scanner, qtbot):
    visu.afficher(scanner)
    vue = visu.vues["axial"]
    assert vue.indice == 4
    vue.defilement.emit(1)
    assert vue.indice == 5
    assert visu.panneaux["axial"].curseur.value() == 5
    assert "6/8" in visu.panneaux["axial"].titre.text()
    for _ in range(20):
        vue.defilement.emit(1)
    assert vue.indice == 7
    for _ in range(20):
        vue.defilement.emit(-1)
    assert vue.indice == 0


def test_le_curseur_fait_defiler_et_change_l_image_affichee(visu, scanner):
    visu.afficher(scanner)
    visu.panneaux["axial"].curseur.setValue(2)
    vue = visu.vues["axial"]
    assert vue.indice == 2
    assert vue.valeurs_affichees() == pytest.approx(coupe(scanner, "axial", 2))


def test_un_clic_dans_une_vue_deplace_les_deux_autres(visu, scanner, qtbot):
    """On clique à l'écran : la croix doit désigner ce voxel dans les trois coupes."""
    visu.afficher(scanner)
    axiale = visu.vues["axial"]
    point = axiale.ecran_du_pixel(10, 20).toPoint()
    QTest.mouseClick(axiale, Qt.MouseButton.LeftButton, pos=point)
    # Ligne 10, colonne 20 : y = 10, x = 20, soit les coupes coronale 10 et sagittale 20.
    assert visu.voxel[1:] == [10, 20]
    assert visu.vues["coronal"].indice == 10
    assert visu.vues["sagittal"].indice == 20
    assert visu.vues["sagittal"].croix == tuple(visu.voxel)


def test_la_souris_lit_la_valeur_physique_et_son_unite(visu, qtbot):
    """Stockée 3057, pente 2, ordonnée −1024 : on doit lire 5090 HU, et jamais 3057."""
    volume = construire_volume(serie_ct(melanger=False))
    visu.afficher(volume)
    visu.aller_a_la_coupe("axial", 3)
    vue = visu.vues["axial"]
    QTest.mouseMove(vue, vue.ecran_du_pixel(5, 7).toPoint())
    qtbot.waitUntil(lambda: "5090 HU" in visu.lecture.text())
    assert "3057" not in visu.lecture.text()
    # x = −10 + 7 × 2, y = −20 + 5 × 1, z = 100 + 3 × 5.
    assert "x = 4.0, y = -15.0, z = 115.0 mm" in visu.lecture.text()


def test_la_molette_reelle_passe_par_l_evenement(visu, scanner, qtbot):
    from PySide6.QtGui import QWheelEvent

    visu.afficher(scanner)
    vue = visu.vues["axial"]
    evenement = QWheelEvent(
        QPointF(50, 50),
        QPointF(vue.mapToGlobal(QPoint(50, 50))),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )
    QApplication.sendEvent(vue, evenement)
    assert vue.indice == 5


# --- Fenêtrage ---------------------------------------------------------------------------


def test_un_scanner_s_ouvre_sur_une_fenetre_de_tissus_mous(visu, scanner):
    """Sur toute sa dynamique, un scanner serait saturé et sa vue 3D toute blanche."""
    visu.afficher(scanner)
    assert visu.choix_fenetre.currentText() == "Tissus mous"
    assert (visu.vues["axial"].bas, visu.vues["axial"].haut) == (-160.0, 240.0)


def test_les_reperes_restent_lisibles_sur_une_image_toute_blanche(visu):
    """Une image claire : la lettre doit quand même se détacher, en sombre et en clair."""
    jeux = serie_ct(
        coupes=8, lignes=64, colonnes=64, pente=0.0, ordonnee=3000.0, melanger=False
    )
    visu.afficher(construire_volume(jeux))
    QApplication.processEvents()
    vue = visu.vues["axial"]
    image = vue.grab().toImage()
    zone = vue.zone_image()
    # La lettre du haut, en haut au milieu de l'image.
    centre = round(zone.center().x())
    haut = round(zone.top())
    clartes = [
        image.pixelColor(x, y).lightness()
        for x in range(centre - 8, centre + 8)
        for y in range(haut + 2, haut + 20)
    ]
    assert min(clartes) < 60


def test_le_clic_droit_tire_change_la_fenetre_et_jamais_les_donnees(
    visu, scanner, qtbot
):
    visu.afficher(scanner)
    avant = visu.vues["axial"].haut - visu.vues["axial"].bas
    copie = scanner.donnees.copy()
    vue = visu.vues["axial"]
    vue.fenetre_tiree.emit(50.0, 0.0)
    assert visu.vues["coronal"].haut - visu.vues["coronal"].bas > avant
    assert visu.choix_fenetre.currentText() == "Personnalisée"
    assert np.array_equal(scanner.donnees, copie)


def test_les_niveaux_du_scanner_sont_proposes_en_hu(visu, scanner):
    visu.afficher(scanner)
    choix = [visu.choix_fenetre.itemText(k) for k in range(visu.choix_fenetre.count())]
    assert "Os" in choix
    assert "Poumon" in choix
    visu.choix_fenetre.setCurrentText("Os")
    visu._fenetre_choisie(0)
    assert (visu.champ_bas.value(), visu.champ_haut.value()) == (-450.0, 1050.0)
    assert visu.vues["axial"].bas == -450.0


def test_une_dose_ne_propose_pas_de_niveaux_de_scanner(visu):
    visu.afficher(construire_volume([dose_rt()]))
    choix = [visu.choix_fenetre.itemText(k) for k in range(visu.choix_fenetre.count())]
    assert "Os" not in choix


def test_une_dose_garde_une_fenetre_automatique(visu):
    visu.afficher(construire_volume([dose_rt()]))
    assert visu.choix_fenetre.currentText() == "Automatique"


def test_une_fenetre_saisie_s_applique(visu, scanner):
    visu.afficher(scanner)
    visu.champ_bas.setValue(10.0)
    visu.champ_haut.setValue(20.0)
    assert visu.vues["sagittal"].bas == 10.0
    assert visu.vues["sagittal"].haut == 20.0


def test_une_fenetre_inversee_est_ignoree(visu, scanner):
    visu.afficher(scanner)
    visu.definir_fenetre(100.0, -100.0)
    assert visu.vues["axial"].bas < visu.vues["axial"].haut


# --- 4D ----------------------------------------------------------------------------------


def test_le_curseur_de_temps_n_apparait_qu_en_4d(visu, scanner):
    visu.afficher(scanner)
    assert not visu.zone_temps.isVisible()
    visu.afficher(construire_volume(serie_4d_classique(phases=3, coupes=4)))
    assert visu.zone_temps.isVisible()
    assert visu.libelle_temps.text() == "Phase 1/3"


def test_changer_de_phase_change_les_valeurs_lues(visu, qtbot):
    volume = construire_volume(serie_4d_classique(phases=3, coupes=4))
    visu.afficher(volume)
    visu.curseur_temps.setValue(2)
    assert visu.libelle_temps.text() == "Phase 3/3"
    vue = visu.vues["axial"]
    assert vue.t == 2
    assert vue.valeurs_affichees() == pytest.approx(volume.donnees[2, vue.indice])
    assert visu.lecture.text().startswith(f"{200 + vue.indice}")


# --- Dose --------------------------------------------------------------------------------


def test_la_dose_se_superpose_et_se_lit_en_gray(visu, scanner, qtbot):
    dose = construire_volume([dose_rt()])
    visu.afficher(scanner, dose=dose)
    assert visu.zone_dose.isVisible()
    vue = visu.vues["axial"]
    assert vue.dose is dose
    # Le centre du scanner est au-delà de la grille de dose : cela doit se dire.
    assert "dose : hors de la grille" in visu.lecture.text()
    # Voxel (z=1, y=0, x=0) du scanner : z = 105 mm, soit le voxel (1, 0, 0) de la dose.
    visu.aller_au_voxel((1, 0, 0))
    assert "dose : 200 Gy" in visu.lecture.text()
    assert "Gy" in visu.echelle_dose.text()
    assert vue.dose_affichee() is not None


def test_la_dose_change_vraiment_les_pixels_affiches(visu, scanner, qtbot):
    dose = construire_volume([dose_rt()])
    visu.afficher(scanner, dose=dose)
    vue = visu.vues["axial"]
    visu.aller_a_la_coupe("axial", 0)
    QApplication.processEvents()
    avec = vue.grab().toImage()
    visu.case_dose.setChecked(False)
    QApplication.processEvents()
    sans = vue.grab().toImage()
    point = vue.ecran_du_pixel(1, 1).toPoint()
    assert avec.pixelColor(point) != sans.pixelColor(point)


def test_une_dose_d_un_autre_repere_n_est_pas_superposee(visu, scanner):
    jeu = dose_rt()
    jeu.FrameOfReferenceUID = "1.2.3.4"
    visu.afficher(scanner, dose=construire_volume([jeu]))
    assert visu.dose is None
    assert not visu.zone_dose.isVisible()
    assert "repère" in visu.remarques.text()
    assert "dose :" not in visu.lecture.text()


# --- Structures --------------------------------------------------------------------------


def pixels_de_couleur(image, couleur):
    return sum(
        image.pixelColor(x, y).name() == couleur
        for x in range(image.width())
        for y in range(image.height())
    )


def test_les_structures_se_listent_et_se_dessinent_a_l_ecran(visu):
    for_spacing = serie_ct(
        coupes=4, origine=(-20.0, -20.0, 100.0), melanger=False, lignes=41, colonnes=41
    )
    for jeu in for_spacing:
        jeu.PixelSpacing = [1.0, 1.0]
    volume = construire_volume(for_spacing)
    structures = lire_structures(structures_rt())
    visu.afficher(volume, structures)
    assert visu.liste_structures.count() == 2
    assert visu.liste_structures.item(0).text() == "PTV"
    assert visu.liste_structures.isVisible()
    visu.aller_a_la_coupe("axial", 1)
    QApplication.processEvents()
    vue = visu.vues["axial"]
    assert pixels_de_couleur(vue.grab().toImage(), "#ff0000") > 20
    assert pixels_de_couleur(vue.grab().toImage(), "#00ff00") > 5


def test_decocher_une_structure_la_retire_de_l_ecran(visu):
    for_spacing = serie_ct(
        coupes=4, origine=(-20.0, -20.0, 100.0), melanger=False, lignes=41, colonnes=41
    )
    for jeu in for_spacing:
        jeu.PixelSpacing = [1.0, 1.0]
    visu.afficher(construire_volume(for_spacing), lire_structures(structures_rt()))
    visu.aller_a_la_coupe("axial", 1)
    visu.liste_structures.item(0).setCheckState(Qt.CheckState.Unchecked)
    QApplication.processEvents()
    image = visu.vues["axial"].grab().toImage()
    assert pixels_de_couleur(image, "#ff0000") == 0
    assert pixels_de_couleur(image, "#00ff00") > 5


def test_un_rtstruct_seul_s_affiche_sur_un_fond_vide(visu):
    structures = lire_structures(structures_rt())
    visu.afficher(volume_pour_structures(structures), structures)
    assert "seules" in visu.remarques.text()
    assert visu.liste_structures.count() == 2


# --- Vue 3D ------------------------------------------------------------------------------


def test_la_vue_3d_se_calcule_sans_figer_et_tourne_a_la_souris(visu, scanner, qtbot):
    visu.afficher(scanner)
    vue = visu.vue_3d
    qtbot.waitUntil(lambda: vue.image is not None, timeout=10000)
    premiere = vue.image.copy()
    QTest.mousePress(vue, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    # QTest.mouseMove ne dit pas qu'un bouton est enfoncé : l'événement est donc écrit à la main.
    point = QPointF(250, 100)
    QApplication.sendEvent(
        vue,
        QMouseEvent(
            QEvent.Type.MouseMove,
            point,
            QPointF(vue.mapToGlobal(point)),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        ),
    )
    QTest.mouseRelease(vue, Qt.MouseButton.LeftButton, pos=QPoint(250, 100))
    assert vue.azimut == pytest.approx(90.0)
    qtbot.waitUntil(lambda: vue._rendu_angles[0] == pytest.approx(90.0), timeout=10000)
    assert not np.array_equal(premiere, vue.image)


def test_la_vue_3d_ne_garde_que_le_dernier_etat_demande(visu, scanner, qtbot):
    visu.afficher(scanner)
    vue = visu.vue_3d
    for angle in range(0, 180, 10):
        vue.azimut = float(angle)
        vue.demander_rendu()
    qtbot.waitUntil(lambda: vue._rendu_angles[0] == 170.0, timeout=20000)
    qtbot.waitUntil(lambda: not vue._occupe, timeout=20000)


def test_changer_de_volume_oublie_l_ancienne_vue_3d(visu, scanner, qtbot):
    visu.afficher(scanner)
    qtbot.waitUntil(lambda: visu.vue_3d.image is not None, timeout=10000)
    autre = construire_volume(
        serie_ct(lignes=16, colonnes=16, coupes=4, melanger=False)
    )
    visu.afficher(autre)
    assert visu.vue_3d.volume is autre
    qtbot.waitUntil(lambda: visu.vue_3d._cube_cle == (id(autre), 0), timeout=10000)


def test_le_visualiseur_n_utilise_pas_opengl():
    import sys

    assert not [m for m in sys.modules if "QtOpenGL" in m]


# --- Formes particulières ----------------------------------------------------------------


def test_une_image_plane_n_a_qu_une_vue(visu):
    visu.afficher(construire_volume([capture_ecran()]))
    assert visu.panneaux["axial"].isVisible()
    assert not visu.panneaux["sagittal"].isVisible()
    assert not visu.panneau_3d.isVisible()
    assert visu.panneaux["axial"].titre.text() == "Image"


def test_le_double_clic_agrandit_puis_rend_la_place(visu, scanner, qtbot):
    visu.afficher(scanner)
    vue = visu.vues["coronal"]
    largeur = vue.width()
    QTest.mouseDClick(vue, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    qtbot.waitUntil(lambda: not visu.panneaux["axial"].isVisible())
    qtbot.waitUntil(lambda: vue.width() > largeur * 1.5)
    QTest.mouseDClick(vue, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    qtbot.waitUntil(lambda: visu.panneaux["axial"].isVisible())


def test_vider_efface_tout(visu, scanner):
    visu.afficher(
        scanner, lire_structures(structures_rt()), balises=[("Modalité", "CT")]
    )
    visu.vider()
    assert visu.volume is None
    assert visu.table_balises.rowCount() == 0
    assert visu.liste_structures.count() == 0
    assert visu.vues["axial"].volume is None
    visu.grab()


def test_le_visualiseur_ne_connait_ni_les_pages_ni_les_archives():
    """C'est ce qui le rend réutilisable pour le programme de CQ."""
    import inspect

    from medphystools.interface import visualiseur

    source = inspect.getsource(visualiseur)
    assert "medphystools.archive" not in source
    assert "medphystools.page" not in source


def test_la_vue_seule_s_affiche_sans_visualiseur(qtbot, scanner):
    vue = VueCoupe("axial")
    qtbot.addWidget(vue)
    vue.resize(200, 200)
    vue.definir(scanner)
    vue.haut = 5000.0
    assert not vue.grab().isNull()


# --- Onglet DICOM ------------------------------------------------------------------------


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date_(2026, 11, 1))


@pytest.fixture
def espace(qtbot, arc, monkeypatch, theme_applique):
    resultat = EspaceSaisie()
    resultat.resize(1300, 900)
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    monkeypatch.setattr(resultat, "_confirmer", lambda *_: True)
    assert resultat.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    resultat.show()
    qtbot.waitExposed(resultat)
    return resultat


@pytest.fixture
def editeur(espace):
    resultat = espace.ajouter_onglet("dicom")
    espace.onglets.setCurrentWidget(resultat)
    return resultat


def importer(qtbot, editeur, chemins):
    editeur.importer(chemins)
    qtbot.waitUntil(lambda: not editeur.import_en_cours, timeout=20000)


def montree(qtbot, editeur, rang=0):
    editeur.table.selectRow(rang)
    qtbot.waitUntil(lambda: not editeur.panneau.en_cours, timeout=20000)
    return editeur.panneau.visualiseur


def relue(espace):
    espace.enregistrer_maintenant()
    return espace.archive.lire_page(espace.page.id)


def test_l_onglet_dicom_est_propose():
    assert "dicom" in [d.nom_type for d in types_disponibles()]


def test_l_onglet_s_ouvre_avec_ses_commandes_visibles(espace, editeur):
    espace.onglets.setCurrentWidget(editeur)
    assert isinstance(editeur, EditeurDicom)
    assert editeur.bouton_fichiers.isVisible()
    assert editeur.choix_mode.currentData() == "pseudonymise"
    assert "n'est pas détecté" in editeur.limite.text()
    assert editeur.limite.isVisible()


def test_importer_pseudonymise_par_defaut_et_enregistre_dans_la_page(
    qtbot, espace, editeur, tmp_path
):
    chemins = ecrire(serie_ct(), tmp_path / "DUPONT")
    importer(qtbot, editeur, chemins)
    assert editeur.table.rowCount() == 1
    assert editeur.table.item(0, 3).text() == "Pseudonymisé"
    page = relue(espace)
    fichier = page.grandeur("dicom_CT")
    assert fichier.dicom.mode == "pseudonymise"
    assert page.onglets[-1].grandeurs == ("dicom_CT",)
    assert "DUPONT" not in vers_json(page)
    for contenu in Depot(espace.archive.racine).fichiers_suivis().values():
        assert b"DUPONT" not in contenu
    assert espace.erreurs == []


def test_les_balises_identifiantes_ne_s_affichent_jamais(
    qtbot, espace, editeur, tmp_path
):
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    visu = montree(qtbot, editeur)
    assert visu.volume is not None
    texte = textes_visibles(editeur)
    assert "Fabricant fictif" in texte
    for interdit in INTERDITS:
        assert interdit not in texte


def test_un_fantome_tel_quel_n_affiche_pas_non_plus_l_identite(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    espace.onglets.setCurrentWidget(editeur)
    monkeypatch.setattr(editeur, "_confirmer_fantome", lambda _b: True)
    editeur.choix_mode.setCurrentIndex(1)
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    montree(qtbot, editeur)
    texte = " ".join(
        editeur.panneau.visualiseur.table_balises.item(i, 1).text()
        for i in range(editeur.panneau.visualiseur.table_balises.rowCount())
    )
    for interdit in INTERDITS:
        assert interdit not in texte


def test_un_fantome_suspect_demande_confirmation_et_peut_etre_annule(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    vues = []
    monkeypatch.setattr(
        editeur, "_confirmer_fantome", lambda balises: vues.append(balises) or False
    )
    editeur.choix_mode.setCurrentIndex(1)
    objets = sorted((espace.archive.racine / "objets").rglob("*"))
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    assert "nom du patient" in vues[0]
    assert editeur.table.rowCount() == 0
    assert "annulé" in editeur.message.text()
    assert sorted((espace.archive.racine / "objets").rglob("*")) == objets
    assert editeur.bouton_fichiers.isEnabled()


def test_un_fantome_confirme_reste_tel_quel_et_avertit(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    espace.onglets.setCurrentWidget(editeur)
    monkeypatch.setattr(editeur, "_confirmer_fantome", lambda _b: True)
    editeur.choix_mode.setCurrentIndex(1)
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    assert editeur.table.item(0, 3).text() == "Fantôme, tel quel"
    assert "ressemblent à un patient" in editeur.message.text()
    assert editeur.message.isVisible()
    assert relue(espace).grandeur("dicom_CT").dicom.mode == "fantome"


def test_la_confirmation_propose_non_par_defaut(editeur, monkeypatch):
    vus = {}

    def question(_parent, _titre, _texte, _boutons, defaut):
        vus["defaut"] = defaut
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "warning", question)
    assert not editeur._confirmer_fantome(["nom du patient"])
    assert vus["defaut"] == QMessageBox.StandardButton.No


def test_une_reference_ne_garde_que_les_uid_et_s_explique(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    espace.onglets.setCurrentWidget(editeur)
    monkeypatch.setattr(editeur, "_demander_noeud", lambda: "PACS_CHU")
    editeur.choix_mode.setCurrentIndex(2)
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    fichier = relue(espace).grandeur("reference_CT")
    assert isinstance(fichier.stockage, ReferenceDicom)
    assert fichier.stockage.noeud == "PACS_CHU"
    montree(qtbot, editeur)
    assert "PACS" in editeur.panneau.etat.text()
    assert editeur.panneau.etat.isVisible()


def test_annuler_le_noeud_n_importe_rien(qtbot, editeur, tmp_path, monkeypatch):
    monkeypatch.setattr(editeur, "_demander_noeud", lambda: None)
    editeur.choix_mode.setCurrentIndex(2)
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    assert editeur.table.rowCount() == 0


def test_un_fichier_non_pris_en_charge_s_explique_sans_planter(
    qtbot, espace, editeur, tmp_path
):
    espace.onglets.setCurrentWidget(editeur)
    source = tmp_path / "DUPONT.txt"
    source.write_text("pas un dicom", encoding="utf-8")
    importer(qtbot, editeur, [source])
    assert "Aucun fichier DICOM" in editeur.message.text()
    assert "DUPONT" not in editeur.message.text()
    assert editeur.table.rowCount() == 0
    assert editeur.bouton_fichiers.isEnabled()


def test_les_fichiers_ignores_sont_comptes(qtbot, espace, editeur, tmp_path):
    espace.onglets.setCurrentWidget(editeur)
    ecrire(serie_ct(coupes=2), tmp_path / "src")
    (tmp_path / "src" / "notes.txt").write_text("x", encoding="utf-8")
    importer(qtbot, editeur, [tmp_path / "src"])
    assert "1 fichier(s) ignoré(s)" in editeur.message.text()
    assert editeur.table.rowCount() == 1


def test_une_serie_sans_image_s_explique_sans_planter(qtbot, espace, editeur, tmp_path):
    """Un objet DICOM sans pixels ni structures : rien à montrer, mais pas de plantage."""
    jeu = structures_rt()
    jeu.Modality = "RTPLAN"
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire(jeu, tmp_path / "src"))
    montree(qtbot, editeur)
    assert "pas d'image" in editeur.panneau.etat.text()
    assert editeur.panneau.visualiseur.volume is None


def test_un_decodage_impossible_s_explique_sans_planter(
    qtbot, espace, editeur, tmp_path
):
    jeux = serie_ct(coupes=1)
    jeux[0].file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.4.100"
    jeux[0].PixelData = encapsulate([b"\x01\x02\x03\x04"])
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire(jeux, tmp_path / "src"))
    montree(qtbot, editeur)
    assert "impossible à décoder" in editeur.panneau.etat.text()


def test_choisir_une_serie_l_affiche_avec_ses_valeurs_physiques(
    qtbot, espace, editeur, tmp_path
):
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire(serie_ct(melanger=False), tmp_path / "src"))
    visu = montree(qtbot, editeur)
    assert visu.volume.unite == "HU"
    assert visu.volume.valeur(0, 3, 5, 7) == 5090.0
    assert "HU" in visu.lecture.text()


def test_une_dose_s_affiche_sur_l_image_de_meme_repere(
    qtbot, espace, editeur, tmp_path
):
    espace.onglets.setCurrentWidget(editeur)
    importer(
        qtbot, editeur, ecrire(serie_ct(melanger=False) + [dose_rt()], tmp_path / "src")
    )
    rang_dose = next(
        i
        for i in range(editeur.table.rowCount())
        if editeur.table.item(i, 1).text() == "RTDOSE"
    )
    visu = montree(qtbot, editeur, rang_dose)
    assert visu.volume.modalite == "CT"
    assert visu.dose is not None
    assert visu.dose.valeur(0, 1, 3, 4) == pytest.approx(200.304)
    assert visu.dose.unite == "Gy"
    assert "même repère" in visu.remarques.text()
    assert "DoseGridScaling" in textes_visibles(editeur)


def test_une_dose_seule_s_affiche_en_valeurs_de_dose(qtbot, espace, editeur, tmp_path):
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire(dose_rt(), tmp_path / "src"))
    visu = montree(qtbot, editeur)
    assert visu.volume.modalite == "RTDOSE"
    assert "Gy" in visu.lecture.text()


def test_des_structures_s_affichent_sur_l_image_de_meme_repere(
    qtbot, espace, editeur, tmp_path
):
    espace.onglets.setCurrentWidget(editeur)
    importer(
        qtbot,
        editeur,
        ecrire(serie_ct(melanger=False) + [structures_rt()], tmp_path / "src"),
    )
    rang = next(
        i
        for i in range(editeur.table.rowCount())
        if editeur.table.item(i, 1).text() == "RTSTRUCT"
    )
    visu = montree(qtbot, editeur, rang)
    assert visu.volume.modalite == "CT"
    assert [s.nom for s in visu.structures] == ["PTV", "Coeur"]


def test_un_rtstruct_seul_s_affiche_dans_l_onglet(qtbot, espace, editeur, tmp_path):
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire(structures_rt(), tmp_path / "src"))
    visu = montree(qtbot, editeur)
    assert visu.volume.modalite == "RTSTRUCT"
    assert visu.liste_structures.count() == 2


def test_les_series_se_retrouvent_a_la_reouverture(qtbot, espace, editeur, tmp_path):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    relue(espace)
    assert espace.ouvrir_page(espace.page.id)
    rouvert = next(e for e in espace.editeurs() if isinstance(e, EditeurDicom))
    assert rouvert.table.rowCount() == 1
    assert rouvert.table.item(0, 3).text() == "Pseudonymisé"
    qtbot.waitUntil(lambda: rouvert.table.item(0, 4).text() == "intact", timeout=10000)


def test_un_nom_et_un_commentaire_se_modifient(qtbot, espace, editeur, tmp_path):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    editeur.table.item(0, 0).setText("scanner_cq")
    editeur.table.item(0, 6).setText("Catphan")
    fichier = relue(espace).grandeur("scanner_cq")
    assert fichier.commentaire == "Catphan"
    assert fichier.dicom.modalite == "CT"


def test_un_nom_invalide_est_signale(qtbot, editeur, tmp_path):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    editeur.table.item(0, 0).setText("nom invalide")
    assert "invalide" in editeur.erreurs()[0]


def test_retirer_une_serie_ne_l_efface_pas_du_magasin(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    fichier = relue(espace).grandeur("dicom_CT")
    chemin = espace.archive.emplacement_fichier(fichier)
    monkeypatch.setattr(editeur, "_confirmer_retrait", lambda _n: True)
    editeur.table.selectRow(0)
    editeur.demander_retrait()
    page = relue(espace)
    assert not [g for g in page.mesures if isinstance(g, Fichier)]
    assert chemin.is_file()
    assert editeur.panneau.visualiseur.volume is None


def test_la_verification_n_interrompt_pas_une_saisie_de_commentaire(
    qtbot, espace, editeur, tmp_path
):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    commentaire = editeur.table.item(0, 6)
    editeur._empreinte_calculee("dicom_CT", "altéré")
    assert editeur.table.item(0, 6) is commentaire
    assert editeur.table.item(0, 4).text() == "altéré"
    assert editeur.liens[0].etat == "altéré"


def test_trois_series_se_voient_dans_la_liste(qtbot, espace, editeur, tmp_path):
    espace.onglets.setCurrentWidget(editeur)
    importer(
        qtbot,
        editeur,
        ecrire(serie_ct() + [dose_rt(), structures_rt()], tmp_path / "src"),
    )
    table = editeur.table
    utile = table.viewport().height()
    assert sum(table.rowHeight(k) for k in range(3)) <= utile


def test_afficher_en_grand_ouvre_la_serie_choisie(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire(serie_ct() + [dose_rt()], tmp_path / "src"))
    editeur.table.selectRow(1)
    vus = []

    def exec_(dialogue):
        vus.append(dialogue)
        dialogue.show()
        qtbot.waitUntil(lambda: not dialogue.panneau.en_cours, timeout=20000)
        assert dialogue.panneau.visualiseur.volume is not None
        return 0

    monkeypatch.setattr(DialogueSeries, "exec", exec_)
    editeur.afficher_en_grand()
    dialogue = vus[0]
    qtbot.addWidget(dialogue)
    assert dialogue.liste.currentRow() == 1


def test_afficher_en_grand_sans_serie_s_explique(espace, editeur):
    editeur.afficher_en_grand()
    assert "Aucune série" in editeur.message.text()


def test_un_fichier_altere_est_signale(qtbot, espace, editeur, tmp_path):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    fichier = relue(espace).grandeur("dicom_CT")
    chemin = espace.archive.emplacement_fichier(fichier)
    chemin.chmod(0o644)
    with open(chemin, "ab") as sortie:
        sortie.write(b"x")
    assert espace.ouvrir_page(espace.page.id)
    rouvert = next(e for e in espace.editeurs() if isinstance(e, EditeurDicom))
    qtbot.waitUntil(lambda: rouvert.table.item(0, 4).text() == "altéré", timeout=10000)


def test_une_grosse_serie_ne_bloque_pas_l_interface(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    """La lecture dure 1,5 s : l'horloge de l'interface doit continuer à battre pendant."""
    importer(qtbot, editeur, ecrire(serie_ct() + serie_ct(), tmp_path / "src"))
    qtbot.waitUntil(lambda: not editeur.panneau.en_cours, timeout=20000)
    import medphystools.volumes as volumes

    vraie = volumes.assembler_contenu

    def lente(*arguments):
        time.sleep(1.5)
        return vraie(*arguments)

    monkeypatch.setattr(volumes, "assembler_contenu", lente)
    battements = []
    minuteur = QTimer()
    minuteur.timeout.connect(lambda: battements.append(time.perf_counter()))
    minuteur.start(20)
    debut = time.perf_counter()
    editeur.table.selectRow(1)
    assert time.perf_counter() - debut < 0.5
    assert editeur.panneau.en_cours
    assert "Lecture" in editeur.panneau.etat.text()
    qtbot.waitUntil(lambda: not editeur.panneau.en_cours, timeout=20000)
    minuteur.stop()
    assert len(battements) > 20
    assert max(np.diff(battements)) < 0.5


def test_un_gros_import_ne_bloque_pas_l_interface(
    qtbot, espace, editeur, tmp_path, monkeypatch
):
    import medphystools.dicom as module

    vraie = module.importer_examen

    def lente(*arguments, **options):
        time.sleep(1.0)
        return vraie(*arguments, **options)

    monkeypatch.setattr(module, "importer_examen", lente)
    chemins = ecrire(serie_ct(), tmp_path / "src")
    battements = []
    minuteur = QTimer()
    minuteur.timeout.connect(lambda: battements.append(time.perf_counter()))
    minuteur.start(20)
    editeur.importer(chemins)
    assert editeur.import_en_cours
    assert not editeur.bouton_fichiers.isEnabled()
    qtbot.waitUntil(lambda: not editeur.import_en_cours, timeout=20000)
    minuteur.stop()
    assert len(battements) > 15
    assert max(np.diff(battements)) < 0.5
    assert editeur.table.rowCount() == 1


def test_changer_vite_de_serie_n_affiche_que_la_derniere(
    qtbot, espace, editeur, tmp_path
):
    """Une dose et une capture, sans repère commun : on voit laquelle est affichée."""
    espace.onglets.setCurrentWidget(editeur)
    importer(qtbot, editeur, ecrire([dose_rt(), capture_ecran()], tmp_path / "s"))
    modalites = [lien.fichier.dicom.modalite for lien in editeur.liens]
    editeur.table.selectRow(0)
    editeur.table.selectRow(1)
    qtbot.waitUntil(lambda: not editeur.panneau.en_cours, timeout=20000)
    QTest.qWait(300)
    assert editeur.panneau.visualiseur.volume.modalite == modalites[1]


def test_fermer_pendant_une_lecture_ne_plante_pas(qtbot, arc, tmp_path, monkeypatch):
    import medphystools.volumes as volumes

    vraie = volumes.assembler_contenu

    def lente(*arguments):
        time.sleep(0.3)
        return vraie(*arguments)

    monkeypatch.setattr(volumes, "assembler_contenu", lente)
    fichier = _importer_une_serie_de_test(arc, tmp_path)
    panneau = PanneauSerie()
    panneau.show()
    panneau.montrer(arc, fichier, [fichier])
    panneau.deleteLater()
    QApplication.processEvents()
    QTest.qWait(600)


def _importer_une_serie_de_test(arc, tmp_path):
    from medphystools.dicom import importer_series

    return importer_series(arc, ecrire(serie_ct(), tmp_path / "s")).fichiers[0]


def test_ouvrir_un_onglet_de_notes_ne_charge_pas_pydicom():
    """Les modules DICOM attendent que l'on ouvre un onglet DICOM."""
    import subprocess
    import sys

    code = (
        "import sys; "
        "from medphystools.interface import espace_saisie; "
        "assert not [m for m in sys.modules if m.startswith('pydicom')]; "
        "assert not [m for m in sys.modules if m.startswith('scipy.ndimage')]"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=120)


# --- Mémoire ------------------------------------------------------------------------------


def serie_en_memoire(panneau):
    """Une référence faible : elle ne retient pas le volume qu'elle observe."""
    return weakref.ref(panneau.visualiseur.volume.donnees)


def attendre_la_lecture(qtbot, panneau):
    qtbot.waitUntil(lambda: not panneau.en_cours, timeout=20000)
    return panneau.visualiseur.volume


@pytest.fixture
def deux_series(qtbot, editeur, tmp_path):
    importer(qtbot, editeur, ecrire(serie_ct() + serie_ct(), tmp_path / "src"))
    attendre_la_lecture(qtbot, editeur.panneau)
    return editeur


def test_changer_de_serie_libere_vraiment_l_ancienne(qtbot, deux_series):
    panneau = deux_series.panneau
    # La vue 3D garde le volume le temps de son calcul.
    qtbot.waitUntil(lambda: not panneau.visualiseur.vue_3d._occupe, timeout=20000)
    ancienne = serie_en_memoire(panneau)
    deux_series.table.selectRow(1)
    attendre_la_lecture(qtbot, panneau)
    qtbot.waitUntil(lambda: not panneau.visualiseur.vue_3d._occupe, timeout=20000)
    gc.collect()
    QApplication.processEvents()
    assert ancienne() is None
    assert panneau.visualiseur.volume is not None


def test_un_onglet_cache_ne_lit_pas_sa_serie(qtbot, espace, deux_series, monkeypatch):
    espace.onglets.setCurrentIndex(0)
    deux_series.table.selectRow(1)
    assert not deux_series.panneau.en_cours
    assert deux_series.panneau.visualiseur.volume is None
    espace.onglets.setCurrentWidget(deux_series)
    assert attendre_la_lecture(qtbot, deux_series.panneau) is not None


def test_un_onglet_cache_rend_sa_memoire_apres_le_delai(
    qtbot, espace, deux_series, monkeypatch
):
    monkeypatch.setattr("medphystools.interface.onglet_dicom.DELAI_LIBERATION_MS", 50)
    panneau = deux_series.panneau
    espace.onglets.setCurrentIndex(0)
    assert panneau.visualiseur.volume is not None
    qtbot.waitUntil(lambda: panneau.visualiseur.volume is None, timeout=5000)
    espace.onglets.setCurrentWidget(deux_series)
    assert attendre_la_lecture(qtbot, panneau) is not None


def test_revenir_vite_sur_l_onglet_evite_une_relecture(qtbot, espace, deux_series):
    panneau = deux_series.panneau
    volume = panneau.visualiseur.volume
    espace.onglets.setCurrentIndex(0)
    espace.onglets.setCurrentWidget(deux_series)
    assert not panneau.en_cours
    assert panneau.visualiseur.volume is volume
    assert not panneau._minuteur.isActive()


def test_deux_onglets_dicom_ne_gardent_qu_un_volume(
    qtbot, espace, deux_series, tmp_path
):
    second = espace.ajouter_onglet("dicom")
    espace.onglets.setCurrentWidget(second)
    importer(qtbot, second, ecrire(serie_ct(), tmp_path / "autre"))
    assert attendre_la_lecture(qtbot, second.panneau) is not None
    assert deux_series.panneau.visualiseur.volume is None
    espace.onglets.setCurrentWidget(deux_series)
    assert attendre_la_lecture(qtbot, deux_series.panneau) is not None
    assert second.panneau.visualiseur.volume is None


def test_la_grande_fenetre_prend_la_place_et_la_rend_a_sa_fermeture(
    qtbot, deux_series, monkeypatch
):
    onglet = deux_series.panneau
    vus = []

    def exec_(dialogue):
        vus.append(dialogue)
        dialogue.show()
        attendre_la_lecture(qtbot, dialogue.panneau)
        # Le volume de l'onglet a laissé sa place à celui de la grande fenêtre.
        assert onglet.visualiseur.volume is None
        assert dialogue.panneau.visualiseur.volume is not None
        dialogue.reject()
        return 0

    monkeypatch.setattr(DialogueSeries, "exec", exec_)
    deux_series.afficher_en_grand()
    assert vus[0].panneau.visualiseur.volume is None
    assert attendre_la_lecture(qtbot, onglet) is not None


def test_changer_de_serie_arrete_la_lecture_en_cours(qtbot, deux_series, monkeypatch):
    """Une seule lecture à la fois : la précédente s'arrête au fichier suivant."""
    import medphystools.dicom as module
    import medphystools.volumes as volumes

    lues = []
    vraie = module.lire_serie

    def lente(chemin, annule=None):
        for jeu in vraie(chemin, annule):
            lues.append(chemin)
            time.sleep(0.2)
            yield jeu

    monkeypatch.setattr(volumes, "lire_serie", lente)
    panneau = deux_series.panneau
    deux_series.table.selectRow(1)
    assert panneau.en_cours
    qtbot.waitUntil(lambda: bool(lues), timeout=5000)
    premier = lues[0]
    deux_series.table.selectRow(0)
    assert attendre_la_lecture(qtbot, panneau) is not None
    # 4 fichiers par série : la première n'a pas été lue jusqu'au bout.
    assert lues.count(premier) < 4


def test_seule_la_serie_affichee_est_lue(qtbot, editeur, tmp_path, monkeypatch):
    """Une capture sans repère et un scanner : afficher l'un ne lit pas les pixels de l'autre."""
    import medphystools.dicom as module
    import medphystools.volumes as volumes

    importer(qtbot, editeur, ecrire(serie_ct() + [capture_ecran()], tmp_path / "src"))
    attendre_la_lecture(qtbot, editeur.panneau)
    lus = []
    vraie = module.lire_serie

    def espion(chemin, annule=None):
        lus.append(chemin)
        return vraie(chemin, annule)

    monkeypatch.setattr(volumes, "lire_serie", espion)
    autre = 1 if editeur.table.currentRow() == 0 else 0
    editeur.table.selectRow(autre)
    attendre_la_lecture(qtbot, editeur.panneau)
    assert lus == [espace_de(editeur, autre)]


def espace_de(editeur, rang):
    return editeur.archive.emplacement_fichier(editeur.liens[rang].fichier)


# --- Les séries dans l'onglet de fichiers -------------------------------------------------


@pytest.fixture
def fichiers(espace):
    return espace.ajouter_onglet("fichiers")


def lignes_de(table):
    return [table.item(i, 0).text() for i in range(table.rowCount())]


def test_une_serie_importee_apparait_dans_l_onglet_de_fichiers(
    qtbot, espace, fichiers, editeur, tmp_path
):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    relue(espace)
    assert lignes_de(fichiers.table) == ["dicom_CT"]
    assert fichiers.table.item(0, 2).text() == "Copie DICOM"
    assert fichiers.table.item(0, 3).text() == "CT, 4 fichier(s), pseudonymisé"


def test_retirer_la_serie_la_retire_aussi_de_l_onglet_de_fichiers(
    qtbot, espace, fichiers, editeur, tmp_path, monkeypatch
):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    relue(espace)
    assert lignes_de(fichiers.table) == ["dicom_CT"]
    monkeypatch.setattr(editeur, "_confirmer_retrait", lambda _n: True)
    editeur.table.selectRow(0)
    editeur.demander_retrait()
    relue(espace)
    assert lignes_de(fichiers.table) == []


def test_une_reference_uid_apparait_aussi_et_se_retire(
    qtbot, espace, fichiers, editeur, tmp_path, monkeypatch
):
    monkeypatch.setattr(editeur, "_demander_noeud", lambda: "PACS_CHU")
    editeur.choix_mode.setCurrentIndex(2)
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    relue(espace)
    assert fichiers.table.item(0, 2).text() == "Référence DICOM"
    assert "PACS_CHU" in fichiers.table.item(0, 3).text()


def test_la_serie_se_retrouve_dans_l_onglet_de_fichiers_a_la_reouverture(
    qtbot, espace, fichiers, editeur, tmp_path
):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    relue(espace)
    assert espace.ouvrir_page(espace.page.id)
    rouvert = next(
        e for e in espace.editeurs() if type(e).__name__ == "EditeurFichiers"
    )
    assert lignes_de(rouvert.table) == ["dicom_CT"]


def test_l_onglet_de_fichiers_ne_retire_pas_une_serie_et_l_explique(
    qtbot, espace, fichiers, editeur, tmp_path
):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    page = relue(espace)
    fichiers.table.selectRow(0)
    fichiers.demander_retrait()
    assert "onglet DICOM" in fichiers.message.text()
    fichiers.ouvrir_selection()
    assert "onglet DICOM" in fichiers.message.text()
    assert relue(espace).grandeur("dicom_CT") == page.grandeur("dicom_CT")
    flags = fichiers.table.item(0, 5).flags()
    assert not flags & Qt.ItemFlag.ItemIsEditable


def test_une_autre_ligne_de_l_onglet_de_fichiers_reste_utilisable(
    qtbot, espace, fichiers, editeur, tmp_path
):
    """Les séries s'ajoutent à la fin : les pièces jointes gardent leur rang et leur commentaire."""
    piece = tmp_path / "note.txt"
    piece.write_text("x", encoding="utf-8")
    fichiers.joindre([piece])
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    relue(espace)
    assert lignes_de(fichiers.table) == ["pieces_jointes/note.txt", "dicom_CT"]
    fichiers.table.item(0, 5).setText("Commentaire")
    assert relue(espace).pieces_jointes[0].commentaire == "Commentaire"


def test_un_enregistrement_ne_ferme_pas_une_saisie_dans_l_onglet_de_fichiers(
    qtbot, espace, fichiers, editeur, tmp_path
):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    relue(espace)
    element = fichiers.table.item(0, 0)
    espace.enregistrer_maintenant()
    assert fichiers.table.item(0, 0) is element


# --- Fiche et export ---------------------------------------------------------------------


@pytest.fixture
def page_dicom(qtbot, espace, editeur, tmp_path):
    importer(qtbot, editeur, ecrire(serie_ct(), tmp_path / "src"))
    return relue(espace)


def test_la_fiche_resume_la_serie_et_propose_de_la_voir(qtbot, espace, page_dicom):
    fiche = FichePage()
    qtbot.addWidget(fiche)
    fiche.resize(1000, 800)
    fiche.afficher(espace.archive, page_dicom.id)
    table = fiche.tables["__fichiers__"]
    assert table.item(0, 2).text() == "Copie DICOM"
    assert table.item(0, 3).text() == "CT, 4 fichier(s), pseudonymisé"
    assert "Voir les séries" in fiche.bouton_dicom.text()
    serie = page_dicom.mesures[0].dicom
    assert table.item(0, 4).text() == serie.series_instance_uid
    assert serie.study_instance_uid in table.item(0, 4).toolTip()


def test_la_fiche_ouvre_le_visualiseur(qtbot, espace, page_dicom):
    dialogue = DialogueSeries(espace.archive, page_dicom)
    qtbot.addWidget(dialogue)
    dialogue.show()
    qtbot.waitUntil(lambda: not dialogue.panneau.en_cours, timeout=20000)
    assert dialogue.panneau.visualiseur.volume.modalite == "CT"
    texte = textes_visibles(dialogue)
    assert "Fabricant fictif" in texte
    for interdit in INTERDITS:
        assert interdit not in texte


def test_l_export_pdf_mentionne_le_traitement(espace, page_dicom, tmp_path):
    from medphystools.export import SansImages, page_en_html

    html = page_en_html(espace.archive, page_dicom.id, SansImages())
    assert "CT, 4 fichier(s), pseudonymisé" in html
    assert "DUPONT" not in html


def test_une_recherche_trouve_la_modalite(qtbot, espace, editeur, tmp_path):
    """Le nom de la grandeur ne contient plus la modalité : seule la description la donne."""
    importer(qtbot, editeur, ecrire(dose_rt(), tmp_path / "src"))
    editeur.table.item(0, 0).setText("plan_du_jour")
    page = relue(espace)
    assert page.id in espace.archive.chercher_identifiants(texte="RTDOSE")
