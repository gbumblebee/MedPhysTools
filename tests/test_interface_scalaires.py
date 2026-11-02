"""Vérifie l'onglet de scalaires : unité à la frappe, incertitude, répétitions, clavier."""

import math
from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import QApplication

from pymphub import Archive
from pymphub.depot import Depot
from pymphub.edition import (
    conflits_de_noms,
    fusionner_grandeurs,
    lire_nombre,
    lire_serie,
    suggerer_nom,
)
from pymphub.incertitudes import Composante, composante_exacte
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.onglet_scalaires import (
    DialogueIncertitudes,
    DialogueRepetitions,
    EditeurScalaires,
)
from pymphub.page import (
    VERSION_SCHEMA,
    Identifiant,
    Page,
    Scalaire,
    depuis_json,
    vers_json,
)

PARIS = timezone(timedelta(hours=1))
HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=PARIS)
CERTIFICAT = Composante(type="B", valeur=1.0, unite="MBq", k=2.0, source="certificat")


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
    return resultat


@pytest.fixture
def editeur(espace):
    resultat = espace.ajouter_onglet("scalaires")
    assert isinstance(resultat, EditeurScalaires)
    return resultat


def remplir(
    ligne, nom="activite", valeur="10,5", unite="MBq", composantes=(CERTIFICAT,)
):
    ligne.nom.setText(nom)
    ligne.valeur.setText(valeur)
    ligne.valeur.textEdited.emit(valeur)
    ligne.unite.setText(unite)
    ligne.composantes = tuple(composantes)
    ligne.editeur.ligne_modifiee()
    ligne.actualiser()


def relue(espace):
    return espace.archive.lire_page(espace.page.id)


def commits(espace):
    return len(Depot(espace.archive.racine).commits())


# --- Saisie ---------------------------------------------------------------------------


def test_le_type_d_onglet_est_propose(espace):
    espace._remplir_menu_ajouter()
    assert "Scalaires" in [a.text() for a in espace.menu_ajouter.actions()]


def test_une_mesure_complete_s_enregistre(espace, editeur):
    remplir(editeur.lignes[0])
    assert espace.saisies_incompletes() == []
    assert espace.enregistrer_maintenant()
    grandeur = relue(espace).grandeur("activite")
    assert isinstance(grandeur, Scalaire)
    assert grandeur.valeur == 10.5
    assert grandeur.unite == "MBq"
    assert grandeur.composantes == (CERTIFICAT,)


def test_l_onglet_reference_ses_mesures(espace, editeur):
    remplir(editeur.lignes[0])
    espace.enregistrer_maintenant()
    assert relue(espace).onglets[1].grandeurs == ("activite",)


def test_la_mesure_se_relit_en_python(espace, editeur):
    remplir(editeur.lignes[0])
    espace.enregistrer_maintenant()
    mesure = espace.archive.page(espace.page.id).mesures["activite"]
    assert mesure.valeur == 10.5
    assert mesure.elargie() == pytest.approx(1.0)


def test_une_mesure_declenche_l_enregistrement_automatique(espace, editeur, qtbot):
    remplir(editeur.lignes[0])
    assert espace._minuteur.isActive()
    qtbot.waitUntil(lambda: bool(relue(espace).mesures), timeout=3000)


def test_ouvrir_la_page_redonne_les_memes_valeurs_sans_commit(espace, editeur):
    remplir(editeur.lignes[0], valeur="0.1")
    espace.enregistrer_maintenant()
    avant = commits(espace)
    espace.ouvrir_page(espace.page.id)
    assert not espace.modifications_en_attente()
    ligne = espace.onglets.widget(1).lignes[0]
    assert ligne.valeur.text() == "0.1"
    assert ligne.unite.text() == "MBq"
    espace.enregistrer_maintenant()
    assert commits(espace) == avant


@pytest.mark.parametrize("valeur", [0.1 + 0.2, 1e-30, 123456789.12345678, -2.5])
def test_un_nombre_se_relit_sans_perte(espace, editeur, valeur):
    remplir(editeur.lignes[0], valeur=repr(valeur))
    espace.enregistrer_maintenant()
    espace.ouvrir_page(espace.page.id)
    assert espace.onglets.widget(1).lignes[0].grandeur().valeur == valeur
    assert not espace.modifications_en_attente()


def test_une_virgule_decimale_est_acceptee(espace, editeur):
    remplir(editeur.lignes[0], valeur="1 234,5")
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").valeur == 1234.5


def test_un_identifiant_reste_exact(espace, editeur):
    ligne = editeur.lignes[0]
    ligne.nature.setCurrentIndex(1)
    ligne.nom.setText("lot")
    ligne.valeur.setText("007")
    assert espace.saisies_incompletes() == []
    espace.enregistrer_maintenant()
    grandeur = relue(espace).grandeur("lot")
    assert isinstance(grandeur, Identifiant)
    assert grandeur.valeur == "007"
    espace.ouvrir_page(espace.page.id)
    autre = espace.onglets.widget(1).lignes[0]
    assert autre.est_identifiant()
    assert autre.valeur.text() == "007"


def test_un_identifiant_n_a_ni_unite_ni_incertitude(editeur):
    ligne = editeur.lignes[0]
    ligne.nature.setCurrentIndex(1)
    assert not ligne.unite.isEnabled()
    assert not ligne.bouton_incertitude.isEnabled()


def test_horodatage_et_commentaire(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne)
    ligne.horodatage.setDateTime(QDateTime(2026, 11, 14, 10, 15, 0))
    ligne.commentaire.setText("au centre")
    espace.enregistrer_maintenant()
    grandeur = relue(espace).grandeur("activite")
    assert grandeur.commentaire == "au centre"
    assert grandeur.horodatage.tzinfo is not None
    assert grandeur.horodatage.replace(tzinfo=None) == datetime(2026, 11, 14, 10, 15)


def test_le_bouton_maintenant(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne)
    ligne.bouton_maintenant.click()
    assert ligne.grandeur().horodatage is not None


def test_sans_horodatage_le_champ_reste_vide(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne)
    assert ligne.grandeur().horodatage is None


