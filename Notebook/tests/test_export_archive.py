"""Vérifie le PDF d'une archive entière : tableaux longs ou larges, avancement, séries DICOM."""

import re
from datetime import date, datetime, timedelta, timezone

import pytest
from fabrique_dicom import ecrire, serie_ct
from pypdf import PdfReader
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMessageBox

from medphystools import Archive
from medphystools.dicom import importer_series
from medphystools.export import (
    LARGEUR_UTILE,
    EstimationExport,
    SansImages,
    decouper_colonnes,
    estimer_export,
    page_en_html,
)
from medphystools.incertitudes import Composante
from medphystools.interface.export_pdf import exporter_pdf, largeur_du_texte
from medphystools.interface.fenetre import (
    DescriptionArchive,
    FenetrePrincipale,
    message_export,
)
from medphystools.page import (
    ColonneNumerique,
    Fichier,
    OngletNotes,
    Page,
    PhotoAnnotee,
    ReferenceDicom,
    Tableau,
)

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MBQ = Composante(type="B", valeur=1.0, unite="MBq", source="certificat")
ETUDE = "1.2.826.0.1.3680043.8.498.1"
SERIE = "1.2.826.0.1.3680043.8.498.1.2.3.4.5.6.7.8.9.10.11.12.13.14.15"


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


def page_tableau(arc, colonnes: int, lignes: int, titre: str = "Grand tableau") -> str:
    """Une page avec un tableau dont chaque cellule dit sa colonne et sa ligne."""
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    noms = [f"c{j}" for j in range(colonnes)]
    arc.creer_page(
        Page(
            id=identifiant,
            titre=titre,
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(
                Tableau(
                    nom="t",
                    fichier="brut/t.csv",
                    colonnes=tuple(
                        ColonneNumerique(nom=n, unite="MBq", composantes=(MBQ,))
                        for n in noms
                    ),
                ),
            ),
        ),
        tableaux={
            "t": {
                n: [1000.0 * j + i + 0.5 for i in range(lignes)]
                for j, n in enumerate(noms)
            }
        },
    )
    return identifiant


def textes_par_page(chemin) -> list[str]:
    """Le texte de chaque feuille. Qt sépare les mots par des tabulations dans le PDF."""
    return [
        re.sub(r"[ \t]+", " ", page.extract_text()) for page in PdfReader(chemin).pages
    ]


# --- Découpage ----------------------------------------------------------------------------


def test_la_largeur_utile_est_celle_du_texte():
    assert LARGEUR_UTILE <= largeur_du_texte()


def test_decouper_garde_toutes_les_colonnes_dans_l_ordre():
    groupes = decouper_colonnes([300] * 7, reservee=40)
    assert groupes == [[0, 1], [2, 3], [4, 5], [6]]


def test_decouper_ne_laisse_aucun_groupe_vide_pour_une_colonne_trop_large():
    assert decouper_colonnes([2000, 100, 2000]) == [[0], [1], [2]]


def test_un_tableau_etroit_n_est_pas_decoupe(arc):
    html = page_en_html(arc, page_tableau(arc, 3, 2), SansImages())
    assert "Partie 1" not in html


def test_un_tableau_large_garde_chaque_colonne_et_rappelle_la_ligne(arc):
    html = page_en_html(arc, page_tableau(arc, 40, 3), SansImages())
    parties = re.findall(r"Partie (\d+) sur (\d+)", html)
    assert len(parties) > 1
    assert all(total == str(len(parties)) for _rang, total in parties)
    for j in range(40):
        assert html.count(f"<th>c{j} (MBq)</th>") == 1
    assert html.count("<th>Ligne</th>") == len(parties)


def test_un_nombre_n_est_jamais_coupe(arc):
    html = page_en_html(arc, page_tableau(arc, 2, 1), SansImages())
    assert '<td style="white-space:nowrap">1000.5</td>' in html


# --- Dans le vrai PDF ---------------------------------------------------------------------


def test_le_pdf_d_un_tableau_large_montre_les_nombres_entiers(arc, tmp_path):
    identifiant = page_tableau(arc, 40, 3)
    chemin = tmp_path / "large.pdf"
    assert exporter_pdf(arc, [identifiant], chemin)
    texte = "\n".join(textes_par_page(chemin))
    for j in range(40):
        for i in range(3):
            attendu = f"{1000.0 * j + i + 0.5:.15g}"
            assert re.search(rf"(?<![\d.]){re.escape(attendu)}(?![\d.])", texte), (
                attendu
            )


def test_le_pdf_d_un_tableau_long_repete_son_en_tete(arc, tmp_path):
    identifiant = page_tableau(arc, 3, 300)
    chemin = tmp_path / "long.pdf"
    assert exporter_pdf(arc, [identifiant], chemin)
    pages = textes_par_page(chemin)
    avec_lignes = [p for p in pages if "c1 (MBq)" in p]
    assert len(avec_lignes) >= 5
    texte = "\n".join(pages)
    for i in range(300):
        assert f"{i + 0.5:.15g}" in texte


