"""Vérifie l'espace Archive : filtres, liste, fiche, corrections barrées et alertes."""

import gc
import json
import os
import shutil
import subprocess
import sys
import time
import weakref
from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QDate, QTimer
from PySide6.QtWidgets import QApplication, QLabel
from scenario_activimetre import ID_PAGE, ID_SUIVANTE, MACHINE, construire

from pymphub import Archive
from pymphub.historique import corrections_par_champ
from pymphub.index import Criteres
from pymphub.interface.espace_archive import DialogueSuppression, EspaceArchive
from pymphub.interface.fenetre import FenetrePrincipale
from pymphub.interface.fiche_page import (
    LIGNES_MAX_AFFICHEES,
    etiquette_tracee,
    formater_date,
    formater_valeur,
)
from pymphub.interface.ramasse_miettes import (
    _RamasseMiettes,
    assurer_ramasse_miettes_principal,
)
from pymphub.interface.theme import appliquer_theme
from pymphub.page import Page, vers_json

PARIS = timezone(timedelta(hours=1))


@pytest.fixture(scope="module")
def modele_archive(tmp_path_factory):
    dossier = tmp_path_factory.mktemp("scenario")
    construire(dossier / "A", dossier / "travail")
    return dossier / "A"


@pytest.fixture
def arc(modele_archive, tmp_path):
    """Une copie de l'archive du scénario, modifiable par chaque test."""
    shutil.copytree(modele_archive, tmp_path / "A")
    return Archive(tmp_path / "A")


@pytest.fixture
def espace(qtbot):
    resultat = EspaceArchive()
    qtbot.addWidget(resultat)
    yield resultat
    resultat.arreter()


def attendre(espace, qtbot):
    qtbot.waitUntil(espace.au_repos, timeout=30000)


@pytest.fixture
def ouvert(espace, arc, qtbot):
    espace.installer(arc)
    attendre(espace, qtbot)
    return espace


def ids_affiches(espace):
    return [resume.id for resume in espace.modele.resumes]


def selectionner(espace, id_page):
    espace.liste.selectRow(ids_affiches(espace).index(id_page))
    return espace.fiche


# --- Liste et filtres ---------------------------------------------------------------


def test_la_liste_affiche_toutes_les_pages_de_la_plus_recente_a_la_plus_ancienne(
    ouvert,
):
    assert ids_affiches(ouvert) == [ID_SUIVANTE, ID_PAGE]
    assert ouvert.compteur.text() == "2 pages"


def test_les_colonnes_de_la_liste(ouvert):
    modele = ouvert.modele
    assert modele.rowCount() == 2
    assert modele.index(1, 0).data() == "14/11/2026 09:30"
    assert modele.index(1, 1).data() == "Linéarité de l'activimètre"
    assert modele.index(1, 2).data() == MACHINE
    assert modele.index(1, 3).data() == "corrigée"
    assert modele.index(1, 4).data() == "Physicien fictif"


def appliquer(espace, qtbot, **champs):
    filtres = espace.filtres
    if "texte" in champs:
        filtres.texte.setText(champs["texte"])
    if "tags" in champs:
        filtres.tags.setText(", ".join(champs["tags"]))
    if "mode_tags" in champs:
        filtres.mode_tags.setCurrentIndex(
            filtres.mode_tags.findData(champs["mode_tags"])
        )
    if "depuis" in champs:
        filtres.depuis.setDate(QDate.fromString(champs["depuis"], "yyyy-MM-dd"))
    if "jusqua" in champs:
        filtres.jusqua.setDate(QDate.fromString(champs["jusqua"], "yyyy-MM-dd"))
    for nom in ("gabarit", "machine", "statut", "auteur"):
        if nom in champs:
            liste = getattr(filtres, nom)
            liste.setCurrentIndex(liste.findData(champs[nom]))
    attendre(espace, qtbot)


@pytest.mark.parametrize(
    "criteres",
    [
        {},
        {"texte": "flacon"},
        {"texte": "décembre"},
        {"tags": ["Tc99m"]},
        {"tags": ["Tc99m", "linearite"]},
        {"tags": ["Tc99m", "inconnu"]},
        {"tags": ["Tc99m", "inconnu"], "mode_tags": "ou"},
        {"depuis": "2026-12-01"},
        {"jusqua": "2026-11-30"},
        {"depuis": "2026-11-14", "jusqua": "2026-11-14"},
        {"gabarit": "activimetre.linearite@1"},
        {"machine": MACHINE},
        {"statut": "validée"},
        {"statut": "corrigée"},
        {"auteur": "Physicien fictif"},
        {"tags": ["activimetre"], "statut": "validée", "depuis": "2026-11-01"},
    ],
)
def test_les_filtres_donnent_les_memes_resultats_que_le_coeur(
    ouvert, arc, qtbot, criteres
):
    appliquer(ouvert, qtbot, **criteres)
    assert ids_affiches(ouvert) == arc.chercher_identifiants(**criteres)


