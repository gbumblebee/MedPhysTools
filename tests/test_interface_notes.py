"""Vérifie les tags et les captures de graphe dans les notes."""

from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtGui import QImage

from pymphub import Archive
from pymphub.incertitudes import Composante
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.onglet_tableau import EditeurTableau
from pymphub.interface.onglets import EditeurNotes

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MBQ = Composante(type="B", valeur=1.0, unite="MBq", source="certificat")
HEURES = Composante(type="B", valeur=0.1, unite="h", source="horloge")


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def espace(qtbot, arc, monkeypatch):
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    monkeypatch.setattr(resultat, "_confirmer", lambda *_: True)
    monkeypatch.setattr(resultat, "_demander_motif", lambda: "Motif")
    assert resultat.creer_page(DescriptionPage("CQ", HEURE, "G.", None, ()))
    scalaires = resultat.ajouter_onglet("scalaires")
    ligne = scalaires.lignes[0]
    ligne.nom.setText("activite")
    ligne.valeur.setText("100")
    ligne.unite.setText("MBq")
    ligne.composantes = (MBQ,)
    scalaires.ligne_modifiee()
    tableau = resultat.ajouter_onglet("tableau")
    tableau.champ_nom.setText("t")
    tableau.colonnes[0].nom.setText("heure")
    tableau.colonnes[0].unite.setText("h")
    tableau.colonnes[0].composantes = (HEURES,)
    tableau.colonnes[1].nom.setText("act")
    tableau.colonnes[1].unite.setText("MBq")
    tableau.colonnes[1].composantes = (MBQ,)
    tableau.coller("0\t100\n1\t80\n2\t60")
    assert resultat.enregistrer_maintenant()
    assert resultat.erreurs == []
    return resultat


@pytest.fixture
def notes(espace):
    resultat = espace.editeurs()[0]
    assert isinstance(resultat, EditeurNotes)
    return resultat


def relue(espace):
    return espace.archive.lire_page(espace.page.id)


@pytest.fixture
def fiche(qtbot):
    resultat = FichePage()
    qtbot.addWidget(resultat)
    return resultat


def changer_scalaire(espace, valeur):
    scalaires = espace.editeurs()[1]
    scalaires.lignes[0].valeur.setText(valeur)
    scalaires.lignes[0].valeur.textEdited.emit(valeur)


# --- Tags --------------------------------------------------------------------------------


def test_la_note_enregistree_garde_le_tag(espace, notes):
    notes.texte.setPlainText("Activité : {{activite}}")
    assert espace.enregistrer_maintenant()
    assert relue(espace).onglets[0].texte == "Activité : {{activite}}"


