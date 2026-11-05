"""Vérifie l'onglet de calculs : résultats, enregistrement, périmés et fiche."""

from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from medphystools import Archive
from medphystools.depot import Depot
from medphystools.incertitudes import Composante
from medphystools.interface.espace_saisie import DescriptionPage, EspaceSaisie
from medphystools.interface.fiche_page import FichePage
from medphystools.interface.onglet_calculs import (
    DialogueBudget,
    DialogueCourbe,
    EditeurCalculs,
    ecrire_resultat,
)
from medphystools.interface.onglet_scalaires import EditeurScalaires
from medphystools.interface.onglet_tableau import EditeurTableau
from medphystools.interface.texte_en_grand import AffichageEnGrand
from medphystools.page import ColonneCalculee, GrandeurDerivee, Scalaire

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
CERTIFICAT = Composante(
    type="B", valeur=1.0, relative=True, source="certificat", erreur_commune="CERT"
)


def scalaire(nom, valeur, composantes=(CERTIFICAT,), unite="MBq"):
    return Scalaire(nom=nom, valeur=valeur, unite=unite, composantes=composantes)


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
    for ligne, (nom, valeur) in zip(
        (scalaires.lignes[0], scalaires.ajouter_ligne()), (("a", "100"), ("b", "50"))
    ):
        ligne.nom.setText(nom)
        ligne.valeur.setText(valeur)
        ligne.unite.setText("MBq")
        ligne.composantes = (CERTIFICAT,)
    scalaires.ligne_modifiee()
    assert resultat.enregistrer_maintenant()
    assert resultat.erreurs == []
    return resultat


@pytest.fixture
def editeur(espace):
    resultat = espace.ajouter_onglet("calculs")
    assert isinstance(resultat, EditeurCalculs)
    return resultat


def relue(espace):
    return espace.archive.lire_page(espace.page.id)


def remplir(ligne, nom, formule, unite="", commentaire=""):
    ligne.nom.setText(nom)
    ligne.formule.setText(formule)
    ligne.unite.setText(unite)
    ligne.commentaire.setText(commentaire)


def test_le_resultat_s_affiche_pendant_la_frappe(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, "somme", "a + b")
    # u = (1 + 0,5) = 1,5 MBq à cause du certificat commun, donc U = 3 MBq à k = 2.
    assert ligne.affichage.text() == "150 ± 3 MBq (2 %)"
    assert ligne.message.isHidden()


def test_le_rapport_compense_le_certificat_a_l_ecran(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, "rapport", "a / b")
    assert ligne.affichage.text() == "2 ± 0 (0 %)"


