"""Vérifie les retours d'ergonomie : dates, clics droits, affichage et décroissance."""

import math
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from menus_reels import clic_droit
from PIL import Image
from PySide6.QtCore import QDateTime
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMainWindow

from pymphub import Archive
from pymphub.dates import lire_date, lire_date_iso, maintenant
from pymphub.gabarit import _colonne_attendue
from pymphub.importation import Grille
from pymphub.interface import application, espace_saisie
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.onglet_calculs import DialogueDecroissance, EditeurCalculs
from pymphub.interface.onglet_tableau import (
    ACTION_COLONNE_DATE,
    ACTION_DECROISSANCE,
    ACTION_INSERER_DATE,
    ACTION_SUPPRIMER,
    EditeurTableau,
)
from pymphub.interface.onglets import ACTION_HORODATER, EditeurNotes
from pymphub.interface.texte_en_grand import LIBELLE_ACTION
from pymphub.page import ColonneDate, ColonneNumerique, Composante, Page, Scalaire

HEURE = datetime(2026, 11, 4, 9, 0, tzinfo=timezone(timedelta(hours=1)))
RACINE = Path(__file__).parent.parent
PREMIERE = "04/11/2026 09:00:00"
SECONDE = "04/11/2026 15:00:00"


def composante(unite: str = "MBq") -> Composante:
    return Composante(source="s", type="B", valeur=1.0, unite=unite)


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def fenetre(qtbot):
    resultat = QMainWindow()
    resultat.resize(1400, 900)
    qtbot.addWidget(resultat)
    return resultat


@pytest.fixture
def espace(qtbot, arc, fenetre, monkeypatch):
    resultat = EspaceSaisie()
    fenetre.setCentralWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    monkeypatch.setattr(resultat, "_confirmer", lambda *_: True)
    monkeypatch.setattr(resultat, "_demander_motif", lambda: "Motif")
    assert resultat.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    fenetre.show()
    qtbot.waitExposed(fenetre)
    return resultat


@pytest.fixture
def tableau(espace, monkeypatch) -> EditeurTableau:
    """Un tableau : une activité, puis une colonne de dates, avec deux lignes."""
    editeur = espace.ajouter_onglet("tableau")
    monkeypatch.setattr(editeur, "_confirmer_suppression", lambda _nom: True)
    editeur.champ_nom.setText("t")
    editeur.colonnes[0].nom.setText("A")
    editeur.colonnes[0].unite.setText("MBq")
    editeur.colonnes[0].composantes = (composante(),)
    editeur.colonnes[1].nom.setText("heure")
    nature = editeur.colonnes[1].nature
    nature.setCurrentIndex(nature.findData("date"))
    editeur.ecrire_cellules([["100", PREMIERE], ["80", SECONDE]], 0, 0)
    assert editeur.erreur() is None
    assert espace.enregistrer_maintenant()
    return editeur


def centre_cellule(editeur: EditeurTableau, i: int, j: int):
    editeur.grille.setCurrentCell(i, j)
    return editeur.grille.visualRect(editeur.grille.model().index(i, j)).center()


def libelles(menus_ouverts) -> list[str]:
    return [texte for texte, _actif in menus_ouverts[-1]]


# --- Informations de la page ------------------------------------------------------------


def test_les_informations_sont_repliees_par_defaut(espace):
    assert espace.bloc_informations.isHidden()
    assert espace.bouton_informations.text() == "Afficher les informations"
    assert espace.champ_titre.text() == "CQ"


def test_replier_les_informations_ne_les_perd_pas(espace):
    espace.champ_titre.setText("Nouveau titre")
    espace.champ_titre.textEdited.emit("Nouveau titre")
    assert espace.enregistrer_maintenant()
    assert espace.archive.lire_page(espace.page.id).titre == "Nouveau titre"


# --- Colonnes de dates ------------------------------------------------------------------


