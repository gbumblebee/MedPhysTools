"""Vérifie le socle de l'application : démarrage, création et ouverture d'une archive."""

import importlib.metadata
import os
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtCore import QStandardPaths, Qt

from medphystools import Archive
from medphystools.archive import VERSION_FORMAT
from medphystools.interface.application import main
from medphystools.interface.fenetre import (
    DescriptionArchive,
    DialogueNouvelleArchive,
    FenetrePrincipale,
)
from medphystools.interface.preferences import (
    derniere_archive,
    dossier_preferences,
    memoriser_derniere_archive,
)
from medphystools.interface.theme import appliquer_theme


@pytest.fixture
def fenetre(qtbot, monkeypatch):
    """Une fenêtre dont les messages d'erreur sont relevés au lieu de bloquer le test."""
    resultat = FenetrePrincipale()
    qtbot.addWidget(resultat)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    return resultat


def description(tmp_path, nom="ArchiveStage1"):
    return DescriptionArchive(tmp_path / nom, "CHU X", "G.", date(2026, 11, 1))


def test_au_demarrage_l_accueil_s_affiche(fenetre):
    assert fenetre.archive is None
    assert fenetre.pile.currentWidget() is fenetre.accueil
    assert fenetre.statusBar().currentMessage() == "Aucune archive ouverte"


def test_l_application_a_deux_espaces(fenetre):
    noms = [fenetre.espaces.tabText(i) for i in range(fenetre.espaces.count())]
    assert noms == ["Archive", "Saisie"]


def test_creer_une_archive(fenetre, tmp_path, qtbot):
    with qtbot.waitSignal(fenetre.archive_ouverte) as signal:
        assert fenetre.creer_archive(description(tmp_path))
    assert (tmp_path / "ArchiveStage1" / "archive.toml").is_file()
    assert signal.args[0] is fenetre.archive
    assert fenetre.archive.configuration.site == "CHU X"
    assert fenetre.pile.currentWidget() is fenetre.espaces
    assert "ArchiveStage1" in fenetre.windowTitle()
    # La barre d'état montre l'emplacement une fois les fichiers vérifiés.
    qtbot.waitUntil(fenetre.espace_archive.au_repos, timeout=30000)
    assert str(tmp_path) in fenetre.statusBar().currentMessage()
    assert fenetre.erreurs == []


def test_creer_puis_rouvrir_dans_une_nouvelle_fenetre(fenetre, tmp_path, qtbot):
    fenetre.creer_archive(description(tmp_path))

    autre = FenetrePrincipale()
    qtbot.addWidget(autre)
    assert autre.ouvrir_archive(tmp_path / "ArchiveStage1")
    assert autre.archive.configuration.responsable == "G."
    assert autre.pile.currentWidget() is autre.espaces


def test_le_cœur_voit_l_archive_creee_par_l_interface(fenetre, tmp_path):
    fenetre.creer_archive(description(tmp_path))
    assert Archive(tmp_path / "ArchiveStage1").modifications_non_tracees() == []


def test_un_dossier_ordinaire_affiche_un_message_clair(fenetre, tmp_path):
    assert not fenetre.ouvrir_archive(tmp_path)
    assert len(fenetre.erreurs) == 1
    assert "pas une archive" in fenetre.erreurs[0]
    assert fenetre.archive is None
    assert fenetre.pile.currentWidget() is fenetre.accueil


def test_un_dossier_absent_affiche_un_message_clair(fenetre, tmp_path):
    assert not fenetre.ouvrir_archive(tmp_path / "absent")
    assert fenetre.erreurs


def ecrire_configuration(fenetre, tmp_path, texte):
    fenetre.creer_archive(description(tmp_path, "A"))
    (tmp_path / "A" / "archive.toml").write_text(texte, encoding="utf-8")
    fenetre.archive = None
    return tmp_path / "A"