def test_chaque_page_d_archive_commence_une_feuille_sans_feuille_blanche(arc, tmp_path):
    ids = [page_tableau(arc, 2, 2, f"Titre {n}") for n in range(3)]
    chemin = tmp_path / "c.pdf"
    assert exporter_pdf(arc, ids, chemin)
    pages = textes_par_page(chemin)
    assert len(pages) == 4
    assert "Sommaire" in pages[0]
    for n in range(3):
        assert pages[n + 1].lstrip().startswith(f"Titre {n}")


def test_les_feuilles_sont_numerotees_de_suite(arc, tmp_path):
    ids = [page_tableau(arc, 2, 2, f"Titre {n}") for n in range(3)]
    chemin = tmp_path / "c.pdf"
    assert exporter_pdf(arc, ids, chemin)
    for numero, texte in enumerate(textes_par_page(chemin), start=1):
        assert texte.rstrip().endswith(str(numero))


def test_le_texte_reste_noir_avec_un_theme_sombre(arc, tmp_path, qapp):
    """Le thème de l'ordinateur ne doit pas changer le papier : texte clair illisible."""
    from PySide6.QtGui import QPalette

    avant = qapp.palette()
    sombre = QPalette(avant)
    sombre.setColor(QPalette.ColorRole.Text, QColor("white"))
    qapp.setPalette(sombre)
    try:
        chemin = tmp_path / "c.pdf"
        assert exporter_pdf(arc, [page_tableau(arc, 2, 2)], chemin)
    finally:
        qapp.setPalette(avant)
    contenu = PdfReader(chemin).pages[0].get_contents().get_data()
    assert re.search(rb"(?m)^1 1 1 (scn|rg)$", contenu) is None


# --- Avancement et annulation -------------------------------------------------------------


def test_l_avancement_suit_les_pages_ecrites(arc, tmp_path):
    ids = [page_tableau(arc, 2, 2) for _ in range(3)]
    vus = []
    assert exporter_pdf(
        arc, ids, tmp_path / "c.pdf", avancer=lambda r: vus.append(r) or True
    )
    assert vus == [1, 2, 3]


def test_annuler_ne_laisse_aucun_fichier(arc, tmp_path):
    ids = [page_tableau(arc, 2, 2) for _ in range(3)]
    chemin = tmp_path / "sortie" / "c.pdf"
    chemin.parent.mkdir()
    assert not exporter_pdf(arc, ids, chemin, avancer=lambda r: r < 2)
    assert list(chemin.parent.iterdir()) == []


def test_annuler_garde_l_ancien_export(arc, tmp_path):
    chemin = tmp_path / "c.pdf"
    chemin.write_bytes(b"ancien")
    assert not exporter_pdf(
        arc, [page_tableau(arc, 2, 2)], chemin, avancer=lambda _r: False
    )
    assert chemin.read_bytes() == b"ancien"


def test_une_page_qui_echoue_n_arrete_pas_l_archive(arc, tmp_path, monkeypatch):
    bonne = page_tableau(arc, 2, 2, "Bonne page")
    mauvaise = page_tableau(arc, 2, 2, "Mauvaise page")
    fichier = arc.emplacement_page(mauvaise) / "brut" / "t.csv"
    original = arc.etats_fichiers

    def echouer(page):
        if page.id == mauvaise:
            raise OSError("disque abîmé")
        return original(page)

    monkeypatch.setattr(arc, "etats_fichiers", echouer)
    assert fichier.is_file()
    chemin = tmp_path / "c.pdf"
    assert exporter_pdf(arc, [bonne, mauvaise], chemin)
    texte = "\n".join(textes_par_page(chemin))
    assert "Bonne page" in texte
    assert "Page illisible : disque abîmé" in texte


# --- Avertissement ------------------------------------------------------------------------


def test_l_estimation_compte_les_images(arc, tmp_path):
    photo = tmp_path / "montage.png"
    image = QImage(20, 10, QImage.Format.Format_RGB32)
    image.fill(QColor("#808080"))
    assert image.save(str(photo))
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="P",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
        )
    )
    piece = arc.joindre_fichier(identifiant, photo)
    page = arc.lire_page(identifiant).model_copy(
        update={
            "pieces_jointes": (piece,),
            "onglets": (
                OngletNotes(
                    id="o1",
                    type="notes",
                    titre="Notes",
                    photos=(PhotoAnnotee(chemin=piece.chemin),),
                ),
            ),
        }
    )
    arc.modifier_page(page)
    estimation = estimer_export(arc, [identifiant, "2000-01-01_absente"])
    assert estimation == EstimationExport(2, 1, 0)
    assert estimation.octets > 1_000_000


def test_le_message_annonce_la_taille_et_ce_qui_manque():
    message = message_export(EstimationExport(10, 200, 5))
    assert "10 page(s)" in message
    assert "200 photo(s)" in message
    assert "environ 201 Mo" in message
    assert "DICOM" in message