def test_un_horodatage_inchange_garde_son_fuseau(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne)
    tokyo = datetime(2026, 11, 14, 8, 0, tzinfo=timezone(timedelta(hours=9)))
    grandeur = ligne.grandeur().model_copy(update={"horodatage": tokyo})
    espace.archive.modifier_page(
        Page.model_validate(
            espace.page.model_dump()
            | {
                "mesures": [grandeur],
                "onglets": [
                    {
                        "id": "s",
                        "type": "scalaires",
                        "titre": "S",
                        "grandeurs": ["activite"],
                    }
                ],
            }
        )
    )
    espace._charger(espace.archive.lire_page(espace.page.id))
    assert not espace.modifications_en_attente()
    assert espace.onglets.widget(0).lignes[0].grandeur().horodatage == tokyo


# --- Unité vérifiée à la frappe -------------------------------------------------------


@pytest.mark.parametrize(
    ("saisie", "suggestion"),
    [
        ("Mbq", "MBq"),
        ("Bq/ml", "Bq/mL"),
        ("g/cm^3", "g/cm**3"),
        ("mAs", "mA*s"),
    ],
)
def test_une_unite_fausse_est_signalee_pendant_la_frappe(editeur, saisie, suggestion):
    ligne = editeur.lignes[0]
    ligne.unite.setText(saisie)
    assert ligne.unite.property("invalide") is True
    assert not ligne.bouton_suggestion.isHidden()
    assert ligne.bouton_suggestion.text() == f"Utiliser {suggestion}"
    assert not ligne.message.isHidden()
    assert saisie in ligne.message.text()


def test_une_unite_correcte_n_est_pas_signalee(editeur):
    ligne = editeur.lignes[0]
    ligne.unite.setText("MBq")
    assert not ligne.unite.property("invalide")
    assert ligne.bouton_suggestion.isHidden()


def test_le_signalement_disparait_quand_l_unite_est_corrigee(editeur):
    ligne = editeur.lignes[0]
    ligne.unite.setText("Mbq")
    ligne.unite.setText("MBq")
    assert not ligne.unite.property("invalide")
    assert ligne.bouton_suggestion.isHidden()


def test_la_suggestion_s_accepte_d_un_clic(editeur):
    ligne = editeur.lignes[0]
    ligne.unite.setText("Mbq")
    ligne.bouton_suggestion.click()
    assert ligne.unite.text() == "MBq"
    assert ligne.bouton_suggestion.isHidden()


def test_la_suggestion_s_accepte_avec_la_touche_entree(editeur, qtbot):
    ligne = editeur.lignes[0]
    ligne.unite.setText("Mbq")
    qtbot.keyClick(ligne.unite, Qt.Key.Key_Return)
    assert ligne.unite.text() == "MBq"


def test_la_touche_entree_ne_change_pas_une_unite_correcte(editeur, qtbot):
    ligne = editeur.lignes[0]
    ligne.unite.setText("mCi")
    qtbot.keyClick(ligne.unite, Qt.Key.Key_Return)
    assert ligne.unite.text() == "mCi"


def test_une_unite_inconnue_est_signalee_sans_suggestion(editeur):
    ligne = editeur.lignes[0]
    ligne.unite.setText("pixel")
    assert ligne.unite.property("invalide") is True
    assert ligne.bouton_suggestion.isHidden()
    assert "pixel" in ligne.message.text()
    assert not ligne.accepter_suggestion()


def test_la_suggestion_n_est_jamais_appliquee_seule(editeur):
    ligne = editeur.lignes[0]
    ligne.unite.setText("Mbq")
    assert ligne.unite.text() == "Mbq"


# --- Saisie obligatoire ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({"unite": ""}, "unité"),
        ({"composantes": ()}, "incertitude"),
        ({"valeur": ""}, "valeur"),
        ({"nom": ""}, "nom"),
        ({"unite": "Mbq"}, "Mbq"),
        ({"valeur": "abc"}, "abc"),
    ],
)
def test_une_valeur_incomplete_ne_s_enregistre_pas(espace, editeur, champs, attendu):
    remplir(editeur.lignes[0], **champs)
    assert any(attendu in erreur for erreur in espace.saisies_incompletes())
    espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()
    assert "À compléter" in espace.etat.text()


def test_une_ligne_vide_n_est_pas_une_erreur(espace, editeur):
    assert editeur.lignes[0].est_vide()
    assert espace.saisies_incompletes() == []
    assert espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()
    assert relue(espace).onglets[1].grandeurs == ()


def test_une_valeur_exacte_declare_une_incertitude_nulle(espace, editeur):
    remplir(editeur.lignes[0], "nombre_coupes", "40", "1", (composante_exacte("1"),))
    espace.enregistrer_maintenant()
    mesure = espace.archive.page(espace.page.id).mesures["nombre_coupes"]
    assert mesure.elargie() == 0


def test_une_composante_incompatible_est_refusee(espace, editeur):
    regle = Composante(type="B", valeur=1.0, unite="mm", source="règle")
    remplir(editeur.lignes[0], composantes=(regle,))
    assert espace.saisies_incompletes()
    espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()


def test_une_ligne_incomplete_ne_retient_pas_les_autres(espace, editeur):
    remplir(editeur.lignes[0])
    autre = editeur.ajouter_ligne()
    remplir(autre, nom="temperature", unite="degC", composantes=())
    espace.enregistrer_maintenant()
    page = relue(espace)
    assert [g.nom for g in page.mesures] == ["activite"]
    assert page.onglets[1].grandeurs == ("activite",)
    assert espace.modifications_en_attente()


def test_une_ligne_incomplete_empeche_de_valider(espace, editeur):
    remplir(editeur.lignes[0], composantes=())
    espace._actualiser_etat()
    assert not espace.bouton_valider.isEnabled()
    remplir(editeur.lignes[0])
    espace._actualiser_etat()
    assert espace.bouton_valider.isEnabled()