def test_une_archive_trop_recente_affiche_un_message_clair(fenetre, tmp_path):
    chemin = ecrire_configuration(
        fenetre, tmp_path, f"version_format = {VERSION_FORMAT + 1}\n"
    )
    assert not fenetre.ouvrir_archive(chemin)
    assert "mettre à jour" in fenetre.erreurs[0]


@pytest.mark.parametrize("texte", ["pas du TOML", "version_format = 1\n", ""])
def test_une_archive_invalide_affiche_un_message_clair(fenetre, tmp_path, texte):
    chemin = ecrire_configuration(fenetre, tmp_path, texte)
    assert not fenetre.ouvrir_archive(chemin)
    assert len(fenetre.erreurs) == 1


def test_un_echec_laisse_l_archive_ouverte_en_place(fenetre, tmp_path):
    fenetre.creer_archive(description(tmp_path))
    ouverte = fenetre.archive
    titre = fenetre.windowTitle()
    assert not fenetre.ouvrir_archive(tmp_path)
    assert fenetre.archive is ouverte
    assert fenetre.windowTitle() == titre
    assert fenetre.pile.currentWidget() is fenetre.espaces


def test_creer_sur_un_dossier_existant_affiche_un_message(fenetre, tmp_path):
    (tmp_path / "ArchiveStage1").mkdir()
    assert not fenetre.creer_archive(description(tmp_path))
    assert "existe déjà" in fenetre.erreurs[0]
    assert fenetre.archive is None


@pytest.mark.parametrize("nom", ["CON", "archive:1", "archive."])
def test_un_nom_invalide_sous_windows_affiche_un_message(fenetre, tmp_path, nom):
    assert not fenetre.creer_archive(description(tmp_path, nom))
    assert fenetre.erreurs
    assert not (tmp_path / nom).exists()


def test_le_menu_ouvre_l_archive_choisie(fenetre, tmp_path, monkeypatch):
    Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1))
    monkeypatch.setattr(fenetre, "_choisir_archive", lambda: str(tmp_path / "A"))
    fenetre.action_ouvrir.trigger()
    assert fenetre.archive.configuration.nom == "A"


def test_annuler_le_choix_du_dossier_ne_change_rien(fenetre, monkeypatch):
    monkeypatch.setattr(fenetre, "_choisir_archive", str)
    fenetre.action_ouvrir.trigger()
    assert fenetre.archive is None
    assert fenetre.erreurs == []


def test_le_menu_cree_l_archive_decrite(fenetre, tmp_path, monkeypatch):
    monkeypatch.setattr(
        fenetre, "_decrire_nouvelle_archive", lambda: description(tmp_path)
    )
    fenetre.action_nouvelle.trigger()
    assert fenetre.archive is not None


def test_annuler_la_creation_ne_change_rien(fenetre, monkeypatch):
    monkeypatch.setattr(fenetre, "_decrire_nouvelle_archive", lambda: None)
    fenetre.action_nouvelle.trigger()
    assert fenetre.archive is None


