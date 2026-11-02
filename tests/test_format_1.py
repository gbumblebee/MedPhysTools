"""Vérifie que l'archive figée du format 1 se relit toujours. Elle ne se régénère pas.

Si ces tests échouent, c'est le code qu'il faut corriger, ou une migration qu'il faut écrire.
"""

import zipfile
from pathlib import Path

import pytest

from pymphub import Archive
from pymphub.calculs import calculer

ZIP = Path(__file__).parent / "donnees" / "archive_format_1.zip"
ID_PAGE = "2026-11-14_activimetre-linearite_0001"


@pytest.fixture
def arc(tmp_path):
    with zipfile.ZipFile(ZIP) as archive_zip:
        archive_zip.extractall(tmp_path)
    return Archive(tmp_path / "ArchiveFormat1")


def test_toutes_les_pages_se_relisent(arc):
    identifiants = arc.identifiants()
    assert ID_PAGE in identifiants
    for identifiant in identifiants:
        assert arc.lire_page(identifiant).id == identifiant


def test_rien_n_apparait_comme_modifie(arc):
    assert arc.modifications_non_tracees() == []


def test_les_valeurs_et_l_historique_se_relisent(arc):
    lue = arc.page(ID_PAGE)
    assert lue.mesures["activite_ref"].valeur == 100.0
    assert lue.mesures["lot"].valeur == "007"
    assert lue.tableau("decroissance")["activite"].iloc[0] == 100.0
    assert lue.statut == "corrigée"
    assert len(lue.historique()) >= 2


def test_une_page_ancienne_se_modifie_et_passe_au_format_courant(arc):
    page = arc.lire_page(ID_PAGE)
    assert arc.modifier_page(page.model_copy(update={"titre": "Autre"}), "Essai")
    assert arc.lire_page(ID_PAGE).titre == "Autre"


def test_un_calcul_marche_sur_une_page_ancienne(arc):
    page = arc.lire_page(ID_PAGE)
    budget = calculer(page, "activite_ref * 2")
    assert budget.valeur == 200.0
    assert budget.unite == "MBq"