def test_une_colonne_de_dates_s_enregistre_avec_son_fuseau(espace, tableau):
    page = espace.archive.lire_page(espace.page.id)
    colonnes = next(g for g in page.mesures if g.nom == "t").colonnes
    assert isinstance(colonnes[1], ColonneDate)
    donnees = espace.archive.lire_tableau(page.id, "t")
    assert lire_date_iso(donnees["heure"].iloc[0]) == lire_date(PREMIERE)
    assert donnees["heure"].iloc[0].startswith("2026-11-04T09:00:00")
    assert donnees["heure"].iloc[0][-6] in "+-"


def test_rouvrir_une_page_ne_change_aucune_date(espace, tableau):
    avant = espace.archive.lire_tableau(espace.page.id, "t")
    assert espace.ouvrir_page(espace.page.id)
    relu = next(e for e in espace.editeurs() if isinstance(e, EditeurTableau))
    assert relu.grille.item(0, 1).text() == PREMIERE
    assert relu.colonnes[1].nature_choisie() == "date"
    assert not relu.colonnes[1].unite.isEnabled()
    assert not relu.tableau_modifie()
    assert not espace.modifications_en_attente()
    assert espace.archive.lire_tableau(espace.page.id, "t").equals(avant)


def test_une_date_mal_ecrite_est_expliquee_et_n_est_pas_enregistree(espace, tableau):
    avant = espace.archive.lire_tableau(espace.page.id, "t")
    tableau.ecrire_cellules([["demain"]], 0, 1)
    assert "heure" in tableau.erreur()
    assert "ligne 1" in tableau.erreur()
    assert "date et une heure" in tableau.erreur()
    espace.enregistrer_maintenant()
    assert espace.archive.lire_tableau(espace.page.id, "t").equals(avant)


def test_un_collage_de_dates_est_reconnu(tableau, monkeypatch):
    monkeypatch.setattr(tableau, "_confirmer_remplacement", lambda: True)
    assert tableau.appliquer_grille(
        Grille(["heure", "A (MBq)"], [[PREMIERE, "1"], [SECONDE, "2"]]), "un essai"
    )
    assert tableau.colonnes[0].nature_choisie() == "date"
    assert tableau.colonnes[1].nature_choisie() == "numerique"


def test_la_fiche_montre_les_dates_lisiblement(espace, tableau, qtbot):
    fiche = FichePage()
    qtbot.addWidget(fiche)
    fiche.afficher(espace.archive, espace.page.id)
    table = fiche.tables["t"]
    colonne = next(
        j
        for j in range(table.columnCount())
        if table.horizontalHeaderItem(j).text().startswith("heure")
    )
    assert table.horizontalHeaderItem(colonne).text() == "heure"
    assert table.item(0, colonne).text() == PREMIERE


def test_un_gabarit_garde_une_colonne_de_dates():
    attendue = _colonne_attendue(ColonneDate(nom="heure"))
    assert attendue.nature == "date"
    assert attendue.unite is None
    numerique = _colonne_attendue(
        ColonneNumerique(nom="A", unite="MBq", composantes=(composante(),))
    )
    assert numerique.unite == "MBq"


# --- Clic droit sur une colonne ---------------------------------------------------------


def test_il_n_y_a_plus_de_bouton_pour_retirer_une_colonne(tableau):
    assert tableau.configuration.rowCount() == 5
    assert not hasattr(tableau.colonnes[0], "bouton_retirer")


def test_le_clic_droit_sur_une_cellule_de_date_propose_l_heure(tableau, menus):
    clic_droit(tableau.grille.viewport(), centre_cellule(tableau, 0, 1))
    assert menus.ouverts == [
        [
            (ACTION_INSERER_DATE, True),
            (ACTION_COLONNE_DATE, True),
            (ACTION_SUPPRIMER, True),
        ]
    ]


def test_le_clic_droit_sur_un_nombre_propose_la_decroissance(tableau, menus):
    clic_droit(tableau.grille.viewport(), centre_cellule(tableau, 0, 0))
    assert libelles(menus.ouverts) == [
        ACTION_DECROISSANCE,
        ACTION_COLONNE_DATE,
        ACTION_SUPPRIMER,
    ]


