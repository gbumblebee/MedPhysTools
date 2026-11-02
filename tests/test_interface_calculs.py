"""Vérifie l'onglet de calculs : résultats, enregistrement, périmés et fiche."""

from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pymphub import Archive
from pymphub.depot import Depot
from pymphub.incertitudes import Composante
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.onglet_calculs import (
    DialogueBudget,
    EditeurCalculs,
    ecrire_resultat,
)
from pymphub.interface.onglet_scalaires import EditeurScalaires
from pymphub.interface.texte_en_grand import AffichageEnGrand
from pymphub.page import GrandeurDerivee, Scalaire

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
    assert ligne.unite.text() == "MBq"
    assert ligne.commentaire.text() == "Total"
    assert ligne.affichage.text() == "150 ± 3 MBq (2 %)"
    assert not espace.modifications_en_attente()
    assert espace.enregistrer_maintenant()
    assert Depot(espace.archive.racine).tete() == tete


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