@pytest.fixture
def fenetre(qtbot, tmp_path):
    resultat = FenetrePrincipale()
    qtbot.addWidget(resultat)
    resultat.creer_archive(
        DescriptionArchive(tmp_path / "F", "S", "R", date(2026, 11, 1))
    )
    yield resultat
    resultat.espace_archive.arreter()


def test_refuser_l_avertissement_ne_demande_meme_pas_de_fichier(fenetre, monkeypatch):
    page_tableau(fenetre.archive, 2, 2)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_a: QMessageBox.StandardButton.No
    )
    monkeypatch.setattr(fenetre, "_choisir_fichier_pdf", pytest.fail)
    assert not fenetre.demander_export_cahier()


def test_accepter_l_avertissement_ecrit_le_pdf(fenetre, tmp_path, monkeypatch):
    page_tableau(fenetre.archive, 2, 2, "Page de la fenêtre")
    questions = []

    def repondre(_parent, titre, message):
        questions.append((titre, message))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", repondre)
    cible = tmp_path / "archive.pdf"
    monkeypatch.setattr(fenetre, "_choisir_fichier_pdf", lambda _nom: str(cible))
    assert fenetre.demander_export_cahier()
    assert "1 page(s)" in questions[0][1]
    assert "Page de la fenêtre" in "\n".join(textes_par_page(cible))


def test_exporter_une_seule_page_ne_previent_pas(fenetre, tmp_path, monkeypatch):
    identifiant = page_tableau(fenetre.archive, 2, 2)
    monkeypatch.setattr(QMessageBox, "question", pytest.fail)
    cible = tmp_path / "page.pdf"
    monkeypatch.setattr(fenetre, "_choisir_fichier_pdf", lambda _nom: str(cible))
    assert fenetre.demander_export_page(identifiant, 2.0)
    assert cible.is_file()


# --- Séries DICOM dans la liste des pièces jointes ----------------------------------------


def page_reference(arc) -> str:
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Référence",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(
                Fichier(
                    nom="scanner",
                    type_fichier="dicom",
                    stockage=ReferenceDicom(
                        study_instance_uid=ETUDE,
                        series_instance_uid=SERIE,
                        sop_instance_uids=("1.2.3",),
                        noeud="PACS_CHU",
                        date=date(2026, 11, 1),
                    ),
                ),
            ),
        )
    )
    return identifiant


def test_une_reference_liste_ses_identifiants_et_son_noeud(arc):
    html = page_en_html(arc, page_reference(arc), SansImages())
    pieces = html[html.index("<h2>Pièces jointes</h2>") :]
    assert f"Study Instance UID : {ETUDE}" in pieces
    assert f"Series Instance UID : {SERIE}" in pieces
    assert "PACS_CHU" in pieces
    assert "Chemin : aucun" in pieces


def test_une_copie_liste_ses_identifiants_et_son_chemin(arc, tmp_path):
    dossier = tmp_path / "dicom"
    resultat = importer_series(arc, ecrire(serie_ct(coupes=2), dossier))
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Copie",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(resultat.fichiers[0],),
        )
    )
    html = page_en_html(arc, identifiant, SansImages())
    pieces = html[html.index("<h2>Pièces jointes</h2>") :]
    fichier = resultat.fichiers[0]
    assert fichier.dicom.study_instance_uid in pieces
    assert fichier.dicom.series_instance_uid in pieces
    assert str(arc.emplacement_fichier(fichier)) in pieces
    assert "DUPONT" not in html
    chemin = tmp_path / "c.pdf"
    assert exporter_pdf(arc, [identifiant], chemin)
    texte = "\n".join(textes_par_page(chemin))
    assert f"Series Instance UID : {fichier.dicom.series_instance_uid}\n" in texte


def test_l_uid_le_plus_long_tient_sur_une_ligne_du_pdf(arc, tmp_path):
    """Un UID coupé en deux ne se copierait plus d'un bloc vers le PACS."""
    long = "1.2." + "3" * 60
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="UID long",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(
                Fichier(
                    nom="scanner",
                    stockage=ReferenceDicom(
                        study_instance_uid=long,
                        series_instance_uid=long,
                        sop_instance_uids=("1.2.3",),
                        noeud="PACS_CHU",
                        date=date(2026, 11, 1),
                    ),
                ),
            ),
        )
    )
    chemin = tmp_path / "c.pdf"
    assert exporter_pdf(arc, [identifiant], chemin)
    texte = "\n".join(textes_par_page(chemin))
    assert f"Study Instance UID : {long}\n" in texte
    assert f"Series Instance UID : {long}\n" in texte


def test_sans_fichier_pas_de_liste_de_pieces(arc):
    html = page_en_html(arc, page_tableau(arc, 2, 2), SansImages())
    assert "Pièces jointes" not in html