def test_inserer_l_heure_dans_une_cellule(espace, tableau, menus, qtbot):
    avant = maintenant()
    menus.action_a_lancer = ACTION_INSERER_DATE
    derniere = tableau.grille.rowCount() - 1
    clic_droit(tableau.grille.viewport(), centre_cellule(tableau, derniere, 1))
    qtbot.waitUntil(lambda: tableau.grille.item(derniere, 1) is not None)
    ecrite = lire_date(tableau.grille.item(derniere, 1).text())
    assert avant <= ecrite <= maintenant()
    # Une ligne libre reste disponible en bas.
    assert tableau.grille.rowCount() == derniere + 2


def test_ajouter_une_colonne_de_dates_lui_donne_un_nom_libre(tableau, menus, qtbot):
    menus.action_a_lancer = ACTION_COLONNE_DATE
    clic_droit(tableau.grille.viewport(), centre_cellule(tableau, 0, 0))
    qtbot.waitUntil(lambda: len(tableau.colonnes) == 3)
    assert tableau.colonnes[2].nom.text() == "date"
    assert tableau.colonnes[2].nature_choisie() == "date"
    assert tableau.ajouter_colonne_date().nom.text() == "date_2"


def test_supprimer_une_colonne_depuis_une_cellule(espace, tableau, menus, qtbot):
    menus.action_a_lancer = ACTION_SUPPRIMER
    clic_droit(tableau.grille.viewport(), centre_cellule(tableau, 0, 1))
    qtbot.waitUntil(lambda: len(tableau.colonnes) == 1)
    assert tableau.colonnes[0].nom.text() == "A"
    assert tableau.grille.columnCount() == 1
    assert tableau.configuration.columnCount() == 1
    assert tableau.erreur() is None


@pytest.mark.parametrize(
    "champ", ["nature", "nom", "unite", "commentaire", "cible", "bouton_incertitude"]
)
def test_supprimer_une_colonne_depuis_sa_description(tableau, menus, qtbot, champ):
    """Depuis chaque case de la description : aucun menu ne doit se perdre ni planter."""
    menus.action_a_lancer = ACTION_SUPPRIMER
    colonne = tableau.colonnes[0]
    colonne.nature.setCurrentIndex(colonne.nature.findData("incertitude"))
    cible = getattr(colonne, champ)
    assert not cible.isHidden()
    clic_droit(cible, cible.rect().center())
    assert ACTION_SUPPRIMER in libelles(menus.ouverts)
    qtbot.waitUntil(lambda: len(tableau.colonnes) == 1)
    assert tableau.colonnes[0].nom.text() == "heure"
    qtbot.wait(20)


def test_un_champ_garde_son_menu_habituel(tableau, menus):
    cible = tableau.colonnes[0].commentaire
    clic_droit(cible, cible.rect().center())
    assert LIBELLE_ACTION in libelles(menus.ouverts)
    assert ACTION_SUPPRIMER in libelles(menus.ouverts)
    cible = tableau.colonnes[0].nom
    clic_droit(cible, cible.rect().center())
    assert len(libelles(menus.ouverts)) > 3


def test_refuser_la_confirmation_garde_la_colonne(tableau, monkeypatch):
    noms = []
    monkeypatch.setattr(
        tableau, "_confirmer_suppression", lambda nom: noms.append(nom) or False
    )
    assert not tableau.demander_suppression(tableau.colonnes[0])
    assert noms == ["A"]
    assert len(tableau.colonnes) == 2


def test_une_colonne_vide_se_supprime_sans_question(tableau, monkeypatch):
    monkeypatch.setattr(tableau, "_confirmer_suppression", pytest.fail)
    assert tableau.demander_suppression(tableau.ajouter_colonne())


# --- Affichage des données brutes ou calculées ------------------------------------------


@pytest.fixture
def avec_calcul(espace, tableau) -> EditeurTableau:
    """Le tableau, avec une colonne calculée à droite."""
    calculs = espace.ajouter_onglet("calculs")
    ligne = calculs.lignes[0]
    ligne.nom.setText("double")
    ligne.formule.setText("t.A * 2")
    assert espace.enregistrer_maintenant()
    espace.onglets.setCurrentWidget(tableau)
    assert tableau.colonnes_calculees()
    return tableau


