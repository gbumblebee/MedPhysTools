"""Vérifie l'espace Saisie : page libre, onglets, enregistrement, validation, correction."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QComboBox

from pymphub import Archive
from pymphub.depot import Depot
from pymphub.edition import (
    date_saisie_vers_mesure,
    decouper_tags,
    modifier_champs,
    nouvel_id_onglet,
    nouvelle_page,
)
from pymphub.interface.espace_saisie import (
    DescriptionPage,
    DialogueNouvellePage,
    EspaceSaisie,
    message_erreur,
)
from pymphub.interface.fenetre import FenetrePrincipale
from pymphub.interface.onglets import (
    EditeurLectureSeule,
    EditeurNotes,
    EditeurOnglet,
    creer_editeur,
    declarer_onglet,
    retirer_onglet_declare,
    types_disponibles,
)
from pymphub.page import Onglet, OngletNotes, Page, depuis_json

PARIS = timezone(timedelta(hours=1))
HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=PARIS)
MACHINE = "chu.mn.activimetre.capintec-crc55.1"


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def espace(qtbot, arc, monkeypatch):
    """Un espace dont les dialogues sont remplacés par des réponses préparées."""
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    resultat.reponses = {
        "motif": "Motif du test",
        "confirmer": True,
        "titre": "Renommé",
    }
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    monkeypatch.setattr(resultat, "_demander_motif", lambda: resultat.reponses["motif"])
    monkeypatch.setattr(
        resultat, "_confirmer", lambda *_: resultat.reponses["confirmer"]
    )
    monkeypatch.setattr(
        resultat, "_demander_titre_onglet", lambda _actuel: resultat.reponses["titre"]
    )
    return resultat


def description(titre="Linéarité activimètre", **champs):
    return DescriptionPage(
        **(
            {
                "titre": titre,
                "date_mesure": HEURE,
                "auteur": "G.",
                "machine": None,
                "tags": (),
            }
            | champs
        )
    )


@pytest.fixture
def ouverte(espace):
    assert espace.creer_page(description())
    return espace


def identifiant(espace):
    return espace.page.id


def relue(espace):
    return espace.archive.lire_page(identifiant(espace))


def commits(espace):
    return len(Depot(espace.archive.racine).commits())


def editeur_notes(espace, position=0):
    editeur = espace.onglets.widget(position)
    assert isinstance(editeur, EditeurNotes)
    return editeur


# --- Création d'une page ------------------------------------------------------------


def test_sans_page_ouverte_l_espace_propose_d_en_creer_une(espace):
    assert espace.pile.currentWidget() is espace.vide
    assert espace.page is None


def test_creer_une_page_libre(ouverte):
    page = relue(ouverte)
    assert page.id == "2026-11-14_linearite-activimetre_0001"
    assert page.titre == "Linéarité activimètre"
    assert page.statut == "brouillon"
    assert page.gabarit is None
    assert page.date_saisie.tzinfo is not None
    assert ouverte.pile.currentWidget() is ouverte.edition
    assert ouverte.erreurs == []


def test_une_nouvelle_page_a_un_onglet_de_notes(ouverte):
    assert [o.type for o in relue(ouverte).onglets] == ["notes"]
    assert isinstance(ouverte.onglets.widget(0), EditeurNotes)
    assert ouverte.onglets.tabText(0) == "Notes"


def test_la_creation_est_tracee(ouverte):
    assert [e.nature for e in ouverte.archive.historique(identifiant(ouverte))] == [
        "création"
    ]
    assert ouverte.archive.modifications_non_tracees() == []


def test_creer_une_page_declare_sa_machine(espace):
    assert espace.creer_page(description(machine=MACHINE))
    assert MACHINE in espace.archive.configuration.machines
    assert relue(espace).machine == MACHINE


def test_une_machine_mal_formee_est_refusee_avec_un_message(espace):
    assert not espace.creer_page(description(machine="capintec"))
    assert "site.service" in espace.erreurs[0]
    assert espace.archive.identifiants() == []
    assert espace.page is None


def test_le_signal_de_modification_a_la_creation(espace, qtbot):
    with qtbot.waitSignal(espace.page_modifiee) as signal:
        espace.creer_page(description())
    assert signal.args == [espace.page.id]


def test_le_bouton_cree_la_page_decrite(espace, monkeypatch):
    monkeypatch.setattr(espace, "_decrire_nouvelle_page", description)
    espace.bouton_nouvelle_vide.click()
    assert espace.page is not None


def test_annuler_la_creation_ne_change_rien(espace, monkeypatch):
    monkeypatch.setattr(espace, "_decrire_nouvelle_page", lambda: None)
    espace.bouton_nouvelle_vide.click()
    assert espace.page is None
    assert espace.archive.identifiants() == []


@pytest.fixture
def dialogue(qtbot, arc):
    arc.declarer_machine(MACHINE)
    resultat = DialogueNouvellePage(arc)
    qtbot.addWidget(resultat)
    return resultat


def test_le_dialogue_exige_titre_et_auteur(dialogue):
    assert not dialogue.bouton_creer.isEnabled()
    dialogue.champ_titre.setText("  Titre  ")
    assert dialogue.bouton_creer.isEnabled()
    dialogue.champ_auteur.setText("")
    assert not dialogue.bouton_creer.isEnabled()


def test_le_dialogue_propose_le_responsable_et_les_machines(dialogue):
    assert dialogue.champ_auteur.text() == "Resp."
    assert dialogue.champ_machine.findText(MACHINE) > 0


def test_le_dialogue_donne_une_description_propre(dialogue):
    dialogue.champ_titre.setText("  Titre  ")
    dialogue.champ_tags.setText("a, b,, a ")
    dialogue.champ_machine.setCurrentText(MACHINE)
    resultat = dialogue.description()
    assert resultat.titre == "Titre"
    assert resultat.tags == ("a", "b")
    assert resultat.machine == MACHINE
    assert resultat.date_mesure.tzinfo is not None


def test_ouvrir_une_page_ne_laisse_aucun_widget_en_trop(ouverte):
    # Sans passage par la boucle d'événements : un widget remplacé y resterait visible.
    ouverte.ouvrir_page(identifiant(ouverte))
    ouverte.ouvrir_page(identifiant(ouverte))
    assert ouverte.edition.findChildren(QComboBox) == [ouverte.champ_machine]


def test_la_liste_propose_les_machines_et_selectionne_celle_de_la_page(espace):
    espace.creer_page(description(machine=MACHINE))
    assert espace.champ_machine.currentText() == MACHINE
    assert espace.champ_machine.findText(MACHINE) > 0
    assert not espace.modifications_en_attente()


# --- Enregistrement automatique d'un brouillon --------------------------------------


def test_un_brouillon_s_enregistre_tout_seul_apres_une_pause(ouverte, qtbot):
    editeur_notes(ouverte).texte.setPlainText("Montage")
    assert relue(ouverte).onglets[0].texte == ""
    assert "en attente" in ouverte.etat.text()
    qtbot.waitUntil(lambda: relue(ouverte).onglets[0].texte == "Montage", timeout=3000)
    assert "enregistré" in ouverte.etat.text()


def test_une_rafale_de_frappes_ne_cree_qu_un_commit(ouverte, qtbot):
    avant = commits(ouverte)
    for lettre in "Montage":
        editeur_notes(ouverte).texte.insertPlainText(lettre)
    qtbot.waitUntil(lambda: not ouverte._minuteur.isActive(), timeout=3000)
    assert commits(ouverte) == avant + 1


def test_l_enregistrement_d_un_brouillon_est_sans_motif(ouverte):
    editeur_notes(ouverte).texte.setPlainText("Montage")
    assert ouverte.enregistrer_maintenant()
    derniere = ouverte.archive.historique(identifiant(ouverte))[-1]
    assert derniere.nature == "enregistrement"
    assert derniere.motif is None


def test_sans_modification_aucun_commit(ouverte):
    avant = commits(ouverte)
    assert ouverte.enregistrer_maintenant()
    assert commits(ouverte) == avant


def test_ouvrir_une_page_ne_compte_pas_comme_une_modification(ouverte, qtbot):
    avant = commits(ouverte)
    ouverte.ouvrir_page(identifiant(ouverte))
    assert not ouverte._minuteur.isActive()
    assert not ouverte.modifications_en_attente()
    qtbot.wait(50)
    assert commits(ouverte) == avant


def test_modifier_les_informations(ouverte):
    ouverte.champ_titre.setText("Nouveau titre")
    ouverte.champ_titre.textEdited.emit("Nouveau titre")
    ouverte.champ_description.setText("Une description")
    ouverte.champ_tags.setText("a, b")
    ouverte.champ_machine.setCurrentText(MACHINE)
    assert ouverte.enregistrer_maintenant()
    page = relue(ouverte)
    assert page.titre == "Nouveau titre"
    assert page.description == "Une description"
    assert page.tags == ("a", "b")
    assert page.machine == MACHINE
    assert MACHINE in ouverte.archive.configuration.machines


def test_l_identifiant_ne_change_pas_avec_le_titre(ouverte):
    avant = identifiant(ouverte)
    ouverte.champ_titre.setText("Autre titre")
    ouverte.enregistrer_maintenant()
    assert relue(ouverte).id == avant


def test_un_titre_vide_n_est_pas_enregistre_et_le_signale(ouverte, qtbot):
    ouverte.champ_titre.setText("")
    ouverte.champ_titre.textEdited.emit("")
    assert "Non enregistré" in ouverte.etat.text()
    assert not ouverte._minuteur.isActive()
    assert not ouverte.bouton_valider.isEnabled()
    assert not ouverte.enregistrer_maintenant()
    assert relue(ouverte).titre == "Linéarité activimètre"
    assert ouverte.erreurs == []


def test_le_retour_d_un_titre_valide_relance_l_enregistrement(ouverte):
    ouverte.champ_titre.setText("")
    ouverte.champ_titre.textEdited.emit("")
    ouverte.champ_titre.setText("Corrigé")
    ouverte.champ_titre.textEdited.emit("Corrigé")
    assert ouverte._minuteur.isActive()
    assert ouverte.bouton_valider.isEnabled()


def test_une_machine_mal_formee_ne_s_enregistre_pas(ouverte):
    ouverte.champ_machine.setCurrentText("capintec")
    assert "Non enregistré" in ouverte.etat.text()
    assert not ouverte.enregistrer_maintenant()
    assert ouverte.archive.configuration.machines == ()


def test_la_date_de_mesure_garde_son_fuseau_si_elle_ne_change_pas(ouverte):
    ouverte.champ_titre.setText("Autre")
    ouverte.enregistrer_maintenant()
    assert relue(ouverte).date_mesure == HEURE
    assert relue(ouverte).date_mesure.utcoffset() == timedelta(hours=1)


def test_modifier_la_date_de_mesure(ouverte):
    ouverte.champ_date.setDateTime(QDateTime(2026, 11, 15, 10, 0, 0))
    assert ouverte.enregistrer_maintenant()
    page = relue(ouverte)
    assert page.date_mesure.tzinfo is not None
    assert page.date_mesure.replace(tzinfo=None) == datetime(2026, 11, 15, 10, 0)


def test_le_bouton_maintenant(ouverte):
    ouverte.bouton_maintenant.click()
    assert ouverte.champ_date.dateTime().toPython().year >= 2026
    assert ouverte._minuteur.isActive()


def test_une_erreur_d_ecriture_est_expliquee_et_garde_la_saisie(ouverte):
    chemin = ouverte.archive.racine / "pages" / identifiant(ouverte) / "page.json"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    editeur_notes(ouverte).texte.setPlainText("Texte à ne pas perdre")
    assert not ouverte.enregistrer_maintenant()
    assert "hors du logiciel" in ouverte.erreurs[0]
    assert editeur_notes(ouverte).texte.toPlainText() == "Texte à ne pas perdre"
    assert ouverte.modifications_en_attente()


# --- Onglets ------------------------------------------------------------------------


def test_ajouter_un_onglet(ouverte):
    editeur = ouverte.ajouter_onglet("notes")
    assert ouverte.onglets.count() == 2
    assert ouverte.onglets.currentWidget() is editeur
    ouverte.enregistrer_maintenant()
    assert [o.id for o in relue(ouverte).onglets] == ["onglet_1", "onglet_2"]


def test_le_menu_propose_les_types_declares(ouverte):
    ouverte._remplir_menu_ajouter()
    libelles = [action.text() for action in ouverte.menu_ajouter.actions()]
    assert "Notes" in libelles
    ouverte.menu_ajouter.actions()[0].trigger()
    assert ouverte.onglets.count() == 2


def test_renommer_un_onglet(ouverte):
    assert ouverte.demander_renommage_onglet(0)
    assert ouverte.onglets.tabText(0) == "Renommé"
    ouverte.enregistrer_maintenant()
    assert relue(ouverte).onglets[0].titre == "Renommé"


@pytest.mark.parametrize("reponse", [None, "", "   "])
def test_un_nom_vide_ne_renomme_pas(ouverte, reponse):
    ouverte.reponses["titre"] = reponse
    assert not ouverte.demander_renommage_onglet(0)
    assert ouverte.onglets.tabText(0) == "Notes"


def test_le_renommage_garde_le_texte(ouverte):
    editeur_notes(ouverte).texte.setPlainText("Montage")
    ouverte.demander_renommage_onglet(0)
    ouverte.enregistrer_maintenant()
    assert relue(ouverte).onglets[0].texte == "Montage"


def test_double_clic_hors_d_un_onglet_ignore(ouverte):
    assert not ouverte.demander_renommage_onglet(-1)


def test_retirer_un_onglet_apres_confirmation(ouverte):
    ouverte.ajouter_onglet("notes")
    assert ouverte.demander_retrait_onglet(0)
    ouverte.enregistrer_maintenant()
    assert [o.id for o in relue(ouverte).onglets] == ["onglet_2"]


def test_refuser_la_confirmation_garde_l_onglet(ouverte):
    ouverte.reponses["confirmer"] = False
    assert not ouverte.demander_retrait_onglet(0)
    assert ouverte.onglets.count() == 1


def test_le_texte_d_un_onglet_retire_reste_dans_l_historique(ouverte):
    editeur_notes(ouverte).texte.setPlainText("Texte retiré")
    ouverte.enregistrer_maintenant()
    versions = ouverte.archive.historique(identifiant(ouverte))
    ouverte.demander_retrait_onglet(0)
    ouverte.enregistrer_maintenant()
    ancienne = ouverte.archive.version_page(identifiant(ouverte), versions[-1].commit)
    assert ancienne.onglets[0].texte == "Texte retiré"


def scalaire(nom="activite", valeur=10.5):
    return {
        "nature": "scalaire",
        "nom": nom,
        "valeur": valeur,
        "unite": "MBq",
        "composantes": [{"type": "B", "valeur": 1.0, "unite": "MBq", "source": "s"}],
    }


@pytest.fixture
def page_a_trois_onglets(espace):
    espace.archive.creer_page(
        Page.model_validate(
            {
                "id": "2026-11-14_cq_0001",
                "titre": "CQ",
                "date_mesure": HEURE,
                "date_saisie": HEURE,
                "auteur": "G.",
                "mesures": [scalaire()],
                "onglets": [
                    {"id": "a", "type": "notes", "titre": "A", "texte": "texte a"},
                    {
                        "id": "b",
                        "type": "scalaires",
                        "titre": "B",
                        "grandeurs": ["activite"],
                    },
                    {"id": "c", "type": "notes", "titre": "C", "texte": "texte c"},
                ],
            }
        )
    )
    assert espace.ouvrir_page("2026-11-14_cq_0001")
    return espace


def test_reorganiser_les_onglets_change_leur_ordre_enregistre(page_a_trois_onglets):
    espace = page_a_trois_onglets
    espace.onglets.tabBar().moveTab(0, 2)
    assert espace._minuteur.isActive()
    espace.enregistrer_maintenant()
    assert [o.id for o in relue(espace).onglets] == ["b", "c", "a"]


def test_reorganiser_ne_change_pas_la_relecture_d_une_grandeur(page_a_trois_onglets):
    espace = page_a_trois_onglets
    avant = espace.archive.page(identifiant(espace)).mesures["activite"].valeur
    espace.onglets.tabBar().moveTab(1, 0)
    espace.enregistrer_maintenant()
    apres = espace.archive.page(identifiant(espace)).mesures["activite"].valeur
    assert apres == avant == 10.5
    assert relue(espace).grandeur("activite") == espace.page.grandeur("activite")


def test_le_changement_d_ordre_apparait_dans_l_historique(page_a_trois_onglets):
    espace = page_a_trois_onglets
    espace.onglets.tabBar().moveTab(0, 2)
    espace.enregistrer_maintenant()
    champs = [
        m.champ
        for m in espace.archive.historique(identifiant(espace))[-1].modifications
    ]
    assert "onglets (ordre)" in champs


def test_retirer_un_onglet_garde_les_mesures(page_a_trois_onglets):
    espace = page_a_trois_onglets
    espace.demander_retrait_onglet(1)
    espace.enregistrer_maintenant()
    assert relue(espace).grandeur("activite").valeur == 10.5
    assert espace.archive.page(identifiant(espace)).mesures["activite"].elargie() == 2.0


def test_un_nouvel_id_d_onglet_n_est_jamais_deja_pris():
    onglets = [Onglet(id="onglet_2", type="notes"), Onglet(id="onglet_3", type="notes")]
    assert nouvel_id_onglet(onglets) == "onglet_4"
    assert nouvel_id_onglet(()) == "onglet_1"


# --- Types d'onglets ----------------------------------------------------------------


class OngletSpectre(Onglet):
    canaux: int = 1024


class EditeurSpectre(EditeurOnglet):
    def valeur(self):
        return self.onglet.model_copy(update={"canaux": 2048})


@pytest.fixture
def type_de_test():
    declarer_onglet("spectre", "Spectre", OngletSpectre, EditeurSpectre)
    yield
    retirer_onglet_declare("spectre")


def test_un_type_s_ajoute_par_une_seule_declaration(ouverte, type_de_test):
    assert "Spectre" in [d.libelle for d in types_disponibles()]
    editeur = ouverte.ajouter_onglet("spectre")
    assert isinstance(editeur, EditeurSpectre)
    assert ouverte.onglets.tabText(1) == "Spectre"

    ouverte.enregistrer_maintenant()
    onglet = relue(ouverte).onglets[1]
    assert isinstance(onglet, OngletSpectre)
    assert onglet.canaux == 2048

    ouverte.ouvrir_page(identifiant(ouverte))
    assert isinstance(ouverte.onglets.widget(1), EditeurSpectre)


def test_un_type_retire_n_est_plus_propose(type_de_test):
    retirer_onglet_declare("spectre")
    assert "Spectre" not in [d.libelle for d in types_disponibles()]
    declarer_onglet("spectre", "Spectre", OngletSpectre, EditeurSpectre)


FUTUR = {
    "id": "futur",
    "type": "spectre_gamma",
    "titre": "Spectre",
    "pics_keV": [140.5, 159.0],
    "reglages": {"echelle": "log"},
}


@pytest.fixture
def page_avec_onglet_inconnu(espace):
    espace.archive.creer_page(
        Page.model_validate(
            {
                "id": "2026-11-14_futur_0001",
                "titre": "Futur",
                "date_mesure": HEURE,
                "date_saisie": HEURE,
                "auteur": "G.",
                "onglets": [{"id": "n", "type": "notes", "titre": "N"}, FUTUR],
            }
        )
    )
    espace.ouvrir_page("2026-11-14_futur_0001")
    return espace


def json_onglet(espace, position):
    chemin = espace.archive.racine / "pages" / identifiant(espace) / "page.json"
    return json.loads(chemin.read_text(encoding="utf-8"))["onglets"][position]


def test_un_type_inconnu_s_affiche_en_lecture_seule(page_avec_onglet_inconnu):
    editeur = page_avec_onglet_inconnu.onglets.widget(1)
    assert isinstance(editeur, EditeurLectureSeule)
    assert editeur.contenu.isReadOnly()
    assert "pics_keV" in editeur.contenu.toPlainText()


def test_ouvrir_un_type_inconnu_ne_cree_pas_de_commit(page_avec_onglet_inconnu):
    espace = page_avec_onglet_inconnu
    avant = commits(espace)
    assert not espace.modifications_en_attente()
    espace.enregistrer_maintenant()
    assert commits(espace) == avant


def test_les_donnees_d_un_type_inconnu_restent_intactes(page_avec_onglet_inconnu):
    espace = page_avec_onglet_inconnu
    editeur_notes(espace).texte.setPlainText("Autre chose")
    espace.onglets.tabBar().moveTab(0, 1)
    espace.enregistrer_maintenant()
    assert json_onglet(espace, 0) == FUTUR


def test_renommer_un_type_inconnu_garde_ses_donnees(page_avec_onglet_inconnu):
    espace = page_avec_onglet_inconnu
    espace.demander_renommage_onglet(1)
    espace.enregistrer_maintenant()
    assert json_onglet(espace, 1) == FUTUR | {"titre": "Renommé"}


def test_un_type_connu_sans_editeur_garde_ses_donnees(espace):
    espace.archive.creer_page(
        Page.model_validate(
            {
                "id": "2026-11-14_cq_0001",
                "titre": "CQ",
                "date_mesure": HEURE,
                "date_saisie": HEURE,
                "auteur": "G.",
                "mesures": [scalaire()],
                "onglets": [
                    {"id": "a", "type": "notes", "titre": "A"},
                    {
                        "id": "b",
                        "type": "dicom",
                        "titre": "B",
                        "grandeurs": ["activite"],
                    },
                ],
            }
        )
    )
    espace.ouvrir_page("2026-11-14_cq_0001")
    assert isinstance(espace.onglets.widget(1), EditeurLectureSeule)
    editeur_notes(espace).texte.setPlainText("x")
    espace.enregistrer_maintenant()
    assert relue(espace).onglets[1].grandeurs == ("activite",)


def test_l_editeur_de_notes_garde_les_photos(qtbot):
    onglet = OngletNotes.model_validate(
        {
            "id": "n",
            "type": "notes",
            "photos": [{"chemin": "pieces_jointes/a.jpg", "legende": "L"}],
            "champ_futur": 1,
        }
    )
    editeur = creer_editeur(onglet)
    qtbot.addWidget(editeur)
    editeur.texte.setPlainText("Texte")
    valeur = editeur.valeur()
    assert valeur.texte == "Texte"
    assert valeur.photos[0].legende == "L"
    assert valeur.champ_futur == 1


# --- Validation et correction -------------------------------------------------------


@pytest.fixture
def validee(ouverte):
    editeur_notes(ouverte).texte.setPlainText("Texte validé")
    assert ouverte.demander_validation()
    return ouverte


def test_valider_enregistre_d_abord_le_brouillon(validee):
    page = relue(validee)
    assert page.statut == "validée"
    assert page.onglets[0].texte == "Texte validé"
    natures = [e.nature for e in validee.archive.historique(page.id)]
    assert natures == ["création", "enregistrement", "validation"]


def test_refuser_la_confirmation_ne_valide_pas(ouverte):
    ouverte.reponses["confirmer"] = False
    assert not ouverte.demander_validation()
    assert relue(ouverte).statut == "brouillon"


def test_les_boutons_suivent_le_statut(ouverte):
    assert not ouverte.bouton_valider.isHidden()
    assert ouverte.bouton_corriger.isHidden()
    ouverte.demander_validation()
    assert ouverte.bouton_valider.isHidden()
    assert not ouverte.bouton_corriger.isHidden()
    assert not ouverte.bouton_corriger.isEnabled()


def test_une_page_validee_ne_s_enregistre_jamais_toute_seule(validee, qtbot):
    avant = commits(validee)
    editeur_notes(validee).texte.setPlainText("Modifié")
    assert not validee._minuteur.isActive()
    assert validee.enregistrer_maintenant()
    qtbot.wait(50)
    assert commits(validee) == avant
    assert relue(validee).onglets[0].texte == "Texte validé"
    assert "motif" in validee.etat.text()
    assert validee.bouton_corriger.isEnabled()


def test_une_correction_demande_un_motif(validee):
    editeur_notes(validee).texte.setPlainText("Corrigé")
    assert validee.demander_correction()
    page = relue(validee)
    assert page.statut == "corrigée"
    assert page.onglets[0].texte == "Corrigé"
    correction = validee.archive.corrections(page.id)[-1]
    assert correction.motif == "Motif du test"
    assert not validee.modifications_en_attente()


def test_annuler_le_dialogue_du_motif_n_enregistre_rien(validee):
    validee.reponses["motif"] = None
    editeur_notes(validee).texte.setPlainText("Corrigé")
    assert not validee.demander_correction()
    assert relue(validee).onglets[0].texte == "Texte validé"
    assert validee.modifications_en_attente()


@pytest.mark.parametrize("motif", ["", "   "])
def test_un_motif_vide_est_refuse(validee, motif):
    validee.reponses["motif"] = motif
    editeur_notes(validee).texte.setPlainText("Corrigé")
    assert not validee.demander_correction()
    assert "motivée" in validee.erreurs[0]
    assert relue(validee).statut == "validée"
    assert editeur_notes(validee).texte.toPlainText() == "Corrigé"


def test_annuler_les_modifications(validee):
    editeur_notes(validee).texte.setPlainText("Modifié")
    validee.bouton_annuler.click()
    assert editeur_notes(validee).texte.toPlainText() == "Texte validé"
    assert not validee.modifications_en_attente()


def test_corriger_un_onglet_reorganise_exige_aussi_un_motif(validee):
    validee.ajouter_onglet("notes")
    assert validee.modifications_en_attente()
    assert not validee._minuteur.isActive()
    validee.demander_correction()
    assert (
        validee.archive.corrections(identifiant(validee))[-1].motif == "Motif du test"
    )


def test_une_page_corrigee_exige_encore_un_motif(validee):
    editeur_notes(validee).texte.setPlainText("Une")
    validee.demander_correction()
    editeur_notes(validee).texte.setPlainText("Deux")
    assert validee.bouton_corriger.isEnabled()
    assert not validee._minuteur.isActive()
    validee.demander_correction()
    assert [c.motif for c in validee.archive.corrections(identifiant(validee))] == [
        "Motif du test",
        "Motif du test",
    ]


# --- Changer de page ----------------------------------------------------------------


def test_ouvrir_une_autre_page_enregistre_d_abord_le_brouillon(ouverte):
    premiere = identifiant(ouverte)
    ouverte.archive.creer_page(
        Page(
            id="2026-11-20_autre_0002",
            titre="Autre",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
        )
    )
    editeur_notes(ouverte).texte.setPlainText("Ne pas perdre")
    assert ouverte.ouvrir_page("2026-11-20_autre_0002")
    assert ouverte.archive.lire_page(premiere).onglets[0].texte == "Ne pas perdre"


def test_quitter_une_correction_en_attente_demande_confirmation(validee):
    editeur_notes(validee).texte.setPlainText("Modifié")
    validee.reponses["confirmer"] = False
    assert not validee.peut_quitter_la_page()
    validee.reponses["confirmer"] = True
    assert validee.peut_quitter_la_page()
    assert relue(validee).onglets[0].texte == "Texte validé"


def test_quitter_un_brouillon_invalide_demande_confirmation(ouverte):
    ouverte.champ_titre.setText("")
    ouverte.reponses["confirmer"] = False
    assert not ouverte.peut_quitter_la_page()


def test_refuser_de_quitter_garde_la_page_ouverte(validee):
    id_page = identifiant(validee)
    editeur_notes(validee).texte.setPlainText("Modifié")
    validee.reponses["confirmer"] = False
    assert not validee.ouvrir_page(id_page)
    assert editeur_notes(validee).texte.toPlainText() == "Modifié"


def test_ouvrir_une_page_illisible_donne_un_message(espace):
    espace.archive.creer_page(
        Page(
            id="2026-11-14_futur_0001",
            titre="F",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G",
        )
    )
    chemin = espace.archive.racine / "pages" / "2026-11-14_futur_0001" / "page.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    contenu["version_schema"] = 99
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    assert not espace.ouvrir_page("2026-11-14_futur_0001")
    assert "mettre à jour" in espace.erreurs[0]
    assert espace.page is None


# --- Fenêtre : lien entre les espaces -----------------------------------------------


@pytest.fixture
def fenetre(qtbot, arc, monkeypatch):
    resultat = FenetrePrincipale()
    qtbot.addWidget(resultat)
    saisie = resultat.espace_saisie
    saisie.reponses = {"confirmer": True}
    monkeypatch.setattr(saisie, "_confirmer", lambda *_: saisie.reponses["confirmer"])
    monkeypatch.setattr(saisie, "_demander_motif", lambda: "Motif")
    assert resultat.ouvrir_archive(arc.racine)
    yield resultat
    resultat.espace_archive.arreter()


def attendre(fenetre, qtbot):
    qtbot.waitUntil(fenetre.espace_archive.au_repos, timeout=30000)


def test_une_page_creee_apparait_dans_l_espace_archive(fenetre, qtbot):
    attendre(fenetre, qtbot)
    assert fenetre.espace_saisie.creer_page(description())
    attendre(fenetre, qtbot)
    assert [r.titre for r in fenetre.espace_archive.modele.resumes] == [
        "Linéarité activimètre"
    ]


def test_la_fiche_ouverte_suit_les_enregistrements(fenetre, qtbot):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    attendre(fenetre, qtbot)
    fenetre.espace_archive.liste.selectRow(0)
    fiche = fenetre.espace_archive.fiche
    assert fiche.page.titre == "Linéarité activimètre"
    saisie.champ_titre.setText("Autre titre")
    saisie.enregistrer_maintenant()
    attendre(fenetre, qtbot)
    assert fiche.page.titre == "Autre titre"
    assert "Autre titre" in fiche.entete.text()


def test_le_bouton_modifier_ouvre_la_page_dans_la_saisie(fenetre, qtbot):
    fenetre.espace_saisie.creer_page(description())
    attendre(fenetre, qtbot)
    fenetre.espaces.setCurrentWidget(fenetre.espace_archive)
    id_page = fenetre.espace_saisie.page.id
    fenetre.espace_saisie.installer(fenetre.archive)
    fenetre.espace_archive.liste.selectRow(0)
    fiche = fenetre.espace_archive.fiche
    assert fiche.bouton_modifier.isEnabled()
    fiche.bouton_modifier.click()
    assert fenetre.espaces.currentWidget() is fenetre.espace_saisie
    assert fenetre.espace_saisie.page.id == id_page


def test_le_bouton_voir_la_fiche_enregistre_puis_montre_la_page(fenetre, arc, qtbot):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    attendre(fenetre, qtbot)
    fiche = fenetre.espace_archive.fiche
    editeur_notes(saisie).texte.setPlainText("Texte de la fiche")
    saisie.bouton_fiche.click()
    assert fenetre.espaces.currentWidget() is fenetre.espace_archive
    assert fiche.page.id == saisie.page.id
    assert fiche.onglets.currentIndex() == 0
    assert "Texte de la fiche" in fiche.notes[0].toPlainText()
    attendre(fenetre, qtbot)
    assert fiche.page.id == saisie.page.id


def test_le_bouton_modifier_est_inactif_sans_page(fenetre):
    assert not fenetre.espace_archive.fiche.bouton_modifier.isEnabled()


def test_fermer_la_fenetre_enregistre_le_brouillon(fenetre, arc, qtbot):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    id_page = saisie.page.id
    editeur_notes(saisie).texte.setPlainText("Ne pas perdre")
    assert fenetre.close()
    assert arc.lire_page(id_page).onglets[0].texte == "Ne pas perdre"


def test_refuser_d_abandonner_une_correction_empeche_la_fermeture(fenetre, qtbot):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    saisie.demander_validation()
    editeur_notes(saisie).texte.setPlainText("Modifié")
    saisie.reponses["confirmer"] = False
    assert not fenetre.close()
    saisie.reponses["confirmer"] = True
    assert fenetre.close()


def test_changer_d_archive_enregistre_le_brouillon(fenetre, arc, tmp_path, qtbot):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    id_page = saisie.page.id
    editeur_notes(saisie).texte.setPlainText("Ne pas perdre")
    Archive.creer(tmp_path / "B", "s", "r", date(2026, 1, 1))
    assert fenetre.ouvrir_archive(tmp_path / "B")
    assert arc.lire_page(id_page).onglets[0].texte == "Ne pas perdre"
    assert saisie.page is None


def test_refuser_d_abandonner_garde_l_archive_ouverte(fenetre, arc, tmp_path, qtbot):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    saisie.demander_validation()
    editeur_notes(saisie).texte.setPlainText("Modifié")
    saisie.reponses["confirmer"] = False
    Archive.creer(tmp_path / "B", "s", "r", date(2026, 1, 1))
    assert not fenetre.ouvrir_archive(tmp_path / "B")
    assert fenetre.archive.racine == arc.racine


# --- Reprise depuis l'espace Archive ------------------------------------------------


def test_une_modification_hors_du_logiciel_se_reprend_depuis_l_alerte(
    fenetre, arc, qtbot, monkeypatch
):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    id_page = saisie.page.id
    attendre(fenetre, qtbot)
    chemin = arc.racine / "pages" / id_page / "page.json"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    espace = fenetre.espace_archive
    espace.actualiser()
    attendre(fenetre, qtbot)
    assert not espace.bouton_reprise.isHidden()

    monkeypatch.setattr(espace, "_demander_motif_reprise", lambda: "Vérifié à la main")
    espace.bouton_reprise.click()
    attendre(fenetre, qtbot)
    assert espace.non_tracees == []
    assert not espace.bandeau.isVisibleTo(espace)
    assert arc.historique(id_page)[-1].motif == "Vérifié à la main"


def test_annuler_le_motif_de_reprise_ne_change_rien(fenetre, arc, qtbot, monkeypatch):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    id_page = saisie.page.id
    chemin = arc.racine / "pages" / id_page / "page.json"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    espace = fenetre.espace_archive
    attendre(fenetre, qtbot)
    espace.actualiser()
    attendre(fenetre, qtbot)
    monkeypatch.setattr(espace, "_demander_motif_reprise", lambda: None)
    espace.bouton_reprise.click()
    assert espace.non_tracees


def test_un_motif_de_reprise_vide_est_refuse(fenetre, arc, qtbot, monkeypatch):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    chemin = arc.racine / "pages" / saisie.page.id / "page.json"
    chemin.write_text(chemin.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    espace = fenetre.espace_archive
    attendre(fenetre, qtbot)
    espace.actualiser()
    attendre(fenetre, qtbot)
    erreurs = []
    monkeypatch.setattr(espace, "afficher_erreur", erreurs.append)
    monkeypatch.setattr(espace, "_demander_motif_reprise", lambda: "  ")
    espace.bouton_reprise.click()
    assert "motif" in erreurs[0]
    assert espace.non_tracees


# --- Cœur de l'édition --------------------------------------------------------------


def page_simple(**champs):
    return Page.model_validate(
        {
            "id": "2026-11-14_a_0001",
            "titre": "T",
            "date_mesure": HEURE,
            "date_saisie": HEURE,
            "auteur": "G",
        }
        | champs
    )


def test_modifier_champs_verifie_les_regles():
    page = page_simple()
    assert modifier_champs(page, titre="Autre").titre == "Autre"
    assert page.titre == "T"
    with pytest.raises(ValueError):
        modifier_champs(page, titre="")
    with pytest.raises(ValueError):
        modifier_champs(page, machine="capintec")


def test_modifier_champs_garde_les_champs_d_un_onglet_inconnu():
    page = page_simple(onglets=[FUTUR])
    relue_page = modifier_champs(page, titre="Autre")
    assert relue_page.onglets[0].pics_keV == [140.5, 159.0]
    assert depuis_json(relue_page.model_dump_json()) == relue_page


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [("", ()), ("a", ("a",)), (" a , b ,, a", ("a", "b")), (",,", ())],
)
def test_decouper_tags(texte, attendu):
    assert decouper_tags(texte) == attendu


def test_une_date_inchangee_garde_son_fuseau_et_ses_secondes():
    ancienne = datetime(2026, 11, 14, 9, 30, 17, tzinfo=timezone(timedelta(hours=9)))
    assert date_saisie_vers_mesure(datetime(2026, 11, 14, 9, 30), ancienne) is ancienne


def test_une_date_changee_recoit_un_fuseau():
    ancienne = datetime(2026, 11, 14, 9, 30, tzinfo=PARIS)
    nouvelle = date_saisie_vers_mesure(datetime(2026, 11, 14, 9, 31), ancienne)
    assert nouvelle.tzinfo is not None
    assert nouvelle.replace(tzinfo=None) == datetime(2026, 11, 14, 9, 31)


def test_nouvelle_page_prepare_un_brouillon_sans_l_enregistrer(arc):
    page = nouvelle_page(arc, "Contrôle n°3", HEURE, "G.", tags=["a"])
    assert page.id == "2026-11-14_controle-n3_0001"
    assert page.statut == "brouillon"
    assert page.date_saisie.tzinfo is not None
    assert arc.identifiants() == []


def test_message_erreur_lisible():
    with pytest.raises(ValueError) as erreur:
        modifier_champs(page_simple(), mesures=[scalaire() | {"unite": "Mbq"}])
    assert "vouliez-vous dire" in message_erreur(erreur.value)
    assert "pydantic" not in message_erreur(erreur.value)
    assert message_erreur(KeyError("absente")) == "absente"


def test_une_reprise_recharge_la_page_ouverte(fenetre, arc, qtbot, monkeypatch):
    saisie = fenetre.espace_saisie
    saisie.creer_page(description())
    chemin = arc.racine / "pages" / saisie.page.id / "page.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    contenu["titre"] = "Modifié à la main"
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    espace = fenetre.espace_archive
    attendre(fenetre, qtbot)
    espace.actualiser()
    attendre(fenetre, qtbot)
    monkeypatch.setattr(espace, "_demander_motif_reprise", lambda: "Vérifié")
    espace.bouton_reprise.click()
    attendre(fenetre, qtbot)
    assert saisie.champ_titre.text() == "Modifié à la main"
    assert saisie.enregistrer_maintenant()
    assert arc.lire_page(saisie.page.id).titre == "Modifié à la main"
