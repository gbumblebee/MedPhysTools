"""Vérifie le clic droit « Afficher en grand » partout où un texte peut être long."""

from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

from pymphub import Archive
from pymphub.incertitudes import Composante
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.onglet_scalaires import EditeurScalaires
from pymphub.interface.onglet_tableau import EditeurTableau
from pymphub.interface.onglets import EditeurNotes
from pymphub.interface.texte_en_grand import (
    LIBELLE_ACTION,
    AffichageEnGrand,
    DialogueTexte,
    afficher_en_grand,
)
from pymphub.page import Identifiant, OngletNotes, Page, Scalaire

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
LONG = "Un très long commentaire. " * 40


@pytest.fixture
def ouverts(monkeypatch):
    """Remplace l'ouverture du dialogue et note ce qu'il aurait affiché."""
    resultat = []

    def exec_note(dialogue):
        resultat.append((dialogue.windowTitle(), dialogue.texte.toPlainText()))
        return 0

    monkeypatch.setattr(DialogueTexte, "exec", exec_note)
    return resultat


def action_de(menu):
    return next(a for a in menu.actions() if a.text() == LIBELLE_ACTION)


def declencher(widget, position=QPoint()):
    affichage = widget.findChild(AffichageEnGrand)
    assert affichage is not None, f"Pas d'affichage en grand sur {widget!r}."
    action_de(affichage.menu(position)).trigger()


def test_le_dialogue_est_en_lecture_seule_et_garde_tout_le_texte(qtbot):
    dialogue = DialogueTexte("Titre", LONG)
    qtbot.addWidget(dialogue)
    assert dialogue.texte.isReadOnly()
    assert dialogue.texte.toPlainText() == LONG
    assert dialogue.windowTitle() == "Titre"
    assert dialogue.width() >= 800


def test_un_champ_vide_grise_l_action(qtbot):
    champ = QLineEdit()
    qtbot.addWidget(champ)
    affichage = afficher_en_grand(champ, "Titre")
    assert not action_de(affichage.menu()).isEnabled()
    champ.setText("   ")
    assert not action_de(affichage.menu()).isEnabled()
    champ.setText("Texte")
    assert action_de(affichage.menu()).isEnabled()


def test_le_menu_habituel_reste_disponible(qtbot):
    champ = QPlainTextEdit("Texte")
    qtbot.addWidget(champ)
    menu = afficher_en_grand(champ, "Titre").menu()
    libelles = [a.text() for a in menu.actions()]
    assert libelles[0] == LIBELLE_ACTION
    assert len(libelles) > 3


def test_une_etiquette_garde_la_copie(qtbot):
    etiquette = QLabel("Affiché")
    qtbot.addWidget(etiquette)
    menu = afficher_en_grand(etiquette, "Titre", lambda: "Texte complet").menu()
    copier = next(a for a in menu.actions() if a.text() == "Copier")
    copier.trigger()
    assert QApplication.clipboard().text() == "Texte complet"


@pytest.fixture
def espace(qtbot, tmp_path, monkeypatch):
    arc = Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    monkeypatch.setattr(
        resultat, "afficher_erreur", lambda m: pytest.fail(f"Erreur : {m}")
    )
    assert resultat.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    return resultat


def test_la_description_dans_la_saisie(espace, ouverts):
    espace.champ_description.setText(LONG)
    declencher(espace.champ_description)
    assert ouverts == [("Description de la page", LONG)]


def test_les_notes_dans_la_saisie(espace, ouverts):
    notes = espace.editeurs()[0]
    assert isinstance(notes, EditeurNotes)
    notes.texte.setPlainText(LONG)
    declencher(notes.texte)
    assert ouverts == [("Notes", LONG)]


def test_le_commentaire_d_un_scalaire_dans_la_saisie(espace, ouverts):
    editeur = espace.ajouter_onglet("scalaires")
    assert isinstance(editeur, EditeurScalaires)
    ligne = editeur.lignes[0]
    ligne.nom.setText("activite")
    ligne.commentaire.setText(LONG)
    declencher(ligne.commentaire)
    assert ouverts == [("Commentaire de activite", LONG)]


def test_le_commentaire_d_une_colonne_dans_la_saisie(espace, ouverts):
    editeur = espace.ajouter_onglet("tableau")
    assert isinstance(editeur, EditeurTableau)
    colonne = editeur.colonnes[0]
    colonne.nom.setText("heure")
    colonne.commentaire.setText(LONG)
    declencher(colonne.commentaire)
    assert ouverts == [("Commentaire de heure", LONG)]


@pytest.fixture
def fiche(qtbot, tmp_path):
    arc = Archive.creer(tmp_path / "F", "CHU X", "Resp.", date(2026, 11, 1))
    identifiant = arc.nouvel_identifiant(HEURE, "fiche")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Fiche",
            description=LONG,
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(
                Scalaire(
                    nom="activite",
                    valeur=1.0,
                    unite="MBq",
                    composantes=(
                        Composante(type="B", valeur=0.1, unite="MBq", source="s"),
                    ),
                    commentaire=LONG,
                ),
                Identifiant(nom="lot", valeur="007"),
            ),
            onglets=(OngletNotes(id="n", type="notes", titre="Montage", texte=LONG),),
        )
    )
    resultat = FichePage()
    qtbot.addWidget(resultat)
    resultat.show()
    resultat.afficher(arc, identifiant)
    return resultat


def test_la_description_dans_la_fiche(fiche, ouverts):
    declencher(fiche.champs["description"])
    assert ouverts == [("Description de la page", LONG)]


def test_les_notes_dans_la_fiche(fiche, ouverts):
    declencher(fiche.notes[0])
    assert ouverts == [("Montage", LONG)]


def test_le_commentaire_d_une_mesure_dans_la_fiche(fiche, ouverts, qtbot):
    table = fiche.table_mesures
    qtbot.waitExposed(table)
    rectangle = table.visualItemRect(table.item(0, 5))
    declencher(table, rectangle.center())
    assert ouverts == [("Commentaire", LONG)]


def test_une_autre_colonne_de_la_fiche_grise_l_action(fiche, qtbot):
    table = fiche.table_mesures
    qtbot.waitExposed(table)
    rectangle = table.visualItemRect(table.item(0, 0))
    affichage = table.findChild(AffichageEnGrand)
    assert not action_de(affichage.menu(rectangle.center())).isEnabled()
    rectangle = table.visualItemRect(table.item(1, 5))
    assert not action_de(affichage.menu(rectangle.center())).isEnabled()


@pytest.mark.parametrize("nature", ["champ", "zone", "table"])
def test_un_vrai_clic_droit_ouvre_le_menu(fiche, qtbot, nature):
    """Parcourt le vrai chemin de Qt, jusqu'au menu affiché à l'écran."""
    table = fiche.table_mesures
    widget, position = {
        "champ": (fiche.champs["description"], QPoint(2, 2)),
        "zone": (fiche.notes[0], QPoint(5, 5)),
        "table": (table, table.visualItemRect(table.item(0, 5)).center()),
    }[nature]
    vus = []

    def fermer():
        menu = QApplication.activePopupWidget()
        vus.append(menu.actions()[0].text() if menu is not None else None)
        if menu is not None:
            menu.close()

    QTimer.singleShot(50, fermer)
    widget.customContextMenuRequested.emit(position)
    assert vus == [LIBELLE_ACTION]