def choisir(editeur: EditeurTableau, mode: str) -> None:
    editeur.choix_affichage.setCurrentIndex(editeur.choix_affichage.findData(mode))


def colonnes_visibles(editeur: EditeurTableau) -> list[bool]:
    return [not editeur.grille.isColumnHidden(j) for j in range(len(editeur.colonnes))]


def test_tout_est_affiche_par_defaut(avec_calcul):
    assert colonnes_visibles(avec_calcul) == [True, True]
    assert avec_calcul.grille_calculee.isVisible()


def test_n_afficher_que_les_donnees_brutes(avec_calcul):
    choisir(avec_calcul, "brutes")
    assert colonnes_visibles(avec_calcul) == [True, True]
    assert not avec_calcul.grille_calculee.isVisible()
    assert not avec_calcul.configuration_calculee.isVisible()


def test_n_afficher_que_les_donnees_calculees(avec_calcul):
    choisir(avec_calcul, "calculees")
    assert colonnes_visibles(avec_calcul) == [False, False]
    assert avec_calcul.configuration.isColumnHidden(0)
    assert avec_calcul.grille_calculee.isVisible()
    assert avec_calcul.grille_calculee.item(0, 0).text().startswith("200")


def test_l_affichage_revient_entier(avec_calcul):
    choisir(avec_calcul, "calculees")
    choisir(avec_calcul, "tout")
    assert colonnes_visibles(avec_calcul) == [True, True]
    assert avec_calcul.grille_calculee.isVisible()


def test_changer_l_affichage_ne_touche_pas_aux_donnees(espace, avec_calcul):
    commits = len(espace.archive.historique(espace.page.id))
    avant = avec_calcul.donnees()
    for mode in ("brutes", "calculees", "tout"):
        choisir(avec_calcul, mode)
        assert avec_calcul.donnees() == avant
        assert not avec_calcul.tableau_modifie()
    assert not espace.modifications_en_attente()
    assert espace.enregistrer_maintenant()
    assert len(espace.archive.historique(espace.page.id)) == commits


def test_le_choix_survit_a_un_enregistrement(espace, avec_calcul):
    choisir(avec_calcul, "brutes")
    avec_calcul.enregistre(espace.page)
    assert not avec_calcul.grille_calculee.isVisible()


def test_une_incertitude_calculee_compte_comme_une_donnee_calculee(tableau):
    colonne = tableau.ajouter_colonne("u", "MBq")
    colonne.nature.setCurrentIndex(colonne.nature.findData("incertitude_calculee"))
    colonne.cible.setCurrentText("A")
    colonne.formule.setText("sqrt(A / (1 MBq))")
    assert tableau.choix_affichage.isEnabled()
    choisir(tableau, "brutes")
    assert colonnes_visibles(tableau) == [True, True, False]
    choisir(tableau, "calculees")
    assert colonnes_visibles(tableau) == [False, False, True]


def test_sans_calcul_le_choix_est_grise(tableau):
    assert not tableau.choix_affichage.isEnabled()


def test_une_colonne_ajoutee_se_voit_toujours(avec_calcul):
    choisir(avec_calcul, "calculees")
    avec_calcul.ajouter_colonne("nouvelle")
    assert avec_calcul.choix_affichage.currentData() == "tout"
    assert all(colonnes_visibles(avec_calcul))


def test_l_affichage_tient_dans_la_fenetre(avec_calcul, fenetre, qtbot):
    """Le choix d'affichage ne doit pas élargir la fenêtre au-delà de l'écran."""
    qtbot.wait(20)
    assert fenetre.minimumSizeHint().width() <= 1400
    assert avec_calcul.choix_affichage.isVisible()


# --- Décroissance ------------------------------------------------------------------------