def test_une_ligne_enregistree_garde_sa_derniere_version_pendant_sa_correction(
    espace, editeur
):
    ligne = editeur.lignes[0]
    remplir(ligne)
    espace.enregistrer_maintenant()
    ligne.unite.setText("")
    espace.enregistrer_maintenant()
    grandeur = relue(espace).grandeur("activite")
    assert grandeur.unite == "MBq"
    assert grandeur.valeur == 10.5
    assert relue(espace).onglets[1].grandeurs == ("activite",)


def test_quitter_avec_une_ligne_incomplete_demande_confirmation(
    espace, editeur, monkeypatch
):
    remplir(editeur.lignes[0], composantes=())
    reponses = []
    monkeypatch.setattr(espace, "_confirmer", lambda *_: reponses.append(1) or False)
    assert not espace.peut_quitter_la_page()
    assert reponses == [1]


def test_quitter_avec_des_lignes_completes_enregistre_sans_demander(
    espace, editeur, monkeypatch
):
    remplir(editeur.lignes[0])
    monkeypatch.setattr(
        espace, "_confirmer", lambda *_: pytest.fail("question inutile")
    )
    assert espace.peut_quitter_la_page()
    assert relue(espace).grandeur("activite").valeur == 10.5


def test_deux_lignes_de_meme_nom_sont_signalees(espace, editeur):
    remplir(editeur.lignes[0])
    remplir(editeur.ajouter_ligne())
    assert any("deux fois" in e for e in espace.saisies_incompletes())
    espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()


def test_un_nom_inutilisable_propose_un_nom_correct(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, nom="Activité (MBq)")
    assert ligne.nom.property("invalide") is True
    assert "Activité_MBq" in ligne.message.text()
    assert espace.saisies_incompletes()
    espace.enregistrer_maintenant()
    assert relue(espace).mesures == ()


# --- Renommer et retirer ---------------------------------------------------------------


def test_renommer_une_mesure_ne_laisse_pas_l_ancienne(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne)
    espace.enregistrer_maintenant()
    ligne.nom.setText("activite_ref")
    espace.enregistrer_maintenant()
    assert [g.nom for g in relue(espace).mesures] == ["activite_ref"]


def test_renommer_deux_fois_de_suite(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne)
    espace.enregistrer_maintenant()
    for nom in ("a1", "a2"):
        ligne.nom.setText(nom)
        espace.enregistrer_maintenant()
    assert [g.nom for g in relue(espace).mesures] == ["a2"]


def test_retirer_une_ligne_retire_la_mesure(espace, editeur):
    remplir(editeur.lignes[0])
    remplir(editeur.ajouter_ligne(), nom="b")
    espace.enregistrer_maintenant()
    editeur.retirer_ligne(editeur.lignes[0])
    espace.enregistrer_maintenant()
    page = relue(espace)
    assert [g.nom for g in page.mesures] == ["b"]
    assert page.onglets[1].grandeurs == ("b",)


def test_retirer_une_ligne_reste_dans_l_historique(espace, editeur):
    remplir(editeur.lignes[0])
    espace.enregistrer_maintenant()
    version = espace.archive.historique(espace.page.id)[-1].commit
    editeur.retirer_ligne(editeur.lignes[0])
    espace.enregistrer_maintenant()
    ancienne = espace.archive.version_page(espace.page.id, version)
    assert ancienne.grandeur("activite").valeur == 10.5


def test_retirer_la_ligne_ne_laisse_aucun_widget(espace, editeur):
    ligne = editeur.lignes[0]
    champs = ligne.champs_grille()
    editeur.retirer_ligne(ligne)
    assert all(champ.parent() is None for champ in champs)


def test_retirer_l_onglet_garde_ses_mesures(espace, editeur):
    remplir(editeur.lignes[0])
    espace.enregistrer_maintenant()
    espace.demander_retrait_onglet(1)
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").valeur == 10.5


def test_deux_onglets_de_scalaires_cohabitent(espace, editeur):
    remplir(editeur.lignes[0])
    autre = espace.ajouter_onglet("scalaires")
    remplir(autre.lignes[0], nom="b")
    espace.enregistrer_maintenant()
    page = relue(espace)
    assert {g.nom for g in page.mesures} == {"activite", "b"}
    assert page.onglets[1].grandeurs == ("activite",)
    assert page.onglets[2].grandeurs == ("b",)


def test_un_nom_deja_pris_dans_un_autre_onglet_n_est_pas_enregistre(espace, editeur):
    remplir(editeur.lignes[0])
    espace.enregistrer_maintenant()
    autre = espace.ajouter_onglet("scalaires")
    remplir(autre.lignes[0], valeur="999")
    espace._sur_modification()
    assert "déjà utilisé" in espace.etat.text()
    assert not espace.enregistrer_maintenant()
    assert [g.nom for g in relue(espace).mesures] == ["activite"]
    assert relue(espace).grandeur("activite").valeur == 10.5
    assert not espace.bouton_valider.isEnabled()


def test_une_saisie_n_ecrase_jamais_une_mesure_d_un_autre_onglet(espace, editeur):
    remplir(editeur.lignes[0], valeur="100")
    espace.enregistrer_maintenant()
    autre = espace.ajouter_onglet("scalaires")
    remplir(autre.lignes[0], valeur="999")
    espace._sur_modification()
    assert not espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").valeur == 100


def test_une_saisie_n_ecrase_pas_la_mesure_d_un_onglet_retire(espace, editeur):
    remplir(editeur.lignes[0], valeur="100")
    espace.enregistrer_maintenant()
    espace.demander_retrait_onglet(1)
    espace.enregistrer_maintenant()
    autre = espace.ajouter_onglet("scalaires")
    remplir(autre.lignes[0], valeur="999")
    espace._sur_modification()
    assert not espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").valeur == 100
    assert any("déjà utilisé" in e for e in espace.saisies_incompletes())


def test_changer_le_nom_leve_le_conflit(espace, editeur):
    remplir(editeur.lignes[0])
    espace.enregistrer_maintenant()
    autre = espace.ajouter_onglet("scalaires")
    remplir(autre.lignes[0])
    autre.lignes[0].nom.setText("autre_nom")
    assert espace.saisies_incompletes() == []
    assert espace.enregistrer_maintenant()
    assert {g.nom for g in relue(espace).mesures} == {"activite", "autre_nom"}


def test_une_correction_en_conflit_est_refusee(espace, editeur):
    remplir(editeur.lignes[0])
    espace.demander_validation()
    autre = espace.ajouter_onglet("scalaires")
    remplir(autre.lignes[0], valeur="999")
    assert not espace.demander_correction()
    assert "déjà utilisé" in espace.erreurs[0]
    assert relue(espace).grandeur("activite").valeur == 10.5


def test_une_meme_mesure_affichee_dans_deux_onglets_n_est_pas_un_conflit(espace):
    onglets = [
        {"id": "a", "type": "scalaires", "titre": "A", "grandeurs": ["activite"]},
        {"id": "b", "type": "scalaires", "titre": "B", "grandeurs": ["activite"]},
    ]
    scalaire = Scalaire(
        nom="activite", valeur=1.0, unite="MBq", composantes=(CERTIFICAT,)
    )
    espace.archive.modifier_page(
        Page.model_validate(
            espace.page.model_dump() | {"mesures": [scalaire], "onglets": onglets}
        )
    )
    espace._charger(espace.archive.lire_page(espace.page.id))
    assert espace.saisies_incompletes() == []
    assert not espace.modifications_en_attente()


def test_une_grandeur_d_un_autre_type_referencee_est_conservee(espace):
    tableau = {
        "nature": "fichier",
        "nom": "export",
        "stockage": {"mode": "copie", "empreinte": "a" * 64, "nom_origine": "x"},
    }
    espace.archive.modifier_page(
        Page.model_validate(
            espace.page.model_dump()
            | {
                "mesures": [tableau],
                "onglets": [
                    {
                        "id": "s",
                        "type": "scalaires",
                        "titre": "S",
                        "grandeurs": ["export"],
                    }
                ],
            }
        )
    )
    espace._charger(espace.archive.lire_page(espace.page.id))
    editeur = espace.onglets.widget(0)
    remplir(editeur.lignes[0])
    espace.enregistrer_maintenant()
    page = relue(espace)
    assert {g.nom for g in page.mesures} == {"activite", "export"}
    assert set(page.onglets[0].grandeurs) == {"activite", "export"}


# --- Affichage de U et facteur k -------------------------------------------------------


def test_u_s_affiche_en_absolu_et_en_relatif(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="100")
    assert ligne.resultat.text() == "± 1 MBq (1 %)"


def test_changer_k_change_l_affichage_sans_aucun_commit(espace, editeur, qtbot):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="100")
    espace.enregistrer_maintenant()
    avant = commits(espace)
    editeur.champ_k.setValue(1.0)
    assert ligne.resultat.text() == "± 0.5 MBq (0.5 %)"
    editeur.champ_k.setValue(3.0)
    assert ligne.resultat.text() == "± 1.5 MBq (1.5 %)"
    assert not espace._minuteur.isActive()
    assert not espace.modifications_en_attente()
    qtbot.wait(50)
    espace.enregistrer_maintenant()
    assert commits(espace) == avant


def test_une_valeur_nulle_n_affiche_pas_de_relatif(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="0")
    assert ligne.resultat.text() == "± 1 MBq"


def test_sans_incertitude_aucun_resultat(editeur):
    remplir(editeur.lignes[0], composantes=())
    assert editeur.lignes[0].resultat.text() == ""


def test_le_bouton_indique_le_nombre_de_composantes(editeur):
    ligne = editeur.lignes[0]
    assert ligne.bouton_incertitude.text() == "Incertitude…"
    remplir(ligne)
    assert ligne.bouton_incertitude.text() == "1 composante…"


# --- Mesures répétées ------------------------------------------------------------------


def test_des_valeurs_repetees_donnent_la_moyenne_et_le_type_a(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="", composantes=())
    ligne.appliquer_repetitions([10.0, 12.0, 14.0, 16.0])
    assert ligne.valeur.text() == "13.0"
    composante = ligne.composantes[0]
    assert composante.type == "A"
    assert composante.ddl == 3
    assert composante.valeur == pytest.approx(math.sqrt(20 / 3) / 2)
    assert espace.saisies_incompletes() == []


def test_les_valeurs_brutes_sont_conservees(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="", composantes=())
    ligne.appliquer_repetitions([10.0, 12.0, 14.0, 16.0])
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").repetitions == (10.0, 12.0, 14.0, 16.0)
    espace.ouvrir_page(espace.page.id)
    assert not espace.modifications_en_attente()


def test_les_repetitions_gardent_les_composantes_de_type_b(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="")
    ligne.appliquer_repetitions([10.0, 12.0])
    assert [c.type for c in ligne.composantes] == ["A", "B"]
    assert ligne.composantes[1] == CERTIFICAT


def test_de_nouvelles_repetitions_remplacent_les_anciennes(editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="")
    ligne.appliquer_repetitions([10.0, 12.0])
    ligne.appliquer_repetitions([20.0, 22.0, 24.0])
    assert ligne.valeur.text() == "22.0"
    assert [c.type for c in ligne.composantes] == ["A", "B"]
    assert ligne.composantes[0].ddl == 2


def test_retaper_la_valeur_oublie_les_repetitions(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="")
    ligne.appliquer_repetitions([10.0, 12.0])
    ligne.valeur.setText("50")
    ligne.valeur.textEdited.emit("50")
    assert ligne.valeurs_repetees is None
    assert [c.type for c in ligne.composantes] == ["B"]
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").repetitions is None


def test_les_repetitions_exigent_d_abord_l_unite(editeur, monkeypatch):
    ligne = editeur.lignes[0]
    monkeypatch.setattr(ligne, "_demander_repetitions", lambda: pytest.fail("trop tôt"))
    ligne.bouton_repetitions.click()
    assert "unité" in ligne.message.text()


def test_le_bouton_de_repetitions(editeur, monkeypatch):
    ligne = editeur.lignes[0]
    ligne.unite.setText("MBq")
    monkeypatch.setattr(ligne, "_demander_repetitions", lambda: [1.0, 3.0])
    ligne.bouton_repetitions.click()
    assert ligne.valeur.text() == "2.0"


def test_le_modele_refuse_une_valeur_differente_de_la_moyenne():
    with pytest.raises(ValueError, match="moyenne"):
        Scalaire(
            nom="a",
            valeur=5.0,
            unite="MBq",
            composantes=(CERTIFICAT,),
            repetitions=(1.0, 2.0),
        )


def test_le_modele_refuse_une_seule_repetition():
    with pytest.raises(ValueError, match="deux"):
        Scalaire(
            nom="a",
            valeur=1.0,
            unite="MBq",
            composantes=(CERTIFICAT,),
            repetitions=(1.0,),
        )


@pytest.fixture
def dialogue_repetitions(qtbot):
    resultat = DialogueRepetitions("MBq")
    qtbot.addWidget(resultat)
    return resultat


@pytest.mark.parametrize(
    "texte",
    ["10\n12\n14\n16", "10 12 14 16", "10;12;14;16", "10,0\n12,0\n14,0\n16,0"],
)
def test_le_dialogue_lit_une_serie_collee(dialogue_repetitions, texte):
    dialogue_repetitions.texte.setPlainText(texte)
    assert dialogue_repetitions.valeurs() == [10.0, 12.0, 14.0, 16.0]
    assert dialogue_repetitions.bouton_ok.isEnabled()
    assert "n = 4" in dialogue_repetitions.resultat.text()
    assert "moyenne = 13" in dialogue_repetitions.resultat.text()
    assert "ddl = 3" in dialogue_repetitions.resultat.text()


@pytest.mark.parametrize("texte", ["", "10", "10 abc", "1,2.3"])
def test_le_dialogue_refuse_une_serie_invalide(dialogue_repetitions, texte):
    dialogue_repetitions.texte.setPlainText(texte)
    assert not dialogue_repetitions.bouton_ok.isEnabled()
    assert dialogue_repetitions.resultat.text()


# --- Dialogue des incertitudes ---------------------------------------------------------


@pytest.fixture
def dialogue(qtbot):
    resultat = DialogueIncertitudes("activite", "MBq", ())
    qtbot.addWidget(resultat)
    return resultat


def test_le_dialogue_exige_une_composante_valide(dialogue):
    assert not dialogue.bouton_ok.isEnabled()
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("1,5")
    # La source par défaut suffit, mais une source vidée reste refusée.
    assert dialogue.bouton_ok.isEnabled()
    ligne.source.clear()
    assert not dialogue.bouton_ok.isEnabled()
    ligne.source.setText("Résolution")
    assert dialogue.bouton_ok.isEnabled()
    composante = dialogue.composantes()[0]
    assert composante.valeur == 1.5
    assert composante.unite == "MBq"
    assert composante.source == "Résolution"


def test_le_dialogue_propose_l_unite_de_la_mesure(dialogue):
    assert dialogue.lignes[0].unite.text() == "MBq"


