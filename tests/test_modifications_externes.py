"""Vérifie la nouvelle version créée à l'ouverture, et le chemin d'accès d'une page."""

import json
import os
import shutil
from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QMainWindow

from pymphub import Archive
from pymphub.export import SansImages, page_en_html
from pymphub.interface.espace_archive import EspaceArchive
from pymphub.interface.fenetre import FenetrePrincipale
from pymphub.page import ColonneNumerique, Page, Scalaire, Tableau

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
ID_PAGE = "2026-11-14_decroissance_0001"
COMPOSANTE = {"type": "B", "valeur": 1.0, "unite": "MBq", "source": "certificat"}


def page_de_test(statut="brouillon"):
    return Page(
        id=ID_PAGE,
        titre="Décroissance",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        statut=statut,
        mesures=(
            Scalaire(
                nom="activite", valeur=100.0, unite="MBq", composantes=(COMPOSANTE,)
            ),
            Tableau(
                nom="t",
                fichier="brut/t.csv",
                colonnes=(
                    ColonneNumerique(nom="act", unite="MBq", composantes=(COMPOSANTE,)),
                ),
            ),
        ),
    )


@pytest.fixture
def arc(tmp_path):
    archive = Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))
    archive.creer_page(page_de_test(), tableaux={"t": {"act": [1.0, 2.0]}})
    return archive


def chemin_json(arc):
    return arc.emplacement_page(ID_PAGE) / "page.json"


def changer_activite(arc, valeur):
    """Modifie la page comme le ferait un éditeur de texte, sans passer par le logiciel."""
    contenu = json.loads(chemin_json(arc).read_text(encoding="utf-8"))
    contenu["mesures"][0]["valeur"] = valeur
    chemin_json(arc).write_text(json.dumps(contenu), encoding="utf-8")
    return chemin_json(arc).read_bytes()


# --- Cœur ------------------------------------------------------------------------------


def test_une_archive_saine_ne_cree_aucune_version(arc):
    avant = len(arc._depot.commits())
    assert arc.enregistrer_modifications_externes() == []
    assert len(arc._depot.commits()) == avant


def test_une_modification_a_la_main_devient_une_version_fidele(arc):
    contenu = changer_activite(arc, 999.0)
    assert arc.enregistrer_modifications_externes() == [f"pages/{ID_PAGE}/page.json"]
    assert arc.modifications_non_tracees() == []
    # Le fichier est gardé octet pour octet : le logiciel ne le réécrit pas.
    assert chemin_json(arc).read_bytes() == contenu

    derniere = arc.historique(ID_PAGE)[-1]
    assert derniere.nature == "modification externe"
    assert derniere.auteur == "hors logiciel"
    assert "hors du logiciel ou enregistrement interrompu" in derniere.motif
    assert "page.json daté du " in derniere.motif
    assert {m.champ: (m.ancien, m.nouveau) for m in derniere.modifications} == {
        "mesures.activite.valeur": (100.0, 999.0)
    }
    # L'ancienne valeur reste lisible dans l'historique.
    ancienne = arc.version_page(ID_PAGE, arc.historique(ID_PAGE)[-2].commit)
    assert ancienne.grandeur("activite").valeur == 100.0


def test_une_page_changee_en_plusieurs_fichiers_donne_un_seul_commit(arc):
    changer_activite(arc, 999.0)
    csv = arc.emplacement_page(ID_PAGE) / "brut" / "t.csv"
    csv.write_text("act\n1.0\n7.0\n", encoding="utf-8")
    avant = len(arc._depot.commits())
    assert arc.enregistrer_modifications_externes() == [
        f"pages/{ID_PAGE}/brut/t.csv",
        f"pages/{ID_PAGE}/page.json",
    ]
    assert len(arc._depot.commits()) == avant + 1
    champs = {m.champ for m in arc.historique(ID_PAGE)[-1].modifications}
    assert champs == {"mesures.activite.valeur", "mesures.t.act[2]"}


def test_un_csv_enregistre_en_latin1_par_un_tableur_ne_casse_pas_l_historique(arc):
    csv = arc.emplacement_page(ID_PAGE) / "brut" / "t.csv"
    csv.write_bytes("act\n1.0\n7,0 é\n".encode("latin-1"))
    arc.enregistrer_modifications_externes()
    assert arc.historique(ID_PAGE)[-1].nature == "modification externe"
    assert arc.modifications_non_tracees() == []


def test_un_page_json_illisible_est_enregistre_sans_casser_l_historique(arc):
    chemin_json(arc).write_bytes("{ abîmé".encode("latin-1"))
    arc.enregistrer_modifications_externes()
    assert arc.modifications_non_tracees() == []
    derniere = arc.historique(ID_PAGE)[-1]
    assert derniere.nature == "modification externe"
    assert derniere.modifications == ()
    assert [p.id for p in arc.pages_illisibles()] == [ID_PAGE]