def test_les_listes_proposent_les_valeurs_de_l_archive(ouvert):
    filtres = ouvert.filtres
    valeurs = [filtres.statut.itemData(i) for i in range(filtres.statut.count())]
    assert valeurs == [None, "corrigée", "validée"]
    assert filtres.gabarit.findData("activimetre.linearite@1") > 0
    assert filtres.machine.findData(MACHINE) > 0
    assert "Tc99m" in filtres.tags.toolTip()


def test_les_listes_gardent_leur_selection(ouvert, qtbot):
    appliquer(ouvert, qtbot, statut="validée")
    ouvert.lancer_recherche()
    attendre(ouvert, qtbot)
    assert ouvert.filtres.statut.currentData() == "validée"


def test_reinitialiser_les_filtres(ouvert, qtbot):
    appliquer(ouvert, qtbot, statut="validée", texte="décembre", depuis="2026-12-01")
    assert ids_affiches(ouvert) == [ID_SUIVANTE]
    ouvert.filtres.reinitialiser()
    attendre(ouvert, qtbot)
    assert ids_affiches(ouvert) == [ID_SUIVANTE, ID_PAGE]
    assert ouvert.filtres.criteres() == Criteres(mode_tags="et")


def test_la_frappe_attend_une_pause_avant_de_chercher(ouvert, qtbot):
    recherches = []
    origine = ouvert.lancer_recherche
    ouvert._minuteur.timeout.disconnect()
    ouvert._minuteur.timeout.connect(lambda: (recherches.append(1), origine()))
    for lettre in "flacon":
        ouvert.filtres.texte.insert(lettre)
    assert recherches == []
    attendre(ouvert, qtbot)
    assert recherches == [1]
    assert ids_affiches(ouvert) == [ID_PAGE]


def test_une_saisie_particuliere_ne_provoque_pas_d_erreur(ouvert, qtbot):
    appliquer(ouvert, qtbot, texte='" OR * ; DROP TABLE pages; --')
    assert ouvert.erreur_recherche is None


def test_deux_recherches_rapprochees_donnent_le_dernier_resultat(ouvert, qtbot):
    ouvert.filtres.statut.setCurrentIndex(ouvert.filtres.statut.findData("validée"))
    ouvert.filtres.statut.setCurrentIndex(ouvert.filtres.statut.findData("corrigée"))
    attendre(ouvert, qtbot)
    assert ids_affiches(ouvert) == [ID_PAGE]


def test_une_archive_vide(espace, tmp_path, qtbot):
    espace.installer(Archive.creer(tmp_path / "V", "s", "r", date(2026, 1, 1)))
    attendre(espace, qtbot)
    assert ids_affiches(espace) == []
    assert espace.compteur.text() == "0 page"
    assert not espace.bandeau.isVisibleTo(espace)


def test_changer_d_archive_repart_de_zero(ouvert, tmp_path, qtbot):
    appliquer(ouvert, qtbot, statut="validée")
    selectionner(ouvert, ID_SUIVANTE)
    ouvert.installer(Archive.creer(tmp_path / "V", "s", "r", date(2026, 1, 1)))
    attendre(ouvert, qtbot)
    assert ids_affiches(ouvert) == []
    assert ouvert.fiche.page is None
    assert ouvert.filtres.criteres().statut is None


def test_changer_d_archive_pendant_une_recherche(espace, arc, tmp_path, qtbot):
    espace.installer(arc)
    autre = Archive.creer(tmp_path / "V", "s", "r", date(2026, 1, 1))
    espace.installer(autre)
    attendre(espace, qtbot)
    assert espace.archive is autre
    assert ids_affiches(espace) == []


def test_la_selection_est_conservee_tant_que_la_page_reste_trouvee(ouvert, qtbot):
    selectionner(ouvert, ID_PAGE)
    appliquer(ouvert, qtbot, tags=["Tc99m"])
    assert ouvert.fiche.page.id == ID_PAGE
    assert ouvert.liste.currentIndex().row() == 0
    appliquer(ouvert, qtbot, tags=["inconnu"])
    assert ouvert.fiche.page is None


def test_actualiser_montre_une_nouvelle_page(ouvert, arc, qtbot):
    identifiant = "2026-12-20_nouvelle_0003"
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Nouvelle",
            date_mesure=datetime(2026, 12, 20, tzinfo=PARIS),
            date_saisie=datetime(2026, 12, 20, tzinfo=PARIS),
            auteur="G.",
        )
    )
    ouvert.bouton_actualiser.click()
    attendre(ouvert, qtbot)
    assert ids_affiches(ouvert)[0] == identifiant


# --- Fiche --------------------------------------------------------------------------