def test_le_dialogue_gere_loi_k_ddl_et_erreur_commune(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("1")
    ligne.source.setText("Certificat")
    ligne.k.setText("2")
    ligne.erreur_commune.setText("CERT-1")
    assert dialogue.composantes()[0].k == 2.0
    assert dialogue.composantes()[0].erreur_commune == "CERT-1"
    ligne.k.clear()
    ligne.loi.setCurrentText("rectangulaire")
    assert dialogue.composantes()[0].loi == "rectangulaire"


def test_le_k_ne_s_applique_qu_a_une_loi_normale(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("1")
    ligne.source.setText("s")
    ligne.k.setText("2")
    ligne.loi.setCurrentText("rectangulaire")
    assert ligne.k.text() == ""
    assert not ligne.k.isEnabled()
    assert dialogue.bouton_ok.isEnabled()
    ligne.loi.setCurrentText("normale")
    assert ligne.k.isEnabled()


@pytest.mark.parametrize(
    ("modele", "loi", "k", "source", "attendu"),
    [
        ("Certificat", "normale", "2", "Certificat", "u = 0.5 MBq"),
        ("Résolution", "rectangulaire", "", "Résolution", "u = 0.577 MBq"),
        ("Tolérance", "rectangulaire", "", "Tolérance", "u = 0.577 MBq"),
    ],
)
def test_un_bouton_regle_la_ligne_pour_une_source_courante(
    dialogue, modele, loi, k, source, attendu
):
    dialogue.boutons_modeles[modele].click()
    assert len(dialogue.lignes) == 1
    ligne = dialogue.lignes[0]
    assert ligne.loi.currentText() == loi
    assert ligne.k.text() == k
    assert ligne.source.text().startswith(source)
    assert ligne.valeur.placeholderText() != "valeur"
    assert not dialogue.bouton_ok.isEnabled()
    ligne.valeur.setText("1")
    assert dialogue.bouton_ok.isEnabled()
    assert ligne.incertitude_type.text() == attendu


def test_un_second_bouton_ajoute_une_ligne(dialogue):
    dialogue.boutons_modeles["Certificat"].click()
    dialogue.lignes[0].valeur.setText("1")
    dialogue.boutons_modeles["Résolution"].click()
    assert len(dialogue.lignes) == 2
    dialogue.lignes[1].valeur.setText("0,05")
    assert [c.loi for c in dialogue.composantes()] == ["normale", "rectangulaire"]


def test_l_incertitude_type_est_convertie_dans_l_unite_de_la_mesure(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("500")
    ligne.k.clear()
    ligne.source.setText("s")
    ligne.unite.setText("kBq")
    assert ligne.incertitude_type.text() == "u = 0.5 MBq"


def test_l_incertitude_type_d_une_composante_relative(qtbot):
    dialogue = DialogueIncertitudes("a", "MBq", (), valeur_mesuree=200.0)
    qtbot.addWidget(dialogue)
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("2")
    ligne.k.clear()
    ligne.source.setText("s")
    ligne.relative.setChecked(True)
    assert ligne.incertitude_type.text() == "u = 4 MBq"


def test_sans_valeur_mesuree_l_incertitude_relative_reste_en_pourcent(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("2")
    ligne.k.clear()
    ligne.source.setText("s")
    ligne.relative.setChecked(True)
    assert ligne.incertitude_type.text() == "u = 2 %"


def test_une_saisie_invalide_n_affiche_pas_d_incertitude_type(dialogue):
    assert dialogue.lignes[0].incertitude_type.text() == ""


def test_les_options_avancees_sont_masquees_par_defaut(dialogue):
    ligne = dialogue.lignes[0]
    assert ligne.ddl.isHidden()
    assert ligne.type.isHidden()
    assert ligne.erreur_commune.isHidden()
    assert not ligne.k.isHidden()
    dialogue.case_options.setChecked(True)
    assert not ligne.ddl.isHidden()
    dialogue.case_options.setChecked(False)
    assert ligne.ddl.isHidden()


@pytest.mark.parametrize(
    "composante",
    [
        Composante(type="A", valeur=1.0, unite="MBq", ddl=4, source="s"),
        Composante(type="B", valeur=1.0, unite="MBq", ddl=20, source="s"),
        Composante(type="B", valeur=1.0, unite="MBq", erreur_commune="E", source="s"),
    ],
)
def test_une_option_deja_renseignee_reste_visible(qtbot, composante):
    dialogue = DialogueIncertitudes("a", "MBq", (composante,))
    qtbot.addWidget(dialogue)
    assert dialogue.case_options.isChecked()
    assert not dialogue.lignes[0].ddl.isHidden()


def test_une_ligne_ajoutee_suit_l_etat_des_options(dialogue):
    assert dialogue.ajouter_ligne().ddl.isHidden()
    dialogue.case_options.setChecked(True)
    assert not dialogue.ajouter_ligne().ddl.isHidden()


def test_une_composante_de_type_a_exige_ses_ddl(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("1")
    ligne.source.setText("s")
    ligne.type.setCurrentText("A")
    assert not dialogue.bouton_ok.isEnabled()
    ligne.ddl.setText("9")
    assert dialogue.bouton_ok.isEnabled()
    assert dialogue.composantes()[0].ddl == 9.0


def test_une_composante_relative_n_a_pas_d_unite(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("2")
    ligne.source.setText("s")
    ligne.relative.setChecked(True)
    assert not ligne.unite.isEnabled()
    composante = dialogue.composantes()[0]
    assert composante.relative
    assert composante.unite is None


def test_une_unite_incompatible_est_refusee_dans_le_dialogue(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("1")
    ligne.source.setText("s")
    ligne.unite.setText("mm")
    assert not dialogue.bouton_ok.isEnabled()
    assert "compatible" in dialogue.message.text()


def test_une_unite_voisine_est_acceptee(dialogue):
    ligne = dialogue.lignes[0]
    ligne.valeur.setText("100")
    ligne.source.setText("s")
    ligne.unite.setText("kBq")
    assert dialogue.bouton_ok.isEnabled()


def test_plusieurs_composantes(dialogue):
    dialogue.lignes[0].valeur.setText("1")
    dialogue.lignes[0].source.setText("a")
    assert dialogue.bouton_ok.isEnabled()
    autre = dialogue.ajouter_ligne()
    assert not dialogue.bouton_ok.isEnabled()
    autre.valeur.setText("2")
    autre.source.setText("b")
    assert [c.source for c in dialogue.composantes()] == ["a", "b"]
    dialogue.retirer_ligne(autre)
    assert len(dialogue.composantes()) == 1


def test_retirer_toutes_les_composantes_est_refuse(dialogue):
    dialogue.retirer_ligne(dialogue.lignes[0])
    assert not dialogue.bouton_ok.isEnabled()
    assert "obligatoire" in dialogue.message.text()


def test_le_bouton_valeur_exacte(dialogue):
    dialogue.bouton_exacte.click()
    composantes = dialogue.composantes()
    assert len(composantes) == 1
    assert composantes[0].valeur == 0
    assert composantes[0].source == "valeur exacte"
    assert dialogue.bouton_ok.isEnabled()


def test_le_dialogue_relit_une_composante_a_l_identique(qtbot):
    composante = Composante(
        type="A", valeur=0.1 + 0.2, unite="kBq", ddl=9, source="s", erreur_commune="E"
    )
    autre = Composante(
        type="B", valeur=2, relative=True, loi="triangulaire", source="t"
    )
    dialogue = DialogueIncertitudes("a", "MBq", (composante, autre))
    qtbot.addWidget(dialogue)
    assert dialogue.composantes() == (composante, autre)


def test_reprendre_les_composantes_d_une_autre_mesure(qtbot):
    dialogue = DialogueIncertitudes("b", "MBq", (), {"activite": (CERTIFICAT,)})
    qtbot.addWidget(dialogue)
    assert dialogue.reprendre.isEnabled()
    dialogue.reprendre_depuis("activite")
    assert dialogue.composantes() == (CERTIFICAT,)
    assert dialogue.bouton_ok.isEnabled()


def test_sans_autre_mesure_rien_a_reprendre(dialogue):
    assert not dialogue.reprendre.isEnabled()


def test_le_bouton_incertitude_applique_le_dialogue(espace, editeur, monkeypatch):
    ligne = editeur.lignes[0]
    remplir(ligne, composantes=())
    monkeypatch.setattr(ligne, "_demander_composantes", lambda: (CERTIFICAT,))
    ligne.bouton_incertitude.click()
    assert ligne.composantes == (CERTIFICAT,)
    assert espace.saisies_incompletes() == []
    assert espace._minuteur.isActive()


def test_annuler_le_dialogue_ne_change_rien(editeur, monkeypatch):
    ligne = editeur.lignes[0]
    remplir(ligne)
    monkeypatch.setattr(ligne, "_demander_composantes", lambda: None)
    ligne.bouton_incertitude.click()
    assert ligne.composantes == (CERTIFICAT,)


# --- Commentaires ----------------------------------------------------------------------


def test_le_commentaire_a_une_place_suffisante(editeur):
    assert editeur.lignes[0].commentaire.minimumWidth() >= 240


def test_le_commentaire_complet_s_affiche_au_survol(editeur):
    ligne = editeur.lignes[0]
    long = "Mesure faite après stabilisation de la source " * 5
    ligne.commentaire.setText(long)
    assert long in ligne.commentaire.toolTip()


def test_un_commentaire_n_est_pas_interprete_comme_une_mise_en_forme(editeur):
    ligne = editeur.lignes[0]
    ligne.commentaire.setText("<img src=x> a < b")
    assert "<img" not in ligne.commentaire.toolTip()
    assert "&lt;img" in ligne.commentaire.toolTip()


def test_sans_commentaire_pas_d_info_bulle(editeur):
    ligne = editeur.lignes[0]
    ligne.commentaire.setText("x")
    ligne.commentaire.clear()
    assert ligne.commentaire.toolTip() == ""


# --- Clavier ---------------------------------------------------------------------------


@pytest.fixture
def affiche(espace, editeur, qtbot):
    espace.resize(1280, 800)
    espace.show()
    qtbot.waitExposed(espace)
    espace.onglets.setCurrentWidget(editeur)
    espace.activateWindow()
    qtbot.waitUntil(espace.isActiveWindow, timeout=3000)
    return editeur


def champ_actif(attendus):
    """Le champ qui a le focus, y compris s'il s'agit de la partie interne d'un champ."""
    actif = QApplication.focusWidget()
    for champ in attendus:
        if actif is champ or champ.isAncestorOf(actif):
            return champ
    return actif


def appuyer_sur_tab(qtbot):
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Tab)


def test_la_touche_tab_suit_l_ordre_de_saisie(affiche, qtbot):
    ligne = affiche.lignes[0]
    ordre = [
        ligne.nature,
        ligne.nom,
        ligne.valeur,
        ligne.unite,
        ligne.bouton_incertitude,
        ligne.bouton_repetitions,
        ligne.commentaire,
    ]
    ordre[0].setFocus()
    for suivant in ordre[1:]:
        appuyer_sur_tab(qtbot)
        assert champ_actif(ordre) is suivant


def test_la_touche_tab_passe_d_une_ligne_a_la_suivante(affiche, qtbot):
    premiere = affiche.lignes[0]
    seconde = affiche.ajouter_ligne()
    premiere.commentaire.setFocus()
    appuyer_sur_tab(qtbot)
    assert QApplication.focusWidget() is seconde.nature


def test_la_touche_tab_ignore_les_boutons_secondaires(affiche, qtbot):
    ligne = affiche.lignes[0]
    secondaires = (
        ligne.bouton_retirer,
        ligne.bouton_maintenant,
        ligne.bouton_suggestion,
        ligne.horodatage,
        ligne.horodatage.lineEdit(),
    )
    ligne.nature.setFocus()
    for _ in range(8):
        appuyer_sur_tab(qtbot)
        assert QApplication.focusWidget() not in secondaires


def test_ctrl_entree_ajoute_une_mesure_et_y_place_le_curseur(affiche, qtbot):
    ligne = affiche.lignes[0]
    ligne.nom.setFocus()
    qtbot.keyClick(ligne.nom, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert len(affiche.lignes) == 2
    assert QApplication.focusWidget() is affiche.lignes[1].nom


def test_une_saisie_complete_au_clavier(espace, affiche, qtbot, monkeypatch):
    ligne = affiche.lignes[0]
    monkeypatch.setattr(ligne, "_demander_composantes", lambda: (CERTIFICAT,))
    ligne.nom.setFocus()
    qtbot.keyClicks(QApplication.focusWidget(), "activite")
    appuyer_sur_tab(qtbot)
    qtbot.keyClicks(QApplication.focusWidget(), "10,5")
    appuyer_sur_tab(qtbot)
    qtbot.keyClicks(QApplication.focusWidget(), "Mbq")
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Return)
    assert ligne.unite.text() == "MBq"
    appuyer_sur_tab(qtbot)
    assert QApplication.focusWidget() is ligne.bouton_incertitude
    qtbot.keyClick(QApplication.focusWidget(), Qt.Key.Key_Space)
    assert ligne.composantes == (CERTIFICAT,)
    ligne.commentaire.setFocus()
    qtbot.keyClicks(QApplication.focusWidget(), "au centre")
    espace.enregistrer_maintenant()
    grandeur = relue(espace).grandeur("activite")
    assert grandeur.valeur == 10.5
    assert grandeur.unite == "MBq"
    assert grandeur.commentaire == "au centre"


# --- Page validée ----------------------------------------------------------------------


def test_corriger_une_mesure_validee_exige_un_motif(espace, editeur):
    ligne = editeur.lignes[0]
    remplir(ligne, valeur="100")
    assert espace.demander_validation()
    ligne = espace.onglets.widget(1).lignes[0]
    ligne.valeur.setText("101")
    ligne.valeur.textEdited.emit("101")
    assert not espace._minuteur.isActive()
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").valeur == 100
    assert espace.demander_correction()
    assert relue(espace).grandeur("activite").valeur == 101
    assert relue(espace).statut == "corrigée"
    corrections = espace.archive.corrections(espace.page.id)
    assert corrections[-1].motif == "Motif"
    assert {m.champ for m in corrections[-1].modifications} >= {
        "mesures.activite.valeur"
    }


def test_une_correction_incomplete_est_refusee(espace, editeur):
    remplir(editeur.lignes[0])
    espace.demander_validation()
    ligne = espace.onglets.widget(1).lignes[0]
    ligne.unite.setText("")
    espace._actualiser_etat()
    assert not espace.bouton_corriger.isEnabled()


# --- Format ----------------------------------------------------------------------------


def test_une_page_est_toujours_ecrite_au_format_courant():
    page = Page(
        version_schema=1,
        id="2026-11-14_a_0001",
        titre="T",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G",
    )
    assert page.version_schema == 1
    assert depuis_json(vers_json(page)).version_schema == VERSION_SCHEMA


def test_une_mesure_sans_repetitions_garde_le_champ_vide():
    grandeur = Scalaire(nom="a", valeur=1.0, unite="MBq", composantes=(CERTIFICAT,))
    assert grandeur.repetitions is None


# --- Cœur de l'édition -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("10.5", 10.5),
        ("10,5", 10.5),
        (" 10,5 ", 10.5),
        ("1 234,5", 1234.5),
        ("1 234,5", 1234.5),
        ("1e-3", 0.001),
        ("-2", -2.0),
    ],
)
def test_lire_nombre(texte, attendu):
    assert lire_nombre(texte) == attendu


@pytest.mark.parametrize(
    "texte", ["", "abc", "1,234.5", "1.234,5", "inf", "nan", "1,2,3"]
)
def test_lire_nombre_refuse(texte):
    with pytest.raises(ValueError):
        lire_nombre(texte)


def test_lire_serie():
    assert lire_serie("1,5 2.5\n3;4\t5") == [1.5, 2.5, 3.0, 4.0, 5.0]
    assert lire_serie("") == []


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("Activité (MBq)", "Activité_MBq"),
        ("a b", "a_b"),
        ("2a", "g_2a"),
        ("  x  ", "x"),
        ("!!!", None),
        ("", None),
    ],
)
def test_suggerer_nom(texte, attendu):
    assert suggerer_nom(texte) == attendu
    if attendu is not None:
        assert attendu.isidentifier()


def _i(nom, valeur="v"):
    return Identifiant(nom=nom, valeur=valeur)


def test_fusionner_remplace_a_sa_place():
    resultat = fusionner_grandeurs(
        [_i("a"), _i("b"), _i("c")], ["b"], [_i("b", "nouvelle")]
    )
    assert [g.nom for g in resultat] == ["a", "b", "c"]
    assert resultat[1].valeur == "nouvelle"


def test_fusionner_retire_ce_qui_n_est_plus_saisi():
    assert [g.nom for g in fusionner_grandeurs([_i("a"), _i("b")], ["a"], [])] == ["b"]


def test_fusionner_ajoute_a_la_fin():
    assert [g.nom for g in fusionner_grandeurs([_i("a")], [], [_i("b")])] == ["a", "b"]


def test_conflit_avec_une_grandeur_non_possedee():
    assert conflits_de_noms([_i("a")], [], [_i("a", "autre")]) == ["a"]


def test_pas_de_conflit_avec_une_grandeur_possedee():
    assert conflits_de_noms([_i("a")], ["a"], [_i("a", "autre")]) == []


def test_conflit_entre_deux_saisies_differentes():
    assert conflits_de_noms([], [], [_i("a", "x"), _i("a", "y")]) == ["a"]


def test_deux_saisies_identiques_ne_sont_pas_un_conflit():
    assert conflits_de_noms([], [], [_i("a"), _i("a")]) == []


def test_fusionner_ne_touche_pas_au_reste():
    a = _i("a")
    assert fusionner_grandeurs([a], [], []) == [a]


# --- Valeurs par défaut d'une composante -------------------------------------------------


def test_une_nouvelle_composante_est_preremplie_avec_k_2_et_source_none(dialogue):
    ligne = dialogue.ajouter_ligne()
    assert ligne.k.text() == "2"
    assert ligne.source.text() == "None"
    ligne.valeur.setText("1")
    composante = ligne.composante()
    assert composante.k == 2.0
    assert composante.source == "None"
    # Une valeur déclarée à k = 2 donne u = valeur / 2 : l'écran le montre tout de suite.
    assert ligne.incertitude_type.text().startswith("u = 0.5")


def test_les_valeurs_par_defaut_restent_modifiables(dialogue):
    ligne = dialogue.ajouter_ligne()
    ligne.valeur.setText("1")
    ligne.k.setText("3")
    ligne.source.setText("Certificat")
    composante = ligne.composante()
    assert composante.k == 3.0
    assert composante.source == "Certificat"
    ligne.k.clear()
    assert ligne.composante().k == 1.0


def test_vider_le_k_pendant_la_frappe_ne_le_remet_pas_a_2(dialogue):
    ligne = dialogue.ajouter_ligne()
    ligne.k.clear()
    assert ligne.k.text() == ""
    ligne.k.setText("3")
    ligne.k.setText("")
    ligne.valeur.setText("1")
    assert ligne.k.text() == ""
    assert ligne.composante().k == 1.0


def test_le_k_par_defaut_ne_s_applique_qu_a_une_loi_normale(dialogue):
    ligne = dialogue.ajouter_ligne()
    ligne.valeur.setText("1")
    ligne.loi.setCurrentText("rectangulaire")
    assert ligne.k.text() == ""
    assert not ligne.k.isEnabled()
    assert ligne.composante().k == 1.0
    ligne.loi.setCurrentText("normale")
    assert ligne.k.text() == "2"
    assert ligne.k.isEnabled()


def test_le_k_par_defaut_ne_s_applique_pas_au_type_a(dialogue):
    ligne = dialogue.ajouter_ligne()
    ligne.valeur.setText("1")
    ligne.ddl.setText("4")
    ligne.type.setCurrentText("A")
    assert ligne.k.text() == ""
    assert not ligne.k.isEnabled()
    composante = ligne.composante()
    assert composante.type == "A"
    assert composante.k == 1.0
    ligne.type.setCurrentText("B")
    assert ligne.k.text() == "2"


def test_une_composante_deja_declaree_garde_son_k_et_sa_source(dialogue):
    """k = 1 s'affiche vide : il ne doit surtout pas devenir 2 à la relecture."""
    declaree = Composante(type="B", valeur=1.0, unite="MBq", source="afficheur")
    ligne = dialogue.ajouter_ligne(declaree)
    assert ligne.k.text() == ""
    assert ligne.source.text() == "afficheur"
    assert ligne.composante() == declaree
    ligne.loi.setCurrentText("rectangulaire")
    ligne.loi.setCurrentText("normale")
    assert ligne.k.text() == ""


def test_un_modele_de_source_ne_reprend_pas_le_k_par_defaut(dialogue):
    ligne = dialogue.ajouter_modele("Résolution")
    assert ligne.k.text() == ""
    ligne.loi.setCurrentText("normale")
    assert ligne.k.text() == ""
    assert ligne.source.text().startswith("Résolution")
