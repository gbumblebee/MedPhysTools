"""Vérifie la sortie de phase : scénario complet, archive figée et matrice d'exigences."""

import math
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from GTC import log, uncertainty
from scenario_activimetre import (
    ID_PAGE,
    ID_SUIVANTE,
    MACHINE,
    REFERENCE_GABARIT,
    construire,
)

from medphystools import Archive
from medphystools.archive import VERSION_FORMAT
from medphystools.fichiers import ReferenceNonResolue
from medphystools.gabarit import verifier_conformite

ICI = Path(__file__).parent
MATRICE = ICI.parent / "docs" / "matrice-exigences.md"


@pytest.fixture(scope="module")
def arc(tmp_path_factory):
    dossier = tmp_path_factory.mktemp("scenario")
    return construire(dossier / "ArchiveStage1", dossier / "travail")


@pytest.fixture(scope="module")
def p(arc):
    return arc.page(ID_PAGE)


# --- Scénario complet ---------------------------------------------------------------


def test_la_page_libre_a_plusieurs_onglets(p):
    assert [o.id for o in p.onglets] == [
        "montage",
        "scalaires",
        "releve",
        "courbe",
        "image",
        "spectre",
    ]
    assert p.onglets[0].photos[0].annotations[0].texte == "source"


def test_les_incertitudes_de_type_a_et_b_se_combinent(p):
    # s/√5 = 0,0707 ; certificat 1/2 = 0,5 ; u = √(0,005 + 0,25) = 0,50498
    mesure = p.mesures["activite_ref"]
    assert mesure.valeur == pytest.approx(100.0)
    assert mesure.budget.incertitude_type == pytest.approx(0.50498, abs=1e-5)
    assert mesure.elargie_relative() == pytest.approx(1.00995, abs=1e-5)


def test_le_repere_d_erreur_commune_est_conserve(p):
    composantes = p.contenu.grandeur("activite_ref").composantes
    assert composantes[1].erreur_commune == "certificat-fictif-001"


def test_propagation_jusqu_a_la_periode_radioactive(p):
    # T = Δt · ln 2 / ln(A0 / A1), comparé à un calcul à la main au premier ordre.
    t0 = p.cellule("decroissance", "heure", 0)
    t1 = p.cellule("decroissance", "heure", 3)
    a0 = p.cellule("decroissance", "activite", 0)
    a1 = p.cellule("decroissance", "activite", 3)
    periode = (
        (t1.incertitude - t0.incertitude)
        * math.log(2)
        / log(a0.incertitude / a1.incertitude)
    )

    delta_t = t1.valeur - t0.valeur
    rapport = math.log(a0.valeur / a1.valeur)
    attendue = delta_t * math.log(2) / rapport
    u_t = 1 / 60
    relative_carree = (2 * u_t**2) / delta_t**2 + (
        (0.2 / a0.valeur) ** 2 + (0.2 / a1.valeur) ** 2
    ) / rapport**2

    assert periode.x == pytest.approx(attendue)
    assert periode.x == pytest.approx(6.0067, abs=0.01)
    assert uncertainty(periode) == pytest.approx(attendue * math.sqrt(relative_carree))


def test_la_correction_est_retrouvee_avec_son_motif(arc):
    corrections = arc.corrections(ID_PAGE)
    assert [c.motif for c in corrections] == [
        "Température mal relevée",
        "Rattachement au gabarit créé depuis cette page",
    ]
    champs = {m.champ: m for m in corrections[0].modifications}
    assert champs["mesures.temperature.valeur"].ancien == 21.5
    assert champs["mesures.temperature.valeur"].nouveau == 22.0


def test_l_historique_de_la_page_est_complet(arc):
    assert [e.nature for e in arc.historique(ID_PAGE)] == [
        "création",
        "enregistrement",
        "validation",
        "correction",
        "correction",
    ]


def test_le_gabarit_garde_les_variables_sans_les_donnees(arc):
    gabarit = arc.lire_gabarit(REFERENCE_GABARIT)
    assert [v.nom for v in gabarit.variables] == ["activite_ref", "lot", "decroissance"]
    texte = (arc.racine / "gabarits" / f"{REFERENCE_GABARIT}.toml").read_text(
        encoding="utf-8"
    )
    assert "100.0" not in texte
    assert "certificat" not in texte


def test_les_deux_pages_sont_conformes_au_gabarit(arc):
    gabarit = arc.lire_gabarit(REFERENCE_GABARIT)
    assert verifier_conformite(arc.lire_page(ID_PAGE), gabarit) == []
    assert verifier_conformite(arc.lire_page(ID_SUIVANTE), gabarit) == []


def test_la_recherche_retrouve_les_deux_cq(arc):
    assert arc.chercher_identifiants(gabarit="activimetre.linearite") == [
        ID_SUIVANTE,
        ID_PAGE,
    ]
    assert arc.chercher_identifiants(machine=MACHINE, depuis="2026-12-01") == [
        ID_SUIVANTE
    ]
    assert arc.chercher_identifiants(texte="flacon") == [ID_PAGE]
    assert arc.chercher_identifiants(statut="corrigée") == [ID_PAGE]