def test_l_apercu_montre_les_valeurs_et_revient_au_texte(espace, notes, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(notes)
    notes.texte.setPlainText("A = {{activite}}, {{absent}}")
    assert espace.enregistrer_maintenant()
    assert notes.texte.isVisible()
    assert not notes.apercu.isVisible()

    notes.bouton_apercu.setChecked(True)
    assert notes.apercu.isVisible()
    assert not notes.texte.isVisible()
    assert notes.apercu.toPlainText() == "A = 100 ± 2 MBq (k = 2), {{absent ?}}"
    assert notes.apercu.isReadOnly()

    notes.bouton_apercu.setChecked(False)
    assert notes.texte.toPlainText() == "A = {{activite}}, {{absent}}"
    assert not espace.modifications_en_attente()


def test_l_apercu_suit_une_valeur_corrigee(espace, notes):
    notes.texte.setPlainText("{{activite:valeur}}")
    assert espace.enregistrer_maintenant()
    notes.bouton_apercu.setChecked(True)
    assert notes.apercu.toPlainText() == "100 MBq"
    changer_scalaire(espace, "200")
    assert espace.enregistrer_maintenant()
    assert notes.apercu.toPlainText() == "200 MBq"
    assert relue(espace).onglets[0].texte == "{{activite:valeur}}"


def test_inserer_une_valeur_au_curseur(espace, notes):
    noms = [notes.insertion.itemText(i) for i in range(notes.insertion.count())]
    assert noms[0] == "Insérer une valeur…"
    assert "activite" in noms
    assert "t.act[1]" in noms
    notes.texte.setPlainText("A = ")
    notes.texte.moveCursor(notes.texte.textCursor().MoveOperation.End)
    rang = notes.insertion.findText("activite")
    notes.insertion.setCurrentIndex(rang)
    notes._inserer_choix(rang)
    assert notes.texte.toPlainText() == "A = {{activite}}"
    assert notes.insertion.currentIndex() == 0


def test_la_fiche_montre_les_valeurs_et_suit_son_facteur_k(espace, notes, fiche):
    notes.texte.setPlainText("A = {{activite}}")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.notes[0].toPlainText() == "A = 100 ± 2 MBq (k = 2)"
    fiche.champ_k.setValue(3.0)
    assert fiche.notes[0].toPlainText() == "A = 100 ± 3 MBq (k = 3)"
    assert fiche.notes[0].isReadOnly()


def test_une_correction_sur_page_validee_change_l_affichage_pas_la_note(
    espace, notes, fiche
):
    notes.texte.setPlainText("A = {{activite:valeur}}")
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()
    changer_scalaire(espace, "200")
    assert espace.demander_correction()
    page = relue(espace)
    assert page.statut == "corrigée"
    assert page.onglets[0].texte == "A = {{activite:valeur}}"
    fiche.afficher(espace.archive, page.id)
    assert fiche.notes[0].toPlainText() == "A = 200 MBq"
    # La correction reste tracée avec son motif, sur la mesure elle-même.
    assert espace.archive.corrections(page.id)[-1].motif == "Motif"


def test_le_texte_saisi_ne_devient_pas_une_mise_en_forme(espace, notes, fiche):
    notes.texte.setPlainText("<b>gras</b> a < b")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.notes[0].toPlainText() == "<b>gras</b> a < b"


def test_les_lignes_d_une_note_restent_dans_la_fiche(espace, notes, fiche):
    notes.texte.setPlainText("un\ndeux")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    assert fiche.notes[0].toPlainText().split() == ["un", "deux"]


# --- Captures ----------------------------------------------------------------------------


@pytest.fixture
def graphe(espace, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    resultat = espace.ajouter_onglet("graphes")
    return resultat


def test_une_capture_est_gardee_dans_la_page_et_inseree_dans_les_notes(
    espace, notes, graphe
):
    graphe.demander_capture()
    assert graphe.message_capture.text() == "Capture ajoutée à l'onglet de notes."
    # On reste sur le graphe : capturer ne doit pas interrompre le travail.
    assert espace.onglets.currentWidget() is graphe
    assert len(notes.photos) == 1
    assert notes.bande.vignettes[0].lisible
    assert espace.enregistrer_maintenant()

    page = relue(espace)
    photo = page.onglets[0].photos[0]
    assert photo.chemin.startswith("pieces_jointes/graphe_Graphes_")
    assert photo.chemin.endswith(".png")
    assert "k = 2" in photo.legende
    assert photo.capture.tableau == "t"
    assert photo.chemin in [p.chemin for p in page.pieces_jointes]
    fichier = espace.archive.racine / "pages" / page.id / photo.chemin
    image = QImage(str(fichier))
    assert not image.isNull()
    # Assez nette pour une impression, et au même format quelle que soit la fenêtre.
    assert (image.width(), image.height()) == (1600, 1000)
    assert espace.erreurs == []


def test_le_format_d_une_capture_ne_depend_pas_de_la_fenetre(
    espace, notes, graphe, qtbot
):
    taille = graphe.toile.canevas.size()
    espace.resize(1200, 300)
    qtbot.wait(50)
    graphe.demander_capture()
    photo = notes.photos[0]
    chemin = espace.pieces.emplacement(photo.chemin)
    image = QImage(str(chemin))
    assert (image.width(), image.height()) == (1600, 1000)
    # Le graphe à l'écran n'a pas bougé pour autant.
    assert graphe.toile.figure.get_size_inches()[0] > 0
    assert taille.isValid()


def test_le_k_de_la_legende_est_celui_de_la_capture(espace, notes, graphe):
    graphe.champ_k.setValue(3.0)
    graphe.demander_capture()
    assert "k = 3" in notes.photos[0].legende


def test_une_capture_crée_un_onglet_de_notes_s_il_n_y_en_a_pas(espace, notes, graphe):
    assert espace.demander_retrait_onglet(0)
    graphe.demander_capture()
    assert espace.onglets.currentWidget() is graphe
    nouvelles = [e for e in espace.editeurs() if isinstance(e, EditeurNotes)]
    assert len(nouvelles) == 1
    assert len(nouvelles[0].photos) == 1


def test_une_capture_signale_que_le_tableau_a_change(espace, notes, graphe, fiche):
    graphe.demander_capture()
    assert espace.enregistrer_maintenant()
    assert notes.bande.vignettes[0].alerte.text() == ""

    tableau = next(e for e in espace.editeurs() if isinstance(e, EditeurTableau))
    tableau.grille.item(0, 1).setText("90")
    assert espace.enregistrer_maintenant()
    assert "« t » a changé depuis" in notes.bande.vignettes[0].alerte.text()
    fiche.afficher(espace.archive, espace.page.id)
    vignette = fiche.bandes_photos[0].vignettes[0]
    assert "« t » a changé depuis" in vignette.alerte.text()
    # L'image reste la même : elle garde ce qu'on a vu ce jour-là.
    photo = relue(espace).onglets[0].photos[0]
    assert (espace.archive.racine / "pages" / espace.page.id / photo.chemin).is_file()


def test_sans_graphe_la_capture_s_explique(espace, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    vide = EspaceSaisie()
    qtbot.addWidget(vide)
    arc2 = espace.archive
    vide.installer(arc2)
    assert vide.creer_page(DescriptionPage("Vide", HEURE, "G.", None, ()))
    graphe = vide.ajouter_onglet("graphes")
    vide.show()
    qtbot.waitExposed(vide)
    graphe.demander_capture()
    assert graphe.message_capture.text() == "Aucun graphe à capturer pour l'instant."
    assert vide.pieces.pieces == ()


def test_une_capture_sur_page_validee_demande_un_motif(espace, notes, graphe):
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()
    relu = espace.editeurs()[-1]
    espace.onglets.setCurrentWidget(relu)
    relu.demander_capture()
    assert espace.modifications_en_attente()
    assert relue(espace).pieces_jointes == ()
    assert espace.demander_correction()
    assert len(relue(espace).pieces_jointes) == 1


def test_la_capture_depuis_l_onglet_d_ajustement(espace, notes, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    editeur = espace.ajouter_onglet("ajustements")
    editeur.champ_nom.setText("fit")
    assert editeur.demander_ajustement(), editeur.message.text()
    editeur.demander_capture()
    assert "Capture ajoutée" in editeur.coefficients.text()
    assert len(notes.photos) == 1
    assert notes.photos[0].capture.tableau == "t"
    assert espace.onglets.currentWidget() is editeur
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []
