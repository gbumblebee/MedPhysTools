"""Vérifie le clic droit « Copier le chemin d'accès » sur les fichiers d'une page."""

import os
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fabrique_dicom import ecrire, serie_ct
from menus_reels import Menus, clic_droit
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtWidgets import QMainWindow, QTableWidget, QWidget

from medphystools import Archive
from medphystools.interface.espace_saisie import DescriptionPage, EspaceSaisie
from medphystools.interface.fiche_page import FichePage
from medphystools.interface.onglet_dicom import EditeurDicom
from medphystools.interface.onglet_fichiers import EditeurFichiers
from medphystools.interface.photos import DialogueAnnotation, VignettePhoto
from medphystools.interface.pieces import (
    LIBELLE_CHEMIN,
    LIBELLE_CHEMIN_SERIE,
    LIBELLE_IDENTIFIANTS_PACS,
    LIBELLE_SANS_CHEMIN,
)
from medphystools.interface.texte_en_grand import LIBELLE_ACTION
from medphystools.page import PhotoAnnotee

HEURE = datetime(2026, 11, 1, 9, 0, tzinfo=timezone(timedelta(hours=1)))
TEMOIN = "presse-papiers intact"
_COLONNE_NOM = 0
_COLONNE_COMMENTAIRE = 5


def clic_droit_ligne(table: QTableWidget, ligne: int, colonne: int = _COLONNE_NOM):
    rectangle = table.visualItemRect(table.item(ligne, colonne))
    assert rectangle.isValid()
    clic_droit(table.viewport(), rectangle.center())


def copier_ligne(menus: Menus, table: QTableWidget, ligne: int, libelle: str) -> str:
    """Clique droit sur une ligne, lance l'action et renvoie ce qui a été copié."""
    QGuiApplication.clipboard().setText(TEMOIN)
    menus.action_a_lancer = libelle
    clic_droit_ligne(table, ligne)
    assert len(menus.ouverts) == 1
    assert (libelle, True) in menus.ouverts[-1]
    copie = QGuiApplication.clipboard().text()
    assert copie != TEMOIN
    assert os.path.isabs(copie)
    assert os.path.isfile(copie)
    return copie


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def fenetre(qtbot):
    resultat = QMainWindow()
    resultat.resize(1300, 900)
    qtbot.addWidget(resultat)
    return resultat


@pytest.fixture
def espace(qtbot, arc, fenetre, monkeypatch):
    resultat = EspaceSaisie()
    fenetre.setCentralWidget(resultat)
    resultat.installer(arc)
    monkeypatch.setattr(resultat, "afficher_erreur", pytest.fail)
    monkeypatch.setattr(resultat, "_confirmer", lambda *_: True)
    assert resultat.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    fenetre.show()
    qtbot.waitExposed(fenetre)
    return resultat


@pytest.fixture
def fichiers(espace) -> EditeurFichiers:
    resultat = espace.ajouter_onglet("fichiers")
    espace.onglets.setCurrentWidget(resultat)
    return resultat


@pytest.fixture
def notes(espace):
    resultat = espace.editeurs()[0]
    espace.onglets.setCurrentWidget(resultat)
    return resultat


@pytest.fixture
def dicom(espace) -> EditeurDicom:
    resultat = espace.ajouter_onglet("dicom")
    espace.onglets.setCurrentWidget(resultat)
    return resultat


@pytest.fixture
def photo(tmp_path) -> Path:
    chemin = tmp_path / "montage.png"
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.gray)
    assert image.save(str(chemin))
    return chemin


@pytest.fixture
def document(tmp_path) -> Path:
    chemin = tmp_path / "mesure.txt"
    chemin.write_text("12.5", encoding="utf-8")
    return chemin


def dans_la_page(espace, nom: str) -> str:
    dossier = espace.archive.emplacement_page(espace.page.id)
    return str((dossier / "pieces_jointes" / nom).resolve())


def importer_dicom(qtbot, editeur: EditeurDicom, tmp_path: Path) -> None:
    editeur.importer(ecrire(serie_ct(), tmp_path / "src"))
    qtbot.waitUntil(lambda: not editeur.import_en_cours, timeout=20000)
    assert editeur.liens


# --- Onglet de fichiers -----------------------------------------------------------------


def test_une_piece_jointe_donne_son_chemin_dans_la_page(
    espace, fichiers, document, menus, fenetre
):
    fichiers.joindre([document])
    copie = copier_ligne(menus, fichiers.table, 0, LIBELLE_CHEMIN)
    assert copie == dans_la_page(espace, "mesure.txt")
    assert fenetre.statusBar().currentMessage() == f"Chemin copié : {copie}"