def test_les_boutons_de_l_accueil_declenchent_les_actions(fenetre, qtbot, monkeypatch):
    appels = []
    monkeypatch.setattr(fenetre, "_choisir_archive", lambda: appels.append("o") or "")
    monkeypatch.setattr(
        fenetre, "_decrire_nouvelle_archive", lambda: appels.append("c")
    )
    qtbot.mouseClick(fenetre.accueil.bouton_ouvrir, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(fenetre.accueil.bouton_creer, Qt.MouseButton.LeftButton)
    assert appels == ["o", "c"]


@pytest.fixture
def dialogue(qtbot):
    resultat = DialogueNouvelleArchive()
    qtbot.addWidget(resultat)
    return resultat


def remplir(
    dialogue, dossier="/tmp/a", nom="ArchiveStage1", site="CHU", responsable="G."
):
    dialogue.champ_dossier.setText(dossier)
    dialogue.champ_nom.setText(nom)
    dialogue.champ_site.setText(site)
    dialogue.champ_responsable.setText(responsable)


def test_le_dialogue_exige_tous_les_champs(dialogue):
    assert not dialogue.bouton_creer.isEnabled()
    remplir(dialogue)
    assert dialogue.bouton_creer.isEnabled()
    dialogue.champ_site.setText("   ")
    assert not dialogue.bouton_creer.isEnabled()


def test_le_dialogue_donne_une_description_nettoyee(dialogue):
    remplir(dialogue, site="  CHU X  ", nom=" Stage1 ")
    resultat = dialogue.description()
    assert resultat.site == "CHU X"
    assert resultat.chemin.name == "Stage1"
    assert isinstance(resultat.periode_debut, date)


def test_le_theme_s_applique(qapp):
    appliquer_theme(qapp)
    assert qapp.styleSheet()


def test_la_commande_medphysnotebook_pointe_vers_l_application():
    points = importlib.metadata.entry_points(group="console_scripts")
    point = next(p for p in points if p.name == "medphysnotebook")
    assert point.load() is main


@pytest.mark.skipif(shutil.which("git") is None, reason="Git standard non installé")
def test_l_archive_creee_par_l_interface_est_valide_pour_git(fenetre, tmp_path):
    fenetre.creer_archive(description(tmp_path))
    subprocess.run(
        ["git", "-C", str(tmp_path / "ArchiveStage1"), "fsck", "--strict"], check=True
    )


def test_l_application_demarre_dans_un_processus_neuf(tmp_path):
    Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1))
    code = (
        "import sys\n"
        "from PySide6.QtCore import QTimer\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from medphystools.interface import application\n"
        "sys.argv = ['medphysnotebook', sys.argv[1]]\n"
        "origine = QApplication.exec\n"
        "def lancer(*_):\n"
        "    QTimer.singleShot(50, QApplication.quit)\n"
        "    return origine()\n"
        "QApplication.exec = lancer\n"
        "sys.exit(application.main())\n"
    )
    environnement = {"QT_QPA_PLATFORM": "offscreen"}
    resultat = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "A")],
        capture_output=True,
        text=True,
        env=os.environ | environnement,
        timeout=60,
    )
    assert resultat.returncode == 0, resultat.stderr


def test_la_derniere_archive_ouverte_est_memorisee(fenetre, tmp_path):
    fenetre.creer_archive(description(tmp_path))
    assert derniere_archive() == tmp_path / "ArchiveStage1"


def test_la_derniere_archive_se_rouvre(fenetre, tmp_path, qtbot):
    fenetre.creer_archive(description(tmp_path))
    autre = FenetrePrincipale()
    qtbot.addWidget(autre)
    assert autre.ouvrir_derniere_archive()
    assert autre.archive.configuration.nom == "ArchiveStage1"
    assert autre.pile.currentWidget() is autre.espaces


def test_une_derniere_archive_disparue_affiche_l_accueil_sans_erreur(
    fenetre, tmp_path, qtbot
):
    fenetre.creer_archive(description(tmp_path))
    qtbot.waitUntil(fenetre.espace_archive.au_repos, timeout=30000)
    shutil.rmtree(tmp_path / "ArchiveStage1")
    autre = FenetrePrincipale()
    qtbot.addWidget(autre)
    erreurs = []
    autre.afficher_erreur = erreurs.append
    assert not autre.ouvrir_derniere_archive()
    assert erreurs == []
    assert autre.pile.currentWidget() is autre.accueil
    assert "introuvable" in autre.statusBar().currentMessage()


def test_un_echec_d_ouverture_ne_remplace_pas_la_derniere_archive(fenetre, tmp_path):
    fenetre.creer_archive(description(tmp_path))
    assert not fenetre.ouvrir_archive(tmp_path)
    assert derniere_archive() == tmp_path / "ArchiveStage1"


