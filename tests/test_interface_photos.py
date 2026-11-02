"""Vérifie les pièces jointes, les photos annotées et l'onglet de fichiers."""

import hashlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QUrl
from PySide6.QtGui import QColor, QDropEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog

from pymphub import Archive
from pymphub.depot import Depot
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.onglet_fichiers import EditeurFichiers
from pymphub.interface.onglets import EditeurNotes
from pymphub.interface.photos import (
    DialogueAnnotation,
    ToileAnnotation,
    VignettePhoto,
    charger_image,
    dessiner_annotations,
    est_image,
    zone_image,
)
from pymphub.interface.theme import ANNOTATION
from pymphub.page import Annotation, PhotoAnnotee

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))


def empreinte(chemin: Path) -> str:
    return hashlib.sha256(chemin.read_bytes()).hexdigest()


def ecrire_image(chemin: Path, largeur: int = 200, hauteur: int = 100) -> Path:
    image = QImage(largeur, hauteur, QImage.Format.Format_RGB32)
    image.fill(QColor("#808080"))
    assert image.save(str(chemin))
    return chemin


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def espace(qtbot, arc, monkeypatch):
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    monkeypatch.setattr(resultat, "_confirmer", lambda *_: True)
    assert resultat.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    return resultat


@pytest.fixture
def notes(espace):
    resultat = espace.editeurs()[0]
    assert isinstance(resultat, EditeurNotes)
    return resultat


@pytest.fixture
def photo(tmp_path):
    return ecrire_image(tmp_path / "montage.png")


def relue(espace):
    return espace.archive.lire_page(espace.page.id)


def rouvrir(espace):
    assert espace.ouvrir_page(espace.page.id)
    return espace.editeurs()


def fichier_de_la_page(espace, chemin):
    return espace.archive.racine / "pages" / espace.page.id / chemin


# --- Joindre une photo à des notes ------------------------------------------------------


def test_une_photo_ajoutee_s_affiche_et_s_enregistre(espace, notes, photo):
    notes.ajouter_fichiers([photo])
    assert len(notes.bande.vignettes) == 1
    assert notes.bande.vignettes[0].image.pixmap() is not None
    assert espace.enregistrer_maintenant()

    page = relue(espace)
    assert [p.chemin for p in page.pieces_jointes] == ["pieces_jointes/montage.png"]
    assert page.pieces_jointes[0].empreinte == empreinte(photo)
    assert [p.chemin for p in page.onglets[0].photos] == ["pieces_jointes/montage.png"]
    copie = fichier_de_la_page(espace, "pieces_jointes/montage.png")
    assert copie.read_bytes() == photo.read_bytes()
    assert espace.erreurs == []


def test_la_photo_ne_passe_pas_par_git(espace, notes, photo):
    notes.ajouter_fichiers([photo])
    assert espace.enregistrer_maintenant()
    assert not any(
        "pieces_jointes" in chemin
        for chemin in Depot(espace.archive.racine).fichiers_suivis()
    )
    assert espace.archive.modifications_non_tracees() == []


def test_ajouter_une_photo_declenche_l_enregistrement_automatique(espace, notes, photo):
    notes.ajouter_fichiers([photo])
    assert espace._minuteur.isActive()
    assert espace.modifications_en_attente()


def test_la_meme_photo_deposee_deux_fois_ne_s_affiche_qu_une_fois(notes, photo):
    notes.ajouter_fichiers([photo, photo])
    assert len(notes.photos) == 1


def test_un_fichier_qui_n_est_pas_une_image_est_joint_sans_vignette(
    espace, notes, tmp_path
):
    pdf = tmp_path / "certificat.pdf"
    pdf.write_bytes(b"%PDF-1.7")
    notes.ajouter_fichiers([pdf])
    assert notes.photos == []
    assert "certificat.pdf" in notes.message.text()
    assert notes.message.isVisibleTo(notes)
    assert espace.enregistrer_maintenant()
    assert [p.chemin for p in relue(espace).pieces_jointes] == [
        "pieces_jointes/certificat.pdf"
    ]


def test_un_dossier_depose_est_explique(espace, notes, tmp_path):
    notes.ajouter_fichiers([tmp_path])
    assert "n'est pas un fichier" in notes.message.text()
    assert espace.pieces.pieces == ()


# --- Aucun DICOM avant la pseudonymisation --------------------------------------------


@pytest.mark.parametrize(
    ("nom", "contenu"),
    [("serie.dcm", b"x"), ("IM0001", bytes(128) + b"DICM" + bytes(8))],
)
def test_un_dicom_n_entre_pas_dans_l_archive(espace, notes, tmp_path, nom, contenu):
    fichier = tmp_path / nom
    fichier.write_bytes(contenu)
    notes.ajouter_fichiers([fichier])
    assert "DICOM" in notes.message.text()
    assert espace.pieces.pieces == ()
    assert not fichier_de_la_page(espace, "pieces_jointes").exists()


# --- Glisser-déposer ------------------------------------------------------------------


def deposer(cible, chemins):
    donnees = QMimeData()
    donnees.setUrls([QUrl.fromLocalFile(str(c)) for c in chemins])
    evenement = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        donnees,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    cible.dropEvent(evenement)