@pytest.fixture
def page_technetium() -> Page:
    return Page(
        id="2026-11-04_cq_0001",
        titre="CQ",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=(
            Scalaire(
                nom="A0",
                valeur=100.0,
                unite="MBq",
                composantes=(composante(),),
                horodatage=HEURE + timedelta(hours=1),
            ),
            Scalaire(nom="T12", valeur=6.0, unite="h", composantes=(composante("h"),)),
            Scalaire(
                nom="lam", valeur=0.1, unite="1/h", composantes=(composante("1/h"),)
            ),
            Scalaire(
                nom="masse", valeur=1.0, unite="g", composantes=(composante("g"),)
            ),
        ),
    )


@pytest.fixture
def dialogue(qtbot, page_technetium) -> DialogueDecroissance:
    resultat = DialogueDecroissance(page_technetium)
    qtbot.addWidget(resultat)
    return resultat


def test_seules_une_periode_ou_une_constante_sont_proposees(dialogue):
    proposees = [
        dialogue.constante.itemData(i) for i in range(dialogue.constante.count())
    ]
    assert proposees == ["T12", "lam"]


def test_l_heure_actuelle_s_ecrit_dans_la_formule(dialogue):
    avant = maintenant()
    formule = dialogue.formule()
    assert formule.startswith('decroissance(A0, T12, "')
    ecrite = lire_date_iso(formule.split('"')[1])
    assert avant <= ecrite <= maintenant()


def test_une_date_choisie_s_ecrit_avec_son_fuseau(dialogue):
    dialogue.cible.setCurrentIndex(dialogue.cible.findData("choisie"))
    dialogue.date_choisie.setDateTime(QDateTime(2026, 11, 5, 14, 30, 15))
    ecrite = lire_date_iso(dialogue.formule().split('"')[1])
    assert ecrite.replace(tzinfo=None) == datetime(2026, 11, 5, 14, 30, 15)
    assert ecrite.tzinfo is not None


def test_le_champ_de_date_n_apparait_que_s_il_sert(dialogue, qtbot):
    dialogue.show()
    qtbot.waitExposed(dialogue)
    assert not dialogue.date_choisie.isVisible()
    dialogue.cible.setCurrentIndex(dialogue.cible.findData("choisie"))
    assert dialogue.date_choisie.isVisible()


def test_le_dialogue_dit_quelle_date_de_reference_il_utilise(dialogue):
    assert dialogue.reference.count() == 1
    assert "04/11/2026 10:00" in dialogue.reference.currentText()
    assert "du scalaire" in dialogue.reference.currentText()
    assert not dialogue.reference.isEnabled()


def test_sans_ce_qu_il_faut_on_ne_peut_rien_ajouter(qtbot, page_technetium):
    vide = page_technetium.model_copy(update={"mesures": ()})
    resultat = DialogueDecroissance(vide)
    qtbot.addWidget(resultat)
    assert not resultat.bouton_ok.isEnabled()


def test_la_decroissance_d_une_colonne_depuis_le_clic_droit(
    espace, tableau, menus, qtbot, monkeypatch
):
    formules = []

    def choisir_formule(page, parent, valeur):
        formules.append(valeur)
        return (
            'decroissance(t.A, T12, "2026-11-04T15:00:00+01:00", t.heure)'
            if isinstance(page, Page)
            else None
        )

    monkeypatch.setattr(espace_saisie, "choisir_decroissance", choisir_formule)
    _ajouter_periode(espace)
    menus.action_a_lancer = ACTION_DECROISSANCE
    espace.onglets.setCurrentWidget(tableau)
    clic_droit(tableau.grille.viewport(), centre_cellule(tableau, 0, 0))
    qtbot.waitUntil(lambda: bool(formules))
    assert formules == ["t.A"]
    qtbot.waitUntil(lambda: bool(tableau.colonnes_calculees()))
    colonne = tableau.colonnes_calculees()[0]
    assert colonne.nom == "A_decru"
    assert colonne.valeurs == pytest.approx((50.0, 80.0))
    # On reste sur le tableau, où la colonne apparaît.
    assert espace.onglets.currentWidget() is tableau
    assert tableau.grille_calculee.isVisible()
    assert tableau.grille_calculee.item(0, 0).text().startswith("50")
    assert espace.erreurs == []