def test_un_fichier_brut_en_copie_donne_son_chemin_dans_le_magasin(
    espace, fichiers, document, menus
):
    fichiers._ajouter_lien(document, copie=True)
    copie = copier_ligne(menus, fichiers.table, 0, LIBELLE_CHEMIN)
    objets = (espace.archive.racine / "objets").resolve()
    assert Path(copie).is_relative_to(objets)
    assert Path(copie).read_text(encoding="utf-8") == "12.5"


def test_un_fichier_en_reference_donne_son_chemin_d_origine(fichiers, document, menus):
    fichiers._ajouter_lien(document, copie=False)
    assert copier_ligne(menus, fichiers.table, 0, LIBELLE_CHEMIN) == str(
        document.resolve()
    )


def test_chaque_ligne_donne_le_chemin_de_son_propre_fichier(
    espace, fichiers, document, photo, menus
):
    """Lien, pièce jointe : les rangs des deux listes ne doivent pas se décaler."""
    fichiers._ajouter_lien(document, copie=False)
    fichiers.joindre([photo])
    assert fichiers.table.rowCount() == 2
    assert copier_ligne(menus, fichiers.table, 1, LIBELLE_CHEMIN) == dans_la_page(
        espace, "montage.png"
    )


def test_une_serie_dicom_listee_dans_les_fichiers_donne_son_zip(
    qtbot, espace, fichiers, dicom, document, menus, tmp_path
):
    fichiers.joindre([document])
    importer_dicom(qtbot, dicom, tmp_path)
    assert espace.enregistrer_maintenant()
    espace.onglets.setCurrentWidget(fichiers)
    assert fichiers.table.rowCount() == 2
    copie = copier_ligne(menus, fichiers.table, 1, LIBELLE_CHEMIN_SERIE)
    assert zipfile.is_zipfile(copie)


def test_le_clic_droit_garde_afficher_en_grand_dans_un_seul_menu(
    fichiers, document, menus
):
    fichiers.joindre([document])
    fichiers.pieces.commenter(fichiers.pieces.pieces[0].chemin, "un commentaire")
    clic_droit_ligne(fichiers.table, 0, _COLONNE_COMMENTAIRE)
    assert menus.ouverts == [[(LIBELLE_ACTION, True), (LIBELLE_CHEMIN, True)]]


def test_le_clic_droit_hors_d_une_ligne_ne_propose_pas_de_chemin(
    fichiers, document, menus
):
    fichiers.joindre([document])
    clic_droit(
        fichiers.table.viewport(), fichiers.table.viewport().rect().bottomRight()
    )
    assert menus.ouverts == [[(LIBELLE_ACTION, False)]]


# --- Onglet de notes --------------------------------------------------------------------


def test_une_photo_des_notes_donne_son_chemin_avant_tout_enregistrement(
    espace, notes, photo, menus, fenetre
):
    notes.ajouter_fichiers([photo])
    vignette = notes.bande.vignettes[0]
    menus.action_a_lancer = LIBELLE_CHEMIN
    # Sur l'image même : l'événement doit remonter de l'étiquette à la vignette.
    clic_droit(vignette.image, vignette.image.rect().center())
    assert menus.ouverts == [[(LIBELLE_CHEMIN, True)]]
    copie = QGuiApplication.clipboard().text()
    assert copie == dans_la_page(espace, "montage.png")
    assert os.path.isfile(copie)
    assert fenetre.statusBar().currentMessage() == f"Chemin copié : {copie}"


def test_la_legende_d_une_photo_propose_aussi_le_chemin(espace, notes, photo, menus):
    notes.ajouter_fichiers([photo])
    ancienne = notes.bande.vignettes[0].photo
    notes.photos = [ancienne.model_copy(update={"legende": "Montage"})]
    notes._afficher_photos()
    vignette = notes.bande.vignettes[0]
    menus.action_a_lancer = LIBELLE_CHEMIN
    clic_droit(vignette.legende, vignette.legende.rect().center())
    assert len(menus.ouverts) == 1
    assert (LIBELLE_ACTION, True) in menus.ouverts[0]
    assert (LIBELLE_CHEMIN, True) in menus.ouverts[0]
    assert QGuiApplication.clipboard().text() == dans_la_page(espace, "montage.png")


