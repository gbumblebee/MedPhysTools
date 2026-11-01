"""Vérifie la traçabilité Git : commits, motifs, historique et modifications externes."""

import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone

import pytest

from pymphub import archive as module_archive
from pymphub.archive import Archive, PageIntrouvable, PageRefusee
from pymphub.depot import CONTENU_GITIGNORE, Depot
from pymphub.gabarit import creer_gabarit
from pymphub.historique import (
    Modification,
    differences,
    lire_message,
    message_commit,
    normaliser_motif,
)
from pymphub.page import Page

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
ID_PAGE = "2026-11-14_activimetre-linearite_0007"
AUTRE_PAGE = "2026-11-15_scanner_0008"

git_disponible = pytest.mark.skipif(
    shutil.which("git") is None, reason="Git standard non installé"
)


def scalaire(valeur=10.5, nom="activite"):
    return {
        "nature": "scalaire",
        "nom": nom,
        "valeur": valeur,
        "unite": "MBq",
        "composantes": [{"type": "B", "valeur": 1.0, "unite": "MBq", "source": "s"}],
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
def validee(arc):
    """Une archive dont la page a été créée, puis validée avec activite = 10,5."""
    arc.creer_page(page(scalaire()))
    arc.valider_page(ID_PAGE)
    return arc


def git(arc, *arguments):
    return subprocess.run(
        ["git", "-C", str(arc.racine), *arguments],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def nombre_commits(arc):
    return len(Depot(arc.racine).commits())


# --- Commits ----------------------------------------------------------------------


def test_la_creation_de_l_archive_est_le_premier_commit(arc):
    commits = Depot(arc.racine).commits()
    assert len(commits) == 1
    assert "création de l'archive A" in commits[0].message.decode()
    assert arc.modifications_non_tracees() == []


def test_creer_une_page_produit_un_commit(arc):
    arc.creer_page(page(scalaire()))
    entrees = arc.historique(ID_PAGE)
    assert [e.nature for e in entrees] == ["création"]
    assert entrees[0].auteur == "G."
    assert entrees[0].motif is None
    assert entrees[0].date.tzinfo is not None


def test_chaque_enregistrement_d_un_brouillon_cree_un_commit_sans_motif(arc):
    arc.creer_page(page(scalaire()))
    assert arc.modifier_page(page(scalaire(11.0))) is True
    assert arc.modifier_page(page(scalaire(12.0))) is True
    entrees = arc.historique(ID_PAGE)
    assert [e.nature for e in entrees] == [
        "création",
        "enregistrement",
        "enregistrement",
    ]
    assert all(e.motif is None for e in entrees)
    assert arc.lire_page(ID_PAGE).statut == "brouillon"


def test_un_enregistrement_sans_changement_ne_cree_pas_de_commit(arc):
    arc.creer_page(page(scalaire()))
    avant = nombre_commits(arc)
    assert arc.modifier_page(page(scalaire())) is False
    assert nombre_commits(arc) == avant


def test_declarer_une_machine_et_un_gabarit_produit_des_commits(arc):
    arc.declarer_machine("chu.mn.tep.ge-discovery.1")
    arc.enregistrer_gabarit(creer_gabarit(page(scalaire()), "g", ["activite"]))
    assert nombre_commits(arc) == 3
    assert arc.modifications_non_tracees() == []


def test_la_nouvelle_page_doit_etre_un_brouillon(arc):
    with pytest.raises(PageRefusee, match="brouillon"):
        arc.creer_page(page(statut="validée"))


# --- Validation et corrections ----------------------------------------------------


def test_validation(arc):
    arc.creer_page(page(scalaire()))
    assert arc.valider_page(ID_PAGE, auteur="Physicien B").statut == "validée"
    assert arc.lire_page(ID_PAGE).statut == "validée"
    derniere = arc.historique(ID_PAGE)[-1]
    assert derniere.nature == "validation"
    assert derniere.auteur == "Physicien B"
    assert derniere.modifications == (Modification("statut", "brouillon", "validée"),)


def test_on_ne_valide_pas_deux_fois(validee):
    with pytest.raises(PageRefusee, match="déjà"):
        validee.valider_page(ID_PAGE)


@pytest.mark.parametrize("motif", [None, "", "   \n "])
def test_une_correction_sans_motif_est_refusee_sur_une_page_validee(validee, motif):
    avant = nombre_commits(validee)
    with pytest.raises(PageRefusee, match="motivée"):
        validee.modifier_page(page(scalaire(11.0), statut="validée"), motif=motif)
    assert validee.lire_page(ID_PAGE).grandeur("activite").valeur == 10.5
    assert nombre_commits(validee) == avant


def test_correction_motivee(validee):
    assert validee.modifier_page(
        page(scalaire(11.0), statut="validée"), motif="Erreur de lecture"
    )
    relue = validee.lire_page(ID_PAGE)
    assert relue.grandeur("activite").valeur == 11.0
    assert relue.statut == "corrigée"

    derniere = validee.historique(ID_PAGE)[-1]
    assert derniere.nature == "correction"
    assert derniere.motif == "Erreur de lecture"


def test_une_page_corrigee_exige_encore_un_motif(validee):
    validee.modifier_page(page(scalaire(11.0), statut="validée"), motif="m1")
    with pytest.raises(PageRefusee, match="motivée"):
        validee.modifier_page(page(scalaire(12.0), statut="corrigée"))
    validee.modifier_page(page(scalaire(12.0), statut="corrigée"), motif="m2")
    assert validee.lire_page(ID_PAGE).statut == "corrigée"


def test_l_ancienne_valeur_est_retrouvee(validee):
    validee.modifier_page(page(scalaire(11.0), statut="validée"), motif="Erreur")
    derniere = validee.historique(ID_PAGE)[-1]
    assert derniere.modifications == (
        Modification("statut", "validée", "corrigée"),
        Modification("mesures.activite.valeur", 10.5, 11.0),
    )
    avant = validee.historique(ID_PAGE)[-2]
    ancienne = validee.version_page(ID_PAGE, avant.commit)
    assert ancienne.grandeur("activite").valeur == 10.5
    assert ancienne.statut == "validée"


def test_version_absente_a_ce_commit(arc):
    premier = Depot(arc.racine).commits()[0].id.decode()
    with pytest.raises(PageIntrouvable):
        arc.version_page(ID_PAGE, premier)


def test_le_statut_ne_se_change_pas_par_modifier_page(arc):
    arc.creer_page(page())
    with pytest.raises(PageRefusee, match="valider_page"):
        arc.modifier_page(page(statut="validée"))


def test_une_page_validee_ne_revient_pas_en_brouillon(validee):
    with pytest.raises(PageRefusee, match="valider_page"):
        validee.modifier_page(page(scalaire(11.0)), motif="retour")


@pytest.mark.parametrize(
    "champs",
    [
        {"auteur": "Autre"},
        {"date_saisie": HEURE + timedelta(hours=1)},
    ],
)
def test_un_champ_fixe_n_est_pas_modifiable(arc, champs):
    arc.creer_page(page())
    with pytest.raises(PageRefusee, match="modifiable"):
        arc.modifier_page(page(**champs))


# --- Retrouver les corrections ----------------------------------------------------


@pytest.fixture
def deux_corrections(validee):
    validee.modifier_page(page(scalaire(11.0), statut="validée"), motif="Lecture")
    validee.creer_page(page(scalaire(), id_page=AUTRE_PAGE))
    validee.valider_page(AUTRE_PAGE)
    validee.modifier_page(
        page(scalaire(9.0), id_page=AUTRE_PAGE, statut="validée", titre="Scanner"),
        motif="Unité oubliée",
    )
    return validee


def test_corrections_d_une_page(deux_corrections):
    corrections = deux_corrections.corrections(ID_PAGE)
    assert [c.motif for c in corrections] == ["Lecture"]
    assert corrections[0].page == ID_PAGE


def test_corrections_de_toute_l_archive(deux_corrections):
    corrections = deux_corrections.corrections()
    assert [(c.page, c.motif) for c in corrections] == [
        (ID_PAGE, "Lecture"),
        (AUTRE_PAGE, "Unité oubliée"),
    ]
    champs = {m.champ for m in corrections[1].modifications}
    assert champs == {"titre", "statut", "mesures.activite.valeur"}


def test_un_brouillon_n_apparait_pas_dans_les_corrections(arc):
    arc.creer_page(page(scalaire()))
    arc.modifier_page(page(scalaire(11.0)))
    assert arc.corrections() == []


def test_historique_d_une_page_absente(arc):
    with pytest.raises(PageIntrouvable):
        arc.historique(ID_PAGE)


def test_le_message_de_commit_nomme_la_page_les_champs_et_le_motif(validee):
    validee.modifier_page(page(scalaire(11.0), statut="validée"), motif="Lecture")
    message = Depot(validee.racine).commits()[-1].message.decode()
    assert message.startswith("correction : Lecture\n")
    assert f"Page: {ID_PAGE}" in message
    assert "mesures.activite.valeur" in message


def test_un_motif_ne_peut_pas_falsifier_les_informations_du_commit(validee):
    validee.modifier_page(
        page(scalaire(11.0), statut="validée"),
        motif="Lecture\nPage: autre\nNature: validation",
    )
    derniere = validee.historique(ID_PAGE)[-1]
    assert derniere.nature == "correction"
    assert derniere.page == ID_PAGE
    assert derniere.motif == "Lecture Page: autre Nature: validation"


def test_l_historique_survit_au_deplacement_de_l_archive(validee, tmp_path):
    destination = tmp_path / "ailleurs"
    shutil.move(validee.racine, destination)
    deplacee = Archive(destination)
    assert [e.nature for e in deplacee.historique(ID_PAGE)] == [
        "création",
        "validation",
    ]
    deplacee.modifier_page(page(scalaire(11.0), statut="validée"), motif="m")
    assert len(deplacee.corrections()) == 1


# --- Comparaison champ par champ --------------------------------------------------


def test_differences_sans_version_precedente():
    assert differences(None, {"a": 1}) == ()


def test_differences_reperent_les_grandeurs_par_leur_nom():
    ancienne = {"mesures": [{"nom": "a", "valeur": 1}, {"nom": "b", "valeur": 2}]}
    nouvelle = {"mesures": [{"nom": "b", "valeur": 3}, {"nom": "a", "valeur": 1}]}
    assert differences(ancienne, nouvelle) == (Modification("mesures.b.valeur", 2, 3),)


def test_differences_signalent_un_ajout_et_un_retrait():
    grandeur = {"nom": "b", "valeur": 2}
    assert differences({"mesures": []}, {"mesures": [grandeur]}) == (
        Modification("mesures.b", None, grandeur),
    )
    assert differences({"mesures": [grandeur]}, {"mesures": []}) == (
        Modification("mesures.b", grandeur, None),
    )


def test_differences_signalent_un_changement_d_ordre():
    ancienne = {"onglets": [{"id": "o1"}, {"id": "o2"}]}
    nouvelle = {"onglets": [{"id": "o2"}, {"id": "o1"}]}
    assert differences(ancienne, nouvelle) == (
        Modification("onglets (ordre)", ["o1", "o2"], ["o2", "o1"]),
    )


def test_differences_dans_une_composante():
    def donnees(valeur):
        return {"composantes": [{"type": "B", "valeur": valeur}]}

    assert differences(donnees(1), donnees(2)) == (
        Modification("composantes[0].valeur", 1, 2),
    )


def test_differences_dans_une_liste_de_textes():
    assert differences({"tags": ["a"]}, {"tags": ["a", "b"]}) == (
        Modification("tags", ["a"], ["a", "b"]),
    )


def test_lire_message_relit_un_message_compose():
    modifications = (Modification("titre", "a", "b"),)
    message = message_commit("correction", "p", "Mon motif", modifications)
    assert lire_message(message) == {
        "Nature": "correction",
        "Page": "p",
        "Champs": "titre",
        "Motif": "Mon motif",
    }


@pytest.mark.parametrize(
    ("motif", "attendu"),
    [(None, None), ("", None), (" \n ", None), (" a \n b ", "a b")],
)
def test_normaliser_motif(motif, attendu):
    assert normaliser_motif(motif) == attendu


# --- Compatibilité avec Git standard ----------------------------------------------


@git_disponible
def test_git_standard_relit_l_historique(deux_corrections):
    sortie = git(deux_corrections, "log", "--format=%an|%s")
    assert "G.|correction : Lecture" in sortie
    assert "Resp.|création de l'archive A" in sortie


@git_disponible
def test_git_fsck_ne_signale_rien(deux_corrections):
    git(deux_corrections, "fsck", "--strict")


@git_disponible
def test_git_status_est_propre(deux_corrections):
    assert git(deux_corrections, "status", "--porcelain") == ""


@git_disponible
def test_les_fichiers_lourds_restent_hors_de_git(arc):
    dossier = arc.creer_page(page())
    (dossier / "pieces_jointes").mkdir()
    (dossier / "pieces_jointes" / "photo.jpg").write_bytes(b"lourd")
    (dossier / "brut").mkdir()
    (dossier / "brut" / "serie.dcm").write_bytes(b"lourd")
    (dossier / "brut" / "releve.csv").write_text("a\n1\n", encoding="utf-8")
    (arc.racine / "objets" / "ab").mkdir()
    (arc.racine / "objets" / "ab" / "cd.dcm.zip").write_bytes(b"lourd")
    (arc.racine / "index.sqlite").write_bytes(b"cache")

    assert git(arc, "status", "--porcelain", "--untracked-files=all").split() == [
        "??",
        f"pages/{ID_PAGE}/brut/releve.csv",
    ]


def test_gitignore_est_suivi(arc):
    assert (arc.racine / ".gitignore").read_text(encoding="utf-8") == CONTENU_GITIGNORE
    assert ".gitignore" in Depot(arc.racine).fichiers_suivis()


def test_configuration_git_pour_windows(arc):
    configuration = (arc.racine / ".git" / "config").read_text(encoding="utf-8")
    assert "longpaths = true" in configuration
    assert "autocrlf = false" in configuration


# --- Modifications hors du logiciel -----------------------------------------------


def alterer(arc, id_page=ID_PAGE, **champs):
    chemin = arc.racine / "pages" / id_page / "page.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    contenu.update(champs)
    chemin.write_text(json.dumps(contenu), encoding="utf-8")


def test_une_archive_saine_n_a_pas_de_modification_non_tracee(deux_corrections):
    assert deux_corrections.modifications_non_tracees() == []


def test_une_modification_a_la_main_est_signalee(arc):
    arc.creer_page(page())
    alterer(arc, titre="Modifié à la main")
    assert arc.modifications_non_tracees() == [f"pages/{ID_PAGE}/page.json"]


def test_une_modification_a_la_main_n_est_pas_ecrasee(arc):
    arc.creer_page(page())
    alterer(arc, titre="Modifié à la main")
    with pytest.raises(PageRefusee, match="hors du logiciel"):
        arc.modifier_page(page(titre="Autre"))
    with pytest.raises(PageRefusee, match="hors du logiciel"):
        arc.valider_page(ID_PAGE)
    assert arc.lire_page(ID_PAGE).titre == "Modifié à la main"


def test_une_page_supprimee_est_signalee(arc):
    dossier = arc.creer_page(page())
    shutil.rmtree(dossier)
    assert arc.modifications_non_tracees() == [f"pages/{ID_PAGE}/page.json"]


def test_un_fichier_de_configuration_modifie_est_signale(arc):
    chemin = arc.racine / "archive.toml"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert arc.modifications_non_tracees() == ["archive.toml"]


def test_reprise_motivee(arc):
    arc.creer_page(page())
    alterer(arc, titre="Modifié à la main")
    assert arc.enregistrer_reprise("Faute corrigée à la main") == [
        f"pages/{ID_PAGE}/page.json"
    ]
    assert arc.modifications_non_tracees() == []

    derniere = arc.historique(ID_PAGE)[-1]
    assert derniere.nature == "reprise"
    assert derniere.motif == "Faute corrigée à la main"
    assert derniere.modifications == (
        Modification("titre", "Linéarité", "Modifié à la main"),
    )
    arc.modifier_page(page(titre="Autre"))
    assert arc.lire_page(ID_PAGE).titre == "Autre"


def test_reprise_sans_motif_refusee(arc):
    arc.creer_page(page())
    alterer(arc, titre="x")
    with pytest.raises(PageRefusee, match="motif"):
        arc.enregistrer_reprise("  ")
    assert arc.modifications_non_tracees() != []


def test_reprise_d_une_page_supprimee_garde_l_ancienne_version(arc):
    dossier = arc.creer_page(page())
    creation = arc.historique(ID_PAGE)[0].commit
    shutil.rmtree(dossier)
    arc.enregistrer_reprise("Suppression d'une page de test")
    assert arc.modifications_non_tracees() == []
    assert arc.version_page(ID_PAGE, creation).titre == "Linéarité"


def test_reprise_d_une_page_illisible_ne_casse_pas_l_historique(arc):
    dossier = arc.creer_page(page())
    (dossier / "page.json").write_text("{ pas du JSON", encoding="utf-8")
    arc.enregistrer_reprise("Fichier abîmé, à restaurer")
    derniere = arc.historique(ID_PAGE)[-1]
    assert derniere.nature == "reprise"
    assert derniere.modifications == ()


def test_commit_interrompu_detecte_puis_repris(arc, monkeypatch):
    def echec(*_, **__):
        raise OSError("coupure")

    arc.creer_page(page(scalaire()))
    monkeypatch.setattr(module_archive.Depot, "enregistrer", echec)
    with pytest.raises(OSError):
        arc.modifier_page(page(scalaire(11.0)))
    monkeypatch.undo()

    assert arc.modifications_non_tracees() == [f"pages/{ID_PAGE}/page.json"]
    with pytest.raises(PageRefusee, match="hors du logiciel"):
        arc.modifier_page(page(scalaire(12.0)))
    arc.enregistrer_reprise("Enregistrement interrompu par une coupure")
    arc.modifier_page(page(scalaire(12.0)))
    assert arc.lire_page(ID_PAGE).grandeur("activite").valeur == 12.0