def test_aucune_derniere_archive_connue(monkeypatch, tmp_path, fenetre):
    monkeypatch.setenv("MEDPHYSNOTEBOOK_CONFIG", str(tmp_path / "vide"))
    assert derniere_archive() is None
    assert not fenetre.ouvrir_derniere_archive()


def test_le_dossier_de_l_ancien_nom_est_repris_une_seule_fois(monkeypatch, tmp_path):
    monkeypatch.delenv("MEDPHYSNOTEBOOK_CONFIG")
    monkeypatch.setattr(QStandardPaths, "writableLocation", lambda _lieu: str(tmp_path))
    ancien = tmp_path / "pymphub"
    ancien.mkdir()
    (ancien / "preferences.toml").write_text(
        'derniere_archive = "/mon/archive"\n', encoding="utf-8"
    )
    (ancien / "imports_personnalises.py").write_text("# mes imports", encoding="utf-8")

    assert dossier_preferences() == tmp_path / "medphysnotebook"
    assert derniere_archive() == Path("/mon/archive")
    assert (dossier_preferences() / "imports_personnalises.py").is_file()
    assert (ancien / "preferences.toml").is_file()

    memoriser_derniere_archive("/autre")
    (ancien / "preferences.toml").write_text(
        'derniere_archive = "/ancienne"\n', encoding="utf-8"
    )
    assert derniere_archive() == Path("/autre")


def test_le_titre_de_la_fenetre_porte_le_nom_de_l_application(fenetre, tmp_path):
    assert fenetre.windowTitle() == "MedPhysNotebook"
    fenetre.creer_archive(description(tmp_path))
    assert fenetre.windowTitle().endswith(" — MedPhysNotebook")


def test_des_preferences_abimees_sont_ignorees(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDPHYSNOTEBOOK_CONFIG", str(tmp_path))
    (tmp_path / "preferences.toml").write_text("pas du TOML [", encoding="utf-8")
    assert derniere_archive() is None
    memoriser_derniere_archive("/une/archive")
    assert derniere_archive() == Path("/une/archive")


def test_des_preferences_impossibles_a_ecrire_ne_bloquent_rien(monkeypatch, tmp_path):
    fichier = tmp_path / "un_fichier"
    fichier.write_text("x", encoding="utf-8")
    monkeypatch.setenv("MEDPHYSNOTEBOOK_CONFIG", str(fichier))
    memoriser_derniere_archive("/une/archive")
    assert derniere_archive() is None


def test_les_tests_n_utilisent_pas_les_vraies_preferences():
    assert "medphysnotebook-tests-" in os.environ["MEDPHYSNOTEBOOK_CONFIG"]


def test_le_lancement_sans_argument_rouvre_la_derniere_archive(tmp_path):
    Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1))
    configuration = tmp_path / "config"
    configuration.mkdir()
    (configuration / "preferences.toml").write_text(
        f'derniere_archive = "{(tmp_path / "A").as_posix()}"\n', encoding="utf-8"
    )
    code = (
        "import sys\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from medphystools.interface import application\n"
        "from medphystools.interface.fenetre import FenetrePrincipale\n"
        "sys.argv = ['medphysnotebook']\n"
        "def lancer(*_):\n"
        "    fenetre = next(w for w in QApplication.topLevelWidgets()"
        " if isinstance(w, FenetrePrincipale))\n"
        "    print(fenetre.archive.configuration.nom)\n"
        "    fenetre.close()\n"
        "    return 0\n"
        "QApplication.exec = lancer\n"
        "sys.exit(application.main())\n"
    )
    resultat = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=os.environ
        | {
            "QT_QPA_PLATFORM": "offscreen",
            "MEDPHYSNOTEBOOK_CONFIG": str(configuration),
        },
        timeout=60,
    )
    assert resultat.returncode == 0, resultat.stderr
    assert resultat.stdout.strip() == "A"


def test_l_application_n_est_pas_necessaire_au_coeur():
    code = "import sys, medphystools; sys.exit('PySide6' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0