class FenetresOuvertes(QObject):
    """Relève les widgets qui s'affichent comme une fenêtre à part entière."""

    def __init__(self) -> None:
        super().__init__()
        self.widgets: list[str] = []

    def eventFilter(self, objet: QObject, evenement: QEvent) -> bool:  # noqa: N802
        if (
            evenement.type() == QEvent.Type.Show
            and isinstance(objet, QWidget)
            and objet.isWindow()
        ):
            self.widgets.append(type(objet).__name__)
        return False


def test_construire_une_vignette_n_ouvre_aucune_fenetre(qapp, qtbot, photo):
    """Une fenêtre parasite clignoterait à l'écran et fermerait tout menu ouvert."""
    fenetres = FenetresOuvertes()
    qapp.installEventFilter(fenetres)
    vignette = VignettePhoto(
        photo,
        PhotoAnnotee(chemin="pieces_jointes/montage.png", legende="Montage"),
        "manquant",
        modifiable=True,
    )
    qapp.removeEventFilter(fenetres)
    qtbot.addWidget(vignette)
    assert fenetres.widgets == []
    assert not vignette.bouton_retirer.isHidden()
    assert not vignette.legende.isHidden()
    assert not vignette.alerte.isHidden()


def test_construire_le_dialogue_d_annotation_n_ouvre_aucune_fenetre(qapp, qtbot, photo):
    fenetres = FenetresOuvertes()
    qapp.installEventFilter(fenetres)
    dialogue = DialogueAnnotation(
        photo,
        PhotoAnnotee(chemin="pieces_jointes/montage.png"),
        "manquant",
        modifiable=True,
    )
    qapp.removeEventFilter(fenetres)
    qtbot.addWidget(dialogue)
    assert fenetres.widgets == []
    assert not dialogue.indication.isHidden()
    assert not dialogue.alerte.isHidden()


def test_un_enregistrement_menu_ouvert_ne_casse_pas_le_menu_d_une_photo(
    espace, notes, photo, menus
):
    """L'enregistrement automatique reconstruit les vignettes, parfois sous le menu."""
    notes.ajouter_fichiers([photo])
    vignette = notes.bande.vignettes[0]
    menus.action_a_lancer = LIBELLE_CHEMIN
    menus.pendant_le_menu = espace.enregistrer_maintenant
    clic_droit(vignette.image, vignette.image.rect().center())
    assert notes.bande.vignettes[0] is not vignette
    assert menus.ouverts == [[(LIBELLE_CHEMIN, True)]]
    assert QGuiApplication.clipboard().text() == dans_la_page(espace, "montage.png")


def test_un_enregistrement_menu_ouvert_ne_casse_pas_le_menu_d_une_legende(
    espace, notes, photo, menus
):
    notes.ajouter_fichiers([photo])
    ancienne = notes.bande.vignettes[0].photo
    notes.photos = [ancienne.model_copy(update={"legende": "Montage"})]
    notes._afficher_photos()
    vignette = notes.bande.vignettes[0]
    menus.action_a_lancer = LIBELLE_CHEMIN
    menus.pendant_le_menu = espace.enregistrer_maintenant
    clic_droit(vignette.legende, vignette.legende.rect().center())
    assert notes.bande.vignettes[0] is not vignette
    assert len(menus.ouverts) == 1
    assert QGuiApplication.clipboard().text() == dans_la_page(espace, "montage.png")


# --- Onglet DICOM -----------------------------------------------------------------------


def test_une_serie_de_l_onglet_dicom_donne_son_zip(
    qtbot, espace, dicom, menus, tmp_path, fenetre
):
    importer_dicom(qtbot, dicom, tmp_path)
    copie = copier_ligne(menus, dicom.table, 0, LIBELLE_CHEMIN_SERIE)
    objets = (espace.archive.racine / "objets").resolve()
    assert Path(copie).is_relative_to(objets)
    assert zipfile.is_zipfile(copie)
    assert fenetre.statusBar().currentMessage() == f"Chemin copié : {copie}"


def _copier_identifiants(menus, table, rang):
    QGuiApplication.clipboard().setText(TEMOIN)
    menus.action_a_lancer = LIBELLE_IDENTIFIANTS_PACS
    clic_droit_ligne(table, rang)
    assert (LIBELLE_IDENTIFIANTS_PACS, True) in menus.ouverts[-1]
    return QGuiApplication.clipboard().text()