def _ajouter_periode(espace) -> None:
    scal = next((e for e in espace.editeurs() if hasattr(e, "lignes")), None)
    if scal is None or isinstance(scal, EditeurCalculs):
        scal = espace.ajouter_onglet("scalaires")
    ligne = scal.ajouter_ligne() if not scal.lignes else scal.lignes[-1]
    ligne.nom.setText("T12")
    ligne.valeur.setText("6")
    ligne.unite.setText("h")
    ligne.composantes = (composante("h"),)
    assert espace.enregistrer_maintenant(), espace.erreurs
    assert any(g.nom == "T12" for g in espace.page.mesures), scal.erreurs()


def test_annuler_la_decroissance_ne_cree_pas_d_onglet(espace, tableau, monkeypatch):
    monkeypatch.setattr(espace_saisie, "choisir_decroissance", lambda *_: None)
    nombre = len(espace.editeurs())
    assert not espace.demander_decroissance_colonne("t.A")
    assert len(espace.editeurs()) == nombre
    assert espace.onglets.currentWidget() is tableau


def test_une_colonne_pas_encore_enregistree_est_expliquee(espace, tableau, monkeypatch):
    monkeypatch.setattr(espace_saisie, "choisir_decroissance", pytest.fail)
    assert not espace.demander_decroissance_colonne("t.inconnue")
    assert "t.inconnue" in espace.erreurs[0]


def test_le_resultat_a_l_heure_actuelle_ne_change_plus_a_la_relecture(
    espace, tableau, monkeypatch
):
    """C'est ce qui garantit qu'une page validée garde sa valeur."""
    _ajouter_periode(espace)
    monkeypatch.setattr(
        espace_saisie,
        "choisir_decroissance",
        lambda *_: 'decroissance(t.A, T12, "2026-11-04T15:00:00+01:00", t.heure)',
    )
    assert espace.demander_decroissance_colonne("t.A")
    premiere = next(g for g in espace.page.mesures if g.nom == "A_decru")
    assert espace.ouvrir_page(espace.page.id)
    assert not espace.modifications_en_attente()
    assert next(g for g in espace.page.mesures if g.nom == "A_decru") == premiere
    assert premiere.valeurs[0] == pytest.approx(100 * math.exp(-math.log(2)))


# --- Horodatage des notes ---------------------------------------------------------------


@pytest.fixture
def notes(espace) -> EditeurNotes:
    resultat = espace.editeurs()[0]
    assert isinstance(resultat, EditeurNotes)
    espace.onglets.setCurrentWidget(resultat)
    return resultat


def test_le_clic_droit_des_notes_garde_son_menu_et_propose_l_heure(notes, menus):
    zone = notes.texte.viewport()
    clic_droit(zone, zone.rect().center())
    assert LIBELLE_ACTION in libelles(menus.ouverts)
    assert ACTION_HORODATER in libelles(menus.ouverts)
    assert len(menus.ouverts) == 1


def test_inserer_l_heure_dans_les_notes(espace, notes, menus, qtbot):
    notes.texte.setPlainText("Début de la manip : ")
    avant = maintenant()
    menus.action_a_lancer = ACTION_HORODATER
    zone = notes.texte.viewport()
    # Au bas de la zone, le clic tombe après le texte.
    clic_droit(zone, zone.rect().bottomRight())
    texte = notes.texte.toPlainText()
    assert texte.startswith("Début de la manip : ")
    ecrite = lire_date(texte.removeprefix("Début de la manip : ").strip())
    assert avant <= ecrite <= maintenant()
    assert espace.enregistrer_maintenant()
    assert texte.strip() in espace.archive.lire_page(espace.page.id).onglets[0].texte


def test_l_heure_arrive_a_l_endroit_clique(notes):
    notes.texte.setPlainText("premiere\nseconde")
    # Le clic tombe après le mot « seconde », sur la deuxième ligne.
    rectangle = notes.texte.cursorRect(notes.texte.document().find("seconde"))
    notes.inserer_horodatage(rectangle.center())
    lignes = notes.texte.toPlainText().split("\n")
    assert lignes[0] == "premiere"
    assert lignes[1].startswith("seconde")
    assert lire_date(lignes[1].removeprefix("seconde"))