def test_la_fiche_affiche_les_informations(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    assert "Linéarité" in fiche.entete.text()
    assert "corrigée" in fiche.entete.text()
    assert fiche.champs["id"].text() == ID_PAGE
    assert fiche.champs["machine"].text() == MACHINE
    assert fiche.champs["date_mesure"].text() == "14/11/2026 09:30"
    assert "Tc99m" in fiche.champs["tags"].text()


def ligne_mesure(fiche, nom):
    table = fiche.table_mesures
    return next(i for i in range(table.rowCount()) if table.item(i, 0).text() == nom)


def test_chaque_valeur_affiche_son_unite_et_son_incertitude(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    table = fiche.table_mesures
    i = ligne_mesure(fiche, "activite_ref")
    assert table.cellWidget(i, 1).text() == "100"
    assert table.cellWidget(i, 2).text() == "MBq"
    assert table.item(i, 3).text() == "1 MBq"
    assert table.item(i, 4).text() == "1 %"
    assert "certificat" in table.item(i, 3).toolTip()
    assert "répétabilité" in table.item(i, 3).toolTip()


def test_changer_k_change_l_affichage_sans_rien_enregistrer(ouvert, arc):
    fiche = selectionner(ouvert, ID_PAGE)
    avant = arc.historique(ID_PAGE)
    table = fiche.table_mesures
    i = ligne_mesure(fiche, "activite_ref")
    fiche.champ_k.setValue(1.0)
    assert table.item(i, 3).text() == "0.5 MBq"
    fiche.champ_k.setValue(3.0)
    assert table.item(i, 3).text() == "1.5 MBq"
    assert arc.historique(ID_PAGE) == avant
    assert arc.modifications_non_tracees() == []


def test_un_identifiant_reste_exact_dans_la_fiche(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    i = ligne_mesure(fiche, "lot")
    assert fiche.table_mesures.cellWidget(i, 1).text() == "007"
    assert fiche.table_mesures.item(i, 3) is None


def test_le_tableau_affiche_ses_unites_et_ses_identifiants(ouvert):
    table = selectionner(ouvert, ID_PAGE).tables["decroissance"]
    assert table.rowCount() == 4
    entetes = [table.horizontalHeaderItem(j).text() for j in range(table.columnCount())]
    assert entetes == ["lot", "heure (h)", "activite (MBq)", "u_activite (MBq)"]
    assert table.item(0, 0).text() == "007"
    assert table.item(1, 2).text() == "79.39"


def test_les_fichiers_sont_listes_sans_chemin_du_magasin(ouvert):
    table = selectionner(ouvert, ID_PAGE).tables["__fichiers__"]
    lignes = [
        [table.item(i, j).text() for j in range(table.columnCount())]
        for i in range(table.rowCount())
    ]
    assert ["export", "Copie", "export_constructeur.txt"] in lignes
    assert any(ligne[1] == "Référence DICOM" for ligne in lignes)
    assert any(ligne[1] == "Pièce jointe" for ligne in lignes)


def test_les_notes_s_affichent(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    assert "Flacon" in fiche.notes[0].toPlainText()


def test_la_fiche_ne_propose_aucune_modification(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    assert fiche.notes[0].isReadOnly()
    assert (
        fiche.tables["decroissance"].editTriggers()
        == fiche.tables["decroissance"].EditTrigger.NoEditTriggers
    )


def test_un_type_d_onglet_inconnu_ne_gene_pas_la_fiche(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    assert fiche.page.onglets[-1].type == "spectre_gamma"


def test_un_grand_tableau_n_affiche_que_son_debut(arc, espace, qtbot):
    page = arc.lire_page(ID_SUIVANTE)
    n = LIGNES_MAX_AFFICHEES + 10
    arc.modifier_page(
        page,
        motif="Grand tableau",
        tableaux={
            "decroissance": {
                "lot": ["a"] * n,
                "heure": [float(i) for i in range(n)],
                "activite": [1.0] * n,
                "u_activite": [0.1] * n,
            }
        },
    )
    espace.installer(arc)
    attendre(espace, qtbot)
    fiche = selectionner(espace, ID_SUIVANTE)
    assert fiche.tables["decroissance"].rowCount() == LIGNES_MAX_AFFICHEES


# --- Valeurs corrigées --------------------------------------------------------------


def test_une_valeur_corrigee_s_affiche_barree_avec_son_motif(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    i = ligne_mesure(fiche, "temperature")
    etiquette = fiche.table_mesures.cellWidget(i, 1)
    assert isinstance(etiquette, QLabel)
    assert etiquette.text() == "<s>21.5</s> 22"
    assert "Température mal relevée" in etiquette.toolTip()
    assert "21.5" in etiquette.toolTip()


def test_une_valeur_non_corrigee_n_est_pas_barree(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    i = ligne_mesure(fiche, "activite_ref")
    etiquette = fiche.table_mesures.cellWidget(i, 1)
    assert "<s>" not in etiquette.text()
    assert etiquette.toolTip() == ""


def page_validee_a_corriger(arc):
    return arc.lire_page(ID_SUIVANTE)


def test_plusieurs_corrections_barrent_toutes_les_anciennes_valeurs(ouvert, arc, qtbot):
    for valeur, motif in ((101.0, "Première"), (102.0, "Seconde")):
        page = arc.lire_page(ID_SUIVANTE)
        scalaire = page.grandeur("activite_ref").model_dump() | {"valeur": valeur}
        mesures = [
            scalaire if g.nom == "activite_ref" else g.model_dump()
            for g in page.mesures
        ]
        arc.modifier_page(
            Page.model_validate(page.model_dump() | {"mesures": mesures}), motif=motif
        )
    ouvert.lancer_recherche()
    attendre(ouvert, qtbot)
    fiche = selectionner(ouvert, ID_SUIVANTE)
    etiquette = fiche.table_mesures.cellWidget(ligne_mesure(fiche, "activite_ref"), 1)
    assert etiquette.text() == "<s>100</s> <s>101</s> 102"
    assert "Première" in etiquette.toolTip()
    assert "Seconde" in etiquette.toolTip()


def test_une_cellule_corrigee_s_affiche_barree(arc, espace, qtbot):
    page = arc.lire_page(ID_SUIVANTE)
    arc.modifier_page(
        page,
        motif="Cellule mal lue",
        tableaux={
            "decroissance": {
                "lot": ["008"] * 4,
                "heure": [0.0, 2.0, 4.0, 6.0],
                "activite": [100.0, 80.0, 63.03, 50.04],
                "u_activite": [0.2] * 4,
            }
        },
    )
    espace.installer(arc)
    attendre(espace, qtbot)
    table = selectionner(espace, ID_SUIVANTE).tables["decroissance"]
    # Deuxième ligne de données : l'historique la note [2], la table la place en 1.
    etiquette = table.cellWidget(1, 2)
    assert etiquette.text() == "<s>79.39</s> 80"
    assert "Cellule mal lue" in etiquette.toolTip()
    assert table.item(0, 2).text() == "100"


def test_un_titre_corrige_s_affiche_barre(arc, espace, qtbot):
    page = arc.lire_page(ID_SUIVANTE)
    arc.modifier_page(page.model_copy(update={"titre": "Nouveau titre"}), motif="Faute")
    espace.installer(arc)
    attendre(espace, qtbot)
    fiche = selectionner(espace, ID_SUIVANTE)
    assert "<s>Linéarité" in fiche.champs["titre"].text()
    assert "Nouveau titre" in fiche.champs["titre"].text()


def test_une_incertitude_corrigee_est_signalee(arc, espace, qtbot):
    page = arc.lire_page(ID_SUIVANTE)
    scalaire = page.grandeur("activite_ref").model_dump()
    scalaire["composantes"][1]["valeur"] = 2.0
    mesures = [
        scalaire if g.nom == "activite_ref" else g.model_dump() for g in page.mesures
    ]
    arc.modifier_page(
        Page.model_validate(page.model_dump() | {"mesures": mesures}),
        motif="Certificat",
    )
    espace.installer(arc)
    attendre(espace, qtbot)
    fiche = selectionner(espace, ID_SUIVANTE)
    element = fiche.table_mesures.item(ligne_mesure(fiche, "activite_ref"), 3)
    assert element.text().endswith("*")
    assert "corrigée" in element.toolTip()


def test_un_texte_saisi_n_est_jamais_interprete_comme_une_mise_en_forme(qtbot):
    from pymphub.historique import CorrectionChamp

    simple = etiquette_tracee("<b>gras</b>")
    qtbot.addWidget(simple)
    assert simple.text() == "<b>gras</b>"
    assert simple.textFormat().name == "PlainText"

    correction = CorrectionChamp(
        "<i>avant</i>",
        "x",
        "<img src=x>",
        datetime(2026, 1, 1, tzinfo=PARIS),
        "<u>G</u>",
    )
    tracee = etiquette_tracee("<b>après</b>", [correction])
    qtbot.addWidget(tracee)
    assert "<b>" not in tracee.text()
    assert "<i>" not in tracee.text()
    assert "<img" not in tracee.toolTip()
    assert "<u>" not in tracee.toolTip()


def test_corrections_par_champ_regroupe_dans_l_ordre(arc):
    groupes = corrections_par_champ(arc.corrections(ID_PAGE))
    assert groupes["mesures.temperature.valeur"][0].ancien == 21.5
    assert groupes["mesures.temperature.valeur"][0].motif == "Température mal relevée"
    assert groupes["gabarit"][0].nouveau == "activimetre.linearite@1"


# --- Chronologie --------------------------------------------------------------------


def lignes_historique(fiche):
    table = fiche.table_historique
    return [
        [table.item(i, j).text() for j in range(table.columnCount())]
        for i in range(table.rowCount())
    ]


def test_la_chronologie_se_charge_a_l_ouverture_de_son_onglet(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    assert fiche.table_historique.rowCount() == 0
    fiche.onglets.setCurrentWidget(fiche.table_historique)
    assert fiche.table_historique.rowCount() > 0


def test_la_chronologie_donne_champ_ancienne_nouvelle_et_motif(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    fiche.onglets.setCurrentWidget(fiche.table_historique)
    lignes = lignes_historique(fiche)
    temperature = next(x for x in lignes if x[3] == "mesures.temperature.valeur")
    assert temperature[1] == "Physicien fictif"
    assert temperature[2] == "correction"
    assert temperature[4:] == ["21.5", "22", "Température mal relevée"]


def test_la_chronologie_va_de_la_plus_recente_a_la_plus_ancienne(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    fiche.onglets.setCurrentWidget(fiche.table_historique)
    lignes = lignes_historique(fiche)
    assert lignes[0][2] == "correction"
    assert lignes[-1][2] == "création"


def test_la_chronologie_suit_le_changement_de_page(ouvert):
    fiche = selectionner(ouvert, ID_PAGE)
    fiche.onglets.setCurrentWidget(fiche.table_historique)
    selectionner(ouvert, ID_SUIVANTE)
    assert lignes_historique(fiche)[-1][2] == "création"
    assert not any(x[6] == "Température mal relevée" for x in lignes_historique(fiche))


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [
        (None, "—"),
        (22.0, "22"),
        (21.5, "21.5"),
        (1e-30, "1e-30"),
        ("007", "007"),
        ({"a": 1}, '{"a": 1}'),
        ("x" * 500, "x" * 119 + "…"),
    ],
)
def test_formater_valeur(valeur, attendu):
    assert formater_valeur(valeur) == attendu


def test_formater_date_garde_le_fuseau_de_la_mesure():
    assert formater_date("2026-11-14T09:30:00+01:00") == "14/11/2026 09:30"


# --- Alertes ------------------------------------------------------------------------


def test_aucune_alerte_sur_une_archive_saine(ouvert):
    assert ouvert.non_tracees == []
    assert not ouvert.bandeau.isVisibleTo(ouvert)


def test_une_modification_hors_du_logiciel_est_signalee(espace, arc, qtbot):
    chemin = arc.racine / "pages" / ID_PAGE / "page.json"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    espace.installer(arc)
    attendre(espace, qtbot)
    assert espace.bandeau.isVisibleTo(espace)
    assert "1 fichier(s) modifié(s)" in espace.alerte.text()
    assert f"pages/{ID_PAGE}/page.json" in espace.texte_details()


def test_une_page_illisible_est_signalee_et_les_autres_restent_affichees(
    espace, arc, qtbot
):
    (arc.racine / "pages" / ID_SUIVANTE / "page.json").write_text(
        "{ abîmé", encoding="utf-8"
    )
    arc.enregistrer_reprise("Fichier abîmé")
    espace.installer(arc)
    attendre(espace, qtbot)
    assert ids_affiches(espace) == [ID_PAGE]
    assert "1 page(s) illisible(s)" in espace.alerte.text()
    assert ID_SUIVANTE in espace.texte_details()


def test_une_alerte_disparait_apres_une_reprise(espace, arc, qtbot):
    chemin = arc.racine / "pages" / ID_PAGE / "page.json"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    espace.installer(arc)
    attendre(espace, qtbot)
    arc.enregistrer_reprise("Vérifié")
    espace.bouton_actualiser.click()
    attendre(espace, qtbot)
    assert not espace.bandeau.isVisibleTo(espace)


def test_une_page_d_un_format_recent_donne_un_message_dans_la_fiche(espace, arc, qtbot):
    chemin = arc.racine / "pages" / ID_SUIVANTE / "page.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    contenu["version_schema"] = 99
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    arc.enregistrer_reprise("Page d'une version plus récente")
    espace.installer(arc)
    attendre(espace, qtbot)
    fiche = selectionner(espace, ID_SUIVANTE)
    assert fiche.page is None
    assert "illisible" in fiche.entete.text()
    assert "mettre à jour" in fiche.entete.text()


def test_un_tableau_illisible_ne_gene_pas_le_reste_de_la_fiche(espace, arc, qtbot):
    (arc.racine / "pages" / ID_PAGE / "brut" / "decroissance.csv").write_text(
        "a,b\n1,2\n", encoding="utf-8"
    )
    espace.installer(arc)
    attendre(espace, qtbot)
    fiche = selectionner(espace, ID_PAGE)
    assert fiche.page is not None
    assert fiche.table_mesures is not None
    assert "decroissance" not in fiche.tables
    assert "1 fichier(s) modifié(s)" in espace.alerte.text()


def test_une_erreur_de_recherche_est_affichee(espace, arc, qtbot, monkeypatch):
    def echec(*_):
        raise OSError("partage réseau indisponible")

    monkeypatch.setattr(arc, "chercher_resumes", echec)
    espace.installer(arc)
    attendre(espace, qtbot)
    assert "partage réseau indisponible" in espace.alerte.text()
    assert espace.compteur.text() == "Recherche impossible"


# --- Fenêtre principale -------------------------------------------------------------


def test_la_fenetre_affiche_l_archive_ouverte(qtbot, arc):
    fenetre = FenetrePrincipale()
    qtbot.addWidget(fenetre)
    assert fenetre.ouvrir_archive(arc.racine)
    espace = fenetre.espace_archive
    assert isinstance(espace, EspaceArchive)
    attendre(espace, qtbot)
    assert ids_affiches(espace) == [ID_SUIVANTE, ID_PAGE]


def test_fermer_la_fenetre_pendant_une_recherche(qtbot, arc):
    fenetre = FenetrePrincipale()
    qtbot.addWidget(fenetre)
    fenetre.ouvrir_archive(arc.racine)
    travaux = (fenetre.espace_archive._recherche, fenetre.espace_archive._verification)
    fenetre.close()
    assert not any(travail is not None and travail.is_alive() for travail in travaux)


def test_detruire_l_espace_pendant_une_recherche_ne_plante_pas(qtbot, arc):
    # Qt arrêterait le programme si un QThread était détruit pendant son travail.
    espace = EspaceArchive()
    espace.installer(arc)
    travaux = (espace._recherche, espace._verification)
    espace.deleteLater()
    del espace
    qtbot.wait(10)
    for travail in travaux:
        travail.join(timeout=30)
    qtbot.wait(10)
    assert not any(travail.is_alive() for travail in travaux)


# --- Mise en page réelle ------------------------------------------------------------


@pytest.fixture
def fiche_affichee(qtbot, arc):
    """Une fiche réellement mise en page, avec le thème de l'application."""
    espace = EspaceArchive()
    qtbot.addWidget(espace)
    appliquer_theme(QApplication.instance())
    espace.resize(1280, 800)
    espace.show()
    qtbot.waitExposed(espace)
    espace.installer(arc)
    attendre(espace, qtbot)
    fiche = selectionner(espace, ID_PAGE)
    qtbot.wait(50)
    yield fiche
    espace.arreter()


def test_les_informations_de_la_fiche_ne_sont_pas_ecrasees(fiche_affichee):
    for etiquette in fiche_affichee.champs.values():
        assert etiquette.height() >= etiquette.fontMetrics().height()


def test_les_blocs_de_la_fiche_ne_se_chevauchent_pas(fiche_affichee):
    disposition = fiche_affichee.disposition_contenu
    blocs = [disposition.itemAt(i).widget() for i in range(disposition.count())]
    blocs = [bloc for bloc in blocs if bloc is not None]
    assert len(blocs) >= 6
    for dessus, dessous in zip(blocs, blocs[1:]):
        assert dessus.geometry().bottom() < dessous.geometry().top()


def test_toutes_les_lignes_d_un_petit_tableau_sont_visibles(fiche_affichee):
    table = fiche_affichee.tables["decroissance"]
    assert table.rowCount() == 4
    assert table.viewport().height() >= sum(table.rowHeight(i) for i in range(4))


def test_toutes_les_mesures_sont_visibles(fiche_affichee):
    table = fiche_affichee.table_mesures
    hauteur = sum(table.rowHeight(i) for i in range(table.rowCount()))
    assert table.viewport().height() >= hauteur


def test_le_titre_a_la_place_de_s_afficher_dans_la_liste(qtbot, arc):
    espace = EspaceArchive()
    qtbot.addWidget(espace)
    espace.resize(1280, 800)
    espace.show()
    qtbot.waitExposed(espace)
    espace.installer(arc)
    attendre(espace, qtbot)
    entete = espace.liste.horizontalHeader()
    assert entete.sectionSize(1) >= max(entete.sectionSize(2), 110)
    espace.arreter()


# --- Ramasse-miettes ----------------------------------------------------------------


def test_le_ramasse_miettes_automatique_est_coupe_des_que_l_espace_existe(espace):
    assert not gc.isenabled()
    assert QApplication.instance().findChild(_RamasseMiettes) is not None


def test_il_n_y_a_qu_un_seul_ramasse_miettes(espace, qtbot):
    autre = EspaceArchive()
    qtbot.addWidget(autre)
    assert len(QApplication.instance().findChildren(_RamasseMiettes)) == 1


def test_le_fil_principal_libere_bien_les_cycles(qapp):
    assurer_ramasse_miettes_principal()

    class Noeud:
        pass

    noeud = Noeud()
    noeud.moi = noeud
    reference = weakref.ref(noeud)
    del noeud
    assert reference() is not None

    collecteur = qapp.findChild(_RamasseMiettes)
    seuil = gc.get_threshold()
    gc.set_threshold(0, 0, 0)
    try:
        collecteur.collecter()
    finally:
        gc.set_threshold(*seuil)
    assert reference() is None


def test_rien_n_est_collecte_sous_les_seuils(qapp, monkeypatch):
    assurer_ramasse_miettes_principal()
    appels = []
    monkeypatch.setattr(gc, "collect", appels.append)
    gc.set_threshold(10**9, 10**9, 10**9)
    try:
        qapp.findChild(_RamasseMiettes).collecter()
    finally:
        gc.set_threshold(700, 10, 10)
    assert appels == []


def test_l_application_coupe_le_ramasse_miettes_automatique(tmp_path):
    code = (
        "import gc, sys\n"
        "from PySide6.QtCore import QTimer\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from pymphub.interface import application\n"
        "sys.argv = ['pymphub']\n"
        "def lancer(*_):\n"
        "    print(gc.isenabled())\n"
        "    return 0\n"
        "QApplication.exec = lancer\n"
        "sys.exit(application.main())\n"
    )
    resultat = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=os.environ | {"QT_QPA_PLATFORM": "offscreen"},
        timeout=60,
    )
    assert resultat.returncode == 0, resultat.stderr
    assert resultat.stdout.strip() == "False"


# --- Réactivité sur 5 000 pages -----------------------------------------------------

NOMBRE_PAGES = 5000


@pytest.fixture(scope="module")
def grande_archive(tmp_path_factory):
    """5 000 pages écrites directement, pour ne pas créer autant de commits."""
    racine = tmp_path_factory.mktemp("grande") / "A"
    archive = Archive.creer(racine, "s", "r", date(2026, 1, 1))
    scalaire = {
        "nature": "scalaire",
        "nom": "activite",
        "valeur": 1.0,
        "unite": "MBq",
        "composantes": [{"type": "B", "valeur": 0.1, "unite": "MBq", "source": "s"}],
    }
    for i in range(NOMBRE_PAGES):
        jour = date(2020, 1, 1) + timedelta(days=i % 2000)
        identifiant = f"{jour.isoformat()}_controle_{i + 1:04d}"
        page = Page.model_validate(
            {
                "id": identifiant,
                "titre": f"Contrôle {i}",
                "date_mesure": datetime(jour.year, jour.month, jour.day, tzinfo=PARIS),
                "date_saisie": datetime(2026, 1, 1, tzinfo=PARIS),
                "auteur": "G.",
                "tags": ["routine", f"groupe{i % 50}"],
                "mesures": [scalaire],
            }
        )
        dossier = racine / "pages" / identifiant
        dossier.mkdir()
        (dossier / "page.json").write_text(vers_json(page), encoding="utf-8")
    return archive


def test_l_interface_reste_reactive_pendant_la_construction_de_l_index(
    espace, grande_archive, qtbot
):
    (grande_archive.racine / "index.sqlite").unlink(missing_ok=True)
    battements = []
    minuteur = QTimer()
    minuteur.setInterval(50)
    minuteur.timeout.connect(lambda: battements.append(time.perf_counter()))
    minuteur.start()

    espace.installer(grande_archive)
    attendre(espace, qtbot)
    minuteur.stop()

    assert espace.modele.rowCount() == NOMBRE_PAGES
    assert len(battements) >= 3
    ecarts = [b - a for a, b in zip(battements, battements[1:])]
    assert max(ecarts) < 1.0


def test_filtrer_et_selectionner_sur_5000_pages(espace, grande_archive, qtbot):
    espace.installer(grande_archive)
    attendre(espace, qtbot)

    debut = time.perf_counter()
    espace.filtres.tags.setText("groupe7")
    espace._minuteur.stop()
    espace.lancer_recherche()
    attendre(espace, qtbot)
    assert time.perf_counter() - debut < 1
    assert espace.modele.rowCount() == 100

    debut = time.perf_counter()
    espace.liste.selectRow(0)
    assert time.perf_counter() - debut < 0.5
    assert espace.fiche.page is not None


def test_le_modele_ne_lit_que_ce_qui_est_affiche(espace, grande_archive, qtbot):
    espace.installer(grande_archive)
    attendre(espace, qtbot)
    debut = time.perf_counter()
    espace.modele.index(4999, 1).data()
    assert time.perf_counter() - debut < 0.01


# --- Suppression d'une page -----------------------------------------------------------


@pytest.fixture
def sans_dialogue(ouvert, monkeypatch):
    """Remplace les dialogues de suppression et note les erreurs affichées."""
    ouvert.erreurs = []
    monkeypatch.setattr(ouvert, "afficher_erreur", ouvert.erreurs.append)
    return ouvert


def test_le_bouton_supprimer_suit_la_page_affichee(ouvert):
    assert not ouvert.fiche.bouton_supprimer.isEnabled()
    fiche = selectionner(ouvert, ID_PAGE)
    assert fiche.bouton_supprimer.isEnabled()
    demandes = []
    fiche.suppression_demandee.connect(demandes.append)
    fiche.bouton_supprimer.click()
    assert demandes == [ID_PAGE]
    fiche.vider()
    assert not fiche.bouton_supprimer.isEnabled()


def test_supprimer_une_page_validee_demande_un_motif(sans_dialogue, qtbot, monkeypatch):
    espace = sans_dialogue
    monkeypatch.setattr(espace, "_demander_motif_suppression", lambda _p: "En double")
    selectionner(espace, ID_PAGE)
    assert espace.demander_suppression(ID_PAGE)
    attendre(espace, qtbot)

    assert ids_affiches(espace) == [ID_SUIVANTE]
    assert espace.fiche.page is None
    assert espace.non_tracees == []
    assert espace.erreurs == []
    derniere = espace.archive.historique(ID_PAGE)[-1]
    assert (derniere.nature, derniere.motif) == ("suppression", "En double")
    assert (espace.archive.racine / "corbeille" / ID_PAGE / "page.json").is_file()


def test_annuler_le_motif_ne_supprime_rien(sans_dialogue, qtbot, monkeypatch):
    espace = sans_dialogue
    monkeypatch.setattr(espace, "_demander_motif_suppression", lambda _p: None)
    assert not espace.demander_suppression(ID_PAGE)
    attendre(espace, qtbot)
    assert ids_affiches(espace) == [ID_SUIVANTE, ID_PAGE]


def creer_brouillon(archive):
    page = Page(
        id=archive.nouvel_identifiant(datetime(2026, 12, 1, tzinfo=PARIS), "brouillon"),
        titre="Brouillon",
        date_mesure=datetime(2026, 12, 1, tzinfo=PARIS),
        date_saisie=datetime(2026, 12, 1, tzinfo=PARIS),
        auteur="G.",
    )
    archive.creer_page(page)
    return page.id


def test_un_brouillon_demande_une_simple_confirmation(
    sans_dialogue, qtbot, monkeypatch
):
    espace = sans_dialogue
    id_page = creer_brouillon(espace.archive)
    monkeypatch.setattr(
        espace,
        "_demander_motif_suppression",
        lambda _p: pytest.fail("Un brouillon ne demande pas de motif."),
    )
    reponses = [False, True]
    monkeypatch.setattr(
        espace, "_confirmer_suppression_brouillon", lambda _p: reponses.pop(0)
    )
    assert not espace.demander_suppression(id_page)
    assert id_page in espace.archive.identifiants()
    assert espace.demander_suppression(id_page)
    attendre(espace, qtbot)
    assert id_page not in espace.archive.identifiants()
    assert espace.archive.historique(id_page)[-1].motif is None


def test_un_refus_de_suppression_s_explique(sans_dialogue, qtbot, monkeypatch):
    espace = sans_dialogue
    monkeypatch.setattr(espace, "_demander_motif_suppression", lambda _p: "Motif")
    chemin = espace.archive.racine / "pages" / ID_PAGE / "page.json"
    chemin.write_bytes(chemin.read_bytes() + b"\n")
    assert not espace.demander_suppression(ID_PAGE)
    attendre(espace, qtbot)
    assert len(espace.erreurs) == 1
    assert "hors du logiciel" in espace.erreurs[0]
    assert ID_PAGE in espace.archive.identifiants()


def test_le_dialogue_exige_le_motif_et_l_identifiant_exact(qtbot, arc):
    dialogue = DialogueSuppression(arc.lire_page(ID_PAGE))
    qtbot.addWidget(dialogue)
    assert not dialogue.bouton_supprimer.isEnabled()
    dialogue.champ_motif.setText("Saisie en double")
    assert not dialogue.bouton_supprimer.isEnabled()
    dialogue.champ_identifiant.setText(ID_PAGE[:-1])
    assert not dialogue.bouton_supprimer.isEnabled()
    dialogue.champ_identifiant.setText(ID_PAGE)
    assert dialogue.bouton_supprimer.isEnabled()
    dialogue.champ_motif.setText("   ")
    assert not dialogue.bouton_supprimer.isEnabled()
    dialogue.champ_motif.setText("Saisie en double")
    assert dialogue.motif() == "Saisie en double"


def test_la_fenetre_referme_la_page_supprimee_dans_la_saisie(qtbot, arc, monkeypatch):
    fenetre = FenetrePrincipale()
    qtbot.addWidget(fenetre)
    fenetre.ouvrir_archive(arc.racine)
    espace, saisie = fenetre.espace_archive, fenetre.espace_saisie
    attendre(espace, qtbot)
    id_page = creer_brouillon(fenetre.archive)
    autre = creer_brouillon(fenetre.archive)
    assert saisie.ouvrir_page(id_page)
    saisie.champ_titre.setText("Modifié à l'écran")
    saisie.champ_titre.textEdited.emit("Modifié à l'écran")
    assert saisie._minuteur.isActive()
    monkeypatch.setattr(espace, "_confirmer_suppression_brouillon", lambda _p: True)

    fenetre._supprimer_page(id_page)
    attendre(espace, qtbot)

    assert saisie.page is None
    assert not saisie._minuteur.isActive()
    assert id_page not in fenetre.archive.identifiants()
    # Le brouillon était enregistré juste avant sa suppression : rien n'est perdu.
    assert (
        json.loads(
            (fenetre.archive.racine / "corbeille" / id_page / "page.json").read_text(
                encoding="utf-8"
            )
        )["titre"]
        == "Modifié à l'écran"
    )

    # Supprimer une autre page laisse en place celle qui est ouverte.
    assert saisie.ouvrir_page(autre)
    monkeypatch.setattr(espace, "_demander_motif_suppression", lambda _p: "Motif")
    fenetre._supprimer_page(ID_SUIVANTE)
    attendre(espace, qtbot)
    assert saisie.page is not None
    assert saisie.page.id == autre
    espace.arreter()