def test_deposer_une_photo_sur_le_texte_la_joint_sans_ecrire_son_chemin(notes, photo):
    deposer(notes.texte, [photo])
    assert notes.texte.toPlainText() == ""
    assert [p.chemin for p in notes.photos] == ["pieces_jointes/montage.png"]


def test_deposer_une_photo_sur_l_onglet_la_joint(notes, photo):
    deposer(notes, [photo])
    assert [p.chemin for p in notes.photos] == ["pieces_jointes/montage.png"]


def test_deposer_du_texte_sur_les_notes_reste_possible(qtbot, notes):
    notes.show()
    qtbot.waitExposed(notes)
    donnees = QMimeData()
    donnees.setText("Texte déposé")
    evenement = QDropEvent(
        QPointF(5, 5),
        Qt.DropAction.CopyAction,
        donnees,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    notes.texte.dropEvent(evenement)
    assert notes.photos == []
    assert notes.texte.toPlainText() == "Texte déposé"


def test_deposer_un_fichier_sur_l_onglet_de_fichiers_le_joint(espace, tmp_path):
    fichiers = espace.ajouter_onglet("fichiers")
    source = tmp_path / "export.txt"
    source.write_text("données", encoding="utf-8")
    deposer(fichiers, [source])
    assert [p.chemin for p in espace.pieces.pieces] == ["pieces_jointes/export.txt"]
    assert fichiers.table.item(0, 0).text() == "pieces_jointes/export.txt"


# --- Annotation -------------------------------------------------------------------------


@pytest.fixture
def toile(qtbot):
    resultat = ToileAnnotation(
        QImage(200, 100, QImage.Format.Format_RGB32), [], modifiable=True
    )
    qtbot.addWidget(resultat)
    resultat.resize(600, 400)
    resultat.show()
    qtbot.waitExposed(resultat)
    return resultat


def tirer(toile, depart, arrivee):
    QTest.mousePress(toile, Qt.MouseButton.LeftButton, pos=depart)
    QTest.mouseMove(toile, arrivee)
    QTest.mouseRelease(toile, Qt.MouseButton.LeftButton, pos=arrivee)


def test_la_souris_donne_des_coordonnees_relatives_a_l_image(toile):
    # Image 2:1 dans 600×400 : elle occupe 600×300, à partir de y = 50.
    assert toile.zone().getRect() == (0.0, 50.0, 600.0, 300.0)
    toile.forme = "rectangle"
    tirer(toile, QPoint(150, 125), QPoint(450, 275))
    assert toile.annotations == [
        Annotation(forme="rectangle", points=((0.25, 0.25), (0.75, 0.75)))
    ]


def test_un_trait_sorti_de_l_image_revient_sur_son_bord(toile):
    toile.forme = "fleche"
    tirer(toile, QPoint(300, 200), QPoint(590, 395))
    assert toile.annotations[0].points == ((0.5, 0.5), (0.983333, 1.0))


def test_un_clic_hors_de_l_image_ne_dessine_rien(toile):
    toile.forme = "point"
    QTest.mouseClick(toile, Qt.MouseButton.LeftButton, pos=QPoint(300, 10))
    assert toile.annotations == []


def test_un_clic_dessine_un_point_mais_pas_une_fleche(toile):
    toile.forme = "fleche"
    QTest.mouseClick(toile, Qt.MouseButton.LeftButton, pos=QPoint(300, 200))
    assert toile.annotations == []
    toile.forme = "point"
    QTest.mouseClick(toile, Qt.MouseButton.LeftButton, pos=QPoint(300, 200))
    assert toile.annotations == [Annotation(forme="point", points=((0.5, 0.5),))]


def test_un_trait_libre_garde_son_parcours(toile):
    toile.forme = "trace"
    QTest.mousePress(toile, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
    for x in (150, 200, 250):
        QTest.mouseMove(toile, QPoint(x, 100))
    QTest.mouseRelease(toile, Qt.MouseButton.LeftButton, pos=QPoint(250, 100))
    assert toile.annotations[0].forme == "trace"
    assert len(toile.annotations[0].points) == 4


def test_une_toile_en_lecture_seule_ne_dessine_pas(qtbot):
    toile = ToileAnnotation(
        QImage(200, 100, QImage.Format.Format_RGB32), [], modifiable=False
    )
    qtbot.addWidget(toile)
    toile.resize(600, 400)
    QTest.mouseClick(toile, Qt.MouseButton.LeftButton, pos=QPoint(300, 200))
    assert toile.annotations == []


def couleur_annotation_en(largeur, hauteur, point):
    """Dessine une annotation et renvoie la couleur à une position relative de l'image."""
    rendu = QImage(largeur, hauteur, QImage.Format.Format_RGB32)
    rendu.fill(QColor("black"))
    taille_photo = QImage(400, 200, QImage.Format.Format_RGB32).size()
    zone = zone_image(taille_photo, largeur, hauteur)
    peintre = QPainter(rendu)
    dessiner_annotations(
        peintre, zone, [Annotation(forme="point", points=((0.75, 0.25),))]
    )
    peintre.end()
    x = zone.x() + point[0] * zone.width()
    y = zone.y() + point[1] * zone.height()
    return rendu.pixelColor(round(x), round(y)).name()


@pytest.mark.parametrize(
    ("largeur", "hauteur"), [(200, 100), (400, 200), (1000, 300), (300, 900)]
)
def test_une_annotation_reste_au_meme_endroit_quelle_que_soit_la_taille(
    largeur, hauteur
):
    assert couleur_annotation_en(largeur, hauteur, (0.75, 0.25)) == ANNOTATION
    assert couleur_annotation_en(largeur, hauteur, (0.25, 0.75)) == "#000000"


def test_annoter_laisse_le_fichier_d_origine_identique(
    espace, notes, photo, monkeypatch
):
    notes.ajouter_fichiers([photo])
    assert espace.enregistrer_maintenant()
    copie = fichier_de_la_page(espace, "pieces_jointes/montage.png")
    avant = empreinte(copie)
    date_avant = copie.stat().st_mtime_ns

    annotee = PhotoAnnotee(
        chemin="pieces_jointes/montage.png",
        legende="Vue de face",
        annotations=(
            Annotation(forme="fleche", points=((0.1, 0.2), (0.3, 0.4)), texte="Source"),
        ),
    )
    monkeypatch.setattr(notes, "_annoter", lambda _photo: annotee)
    notes.demander_annotation(0)
    assert espace.enregistrer_maintenant()

    assert empreinte(copie) == avant == empreinte(photo)
    assert copie.stat().st_mtime_ns == date_avant
    assert relue(espace).pieces_jointes[0].empreinte == avant
    assert sorted(f.name for f in copie.parent.iterdir()) == ["montage.png"]


def test_les_annotations_se_retrouvent_apres_reouverture(
    espace, notes, photo, monkeypatch
):
    notes.ajouter_fichiers([photo])
    annotee = PhotoAnnotee(
        chemin="pieces_jointes/montage.png",
        legende="Vue de face",
        annotations=(
            Annotation(forme="rectangle", points=((0.1, 0.2), (0.3, 0.4)), texte="A"),
            Annotation(forme="trace", points=((0.1, 0.1), (0.2, 0.2), (0.3, 0.1))),
        ),
    )
    monkeypatch.setattr(notes, "_annoter", lambda _photo: annotee)
    notes.demander_annotation(0)
    assert espace.enregistrer_maintenant()

    relu = rouvrir(espace)[0]
    assert relu.photos == [annotee]
    assert relu.bande.vignettes[0].legende.text() == "Vue de face"

    # Et depuis le disque, avec une archive rouverte.
    archive = Archive(espace.archive.racine)
    assert archive.lire_page(espace.page.id).onglets[0].photos == (annotee,)


def test_annuler_l_annotation_ne_change_rien(espace, notes, photo, monkeypatch):
    notes.ajouter_fichiers([photo])
    assert espace.enregistrer_maintenant()
    monkeypatch.setattr(notes, "_annoter", lambda _photo: None)
    notes.demander_annotation(0)
    assert not espace.modifications_en_attente()


def test_le_dialogue_d_annotation_de_bout_en_bout(qtbot, photo):
    dialogue = DialogueAnnotation(
        photo, PhotoAnnotee(chemin="pieces_jointes/montage.png"), "intact", True
    )
    qtbot.addWidget(dialogue)
    dialogue.show()
    qtbot.waitExposed(dialogue)
    toile = dialogue.toile
    centre = toile.zone().center().toPoint()

    dialogue.boutons_formes["point"].click()
    QTest.mouseClick(toile, Qt.MouseButton.LeftButton, pos=centre)
    assert dialogue.liste.count() == 1
    assert dialogue.liste.currentRow() == 0
    QTest.keyClicks(dialogue.champ_texte, "Fuite")
    assert dialogue.liste.item(0).text() == "1. Point : Fuite"

    dialogue.boutons_formes["rectangle"].click()
    tirer(toile, centre, centre + QPoint(50, 30))
    assert dialogue.liste.count() == 2
    assert dialogue.champ_texte.text() == ""

    dialogue.liste.setCurrentRow(0)
    assert dialogue.champ_texte.text() == "Fuite"
    dialogue.champ_legende.setText("Légende")
    resultat = dialogue.photo()
    assert [a.forme for a in resultat.annotations] == ["point", "rectangle"]
    assert resultat.annotations[0].texte == "Fuite"
    assert resultat.legende == "Légende"

    dialogue.liste.setCurrentRow(1)
    dialogue.retirer_annotation()
    assert [a.forme for a in dialogue.photo().annotations] == ["point"]
    assert dialogue.toile.choisie is None
    assert not dialogue.bouton_retirer.isEnabled()


def test_le_dialogue_en_lecture_seule_ne_propose_aucune_modification(qtbot, photo):
    dialogue = DialogueAnnotation(
        photo,
        PhotoAnnotee(
            chemin="pieces_jointes/montage.png",
            annotations=(Annotation(forme="point", points=((0.5, 0.5),), texte="T"),),
        ),
        "intact",
        False,
    )
    qtbot.addWidget(dialogue)
    dialogue.show()
    assert not dialogue.toile.modifiable
    assert dialogue.champ_legende.isReadOnly()
    assert dialogue.champ_texte.isReadOnly()
    assert not dialogue.bouton_retirer.isVisible()
    assert not any(b.isVisible() for b in dialogue.boutons_formes.values())


# --- Orientation EXIF -------------------------------------------------------------------


def test_une_photo_de_telephone_est_redressee(tmp_path):
    image_pil = pytest.importorskip("PIL.Image")
    photo = image_pil.new("RGB", (400, 200), "gray")
    exif = photo.getexif()
    exif[0x0112] = 6  # Orientation : à tourner de 90° pour l'afficher droite.
    chemin = tmp_path / "telephone.jpg"
    photo.save(chemin, exif=exif)

    assert charger_image(chemin).size().toTuple() == (200, 400)
    vignette = charger_image(chemin, 100)
    assert vignette.size().toTuple() == (50, 100)


def test_une_petite_vignette_garde_les_proportions(tmp_path):
    chemin = ecrire_image(tmp_path / "i.png", 400, 200)
    assert charger_image(chemin, 100).size().toTuple() == (100, 50)
    assert charger_image(chemin, 1000).size().toTuple() == (400, 200)


def test_reconnaitre_une_image(tmp_path, photo):
    faux = tmp_path / "faux.png"
    faux.write_text("pas une image", encoding="utf-8")
    assert est_image(photo)
    assert not est_image(faux)


# --- Fichier altéré ou manquant ---------------------------------------------------------


def photo_enregistree(espace, notes, photo):
    notes.ajouter_fichiers([photo])
    assert espace.enregistrer_maintenant()
    return fichier_de_la_page(espace, "pieces_jointes/montage.png")


@pytest.fixture
def fiche(qtbot):
    resultat = FichePage()
    qtbot.addWidget(resultat)
    return resultat


def test_un_fichier_intact_n_affiche_aucune_alerte(espace, notes, photo, fiche):
    photo_enregistree(espace, notes, photo)
    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.alerte_fichiers is None
    assert fiche.tables["__fichiers__"].item(0, 1).text() == "intact"
    vignette = fiche.bandes_photos[0].vignettes[0]
    assert vignette.alerte.text() == ""
    assert not vignette.alerte.isVisibleTo(vignette)


def test_un_fichier_altere_est_signale(espace, notes, photo, fiche):
    copie = photo_enregistree(espace, notes, photo)
    ecrire_image(copie, 50, 50)

    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.alerte_fichiers.text() == (
        "À vérifier : pieces_jointes/montage.png (altéré)"
    )
    assert fiche.tables["__fichiers__"].item(0, 1).text() == "altéré"
    vignette = fiche.bandes_photos[0].vignettes[0]
    assert "altéré" in vignette.alerte.text()
    assert vignette.alerte.isVisibleTo(vignette)

    relu = rouvrir(espace)[0]
    assert "altéré" in relu.bande.vignettes[0].alerte.text()


def test_un_fichier_manquant_est_signale_sans_plantage(espace, notes, photo, fiche):
    copie = photo_enregistree(espace, notes, photo)
    copie.unlink()

    fiche.afficher(espace.archive, espace.page.id)
    assert "manquant" in fiche.alerte_fichiers.text()
    vignette = fiche.bandes_photos[0].vignettes[0]
    assert "manquant" in vignette.alerte.text()
    assert vignette.image.text() == "Aperçu impossible"
    assert not vignette.bouton_ouvrir.isEnabled()

    relu = rouvrir(espace)[0]
    assert "manquant" in relu.bande.vignettes[0].alerte.text()


def test_un_fichier_altere_s_annonce_aussi_dans_le_dialogue(qtbot, photo):
    dialogue = DialogueAnnotation(
        photo, PhotoAnnotee(chemin="pieces_jointes/montage.png"), "altéré", True
    )
    qtbot.addWidget(dialogue)
    assert "altéré" in dialogue.alerte.text()


def test_un_gros_fichier_n_est_pas_signale_comme_altere(qtbot, photo):
    vignette = VignettePhoto(
        photo, PhotoAnnotee(chemin="pieces_jointes/m.png"), "non vérifié", False
    )
    qtbot.addWidget(vignette)
    assert vignette.alerte.text() == ""


# --- Page validée et retrait ------------------------------------------------------------


def test_une_photo_sur_une_page_validee_demande_un_motif(
    espace, notes, photo, monkeypatch
):
    assert espace.demander_validation()
    notes = espace.editeurs()[0]
    notes.ajouter_fichiers([photo])
    assert espace.modifications_en_attente()
    assert not espace._minuteur.isActive()
    assert relue(espace).pieces_jointes == ()

    monkeypatch.setattr(espace, "_demander_motif", lambda: "Photo oubliée")
    assert espace.demander_correction()
    page = relue(espace)
    assert page.statut == "corrigée"
    assert [p.chemin for p in page.pieces_jointes] == ["pieces_jointes/montage.png"]
    assert espace.archive.historique(page.id)[-1].motif == "Photo oubliée"


def test_annuler_les_modifications_retire_la_photo_ajoutee(espace, notes, photo):
    assert espace.demander_validation()
    espace.editeurs()[0].ajouter_fichiers([photo])
    espace.annuler_modifications()
    assert espace.pieces.pieces == ()
    assert espace.editeurs()[0].photos == []
    assert not espace.modifications_en_attente()


def test_retirer_une_photo_garde_le_fichier_et_l_historique(
    espace, notes, photo, monkeypatch
):
    copie = photo_enregistree(espace, notes, photo)
    tete = Depot(espace.archive.racine).tete()
    monkeypatch.setattr(notes, "_confirmer_retrait", lambda _p: True)
    notes.demander_retrait(0)
    assert espace.enregistrer_maintenant()

    page = relue(espace)
    assert page.pieces_jointes == ()
    assert page.onglets[0].photos == ()
    assert copie.is_file()
    ancienne = espace.archive.version_page(page.id, tete)
    assert [p.chemin for p in ancienne.pieces_jointes] == ["pieces_jointes/montage.png"]


def test_refuser_le_retrait_garde_la_photo(notes, photo, monkeypatch):
    notes.ajouter_fichiers([photo])
    monkeypatch.setattr(notes, "_confirmer_retrait", lambda _p: False)
    notes.demander_retrait(0)
    assert len(notes.photos) == 1


def test_une_photo_affichee_dans_deux_notes_reste_jointe(
    espace, notes, photo, monkeypatch
):
    autre = espace.ajouter_onglet("notes")
    notes.ajouter_fichiers([photo])
    autre.ajouter_fichiers([photo])
    monkeypatch.setattr(notes, "_confirmer_retrait", lambda _p: True)
    notes.demander_retrait(0)
    assert [p.chemin for p in espace.pieces.pieces] == ["pieces_jointes/montage.png"]
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []


def test_retirer_un_onglet_de_notes_garde_ses_pieces_jointes(espace, notes, photo):
    espace.ajouter_onglet("notes")
    notes.ajouter_fichiers([photo])
    assert espace.demander_retrait_onglet(0)
    assert espace.enregistrer_maintenant()
    assert [p.chemin for p in relue(espace).pieces_jointes] == [
        "pieces_jointes/montage.png"
    ]
    assert espace.erreurs == []


def test_supprimer_la_page_garde_ses_photos_dans_la_corbeille(espace, notes, photo):
    photo_enregistree(espace, notes, photo)
    cible = espace.archive.supprimer_page(espace.page.id)
    assert (cible / "pieces_jointes" / "montage.png").read_bytes() == photo.read_bytes()


# --- Onglet de fichiers -----------------------------------------------------------------


@pytest.fixture
def fichiers(espace):
    resultat = espace.ajouter_onglet("fichiers")
    assert isinstance(resultat, EditeurFichiers)
    return resultat


@pytest.fixture
def export(tmp_path):
    chemin = tmp_path / "Export constructeur.txt"
    chemin.write_text("données", encoding="utf-8")
    return chemin


def choisir(monkeypatch, editeur, *chemins):
    monkeypatch.setattr(
        editeur, "_choisir_fichiers", lambda _titre: [str(c) for c in chemins]
    )


def test_un_lien_en_copie(espace, fichiers, export, monkeypatch):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_copie()
    assert fichiers.table.item(0, 0).text() == "Export_constructeur"
    assert fichiers.table.item(0, 1).text() == "intact"
    assert fichiers.table.item(0, 2).text() == "Copie"
    assert espace.enregistrer_maintenant()

    grandeur = relue(espace).grandeur("Export_constructeur")
    assert grandeur.stockage.mode == "copie"
    assert grandeur.stockage.nom_origine == "Export constructeur.txt"
    assert relue(espace).onglets[-1].grandeurs == ("Export_constructeur",)
    assert (
        espace.archive.chemin_fichier(
            espace.page.id, "Export_constructeur"
        ).read_bytes()
        == export.read_bytes()
    )


def test_un_lien_en_reference_laisse_le_fichier_a_sa_place(
    espace, fichiers, export, monkeypatch, tmp_path
):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_reference()
    assert fichiers.table.item(0, 2).text() == "Référence fichier"
    assert espace.enregistrer_maintenant()

    grandeur = relue(espace).grandeur("Export_constructeur")
    assert grandeur.stockage.mode == "reference_fichier"
    assert grandeur.stockage.chemin == str(export)
    assert (
        espace.archive.chemin_fichier(espace.page.id, "Export_constructeur") == export
    )
    assert not list((espace.archive.racine / "objets").rglob("*.txt"))


def test_les_liens_se_retrouvent_a_la_reouverture_et_signalent_leur_etat(
    espace, fichiers, export, monkeypatch
):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_reference()
    assert espace.enregistrer_maintenant()
    export.write_text("modifiée", encoding="utf-8")

    relu = rouvrir(espace)[-1]
    assert isinstance(relu, EditeurFichiers)
    assert relu.table.item(0, 0).text() == "Export_constructeur"
    assert relu.table.item(0, 1).text() == "altéré"
    assert not espace.modifications_en_attente()

    export.unlink()
    relu = rouvrir(espace)[-1]
    assert relu.table.item(0, 1).text() == "manquant"


def test_renommer_et_commenter_un_lien(espace, fichiers, export, monkeypatch):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_copie()
    assert espace.enregistrer_maintenant()

    fichiers.table.item(0, 0).setText("export")
    fichiers.table.item(0, 4).setText("Version du 14")
    assert espace.enregistrer_maintenant()

    page = relue(espace)
    assert page.grandeur("export").commentaire == "Version du 14"
    assert page.onglets[-1].grandeurs == ("export",)
    assert [g.nom for g in page.mesures] == ["export"]


def test_un_nom_de_lien_invalide_bloque_sans_rien_perdre(
    espace, fichiers, export, monkeypatch
):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_copie()
    assert espace.enregistrer_maintenant()

    fichiers.table.item(0, 0).setText("nom invalide")
    assert espace.saisies_incompletes() == [
        "Fichier : nom « nom invalide » invalide (lettres, chiffres et _ seulement)"
    ]
    assert [g.nom for g in relue(espace).mesures] == ["Export_constructeur"]


def test_deux_fichiers_de_meme_nom_ne_se_melangent_pas(
    espace, fichiers, export, monkeypatch, tmp_path
):
    (tmp_path / "b").mkdir()
    autre = tmp_path / "b" / export.name
    autre.write_text("autre", encoding="utf-8")
    choisir(monkeypatch, fichiers, export, autre)
    fichiers.demander_copie()
    assert espace.enregistrer_maintenant()
    assert [g.nom for g in relue(espace).mesures] == [
        "Export_constructeur",
        "Export_constructeur_2",
    ]


@pytest.mark.parametrize("methode", ["demander_copie", "demander_reference"])
def test_un_lien_refuse_les_dicom(espace, fichiers, tmp_path, monkeypatch, methode):
    dicom = tmp_path / "patient.dcm"
    dicom.write_bytes(b"x")
    choisir(monkeypatch, fichiers, dicom)
    getattr(fichiers, methode)()
    assert "DICOM" in fichiers.message.text()
    assert fichiers.liens == []
    assert "patient" not in (fichier_de_la_page(espace, "page.json")).read_text(
        encoding="utf-8"
    )


def test_retirer_un_lien(espace, fichiers, export, monkeypatch):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_copie()
    assert espace.enregistrer_maintenant()
    objet = espace.archive.chemin_fichier(espace.page.id, "Export_constructeur")

    monkeypatch.setattr(fichiers, "_confirmer_retrait", lambda _n: True)
    fichiers.table.setCurrentCell(0, 0)
    fichiers.demander_retrait()
    assert espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()
    assert relue(espace).onglets[-1].grandeurs == ()
    assert objet.is_file()


def test_retirer_une_piece_jointe_depuis_l_onglet(
    espace, fichiers, export, monkeypatch
):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_jointure()
    assert fichiers.table.item(0, 2).text() == "Pièce jointe"
    assert espace.enregistrer_maintenant()
    copie = fichier_de_la_page(espace, "pieces_jointes/Export constructeur.txt")

    monkeypatch.setattr(fichiers, "_confirmer_retrait", lambda _n: True)
    fichiers.table.setCurrentCell(0, 0)
    fichiers.demander_retrait()
    assert fichiers.table.rowCount() == 0
    assert espace.enregistrer_maintenant()
    assert relue(espace).pieces_jointes == ()
    assert copie.is_file()


def test_une_photo_affichee_ne_se_retire_pas_depuis_l_onglet_de_fichiers(
    espace, notes, fichiers, photo, monkeypatch
):
    notes.ajouter_fichiers([photo])
    assert fichiers.table.item(0, 0).text() == "pieces_jointes/montage.png"

    monkeypatch.setattr(fichiers, "_confirmer_retrait", lambda _n: True)
    fichiers.table.setCurrentCell(0, 0)
    fichiers.demander_retrait()
    assert "onglet de notes" in fichiers.message.text()
    assert fichiers.table.rowCount() == 1
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []


def test_ouvrir_le_fichier_choisi(espace, fichiers, export, monkeypatch):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_jointure()
    fichiers.demander_reference()
    ouverts = []
    monkeypatch.setattr(fichiers, "_ouvrir", lambda c: ouverts.append(c) or True)

    fichiers.table.setCurrentCell(0, 0)
    fichiers.ouvrir_selection()
    fichiers.table.setCurrentCell(1, 0)
    fichiers.ouvrir_selection()
    assert ouverts == [
        export,
        fichier_de_la_page(espace, "pieces_jointes/Export constructeur.txt"),
    ]


def test_ouvrir_un_fichier_manquant_s_explique(espace, fichiers, export, monkeypatch):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_reference()
    export.unlink()
    monkeypatch.setattr(
        fichiers, "_ouvrir", lambda _c: pytest.fail("Rien à ouvrir pour ce fichier.")
    )
    fichiers.table.setCurrentCell(0, 0)
    fichiers.ouvrir_selection()
    assert "pas disponible" in fichiers.message.text()


def test_les_cases_non_modifiables_ne_s_editent_pas(
    espace, fichiers, export, monkeypatch
):
    choisir(monkeypatch, fichiers, export)
    fichiers.demander_jointure()
    fichiers.demander_copie()
    modifiable = Qt.ItemFlag.ItemIsEditable
    assert fichiers.table.item(0, 0).flags() & modifiable
    assert fichiers.table.item(0, 4).flags() & modifiable
    assert not fichiers.table.item(0, 1).flags() & modifiable
    assert not fichiers.table.item(0, 3).flags() & modifiable
    assert not fichiers.table.item(1, 0).flags() & modifiable
    assert not fichiers.table.item(1, 4).flags() & modifiable


def test_une_reference_conservee_qui_n_est_pas_un_fichier_reste_dans_l_onglet(
    espace, fichiers, export, monkeypatch
):
    scalaires = espace.ajouter_onglet("scalaires")
    ligne = scalaires.lignes[0]
    ligne.nature.setCurrentIndex(1)
    ligne.nom.setText("lot")
    ligne.valeur.setText("007")
    assert espace.enregistrer_maintenant()
    page = relue(espace)
    onglet = page.onglets[1].model_copy(update={"grandeurs": ("lot",)})
    espace.archive.modifier_page(
        page.model_copy(update={"onglets": (page.onglets[0], onglet, page.onglets[2])})
    )

    relu = rouvrir(espace)[1]
    assert isinstance(relu, EditeurFichiers)
    assert relu.valeur().grandeurs == ("lot",)
    assert relue(espace).grandeur("lot").valeur == "007"


# --- Fichier importé dans un tableau ----------------------------------------------------


@pytest.fixture
def tableau(espace):
    return espace.ajouter_onglet("tableau")


def ecrire_csv(chemin):
    chemin.write_text("lot\n007\n", encoding="utf-8")
    return chemin


def test_le_fichier_importe_devient_une_piece_jointe(espace, tableau, tmp_path):
    source = ecrire_csv(tmp_path / "releve.csv")
    assert tableau.importer_fichier(source)
    tableau.champ_nom.setText("t")
    assert espace.enregistrer_maintenant()

    page = relue(espace)
    assert [p.chemin for p in page.pieces_jointes] == ["pieces_jointes/releve.csv"]
    assert page.pieces_jointes[0].empreinte == empreinte(source)
    assert (
        fichier_de_la_page(espace, "pieces_jointes/releve.csv").read_bytes()
        == source.read_bytes()
    )
    assert "pieces_jointes/releve.csv" in page.grandeur("t").commentaire


def test_un_import_refuse_ne_joint_rien(espace, tableau, tmp_path, monkeypatch):
    tableau.coller("1\t2")
    monkeypatch.setattr(tableau, "_confirmer_remplacement", lambda: False)
    assert not tableau.importer_fichier(ecrire_csv(tmp_path / "releve.csv"))
    assert espace.pieces.pieces == ()


def test_un_fichier_illisible_ne_joint_rien(espace, tableau, tmp_path):
    source = tmp_path / "vide.csv"
    source.write_bytes(b"")
    assert not tableau.importer_fichier(source)
    assert espace.pieces.pieces == ()


def test_un_echec_de_copie_garde_le_tableau_et_s_explique(
    espace, tableau, tmp_path, monkeypatch
):
    def refuser(_chemin):
        raise OSError("Disque plein")

    monkeypatch.setattr(espace.pieces, "joindre", refuser)
    assert tableau.importer_fichier(ecrire_csv(tmp_path / "releve.csv"))
    assert tableau.lignes_remplies() == [0]
    assert "Disque plein" in tableau.message.text()
    assert tableau._commentaire == "Importé depuis releve.csv"


def test_l_import_personnalise_joint_aussi_le_fichier(
    espace, tableau, tmp_path, monkeypatch
):
    imports = Path(tmp_path / "config")
    imports.mkdir()
    (imports / "imports_personnalises.py").write_text(
        "def importer_essai(chemin):\n    return {'lot': ['007']}\n", encoding="utf-8"
    )
    monkeypatch.setenv("PYMPHUB_CONFIG", str(imports))
    source = ecrire_csv(tmp_path / "brut.dat")
    monkeypatch.setattr(tableau, "_choisir_import", lambda noms: noms[0])
    monkeypatch.setattr(tableau, "_choisir_fichier", lambda: str(source))
    assert tableau.demander_import_personnalise()
    assert [p.chemin for p in espace.pieces.pieces] == ["pieces_jointes/brut.dat"]


# --- Fiche ------------------------------------------------------------------------------


def test_la_fiche_montre_les_photos_en_lecture_seule(
    espace, notes, photo, fiche, monkeypatch
):
    photo_enregistree(espace, notes, photo)
    fiche.afficher(espace.archive, espace.page.id)
    vignette = fiche.bandes_photos[0].vignettes[0]
    assert vignette.bouton_ouvrir.text() == "Voir…"
    assert not vignette.bouton_retirer.isVisibleTo(vignette)

    vus = []

    def noter(dialogue):
        vus.append(dialogue.toile.modifiable)
        assert isinstance(dialogue, QDialog)

    monkeypatch.setattr(fiche, "_montrer_dialogue", noter)
    vignette.bouton_ouvrir.click()
    assert vus == [False]
    assert espace.archive.lire_page(espace.page.id) == espace.page


def test_la_fiche_affiche_une_page_avec_seulement_des_photos(
    espace, notes, photo, fiche
):
    photo_enregistree(espace, notes, photo)
    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.notes == []
    assert len(fiche.bandes_photos) == 1


def test_le_commentaire_d_import_s_affiche_dans_la_fiche(
    espace, tableau, tmp_path, fiche
):
    tableau.importer_fichier(ecrire_csv(tmp_path / "releve.csv"))
    tableau.champ_nom.setText("t")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    textes = [
        widget.text()
        for widget in fiche.contenu.findChildren(type(fiche.entete))
        if "Importé depuis" in widget.text()
    ]
    assert textes == [
        "Importé depuis releve.csv, conservé dans pieces_jointes/releve.csv"
    ]


def test_le_tableau_des_fichiers_de_la_fiche_montre_toutes_ses_lignes(
    espace, notes, fichiers, tmp_path, fiche, qtbot, monkeypatch
):
    """Un chemin très long fait apparaître l'ascenseur sans masquer la dernière ligne."""
    long = tmp_path / ("dossier_au_nom_long_" * 8)
    long.mkdir()
    source = long / "export.txt"
    source.write_text("données", encoding="utf-8")
    choisir(monkeypatch, fichiers, source)
    fichiers.demander_reference()
    fichiers.demander_jointure()
    assert espace.enregistrer_maintenant()

    fiche.resize(600, 700)
    fiche.show()
    qtbot.waitExposed(fiche)
    fiche.afficher(espace.archive, espace.page.id)
    table = fiche.tables["__fichiers__"]
    qtbot.waitUntil(lambda: table.horizontalScrollBar().isVisible())
    total = sum(table.rowHeight(i) for i in range(table.rowCount()))
    assert table.viewport().height() >= total
    # L'état reste visible sans faire défiler : c'est lui qu'il ne faut pas manquer.
    assert table.columnViewportPosition(1) + table.columnWidth(1) <= (
        table.viewport().width()
    )


def test_le_texte_d_une_annotation_reste_dans_l_image(qapp):
    """Une annotation au bord droit écrit son texte vers l'intérieur de la photo."""

    def nombre_de_pixels_ecrits(rendu):
        return sum(
            rendu.pixelColor(x, y).name() != "#000000"
            for x in range(rendu.width())
            for y in range(rendu.height())
        )

    annotation = Annotation(forme="point", points=((1.0, 0.0),), texte="Afficheur")
    rendu = QImage(200, 100, QImage.Format.Format_RGB32)
    rendu.fill(QColor("black"))
    peintre = QPainter(rendu)
    dessiner_annotations(peintre, QRectF(rendu.rect()), [annotation])
    peintre.end()
    # Le fond clair du texte tient entier dans l'image, en haut à droite.
    assert rendu.pixelColor(150, 1).lightness() > 200
    assert nombre_de_pixels_ecrits(rendu) > 500


def test_des_photos_n_agrandissent_pas_la_fenetre_sur_un_petit_ecran(
    qtbot, espace, notes, photo
):
    """Sinon, le bas de la fenêtre sortirait d'un écran de portable d'hôpital."""
    notes.ajouter_fichiers([photo])
    espace.resize(1100, 660)
    espace.show()
    qtbot.waitExposed(espace)
    qtbot.waitUntil(lambda: notes.bande.height() > 0)
    assert espace.height() == 660
    assert espace.minimumSizeHint().height() <= 660
    assert notes.bande.isVisible()


def test_la_bande_de_photos_prend_sa_place_quand_il_y_en_a(qtbot, espace, notes, photo):
    notes.ajouter_fichiers([photo])
    espace.resize(1100, 1000)
    espace.show()
    qtbot.waitExposed(espace)
    vignette = notes.bande.vignettes[0]
    qtbot.waitUntil(lambda: vignette.height() >= vignette.minimumSizeHint().height())
    assert notes.bande.viewport().height() >= vignette.sizeHint().height()
    assert not notes.bande.verticalScrollBar().isVisible()


def test_la_forme_choisie_se_voit(qtbot, photo):
    dialogue = DialogueAnnotation(
        photo, PhotoAnnotee(chemin="pieces_jointes/montage.png"), "intact", True
    )
    qtbot.addWidget(dialogue)
    assert dialogue.boutons_formes["fleche"].isChecked()
    dialogue.boutons_formes["ellipse"].click()
    assert dialogue.boutons_formes["ellipse"].isChecked()
    assert not dialogue.boutons_formes["fleche"].isChecked()
    assert dialogue.toile.forme == "ellipse"
    assert dialogue.boutons_formes["ellipse"].objectName() == "outil"


# --- Pièces partagées -------------------------------------------------------------------


def test_l_etat_d_une_piece_n_est_verifie_qu_une_fois(espace, photo, monkeypatch):
    piece = espace.pieces.joindre(photo)
    appels = []
    origine = espace.archive.etat_piece_jointe
    monkeypatch.setattr(
        espace.archive,
        "etat_piece_jointe",
        lambda *a: appels.append(a) or origine(*a),
    )
    assert espace.pieces.etat(piece) == "intact"
    assert espace.pieces.etat(piece) == "intact"
    assert len(appels) == 1


def test_un_ajout_previent_les_autres_onglets(espace, notes, fichiers, photo):
    assert fichiers.table.rowCount() == 0
    notes.ajouter_fichiers([photo])
    assert fichiers.table.rowCount() == 1


def test_changer_de_page_oublie_les_pieces_de_la_precedente(espace, notes, photo):
    notes.ajouter_fichiers([photo])
    assert espace.enregistrer_maintenant()
    assert espace.creer_page(DescriptionPage("Autre", HEURE, "G.", None, ()))
    assert espace.pieces.pieces == ()
    assert espace.editeurs()[0].photos == []