def test_l_heure_ne_remplace_pas_une_selection(notes):
    notes.texte.setPlainText("texte")
    notes.texte.selectAll()
    notes.inserer_horodatage()
    assert "texte" in notes.texte.toPlainText()


def test_des_notes_en_lecture_seule_ne_s_horodatent_pas(notes, menus):
    notes.texte.setReadOnly(True)
    zone = notes.texte.viewport()
    clic_droit(zone, zone.rect().center())
    assert (ACTION_HORODATER, False) in menus.ouverts[0]


# --- Démarrage, logo et lanceurs ---------------------------------------------------------


def test_l_application_s_ouvre_en_grand_avec_son_logo(tmp_path):
    Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1))
    code = (
        "import sys\n"
        "from PySide6.QtCore import QTimer\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from pymphub.interface import application\n"
        "from pymphub.interface.fenetre import FenetrePrincipale\n"
        "sys.argv = ['pymphub', sys.argv[1]]\n"
        "origine = QApplication.exec\n"
        "def relever():\n"
        "    f = next(w for w in QApplication.topLevelWidgets()\n"
        "             if isinstance(w, FenetrePrincipale))\n"
        "    print('grand', f.isMaximized(), 'plein', f.isFullScreen())\n"
        "    print('logo', not f.windowIcon().isNull())\n"
        "    print('bureau', QApplication.desktopFileName())\n"
        "    QApplication.quit()\n"
        "def lancer(*_):\n"
        "    QTimer.singleShot(50, relever)\n"
        "    return origine()\n"
        "QApplication.exec = lancer\n"
        "sys.exit(application.main())\n"
    )
    resultat = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "A")],
        capture_output=True,
        text=True,
        env=os.environ | {"QT_QPA_PLATFORM": "offscreen"},
        timeout=60,
    )
    assert resultat.returncode == 0, resultat.stderr
    assert "grand True plein False" in resultat.stdout
    assert "logo True" in resultat.stdout
    assert "bureau pymphub" in resultat.stdout


def test_les_fichiers_du_logo_sont_complets(qapp):
    dossier = application.DOSSIER_RESSOURCES
    assert not QIcon(str(application.LOGO)).isNull()
    assert (dossier / "logo.svg").is_file()
    with Image.open(dossier / "logo.ico") as icone:
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= set(icone.info["sizes"])


@pytest.mark.parametrize(
    "nom", ["pymphub.vbs", "creer_raccourci_bureau.vbs", "lancer.bat"]
)
def test_un_lanceur_windows_est_en_ascii_avec_des_fins_crlf(nom):
    """Windows Script Host et cmd lisent mal le reste. `.gitattributes` protège ces fins."""
    octets = (RACINE / "lanceurs" / "windows" / nom).read_bytes()
    assert octets.isascii()
    assert octets.count(b"\n") == octets.count(b"\r\n") > 0
    assert "eol=crlf" in (RACINE / ".gitattributes").read_text(encoding="utf-8")


def test_le_lanceur_vise_le_bon_module_sans_fenetre_de_commande():
    texte = (RACINE / "lanceurs" / "windows" / "pymphub.vbs").read_text("ascii")
    assert f"-m {application.__spec__.name}" in texte
    assert "coquille.Run(commande, 0, True)" in texte
    assert r"\.venv\Scripts\python.exe" in texte
    assert r"\python\python.exe" in texte
    raccourci = RACINE / "lanceurs" / "windows" / "creer_raccourci_bureau.vbs"
    assert r"\src\pymphub\interface\ressources\logo.ico,0" in raccourci.read_text(
        "ascii"
    )
    # Sous Wine au moins, ce chemin perdait sa fin : le journal n'était pas créé.
    assert "ExpandEnvironmentStrings" not in texte + raccourci.read_text("ascii")