def test_un_nom_de_champ_sur_deux_lignes_ne_peut_pas_imiter_une_correction(arc):
    contenu = json.loads(chemin_json(arc).read_text(encoding="utf-8"))
    contenu["x\nNature: correction"] = 1
    chemin_json(arc).write_text(json.dumps(contenu), encoding="utf-8")
    arc.enregistrer_modifications_externes()
    assert arc.historique(ID_PAGE)[-1].nature == "modification externe"


def test_une_page_supprimee_a_la_main_garde_son_ancienne_version(arc):
    shutil.rmtree(arc.emplacement_page(ID_PAGE))
    assert set(arc.enregistrer_modifications_externes()) == {
        f"pages/{ID_PAGE}/page.json",
        f"pages/{ID_PAGE}/brut/t.csv",
    }
    assert arc.identifiants() == []
    entrees = arc.historique(ID_PAGE)
    assert entrees[-1].nature == "modification externe"
    assert "page.json supprimé" in entrees[-1].motif
    version = arc.version_page(ID_PAGE, entrees[-2].commit)
    assert version.grandeur("activite").valeur == 100.0


def test_un_fichier_hors_page_reste_rattache_a_son_nom(arc):
    chemin = arc.racine / "archive.toml"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert arc.enregistrer_modifications_externes() == ["archive.toml"]
    message = arc._depot.commits()[-1].message.decode("utf-8")
    assert message.startswith("modification externe : archive.toml\n")
    assert "Nature: modification externe" in message


def test_relire_une_archive_depuis_un_script_n_ecrit_rien(arc):
    changer_activite(arc, 999.0)
    avant = len(arc._depot.commits())
    relue = Archive(arc.racine)
    assert relue.lire_page(ID_PAGE).grandeur("activite").valeur == 999.0
    assert len(relue._depot.commits()) == avant
    assert relue.modifications_non_tracees() == [f"pages/{ID_PAGE}/page.json"]


def test_la_correction_a_la_main_d_une_page_validee_compte_comme_correction(arc):
    arc.valider_page(ID_PAGE)
    changer_activite(arc, 999.0)
    arc.enregistrer_modifications_externes()
    # Le statut du fichier n'est pas réécrit : c'est l'historique qui dit ce qui s'est passé.
    assert arc.lire_page(ID_PAGE).statut == "validée"
    assert [c.nature for c in arc.corrections(ID_PAGE)] == ["modification externe"]
    assert [m.nature for m in arc.modifications_externes(ID_PAGE)] == [
        "modification externe"
    ]


def test_apres_la_version_automatique_on_peut_de_nouveau_enregistrer(arc):
    changer_activite(arc, 999.0)
    arc.enregistrer_modifications_externes()
    page = arc.lire_page(ID_PAGE).model_copy(update={"titre": "Autre titre"})
    assert arc.modifier_page(page)
    assert arc.historique(ID_PAGE)[-1].nature == "enregistrement"


def test_l_export_signale_une_page_modifiee_hors_du_logiciel(arc):
    assert "hors du logiciel" not in page_en_html(arc, ID_PAGE, SansImages())
    changer_activite(arc, 999.0)
    arc.enregistrer_modifications_externes()
    html = page_en_html(arc, ID_PAGE, SansImages())
    assert "Page modifiée hors du logiciel" in html


# --- Fenêtre ---------------------------------------------------------------------------


@pytest.fixture
def fenetre(qtbot):
    resultat = FenetrePrincipale()
    qtbot.addWidget(resultat)
    yield resultat
    resultat.espace_archive.arreter()


def ouvrir(fenetre, chemin, qtbot):
    assert fenetre.ouvrir_archive(chemin)
    qtbot.waitUntil(fenetre.espace_archive.au_repos, timeout=30000)


def test_ouvrir_le_logiciel_cree_la_version_et_le_signale(fenetre, arc, qtbot):
    changer_activite(arc, 999.0)
    ouvrir(fenetre, arc.racine, qtbot)
    espace = fenetre.espace_archive

    relue = Archive(arc.racine)
    assert relue.modifications_non_tracees() == []
    assert relue.historique(ID_PAGE)[-1].nature == "modification externe"

    assert espace.versions_automatiques == [f"pages/{ID_PAGE}/page.json"]
    assert espace.non_tracees == []
    assert not espace.bandeau.isHidden()
    assert "nouvelle version créée à l'ouverture" in espace.alerte.text()
    assert f"pages/{ID_PAGE}/page.json" in espace.texte_details()
    # Plus rien à reprendre à la main.
    assert espace.bouton_reprise.isHidden()


def test_ouvrir_une_archive_saine_n_affiche_rien_et_ne_cree_rien(fenetre, arc, qtbot):
    avant = len(arc._depot.commits())
    ouvrir(fenetre, arc.racine, qtbot)
    assert len(Archive(arc.racine)._depot.commits()) == avant
    assert fenetre.espace_archive.bandeau.isHidden()
    assert fenetre.espace_saisie.isEnabled()