def test_une_serie_de_l_onglet_dicom_montre_et_copie_ses_identifiants_pacs(
    qtbot, dicom, menus, tmp_path, fenetre
):
    importer_dicom(qtbot, dicom, tmp_path)
    serie = dicom.liens[0].fichier.dicom
    colonne = [
        dicom.table.horizontalHeaderItem(j).text()
        for j in range(dicom.table.columnCount())
    ].index("Identifiant PACS")
    assert dicom.table.item(0, colonne).text() == serie.series_instance_uid
    assert serie.study_instance_uid in dicom.table.item(0, colonne).toolTip()
    assert _copier_identifiants(menus, dicom.table, 0) == (
        f"Study Instance UID : {serie.study_instance_uid}\n"
        f"Series Instance UID : {serie.series_instance_uid}"
    )
    assert fenetre.statusBar().currentMessage() == "Identifiants PACS copiés."


def test_l_onglet_de_fichiers_montre_les_identifiants_pacs_sans_perdre_le_chemin(
    qtbot, espace, fichiers, dicom, document, menus, tmp_path
):
    fichiers.joindre([document])
    importer_dicom(qtbot, dicom, tmp_path)
    assert espace.enregistrer_maintenant()
    espace.onglets.setCurrentWidget(fichiers)
    serie = dicom.liens[0].fichier.dicom
    assert fichiers.table.item(0, 4).text() == ""
    assert fichiers.table.item(1, 4).text() == serie.series_instance_uid
    assert serie.series_instance_uid in _copier_identifiants(menus, fichiers.table, 1)
    assert (LIBELLE_CHEMIN_SERIE, True) in menus.ouverts[-1]


def test_un_fichier_ordinaire_ne_propose_pas_d_identifiants_pacs(
    fichiers, document, menus
):
    fichiers.joindre([document])
    clic_droit_ligne(fichiers.table, 0)
    assert all(
        libelle != LIBELLE_IDENTIFIANTS_PACS for libelle, _actif in menus.ouverts[-1]
    )


def test_le_chemin_d_une_serie_ne_contient_que_son_empreinte(
    qtbot, espace, dicom, menus, tmp_path
):
    """Le chemin copié peut circuler : il ne doit rien révéler de la série d'origine."""
    importer_dicom(qtbot, dicom, tmp_path)
    copie = copier_ligne(menus, dicom.table, 0, LIBELLE_CHEMIN_SERIE)
    relatif = Path(copie).relative_to(espace.archive.racine.resolve())
    assert relatif.parts[0] == "objets"
    assert "".join(relatif.parts[1:]) == dicom.liens[0].fichier.stockage.empreinte


def test_une_serie_restee_sur_le_pacs_explique_qu_elle_n_a_pas_de_chemin(
    qtbot, dicom, menus, tmp_path, monkeypatch
):
    monkeypatch.setattr(dicom, "_demander_noeud", lambda: "PACS_CHU")
    dicom.choix_mode.setCurrentIndex(2)
    importer_dicom(qtbot, dicom, tmp_path)
    QGuiApplication.clipboard().setText(TEMOIN)
    clic_droit_ligne(dicom.table, 0)
    assert len(menus.ouverts) == 1
    assert (LIBELLE_SANS_CHEMIN, False) in menus.ouverts[0]
    assert (LIBELLE_CHEMIN, True) not in menus.ouverts[0]
    assert QGuiApplication.clipboard().text() == TEMOIN


# --- Fiche d'une page -------------------------------------------------------------------


def test_la_fiche_donne_le_chemin_des_fichiers_et_des_photos(
    qtbot, arc, fenetre, photo, document, menus
):
    espace = EspaceSaisie()
    espace.installer(arc)
    assert espace.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    espace.editeurs()[0].ajouter_fichiers([photo])
    onglet = espace.ajouter_onglet("fichiers")
    onglet._ajouter_lien(document, copie=False)
    assert espace.enregistrer_maintenant()
    id_page = espace.page.id
    attendu_photo = dans_la_page(espace, "montage.png")

    fiche = FichePage()
    fenetre.setCentralWidget(fiche)
    fenetre.show()
    qtbot.waitExposed(fenetre)
    fiche.afficher(arc, id_page)
    table = fiche.tables["__fichiers__"]

    # Le lien vient avant la pièce jointe, dans la fiche comme dans l'onglet.
    copie = copier_ligne(menus, table, 0, LIBELLE_CHEMIN)
    assert copie == str(document.resolve())
    menus.ouverts.clear()
    assert copier_ligne(menus, table, 1, LIBELLE_CHEMIN) == attendu_photo

    menus.ouverts.clear()
    QGuiApplication.clipboard().setText(TEMOIN)
    vignette = fiche.bandes_photos[0].vignettes[0]
    clic_droit(vignette.image, vignette.image.rect().center())
    assert menus.ouverts == [[(LIBELLE_CHEMIN, True)]]
    assert QGuiApplication.clipboard().text() == attendu_photo
    espace.close()