def test_changer_k_change_l_affichage_sans_rien_enregistrer(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    tete = Depot(espace.archive.racine).tete()
    editeur.champ_k.setValue(3.0)
    assert editeur.lignes[0].affichage.text() == "150 ± 4.5 MBq (3 %)"
    assert not espace.modifications_en_attente()
    assert Depot(espace.archive.racine).tete() == tete


def test_un_calcul_s_enregistre_avec_sa_formule_sans_toucher_aux_valeurs(
    espace, editeur
):
    avant = relue(espace).grandeur("a")
    remplir(editeur.lignes[0], "somme", "a + b", commentaire="Total")
    assert espace.enregistrer_maintenant()

    page = relue(espace)
    derivee = page.grandeur("somme")
    assert isinstance(derivee, GrandeurDerivee)
    assert derivee.formule == "a + b"
    assert derivee.valeur == 150.0
    assert derivee.unite == "MBq"
    assert derivee.incertitude_type == pytest.approx(1.5)
    assert derivee.commentaire == "Total"
    assert page.grandeur("a") == avant
    assert page.onglets[-1].grandeurs == ("somme",)
    assert espace.archive.modifications_non_tracees() == []


def test_un_calcul_se_retrouve_a_la_reouverture_sans_modifier_la_page(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b", commentaire="Total")
    assert espace.enregistrer_maintenant()
    tete = Depot(espace.archive.racine).tete()

    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert isinstance(relu, EditeurCalculs)
    ligne = relu.lignes[0]
    assert ligne.nom.text() == "somme"
    assert ligne.formule.text() == "a + b"
    # L'unité automatique ne se fige pas : le champ reste vide, et suit la formule.
    assert ligne.unite.text() == ""
    assert ligne.commentaire.text() == "Total"
    assert ligne.affichage.text() == "150 ± 3 MBq (2 %)"
    assert not espace.modifications_en_attente()
    assert espace.enregistrer_maintenant()
    assert Depot(espace.archive.racine).tete() == tete


def test_l_unite_suit_la_formule_apres_la_reouverture(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    assert espace.ouvrir_page(espace.page.id)
    ligne = espace.editeurs()[-1].lignes[0]
    ligne.formule.setText("a / b")
    assert ligne.erreur is None
    assert ligne.resultat.unite == "1"
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("somme").unite == "1"


def test_une_unite_vraiment_demandee_reste_dans_le_champ(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b", unite="kBq")
    assert espace.enregistrer_maintenant()
    assert espace.ouvrir_page(espace.page.id)
    ligne = espace.editeurs()[-1].lignes[0]
    assert ligne.unite.text() == "kBq"
    assert not espace.modifications_en_attente()


def test_l_unite_demandee_est_gardee(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b", unite="kBq")
    assert editeur.lignes[0].affichage.text() == "150000 ± 3e+03 kBq (2 %)"
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("somme").valeur == pytest.approx(150000.0)


def test_une_ligne_vide_est_ignoree(espace, editeur):
    assert editeur.erreurs() == []
    assert editeur.grandeurs() == []
    assert espace.enregistrer_maintenant()


@pytest.mark.parametrize(
    ("nom", "formule", "attendu"),
    [
        ("", "a + b", "le nom est obligatoire"),
        ("x", "", "la formule est obligatoire"),
        ("x", "a + absent", "Aucune grandeur « absent »"),
        ("x", "open('f')", "fonctions à un argument"),
        ("nom invalide", "a", "invalide"),
    ],
)
def test_une_ligne_incorrecte_s_explique_et_n_est_pas_enregistree(
    espace, editeur, nom, formule, attendu
):
    ligne = editeur.lignes[0]
    remplir(ligne, nom, formule)
    assert attendu in ligne.message.text()
    assert not ligne.message.isHidden()
    assert ligne.affichage.text() == "—"
    assert not ligne.bouton_budget.isEnabled()
    assert attendu in espace.saisies_incompletes()[0]
    assert espace.enregistrer_maintenant()
    assert [g.nom for g in relue(espace).mesures] == ["a", "b"]


def test_une_erreur_garde_le_dernier_resultat(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    editeur.lignes[0].formule.setText("a + absent")
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("somme").formule == "a + b"


def test_un_nom_deja_pris_est_signale_sans_rien_ecraser(espace, editeur):
    remplir(editeur.lignes[0], "a", "b * 2")
    assert espace.saisies_incompletes() == [
        "le nom « a » est déjà utilisé dans cette page"
    ]
    assert not espace.enregistrer_maintenant()
    assert relue(espace).grandeur("a").valeur == 100.0


def test_deux_lignes_de_meme_nom_sont_signalees(espace, editeur):
    remplir(editeur.lignes[0], "x", "a")
    remplir(editeur.ajouter_ligne(), "x", "b")
    assert "nom « x » utilisé deux fois" in espace.saisies_incompletes()


def test_un_calcul_peut_utiliser_le_resultat_d_un_autre(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b")
    seconde = editeur.ajouter_ligne()
    remplir(seconde, "moitie", "somme / 2")
    assert "Aucune grandeur « somme »" in seconde.message.text()
    # Dès que le premier résultat est enregistré, le second se calcule à son tour.
    assert espace.enregistrer_maintenant()
    assert seconde.affichage.text() == "75 ± 1.5 MBq (2 %)"
    assert espace.modifications_en_attente()
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("moitie").valeur == 75.0
    assert not espace.modifications_en_attente()


def changer_a(espace, valeur):
    scalaires = next(e for e in espace.editeurs() if isinstance(e, EditeurScalaires))
    scalaires.lignes[0].valeur.setText(valeur)
    scalaires.lignes[0].valeur.textEdited.emit(valeur)


def test_un_brouillon_met_son_calcul_a_jour_apres_une_correction_de_valeur(
    espace, editeur
):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    espace.onglets.setCurrentIndex(1)
    changer_a(espace, "200")
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("a").valeur == 200.0
    # La première passe a enregistré la valeur ; le résultat suit à la suivante.
    assert editeur.lignes[0].affichage.text().startswith("250 ")
    assert espace._minuteur.isActive()
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("somme").valeur == 250.0
    assert not espace.modifications_en_attente()


def test_une_page_validee_demande_un_motif_pour_son_resultat(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()

    changer_a(espace, "200")
    assert espace.demander_correction()
    page = relue(espace)
    assert page.grandeur("a").valeur == 200.0
    # Rien ne change tout seul : l'ancien résultat reste, et l'écran propose le nouveau.
    assert page.grandeur("somme").valeur == 150.0
    assert espace.modifications_en_attente()
    relu = espace.editeurs()[-1]
    assert relu.lignes[0].affichage.text().startswith("250 ")

    assert espace.demander_correction()
    page = relue(espace)
    assert page.grandeur("somme").valeur == 250.0
    historique = espace.archive.corrections(page.id)
    assert len(historique) == 2
    assert not espace.modifications_en_attente()


def test_retirer_un_calcul_le_retire_de_la_page(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    tete = Depot(espace.archive.racine).tete()
    editeur.retirer_ligne(editeur.lignes[0])
    assert espace.enregistrer_maintenant()
    assert [g.nom for g in relue(espace).mesures] == ["a", "b"]
    assert espace.archive.version_page(espace.page.id, tete).grandeur("somme")


def test_inserer_un_nom_dans_la_formule_en_cours(editeur):
    ligne = editeur.lignes[0]
    noms = [editeur.insertion.itemText(i) for i in range(editeur.insertion.count())]
    assert noms[:3] == ["Insérer…", "a", "b"]
    assert "exp()" in noms
    assert "sqrt()" in noms
    assert "pi" in noms
    editeur.inserer("exp()")
    assert ligne.formule.text() == "exp()"
    assert ligne.formule.cursorPosition() == 4
    editeur.inserer("a")
    assert ligne.formule.text() == "exp(a)"


def test_le_choix_dans_la_liste_insere_et_revient_au_debut(editeur):
    rang = editeur.insertion.findText("b")
    editeur.insertion.setCurrentIndex(rang)
    editeur._inserer_choix(rang)
    assert editeur.lignes[0].formule.text() == "b"
    assert editeur.insertion.currentIndex() == 0


def test_le_commentaire_s_affiche_en_grand(editeur):
    ligne = editeur.lignes[0]
    ligne.nom.setText("somme")
    ligne.commentaire.setText("Long")
    assert ligne.commentaire.findChild(AffichageEnGrand) is not None


def test_le_budget_montre_chaque_source(espace, editeur, qtbot, monkeypatch):
    remplir(editeur.lignes[0], "somme", "a + b")
    vus = []
    monkeypatch.setattr(editeur, "_montrer_dialogue", vus.append)
    editeur.lignes[0].bouton_budget.click()
    dialogue = vus[0]
    qtbot.addWidget(dialogue)
    assert isinstance(dialogue, DialogueBudget)
    assert dialogue.windowTitle() == "Budget de somme"
    assert dialogue.resultat.text() == "somme = 150 ± 3 MBq (2 %), k = 2"
    assert dialogue.table.rowCount() == 1
    assert dialogue.table.item(0, 0).text() == "CERT"
    assert dialogue.table.item(0, 1).text() == "B"
    assert dialogue.table.item(0, 2).text() == "1.5"
    assert dialogue.table.item(0, 3).text() == "100.0 %"


def test_le_vrai_dialogue_de_budget_s_ouvre_et_se_ferme(editeur, qtbot):
    remplir(editeur.lignes[0], "somme", "a + b")

    def fermer():
        QApplication.activeModalWidget().reject()

    QTimer.singleShot(50, fermer)
    editeur.lignes[0].demander_budget()


def test_ecrire_un_resultat_sans_dimension_ou_nul():
    base = scalaire(
        "x", 0.0, (Composante(type="B", valeur=1.0, unite="1", source="s"),), "1"
    )
    assert ecrire_resultat(base.budget(), 2.0) == "0 ± 2"


# --- Fiche -------------------------------------------------------------------------------


@pytest.fixture
def fiche(qtbot):
    resultat = FichePage()
    qtbot.addWidget(resultat)
    return resultat


def test_la_fiche_montre_le_resultat_sa_formule_et_son_budget(espace, editeur, fiche):
    remplir(editeur.lignes[0], "somme", "a + b", commentaire="Total")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    table = fiche.table_mesures
    noms = [table.item(i, 0).text() for i in range(table.rowCount())]
    ligne = noms.index("somme")
    assert table.item(ligne, 3).text() == "3 MBq"
    assert table.item(ligne, 4).text() == "2 %"
    assert table.item(ligne, 5).text() == "Total"
    assert table.item(ligne, 6).text() == "a + b"
    assert "CERT : u = 1.5 MBq (B, normale, 100 %)" in table.item(ligne, 3).toolTip()
    assert fiche.alertes_calculs == []
    fiche.champ_k.setValue(3.0)
    assert table.item(ligne, 3).text() == "4.5 MBq"


def test_la_fiche_arrondit_un_resultat_mais_pas_une_valeur_saisie(
    espace, editeur, fiche
):
    remplir(editeur.lignes[0], "tiers", "a / 3")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    table = fiche.table_mesures
    noms = [table.item(i, 0).text() for i in range(table.rowCount())]
    etiquette = table.cellWidget(noms.index("tiers"), 1)
    assert etiquette.text() == "33.3333"
    assert "33.33333333" in etiquette.toolTip()
    assert table.cellWidget(noms.index("a"), 1).text() == "100"


def test_une_grandeur_sans_dimension_n_affiche_pas_l_unite_1(espace, editeur, fiche):
    remplir(editeur.lignes[0], "somme", "(a + b) / b")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    table = fiche.table_mesures
    noms = [table.item(i, 0).text() for i in range(table.rowCount())]
    assert not table.item(noms.index("somme"), 3).text().endswith(" 1")


def test_la_formule_et_le_commentaire_s_affichent_en_grand_dans_la_fiche(
    espace, editeur, fiche, qtbot, monkeypatch
):
    remplir(editeur.lignes[0], "somme", "a + b", commentaire="Total")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    fiche.show()
    qtbot.waitExposed(fiche)
    table = fiche.table_mesures
    noms = [table.item(i, 0).text() for i in range(table.rowCount())]
    ligne = noms.index("somme")
    affichage = table.findChild(AffichageEnGrand)
    formule = table.visualItemRect(table.item(ligne, 6)).center()
    commentaire = table.visualItemRect(table.item(ligne, 5)).center()
    nom = table.visualItemRect(table.item(ligne, 0)).center()
    assert affichage.lire_texte(formule) == "a + b"
    assert affichage.titre == "Formule"
    assert affichage.lire_texte(commentaire) == "Total"
    assert affichage.titre == "Commentaire"
    assert affichage.lire_texte(nom) == ""
    assert len(table.findChildren(AffichageEnGrand)) == 1


def test_la_fiche_signale_un_resultat_perime(espace, editeur, fiche):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    page = relue(espace)
    mesures = tuple(
        g.model_copy(update={"valeur": 200.0}) if g.nom == "a" else g
        for g in page.mesures
    )
    espace.archive.modifier_page(page.model_copy(update={"mesures": mesures}))
    fiche.afficher(espace.archive, page.id)
    assert len(fiche.alertes_calculs) == 1
    assert fiche.alertes_calculs[0].text() == (
        "somme : Résultat à recalculer : ses grandeurs ont changé, il vaudrait 250 MBq."
    )


def test_la_recherche_trouve_une_formule(espace, editeur):
    remplir(editeur.lignes[0], "decroissance", "a * exp(-ln(2))")
    assert espace.enregistrer_maintenant()
    assert espace.archive.chercher_identifiants(texte="decroissance") == [
        espace.page.id
    ]


def test_l_api_relit_un_resultat_enregistre(espace, editeur):
    remplir(editeur.lignes[0], "somme", "a + b")
    assert espace.enregistrer_maintenant()
    lue = Archive(espace.archive.racine).page(espace.page.id)
    assert lue.mesures["somme"].valeur == 150.0
    assert lue.mesures["somme"].elargie(2) == pytest.approx(3.0)
    assert lue.mesures["somme"].unite == "MBq"


# --- Colonnes calculées ------------------------------------------------------------------


MBQ = Composante(type="B", valeur=0.5, unite="MBq", source="résolution")
HEURES = Composante(type="B", valeur=0.1, unite="h", source="horloge")


def saisir_tableau(espace, nom, colonnes, lignes):
    """Remplit un onglet de tableau : colonnes de (nom, unité, composante)."""
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText(nom)
    while len(tableau.colonnes) < len(colonnes):
        tableau.ajouter_colonne()
    for configuration, (nom_colonne, unite, composante) in zip(
        tableau.colonnes, colonnes
    ):
        configuration.nom.setText(nom_colonne)
        configuration.unite.setText(unite)
        configuration.composantes = (composante,)
    tableau.coller("\n".join("\t".join(ligne) for ligne in lignes))
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []
    return tableau


@pytest.fixture
def releves(espace):
    return saisir_tableau(
        espace,
        "t",
        [("heure", "h", HEURES), ("act", "MBq", CERTIFICAT)],
        [["0", "100"], ["2", "50"], ["4", "25"]],
    )


def editeur_calculs(espace):
    resultat = espace.ajouter_onglet("calculs")
    assert isinstance(resultat, EditeurCalculs)
    return resultat


def test_une_formule_sur_une_colonne_donne_une_colonne(espace, releves):
    calculs = editeur_calculs(espace)
    ligne = calculs.lignes[0]
    remplir(ligne, "rapport", "t.act / a")
    assert ligne.erreur is None
    assert ligne.affichage.text() == "3 lignes, dans t"
    assert ligne.bouton_budget.isEnabled()
    assert espace.enregistrer_maintenant()

    colonne = relue(espace).grandeur("rapport")
    assert isinstance(colonne, ColonneCalculee)
    assert colonne.formule == "t.act / a"
    assert colonne.valeurs == pytest.approx((1.0, 0.5, 0.25))
    assert colonne.tableaux == ("t",)
    # Le certificat commun à t.act et à a se compense à chaque ligne.
    assert colonne.incertitudes_types == pytest.approx((0.0, 0.0, 0.0), abs=1e-12)


def test_une_colonne_calculee_ne_touche_pas_au_csv(espace, releves):
    fichier = espace.archive.racine / "pages" / espace.page.id / "brut" / "t.csv"
    avant = fichier.read_bytes()
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    assert fichier.read_bytes() == avant
    assert list(espace.archive.lire_tableau(espace.page.id, "t").columns) == [
        "heure",
        "act",
    ]
    assert relue(espace).grandeur("t").colonnes[1].nom == "act"


def test_le_tableau_montre_la_colonne_marquee_et_en_lecture_seule(
    espace, releves, qtbot
):
    espace.show()
    qtbot.waitExposed(espace)
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2", commentaire="x2")
    assert espace.enregistrer_maintenant()

    espace.onglets.setCurrentWidget(releves)
    assert releves.grille_calculee.isVisible()
    configuration = releves.configuration_calculee
    assert [configuration.item(i, 0).text() for i in range(5)] == [
        "double",
        "Calculée",
        "MBq",
        "U à k = 2",
        "x2",
    ]
    assert configuration.item(4, 0).toolTip() == "t.act * 2"
    # 200 ± 2·(1 % de 100)·2 = 4 MBq à k = 2.
    assert releves.grille_calculee.item(0, 0).text() == "200 ± 4"
    assert releves.grille_calculee.item(2, 0).text() == "50 ± 1"
    assert (
        releves.grille_calculee.editTriggers()
        == releves.grille_calculee.EditTrigger.NoEditTriggers
    )
    # La grille saisie n'a pas changé : rien à enregistrer, et ses colonnes restent les siennes.
    assert releves.grille.columnCount() == 2
    assert not espace.modifications_en_attente()


def test_les_colonnes_calculees_suivent_les_colonnes_saisies(espace, releves, qtbot):
    """Dans la vraie mise en page : côte à côte, et non repoussées au bord de la fenêtre."""
    espace.resize(1300, 800)
    espace.show()
    qtbot.waitExposed(espace)
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    espace.onglets.setCurrentWidget(releves)
    qtbot.wait(100)
    ecart = releves.grille_calculee.x() - (releves.grille.x() + releves.grille.width())
    assert 0 <= ecart <= 12
    # Toutes les colonnes saisies restent entièrement visibles, sans faire défiler.
    assert releves.grille.horizontalScrollBar().maximum() == 0
    assert releves.grille.viewport().width() >= sum(
        releves.grille.columnWidth(j) for j in range(releves.grille.columnCount())
    )
    assert releves.grille_calculee.y() == releves.grille.y()
    assert releves.configuration_calculee.height() == releves.configuration.height()

    # Une colonne saisie de plus reste visible, et la zone s'élargit d'autant.
    largeur = releves.grille.maximumWidth()
    releves.ajouter_colonne("autre")
    assert releves.grille.maximumWidth() > largeur

    # Sans colonne calculée, la grille reprend toute la largeur.
    editeur = espace.editeurs()[-1]
    editeur.retirer_ligne(editeur.lignes[0])
    releves.retirer_colonne(releves.colonnes[-1])
    assert espace.enregistrer_maintenant()
    qtbot.wait(100)
    assert releves.grille.width() > 1000


def test_sans_colonne_calculee_rien_ne_s_ajoute_au_tableau(espace, releves, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(releves)
    assert not releves.grille_calculee.isVisible()
    assert not releves.configuration_calculee.isVisible()


def test_la_colonne_reste_affichee_a_la_reouverture(espace, releves):
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    assert espace.ouvrir_page(espace.page.id)
    tableau = next(e for e in espace.editeurs() if isinstance(e, EditeurTableau))
    assert tableau.grille_calculee.item(1, 0).text() == "100 ± 2"
    calculs = espace.editeurs()[-1]
    assert calculs.lignes[0].formule.text() == "t.act * 2"
    assert not espace.modifications_en_attente()


def test_une_colonne_s_affiche_dans_tous_les_tableaux_concernes(espace, releves):
    autre = saisir_tableau(
        espace,
        "u",
        [("ref", "MBq", MBQ), ("temps", "h", HEURES)],
        [["50", "1"], ["25", "2"], ["10", "3"]],
    )
    remplir(editeur_calculs(espace).lignes[0], "r", "t.act / u.ref")
    assert espace.enregistrer_maintenant()
    colonne = relue(espace).grandeur("r")
    assert colonne.tableaux == ("t", "u")
    assert colonne.valeurs == pytest.approx((2.0, 2.0, 2.5))
    for tableau in (releves, autre):
        assert tableau.configuration_calculee.item(0, 0).text() == "r"
        assert tableau.grille_calculee.item(0, 0).text().startswith("2 ± ")


def test_des_tableaux_de_longueurs_differentes_s_expliquent(espace, releves):
    saisir_tableau(
        espace,
        "u",
        [("ref", "MBq", MBQ), ("temps", "h", HEURES)],
        [["50", "1"], ["25", "2"]],
    )
    ligne = editeur_calculs(espace).lignes[0]
    remplir(ligne, "r", "t.act / u.ref")
    assert "même nombre de lignes" in ligne.message.text()
    assert ligne.resultat is None


def test_une_ligne_impossible_est_signalee_sans_bloquer(espace, releves):
    ligne = editeur_calculs(espace).lignes[0]
    remplir(ligne, "inverse", "1 / t.heure")
    assert ligne.erreur is None
    assert ligne.message.text() == (
        "inverse : pas de résultat aux lignes 1 (voir leur budget)."
    )
    assert espace.saisies_incompletes() == []
    assert espace.enregistrer_maintenant()
    colonne = relue(espace).grandeur("inverse")
    assert colonne.valeurs[0] is None
    assert releves.grille_calculee.item(0, 0).text() == "—"
    assert releves.grille_calculee.item(1, 0).text().startswith("0.5 ± ")


def changer_cellule(tableau, ligne, colonne, texte):
    tableau.grille.item(ligne, colonne).setText(texte)


def test_changer_une_mesure_recalcule_la_colonne_d_un_brouillon(espace, releves):
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    changer_cellule(releves, 0, 1, "200")
    assert espace.enregistrer_maintenant()
    assert espace._minuteur.isActive()
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("double").valeurs[0] == pytest.approx(400.0)
    assert releves.grille_calculee.item(0, 0).text() == "400 ± 8"
    assert not espace.modifications_en_attente()


def test_ajouter_une_ligne_au_tableau_allonge_la_colonne(espace, releves):
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    releves.grille.setCurrentCell(3, 0)
    releves.coller("6\t10")
    assert espace.enregistrer_maintenant()
    assert espace.enregistrer_maintenant()
    assert relue(espace).grandeur("double").valeurs[3] == pytest.approx(20.0)
    assert releves.grille_calculee.item(3, 0).text() == "20 ± 0.4"


def test_une_page_validee_demande_un_motif_pour_sa_colonne(espace, releves):
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()
    tableau = next(e for e in espace.editeurs() if isinstance(e, EditeurTableau))
    changer_cellule(tableau, 0, 1, "200")
    assert espace.demander_correction()
    assert relue(espace).grandeur("double").valeurs[0] == pytest.approx(200.0)
    assert espace.modifications_en_attente()
    assert espace.demander_correction()
    assert relue(espace).grandeur("double").valeurs[0] == pytest.approx(400.0)
    assert len(espace.archive.corrections(espace.page.id)) == 2


def test_retirer_le_calcul_retire_la_colonne_du_tableau(espace, releves):
    calculs = editeur_calculs(espace)
    remplir(calculs.lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    calculs.retirer_ligne(calculs.lignes[0])
    assert espace.enregistrer_maintenant()
    assert all(g.nom != "double" for g in relue(espace).mesures)
    assert releves.colonnes_calculees() == []
    assert releves.grille_calculee.isHidden()


def test_les_colonnes_sont_proposees_a_l_insertion(espace, releves):
    calculs = editeur_calculs(espace)
    noms = [calculs.insertion.itemText(i) for i in range(calculs.insertion.count())]
    assert "t.act" in noms
    assert "t.heure" in noms
    assert "a" in noms
    remplir(calculs.lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    noms = [calculs.insertion.itemText(i) for i in range(calculs.insertion.count())]
    assert "t.double" in noms


def test_le_budget_d_une_colonne_se_lit_ligne_par_ligne(
    espace, releves, qtbot, monkeypatch
):
    calculs = editeur_calculs(espace)
    remplir(calculs.lignes[0], "inverse", "100 / t.heure")
    vus = []
    monkeypatch.setattr(calculs, "_montrer_dialogue", vus.append)
    calculs.lignes[0].bouton_budget.click()
    dialogue = vus[0]
    qtbot.addWidget(dialogue)
    # La ligne 1 divise par zéro : le dialogue l'explique au lieu de planter.
    assert "Ligne 1" in dialogue.resultat.text()
    assert "division par zéro" in dialogue.resultat.text()
    assert dialogue.table.rowCount() == 0
    dialogue.champ_ligne.setValue(2)
    assert dialogue.resultat.text().startswith("inverse, ligne 2 = 50 ± ")
    assert dialogue.table.item(0, 0).text() == "t.heure[2] : horloge"
    assert dialogue.champ_ligne.maximum() == 3


def test_la_fiche_montre_la_colonne_marquee_et_suit_son_facteur_k(
    espace, releves, fiche
):
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    fiche.afficher(espace.archive, espace.page.id)
    table = fiche.tables["t"]
    assert table.columnCount() == 3
    assert table.horizontalHeaderItem(2).text() == "double (MBq), calculée"
    assert table.horizontalHeaderItem(2).toolTip() == "t.act * 2"
    assert table.item(0, 2).text() == "200 ± 4"
    fiche.champ_k.setValue(3.0)
    assert fiche.tables["t"].item(0, 2).text() == "200 ± 6"
    assert fiche.alertes_calculs == []


def test_la_fiche_signale_une_colonne_perimee(espace, releves, fiche):
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    page = relue(espace)
    espace.archive.modifier_page(
        page,
        tableaux={"t": {"heure": [0.0, 2.0, 4.0], "act": [999.0, 50.0, 25.0]}},
    )
    fiche.afficher(espace.archive, page.id)
    assert fiche.alertes_calculs[0].text() == (
        "double : Colonne à recalculer : ses grandeurs ont changé."
    )


def test_l_api_lit_la_colonne_avec_le_tableau(espace, releves):
    remplir(editeur_calculs(espace).lignes[0], "double", "t.act * 2")
    assert espace.enregistrer_maintenant()
    lue = Archive(espace.archive.racine).page(espace.page.id)
    tableau = lue.tableau("t")
    assert list(tableau["double"]) == pytest.approx([200.0, 100.0, 50.0])
    assert tableau.attrs["unites"]["double"] == "MBq"
    assert lue.mesures["double"].incertitudes_types[0] == pytest.approx(2.0)


# --- Courbes dérivée et primitive ------------------------------------------------------


def _tableau_pour_courbe(espace):
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText("t")
    x, y = tableau.colonnes[:2]
    x.nom.setText("x")
    x.unite.setText("s")
    x.composantes = (Composante(type="B", valeur=0.1, unite="s", source="horloge"),)
    y.nom.setText("y")
    y.unite.setText("m")
    y.composantes = (Composante(type="B", valeur=0.5, unite="m", source="regle"),)
    tableau.ecrire_cellules([["0", "0"], ["1", "1"], ["2", "4"]], 0, 0)
    assert espace.enregistrer_maintenant()
    return tableau


def test_le_bouton_ajoute_la_courbe_derivee(espace, monkeypatch):
    tableau = _tableau_pour_courbe(espace)
    calculs = espace.ajouter_onglet("calculs")
    monkeypatch.setattr(calculs, "_choisir_courbe", lambda: ("derivee", "t", "y", "x"))
    assert calculs.demander_courbe()
    ligne = calculs.lignes[0]
    assert ligne.nom.text() == "d_y"
    assert ligne.formule.text() == "derivee(t.y, t.x)"
    assert espace.enregistrer_maintenant()
    colonne = espace.archive.lire_page(espace.page.id).grandeur("d_y")
    assert colonne.valeurs == pytest.approx((1.0, 2.0, 3.0))
    assert colonne.unite == "m/s"
    assert colonne.tableaux == ("t",)
    assert [c.nom for c in tableau.colonnes_calculees()] == ["d_y"]


def test_le_nom_de_la_courbe_ne_reprend_pas_un_nom_pris(espace):
    _tableau_pour_courbe(espace)
    calculs = espace.ajouter_onglet("calculs")
    assert calculs.ajouter_courbe("primitive", "t", "y", "x").nom.text() == "P_y"
    assert calculs.ajouter_courbe("primitive", "t", "y", "x").nom.text() == "P_y_2"


def test_le_dialogue_de_courbe_propose_les_colonnes(qtbot):
    dialogue = DialogueCourbe({"t": ["x", "y"], "u": ["a", "b", "c"]})
    qtbot.addWidget(dialogue)
    assert dialogue.choix() == ("derivee", "t", "y", "x")
    dialogue.operation.setCurrentIndex(1)
    dialogue.tableau.setCurrentText("u")
    assert dialogue.choix() == ("primitive", "u", "b", "a")
    dialogue.y.setCurrentText("a")
    assert not dialogue.bouton_ok.isEnabled()


def test_le_dialogue_de_courbe_sans_tableau_ne_propose_rien(qtbot):
    dialogue = DialogueCourbe({})
    qtbot.addWidget(dialogue)
    assert not dialogue.bouton_ok.isEnabled()
