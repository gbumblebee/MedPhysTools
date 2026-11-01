"""Vérifie l'API de lecture, dont l'exemple de la section 7 du cahier des charges."""

import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest
from GTC import uncertainty
from GTC.lib import UncertainReal

import pymphub
from pymphub import Archive
from pymphub.fichiers import FichierAltere
from pymphub.page import Identifiant, Page, Tableau

CAHIER = Path(__file__).parent.parent / "docs" / "cahier-des-charges.md"
HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
ID_PAGE = "2026-11-14_activimetre-linearite_0007"


def scalaire(nom="activite", valeur=100.0, u=1.0, unite="MBq"):
    return {
        "nature": "scalaire",
        "nom": nom,
        "valeur": valeur,
        "unite": unite,
        "composantes": [
            {"type": "B", "valeur": u, "unite": unite, "source": "certificat"}
        ],
        "commentaire": "au centre",
        "horodatage": HEURE,
    }


def description_tableau():
    return {
        "nature": "tableau",
        "nom": "decroissance",
        "fichier": "brut/decroissance.csv",
        "colonnes": [
            {"nature": "identifiant", "nom": "lot"},
            {
                "nature": "numerique",
                "nom": "heure",
                "unite": "h",
                "composantes": [
                    {"type": "B", "valeur": 6.0, "unite": "min", "source": "horloge"}
                ],
            },
            {
                "nature": "numerique",
                "nom": "activite",
                "unite": "MBq",
                "composantes": [
                    {"type": "B", "valeur": 3.0, "unite": "MBq", "source": "certificat"}
                ],
            },
            {
                "nature": "incertitude",
                "nom": "u_activite",
                "composante_de": "activite",
                "type": "B",
                "unite": "MBq",
                "source": "afficheur",
            },
        ],
    }


DONNEES = {
    "lot": ["007", "10.50"],
    "heure": [0.0, 6.0],
    "activite": [100.0, 50.0],
    "u_activite": [4.0, 0.0],
}


@pytest.fixture
def arc(tmp_path):
    archive = Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))
    source = tmp_path / "serie.dcm"
    source.write_bytes(b"pixels")
    fichier = archive.importer_copie(source, "serie_ct", type_fichier="DICOM")
    page = Page.model_validate(
        {
            "id": ID_PAGE,
            "titre": "Linéarité",
            "date_mesure": HEURE,
            "date_saisie": HEURE,
            "auteur": "G.",
            "tags": ["activimetre", "Tc99m"],
            "mesures": [
                scalaire(),
                {"nature": "identifiant", "nom": "lot", "valeur": "007"},
                description_tableau(),
                fichier.model_dump(),
            ],
        }
    )
    archive.creer_page(page, tableaux={"decroissance": DONNEES})
    return archive


@pytest.fixture
def p(arc):
    return arc.page(ID_PAGE)


# --- Exemple du cahier des charges --------------------------------------------------


def exemple_du_cahier():
    """Extrait le code de la section 7, pour que la documentation reste exacte."""
    texte = CAHIER.read_text(encoding="utf-8")
    section = texte[texte.index("**API Python (usage visé)**") :]
    return re.search(r"```python\n(.*?)```", section, flags=re.DOTALL).group(1)


def test_l_exemple_de_la_section_7_s_execute_tel_quel(arc):
    code = exemple_du_cahier().replace("D:/ArchiveStage1", arc.racine.as_posix())
    assert "from pymphub import Archive" in code
    assert 'p.fichiers["serie_ct"]' in code

    espace: dict = {}
    exec(code, espace)

    page = espace["p"]
    assert page.id == ID_PAGE
    assert page.mesures["activite"].valeur == 100.0
    assert len(espace["pages"]) == 1


def test_le_critere_de_la_phase_0_dans_un_processus_neuf(arc):
    code = (
        "from pymphub import Archive\n"
        f"print(Archive({str(arc.racine)!r}).page({ID_PAGE!r}).mesures['activite'].valeur)"
    )
    resultat = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert resultat.stdout.strip() == "100.0"


def test_archive_est_exportee_par_le_paquet():
    assert pymphub.Archive is Archive
    assert "Archive" in pymphub.__all__


# --- Page ---------------------------------------------------------------------------


def test_relire_une_page(p):
    assert p.id == ID_PAGE
    assert p.titre == "Linéarité"
    assert p.date_mesure == HEURE
    assert p.auteur == "G."
    assert p.statut == "brouillon"
    assert p.tags == ("activimetre", "Tc99m")
    assert ID_PAGE in repr(p)


def test_page_absente(arc):
    with pytest.raises(KeyError):
        arc.page("2026-01-01_absente_0001")


def test_historique_de_la_page(arc, p):
    assert [e.nature for e in p.historique()] == ["création"]
    assert p.corrections() == []


# --- Mesures ------------------------------------------------------------------------


def test_valeur_brute_dans_l_unite_d_origine(p):
    mesure = p.mesures["activite"]
    assert mesure.valeur == 100.0
    assert mesure.unite == "MBq"
    assert mesure.commentaire == "au centre"
    assert mesure.horodatage == HEURE
    assert float(mesure) == 100.0


def test_conversion(p):
    convertie = p.mesures["activite"].en("mCi")
    assert convertie.unite == "mCi"
    assert convertie.valeur == pytest.approx(100 / 37)
    assert convertie.elargie() == pytest.approx(2 / 37)
    assert p.mesures["activite"].unite == "MBq"


def test_incertitude_objet_gtc(p):
    incertitude = p.mesures["activite"].incertitude
    assert isinstance(incertitude, UncertainReal)
    assert incertitude.x == 100.0
    assert uncertainty(incertitude) == pytest.approx(1.0)


def test_budget_complet(p):
    budget = p.mesures["activite"].budget
    assert budget.lignes[0].source == "certificat"
    assert budget.lignes[0].contribution_pourcent == pytest.approx(100)


def test_incertitude_elargie_absolue_et_relative(p):
    mesure = p.mesures["activite"]
    assert mesure.elargie() == pytest.approx(2.0)
    assert mesure.elargie(k=3) == pytest.approx(3.0)
    assert mesure.elargie_relative() == pytest.approx(2.0)


def test_affichage_d_une_mesure(p):
    assert repr(p.mesures["activite"]) == "<activite = 100 ± 2 MBq (k = 2)>"


def test_meme_objet_pour_une_meme_mesure_donc_correlation_conservee(p):
    a = p.mesures["activite"]
    assert a is p.mesures["activite"]
    assert uncertainty(a.incertitude - p.mesures["activite"].incertitude) == 0


def test_la_correlation_survit_a_en(p):
    a = p.mesures["activite"]
    difference = a.en("kBq").incertitude / 1000 - a.incertitude
    assert uncertainty(difference) == pytest.approx(0, abs=1e-9)


def test_identifiant_exact(p):
    grandeur = p.mesures["lot"]
    assert isinstance(grandeur, Identifiant)
    assert grandeur.valeur == "007"


def test_tableau_et_fichier_restent_leur_description(p):
    assert isinstance(p.mesures["decroissance"], Tableau)
    assert p.mesures["serie_ct"].type_fichier == "DICOM"


def test_parcourir_les_mesures(p):
    assert list(p.mesures) == ["activite", "lot", "decroissance", "serie_ct"]
    assert len(p.mesures) == 4
    assert "activite" in p.mesures
    assert "absente" not in p.mesures


def test_mesure_absente(p):
    with pytest.raises(KeyError, match="absente"):
        p.mesures["absente"]


# --- Tableaux -----------------------------------------------------------------------


def test_tableau_en_dataframe_avec_unites(p):
    donnees = p.tableau("decroissance")
    assert isinstance(donnees, pd.DataFrame)
    assert donnees["activite"].tolist() == [100.0, 50.0]
    assert donnees.attrs["unites"]["activite"] == "MBq"
    assert donnees.attrs["unites"]["heure"] == "h"


def test_les_identifiants_restent_du_texte_dans_le_dataframe(p):
    assert p.tableau("decroissance")["lot"].tolist() == ["007", "10.50"]


def test_cellule_avec_incertitude_de_colonne_et_de_cellule(p):
    # u² = 3² (colonne) + 4² (cellule) = 25
    cellule = p.cellule("decroissance", "activite", 0)
    assert cellule.valeur == 100.0
    assert cellule.unite == "MBq"
    assert cellule.budget.incertitude_type == pytest.approx(5.0)
    assert {ligne.source for ligne in cellule.budget.lignes} == {
        "certificat",
        "afficheur",
    }


def test_cellule_incertitude_propre_a_chaque_ligne(p):
    assert p.cellule(
        "decroissance", "activite", 1
    ).budget.incertitude_type == pytest.approx(3.0)


def test_cellule_avec_incertitude_dans_une_autre_unite(p):
    assert p.cellule(
        "decroissance", "heure", 1
    ).budget.incertitude_type == pytest.approx(0.1)


def test_cellule_invalide(p):
    with pytest.raises(ValueError):
        p.cellule("decroissance", "lot", 0)
    with pytest.raises(ValueError):
        p.cellule("decroissance", "u_activite", 0)
    with pytest.raises(ValueError):
        p.cellule("activite", "x", 0)
    with pytest.raises(KeyError):
        p.cellule("decroissance", "absente", 0)
    with pytest.raises(IndexError):
        p.cellule("decroissance", "activite", 99)


# --- Fichiers -----------------------------------------------------------------------


def test_chemin_verifie_par_empreinte(p):
    assert p.fichiers["serie_ct"].read_bytes() == b"pixels"


def test_fichier_altere_signale(arc, p):
    chemin = p.fichiers["serie_ct"]
    chemin.chmod(0o644)
    chemin.write_bytes(b"abime")
    with pytest.raises(FichierAltere):
        p.fichiers["serie_ct"]


def test_lister_les_fichiers_sans_les_lire(arc, p):
    chemin = p.fichiers["serie_ct"]
    chemin.unlink()
    assert list(p.fichiers) == ["serie_ct"]
    assert len(p.fichiers) == 1
    assert "serie_ct" in p.fichiers
    assert "lot" not in p.fichiers


def test_fichier_absent_de_la_page(p):
    with pytest.raises(KeyError, match="absent"):
        p.fichiers["absent"]
    with pytest.raises(KeyError):
        p.fichiers["lot"]


# --- Recherche ----------------------------------------------------------------------


def test_chercher_renvoie_des_pages(arc):
    pages = arc.chercher(tags=["activimetre"], depuis="2026-11-01")
    assert len(pages) == 1
    assert pages[0].id == ID_PAGE
    assert pages[0].mesures["activite"].valeur == 100.0
    assert pages.identifiants == [ID_PAGE]


def test_chercher_sans_resultat(arc):
    pages = arc.chercher(tags=["inconnu"])
    assert len(pages) == 0
    assert list(pages) == []
    with pytest.raises(IndexError):
        pages[0]


def test_chercher_parcours_et_tranches(arc):
    autre = "2026-11-15_scanner_0008"
    arc.creer_page(
        Page(
            id=autre,
            titre="Scanner",
            date_mesure=HEURE + timedelta(days=1),
            date_saisie=HEURE,
            auteur="G.",
        )
    )
    pages = arc.chercher()
    assert [p.id for p in pages] == [autre, ID_PAGE]
    assert [p.id for p in pages[:1]] == [autre]
    assert pages[-1].id == ID_PAGE
    assert pages[0] is pages[0]


def test_une_page_illisible_ne_gene_que_son_propre_acces(arc):
    autre = "2026-11-15_futur_0008"
    arc.creer_page(
        Page(
            id=autre,
            titre="Futur",
            date_mesure=HEURE + timedelta(days=1),
            date_saisie=HEURE,
            auteur="G.",
        )
    )
    chemin = arc.racine / "pages" / autre / "page.json"
    chemin.write_text(
        re.sub(
            r'"version_schema": \d+',
            '"version_schema": 99',
            chemin.read_text(encoding="utf-8"),
        ),
        encoding="utf-8",
    )
    arc.enregistrer_reprise("Page d'une version plus récente")

    pages = arc.chercher()
    assert pages.identifiants == [autre, ID_PAGE]
    assert pages[1].id == ID_PAGE
    with pytest.raises(ValueError, match="mettre à jour"):
        pages[0]