def test_les_fichiers_sont_verifies(p):
    assert b"export constructeur" in p.fichiers["export"].read_bytes()
    with pytest.raises(ReferenceNonResolue):
        p.fichiers["serie_ct"]


def test_l_archive_ne_contient_aucune_modification_non_tracee(arc):
    assert arc.modifications_non_tracees() == []


def test_critere_de_passage_sur_une_archive_copiee_ailleurs(arc, tmp_path):
    copie = tmp_path / "cle_usb" / "ArchiveStage1"
    shutil.copytree(arc.racine, copie)
    script = tmp_path / "script_neuf.py"
    script.write_text(
        "from medphystools import Archive\n"
        f"print(Archive({str(copie)!r}).page({ID_PAGE!r}).mesures['activite_ref'].valeur)\n",
        encoding="utf-8",
    )
    resultat = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=True
    )
    assert float(resultat.stdout) == pytest.approx(100.0)


# --- Archive figée du format 1 ------------------------------------------------------


def extraire(numero, destination):
    with zipfile.ZipFile(ICI / "donnees" / f"archive_format_{numero}.zip") as zip_:
        zip_.extractall(destination)
    return destination / f"ArchiveFormat{numero}"


@pytest.fixture
def figee(tmp_path):
    return Archive(extraire(1, tmp_path))


@pytest.mark.parametrize("numero", range(1, VERSION_FORMAT + 1))
def test_chaque_version_du_format_a_son_archive_figee(numero, tmp_path):
    arc_figee = Archive(extraire(numero, tmp_path))
    assert arc_figee.configuration.version_format == numero
    assert arc_figee.modifications_non_tracees() == []
    for identifiant in arc_figee.identifiants():
        arc_figee.page(identifiant)


def test_l_archive_figee_se_relit_a_l_identique(figee):
    assert figee.identifiants() == [ID_PAGE, ID_SUIVANTE]
    p = figee.page(ID_PAGE)
    assert p.statut == "corrigée"
    assert p.gabarit == REFERENCE_GABARIT
    assert p.mesures["activite_ref"].valeur == pytest.approx(100.0)
    assert p.mesures["activite_ref"].elargie() == pytest.approx(1.00995, abs=1e-5)
    assert p.mesures["temperature"].valeur == 22.0
    assert p.mesures["temperature"].en("K").valeur == pytest.approx(295.15)
    assert p.mesures["lot"].valeur == "007"
    assert p.tableau("decroissance")["lot"].tolist() == ["007"] * 4
    assert p.tableau("decroissance")["activite"].tolist() == [
        100.0,
        79.39,
        63.03,
        50.04,
    ]
    assert p.cellule(
        "decroissance", "activite", 0
    ).budget.incertitude_type == pytest.approx(0.2)
    assert (
        p.fichiers["export"].read_text(encoding="utf-8")
        == "export constructeur fictif\n"
    )


def test_l_onglet_inconnu_de_l_archive_figee_est_conserve(figee):
    onglet = figee.page(ID_PAGE).onglets[-1]
    assert onglet.type == "spectre_gamma"
    assert onglet.pics_keV == [140.5]


def test_l_historique_de_l_archive_figee_est_lisible(figee):
    assert [c.motif for c in figee.corrections(ID_PAGE)] == [
        "Température mal relevée",
        "Rattachement au gabarit créé depuis cette page",
    ]
    creation = figee.historique(ID_PAGE)[0]
    assert (
        figee.version_page(ID_PAGE, creation.commit).grandeur("temperature").valeur
        == 21.5
    )


def test_la_recherche_fonctionne_sur_l_archive_figee(figee):
    assert not figee.index_a_jour()
    assert figee.chercher_identifiants(tags=["Tc99m"]) == [ID_PAGE]


def test_l_archive_figee_accepte_encore_une_correction(figee):
    page = figee.lire_page(ID_SUIVANTE)
    figee.modifier_page(
        page.model_copy(update={"titre": "Titre corrigé"}), motif="Essai"
    )
    assert figee.lire_page(ID_SUIVANTE).statut == "corrigée"


@pytest.mark.skipif(shutil.which("git") is None, reason="Git standard non installé")
def test_git_standard_relit_l_archive_figee(figee):
    subprocess.run(["git", "-C", str(figee.racine), "fsck", "--strict"], check=True)


# --- Matrice exigences ↔ tests ------------------------------------------------------


def references_de_la_matrice():
    return re.findall(r"`(tests/\w+\.py)::(\w+)`", MATRICE.read_text(encoding="utf-8"))


def test_la_matrice_cite_des_tests():
    assert len(references_de_la_matrice()) > 40


@pytest.mark.parametrize(("fichier", "test"), references_de_la_matrice())
def test_chaque_test_cite_dans_la_matrice_existe(fichier, test):
    contenu = (ICI.parent / fichier).read_text(encoding="utf-8")
    assert re.search(rf"^def {test}\(", contenu, flags=re.MULTILINE)