def test_la_saisie_attend_la_fin_de_la_verification(fenetre, arc, qtbot):
    assert fenetre.ouvrir_archive(arc.racine)
    assert not fenetre.espace_saisie.isEnabled()
    assert fenetre.statusBar().currentMessage().startswith("Vérification")
    fenetre._modifier_page(ID_PAGE)
    assert fenetre.espace_saisie.page is None
    qtbot.waitUntil(fenetre.espace_saisie.isEnabled, timeout=30000)
    qtbot.waitUntil(fenetre.espace_archive.au_repos, timeout=30000)
    assert fenetre.statusBar().currentMessage().endswith("A")
    fenetre._modifier_page(ID_PAGE)
    assert fenetre.espace_saisie.page.id == ID_PAGE


def test_une_modification_en_cours_de_session_reste_a_reprendre_avec_un_motif(
    fenetre, arc, qtbot
):
    ouvrir(fenetre, arc.racine, qtbot)
    changer_activite(arc, 999.0)
    espace = fenetre.espace_archive
    espace.actualiser()
    qtbot.waitUntil(espace.au_repos, timeout=30000)
    assert espace.versions_automatiques == []
    assert espace.non_tracees == [f"pages/{ID_PAGE}/page.json"]
    assert not espace.bouton_reprise.isHidden()


def test_la_fiche_signale_une_page_validee_modifiee_et_barre_l_ancienne_valeur(
    fenetre, arc, qtbot
):
    arc.valider_page(ID_PAGE)
    changer_activite(arc, 999.0)
    ouvrir(fenetre, arc.racine, qtbot)
    fiche = fenetre.espace_archive.fiche
    fenetre.espace_archive.afficher_page(ID_PAGE)
    assert fiche.page.statut == "validée"
    assert "Page modifiée hors du logiciel" in fiche.alerte_externe.text()
    assert fiche.corrections["mesures.activite.valeur"][0].ancien == 100.0


def test_la_fiche_ne_signale_rien_sans_modification_externe(fenetre, arc, qtbot):
    ouvrir(fenetre, arc.racine, qtbot)
    fenetre.espace_archive.afficher_page(ID_PAGE)
    assert fenetre.espace_archive.fiche.alerte_externe is None


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="droits POSIX")
def test_une_archive_en_lecture_seule_reste_signalee(fenetre, arc, qtbot):
    changer_activite(arc, 999.0)
    dossier_objets = arc.racine / ".git" / "objects"
    droits = dossier_objets.stat().st_mode
    dossier_objets.chmod(0o500)
    try:
        ouvrir(fenetre, arc.racine, qtbot)
    finally:
        dossier_objets.chmod(droits)
    espace = fenetre.espace_archive
    assert espace.versions_automatiques == []
    assert espace.non_tracees == [f"pages/{ID_PAGE}/page.json"]
    assert "Nouvelle version automatique impossible" in espace.alerte.text()
    assert fenetre.espace_saisie.isEnabled()


# --- Chemin d'accès --------------------------------------------------------------------


@pytest.fixture
def espace(qtbot, arc):
    resultat = EspaceArchive()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    qtbot.waitUntil(resultat.au_repos, timeout=30000)
    yield resultat
    resultat.arreter()


def test_le_menu_d_une_page_copie_son_chemin(espace, arc, qtbot):
    fenetre = QMainWindow()
    qtbot.addWidget(fenetre)
    fenetre.setCentralWidget(espace)
    menu = espace.creer_menu_page(ID_PAGE)
    assert [a.text() for a in menu.actions()] == ["Copier le chemin d'accès"]
    menu.actions()[0].trigger()
    copie = QGuiApplication.clipboard().text()
    assert copie == str((arc.racine / "pages" / ID_PAGE).resolve())
    assert os.path.isabs(copie)
    assert os.path.isfile(os.path.join(copie, "page.json"))
    assert fenetre.statusBar().currentMessage() == f"Chemin copié : {copie}"
    fenetre.takeCentralWidget()  # La fixture arrête elle-même cet espace.


def test_le_clic_droit_ne_propose_rien_hors_d_une_page(espace, monkeypatch):
    appels = []
    monkeypatch.setattr(espace, "creer_menu_page", appels.append)
    espace._ouvrir_menu_page(espace.liste.viewport().rect().bottomRight())
    assert appels == []


def test_le_clic_droit_sur_une_ligne_ouvre_le_menu_de_sa_page(espace, monkeypatch):
    espace.liste.resize(600, 300)
    position = espace.liste.visualRect(espace.modele.index(0, 1)).center()

    class MenuFactice:
        def exec(self, _position):
            pass

        def deleteLater(self):  # noqa: N802
            pass

    appels = []

    def creer(id_page):
        appels.append(id_page)
        return MenuFactice()

    monkeypatch.setattr(espace, "creer_menu_page", creer)
    espace._ouvrir_menu_page(position)
    assert appels == [ID_PAGE]
