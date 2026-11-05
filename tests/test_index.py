"""Vérifie l'index de recherche : critères, reconstruction et performances."""

import json
import shutil
import time
from datetime import date, datetime, timedelta, timezone

import pytest

from medphystools.archive import Archive
from medphystools.gabarit import creer_gabarit
from medphystools.index import CritereInvalide, _texte_de_page
from medphystools.page import Page, vers_json

PARIS = timezone(timedelta(hours=1))
MACHINE_A = "chu.mn.activimetre.capintec-crc55.1"
MACHINE_B = "chu.radio.scanner.ge-revolution.2"

ACTIVIMETRE = "2026-11-14_activimetre_0001"
SCANNER = "2026-11-20_scanner_0002"
TEP = "2026-12-01_tep_0003"


def scalaire(nom="activite"):
    return {
        "nature": "scalaire",
        "nom": nom,
        "valeur": 10.5,
        "unite": "MBq",
        "composantes": [
            {
                "type": "B",
                "valeur": 1.0,
                "unite": "MBq",
                "source": "certificat Cal-2026",
            }
        ],
        "commentaire": "source au centre du puits",
    }


def page(id_page, jour, heure=9, *mesures, **champs):
    return Page.model_validate(
        {
            "id": id_page,
            "titre": "Contrôle",
            "date_mesure": datetime(2026, *jour, heure, 30, tzinfo=PARIS),
            "date_saisie": datetime(2026, 12, 31, 12, tzinfo=PARIS),
            "auteur": "G.",
            "mesures": mesures,
        }
        | champs
    )


@pytest.fixture
def arc(tmp_path):
    archive = Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))
    archive.declarer_machine(MACHINE_A)
    archive.declarer_machine(MACHINE_B)
    archive.enregistrer_gabarit(
        creer_gabarit(
            page(ACTIVIMETRE, (11, 14), 9, scalaire()), "activimetre", ["activite"]
        )
    )
    archive.enregistrer_gabarit(
        creer_gabarit(
            page(ACTIVIMETRE, (11, 14), 9, scalaire()),
            "activimetre",
            ["activite"],
            version=2,
        )
    )
    archive.creer_page(
        page(
            ACTIVIMETRE,
            (11, 14),
            9,
            scalaire(),
            {"nature": "identifiant", "nom": "numero_lot", "valeur": "LOT-00742"},
            titre="Linéarité de l'activimètre",
            description="Décroissance du Tc-99m",
            tags=["activimetre", "linearite", "Tc99m"],
            machine=MACHINE_A,
            gabarit="activimetre@1",
            onglets=[
                {
                    "id": "o1",
                    "type": "notes",
                    "titre": "Montage",
                    "texte": "Flacon placé dans le puits",
                }
            ],
        )
    )
    archive.creer_page(
        page(
            SCANNER,
            (11, 20),
            14,
            titre="Uniformité du scanner",
            tags=["scanner", "uniformite"],
            machine=MACHINE_B,
            auteur="Physicien B",
        )
    )
    archive.creer_page(
        page(
            TEP,
            (12, 1),
            8,
            titre="Activité de référence TEP",
            tags=["activimetre", "tep"],
            machine=MACHINE_A,
            gabarit="activimetre@2",
        )
    )
    archive.valider_page(SCANNER)
    return archive


def test_sans_critere_toutes_les_pages_de_la_plus_recente_a_la_plus_ancienne(arc):
    assert arc.chercher_identifiants() == [TEP, SCANNER, ACTIVIMETRE]


def test_tri_sur_l_instant_reel_de_la_mesure(arc):
    # 23 h 30 à Tokyo a lieu avant 9 h 30 à Paris, le même jour.
    tokyo = timezone(timedelta(hours=9))
    arc.creer_page(
        page(
            "2026-11-14_tokyo_0004",
            (11, 14),
            date_mesure=datetime(2026, 11, 14, 8, 0, tzinfo=tokyo),
        )
    )
    assert arc.chercher_identifiants(depuis="2026-11-14", jusqua="2026-11-14") == [
        ACTIVIMETRE,
        "2026-11-14_tokyo_0004",
    ]


# --- Tags -------------------------------------------------------------------------


def test_un_tag(arc):
    assert arc.chercher_identifiants(tags=["scanner"]) == [SCANNER]


def test_plusieurs_tags_et(arc):
    assert arc.chercher_identifiants(tags=["activimetre", "tep"]) == [TEP]
    assert arc.chercher_identifiants(tags=["scanner", "tep"]) == []


def test_plusieurs_tags_ou(arc):
    assert arc.chercher_identifiants(tags=["scanner", "tep"], mode_tags="ou") == [
        TEP,
        SCANNER,
    ]


def test_un_tag_ne_tient_pas_compte_de_la_casse(arc):
    assert arc.chercher_identifiants(tags=["TC99M"]) == [ACTIVIMETRE]


def test_un_tag_repete_n_exclut_pas_la_page(arc):
    assert arc.chercher_identifiants(tags=["scanner", "SCANNER"]) == [SCANNER]


def test_un_tag_entier_seulement(arc):
    assert arc.chercher_identifiants(tags=["activi"]) == []


def test_mode_de_tags_invalide(arc):
    with pytest.raises(CritereInvalide):
        arc.chercher_identifiants(tags=["a"], mode_tags="peut-etre")


# --- Dates ------------------------------------------------------------------------


def test_depuis_inclus(arc):
    assert arc.chercher_identifiants(depuis="2026-11-20") == [TEP, SCANNER]


def test_jusqua_inclus(arc):
    assert arc.chercher_identifiants(jusqua="2026-11-20") == [SCANNER, ACTIVIMETRE]


def test_intervalle(arc):
    assert arc.chercher_identifiants(
        depuis=date(2026, 11, 15), jusqua=date(2026, 11, 30)
    ) == [SCANNER]


def test_date_de_type_datetime(arc):
    assert arc.chercher_identifiants(depuis=datetime(2026, 12, 1, 23, 0)) == [TEP]


def test_le_jour_est_celui_du_fuseau_de_la_mesure(arc):
    # 0 h 30 à Paris le 15 est encore le 14 en temps universel : on cherche le 15.
    identifiant = "2026-11-15_nuit_0004"
    arc.creer_page(page(identifiant, (11, 15), 0))
    assert arc.chercher_identifiants(depuis="2026-11-15", jusqua="2026-11-15") == [
        identifiant
    ]
    assert identifiant not in arc.chercher_identifiants(jusqua="2026-11-14")


@pytest.mark.parametrize("valeur", ["hier", "14/11/2026", "2026-13-01"])
def test_date_invalide(arc, valeur):
    with pytest.raises(CritereInvalide, match="AAAA-MM-JJ"):
        arc.chercher_identifiants(depuis=valeur)


# --- Autres critères --------------------------------------------------------------


def test_machine(arc):
    assert arc.chercher_identifiants(machine=MACHINE_A) == [TEP, ACTIVIMETRE]


def test_statut(arc):
    assert arc.chercher_identifiants(statut="validée") == [SCANNER]
    assert arc.chercher_identifiants(statut="brouillon") == [TEP, ACTIVIMETRE]


def test_auteur(arc):
    assert arc.chercher_identifiants(auteur="Physicien B") == [SCANNER]


def test_gabarit_par_reference_exacte(arc):
    assert arc.chercher_identifiants(gabarit="activimetre@1") == [ACTIVIMETRE]


def test_gabarit_par_nom_toutes_versions(arc):
    assert arc.chercher_identifiants(gabarit="activimetre") == [TEP, ACTIVIMETRE]


def test_criteres_combines(arc):
    assert arc.chercher_identifiants(
        tags=["activimetre"],
        depuis="2026-11-01",
        jusqua="2026-11-30",
        machine=MACHINE_A,
        statut="brouillon",
        auteur="G.",
        gabarit="activimetre",
        texte="linearite",
    ) == [ACTIVIMETRE]
    assert arc.chercher_identifiants(tags=["activimetre"], statut="validée") == []


# --- Texte libre ------------------------------------------------------------------


@pytest.mark.parametrize(
    "texte",
    [
        "linéarité",  # titre
        "décroissance",  # description
        "Tc99m",  # tag
        "LOT-00742",  # identifiant
        "lot-00742",  # sans la casse
        "numero_lot",  # nom de grandeur
        "puits",  # commentaire et note
        "flacon",  # onglet de notes
        "Montage",  # titre d'onglet
        "Cal-2026",  # source d'incertitude
        "capintec",  # machine
    ],
)
def test_texte_trouve_partout_dans_la_page(arc, texte):
    assert ACTIVIMETRE in arc.chercher_identifiants(texte=texte)


def test_texte_sans_accents(arc):
    assert arc.chercher_identifiants(texte="activimetre lineairite") == []
    assert arc.chercher_identifiants(texte="LINEARITE") == [ACTIVIMETRE]
    assert arc.chercher_identifiants(texte="uniformite") == [SCANNER]


def test_debut_de_mot(arc):
    assert arc.chercher_identifiants(texte="unif") == [SCANNER]


def test_tous_les_mots_sont_requis(arc):
    assert arc.chercher_identifiants(texte="activimètre linéarité") == [ACTIVIMETRE]
    assert arc.chercher_identifiants(texte="linéarité uniformité") == []


@pytest.mark.parametrize(
    "texte",
    ['"', "OR", "a AND", "NEAR(", "col:x", "*", "'; DROP TABLE pages; --", "a* b^"],
)
def test_une_saisie_particuliere_ne_provoque_pas_d_erreur(arc, texte):
    arc.chercher_identifiants(texte=texte)
    assert len(arc.chercher_identifiants()) == 3


def test_ponctuation_seule_sans_effet(arc):
    assert len(arc.chercher_identifiants(texte="!!!")) == 3


def test_les_valeurs_numeriques_ne_sont_pas_indexees(arc):
    assert arc.chercher_identifiants(texte="10.5") == []


# --- Cache : reconstruction et fraîcheur -------------------------------------------


def fichier_index(arc):
    return arc.racine / "index.sqlite"


def test_l_index_se_cree_a_la_premiere_recherche(arc):
    assert not fichier_index(arc).exists()
    arc.chercher_identifiants()
    assert fichier_index(arc).is_file()


def test_supprime_il_se_reconstruit_a_l_identique(arc):
    arc.chercher_identifiants()
    avant = arc.contenu_index()
    fichier_index(arc).unlink()
    assert arc.chercher_identifiants() == [TEP, SCANNER, ACTIVIMETRE]
    assert arc.contenu_index() == avant


def test_mise_a_jour_progressive_identique_a_une_reconstruction(arc):
    arc.chercher_identifiants()
    arc.modifier_page(
        page(
            TEP,
            (12, 1),
            8,
            titre="Titre changé",
            tags=["nouveau"],
            machine=MACHINE_A,
            gabarit="activimetre@2",
        )
    )
    arc.chercher_identifiants()
    progressive = arc.contenu_index()
    fichier_index(arc).unlink()
    arc.chercher_identifiants()
    assert arc.contenu_index() == progressive


def test_un_index_perime_est_detecte(arc):
    arc.chercher_identifiants()
    assert arc.index_a_jour()
    arc.creer_page(page("2026-12-05_nouvelle_0004", (12, 5), titre="Toute nouvelle"))
    assert not arc.index_a_jour()
    assert arc.chercher_identifiants(texte="nouvelle") == ["2026-12-05_nouvelle_0004"]
    assert arc.index_a_jour()


def test_une_correction_est_visible_dans_la_recherche(arc):
    arc.chercher_identifiants()
    arc.modifier_page(
        page(
            SCANNER,
            (11, 20),
            14,
            titre="Bruit du scanner",
            tags=["bruit"],
            machine=MACHINE_B,
            auteur="Physicien B",
            statut="validée",
        ),
        motif="Mauvais titre",
    )
    assert arc.chercher_identifiants(texte="bruit") == [SCANNER]
    assert arc.chercher_identifiants(texte="uniformité") == []
    assert arc.chercher_identifiants(tags=["uniformite"]) == []
    assert arc.chercher_identifiants(statut="corrigée") == [SCANNER]


def test_une_page_supprimee_et_reprise_quitte_l_index(arc):
    arc.chercher_identifiants()
    shutil.rmtree(arc.racine / "pages" / TEP)
    arc.enregistrer_reprise("Page de test supprimée")
    assert arc.chercher_identifiants() == [SCANNER, ACTIVIMETRE]


def test_un_index_abime_est_reconstruit(arc):
    arc.chercher_identifiants()
    fichier_index(arc).write_bytes(b"ceci n'est pas une base SQLite")
    assert arc.chercher_identifiants() == [TEP, SCANNER, ACTIVIMETRE]


def test_un_index_d_une_autre_version_est_reconstruit(arc):
    import sqlite3

    arc.chercher_identifiants()
    connexion = sqlite3.connect(fichier_index(arc))
    connexion.execute("UPDATE meta SET valeur = '0' WHERE cle = 'version'")
    connexion.commit()
    connexion.close()
    assert not arc.index_a_jour()
    assert arc.chercher_identifiants() == [TEP, SCANNER, ACTIVIMETRE]
    assert arc.index_a_jour()


def test_l_index_reste_hors_de_git(arc):
    arc.chercher_identifiants()
    assert arc.modifications_non_tracees() == []
    from medphystools.depot import Depot

    assert "index.sqlite" not in Depot(arc.racine).fichiers_suivis()


def test_l_index_survit_au_deplacement_de_l_archive(arc, tmp_path):
    arc.chercher_identifiants()
    destination = tmp_path / "ailleurs"
    shutil.move(arc.racine, destination)
    deplacee = Archive(destination)
    assert deplacee.index_a_jour()
    assert deplacee.chercher_identifiants(tags=["scanner"]) == [SCANNER]


# --- Pages illisibles -------------------------------------------------------------


@pytest.mark.parametrize(
    "contenu",
    [
        "{ pas du JSON",
        "[]",
        "{}",
        '{"id": "autre", "date_mesure": "2026-11-14T09:30:00+01:00"}',
        '{"id": "%s"}' % TEP,
        '{"id": "%s", "date_mesure": "pas une date"}' % TEP,
        '{"id": "%s", "date_mesure": "2026-12-01T08:30:00+01:00", "mesures": 3}' % TEP,
    ],
)
def test_une_page_illisible_est_signalee_sans_gener_les_autres(arc, contenu):
    (arc.racine / "pages" / TEP / "page.json").write_text(contenu, encoding="utf-8")
    arc.enregistrer_reprise("Fichier abîmé")
    assert arc.chercher_identifiants() == [SCANNER, ACTIVIMETRE]
    illisibles = arc.pages_illisibles()
    assert [p.id for p in illisibles] == [TEP]
    assert illisibles[0].erreur
    assert arc.historique(TEP)[-1].nature == "reprise"
    assert arc.modifications_non_tracees() == []


def test_une_page_reparee_revient_dans_l_index(arc):
    chemin = arc.racine / "pages" / TEP / "page.json"
    bon = chemin.read_text(encoding="utf-8")
    chemin.write_text("{ abîmé", encoding="utf-8")
    arc.enregistrer_reprise("Fichier abîmé")
    assert arc.pages_illisibles()
    chemin.write_text(bon, encoding="utf-8")
    arc.enregistrer_reprise("Fichier restauré")
    assert arc.pages_illisibles() == []
    assert TEP in arc.chercher_identifiants()


def test_une_page_d_un_format_plus_recent_reste_trouvable(arc):
    chemin = arc.racine / "pages" / TEP / "page.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    contenu["version_schema"] = 99
    contenu["champ_futur"] = {"a": 1}
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    arc.enregistrer_reprise("Page issue d'une version plus récente")
    assert TEP in arc.chercher_identifiants(tags=["tep"])
    assert arc.pages_illisibles() == []


# --- Performances -----------------------------------------------------------------

NOMBRE_PAGES = 5000


@pytest.fixture(scope="module")
def grande_archive(tmp_path_factory):
    """Une archive de 5 000 pages, écrites directement pour ne pas créer 5 000 commits."""
    racine = tmp_path_factory.mktemp("grande") / "A"
    archive = Archive.creer(racine, "CHU X", "Resp.", date(2026, 1, 1))
    machines = [MACHINE_A, MACHINE_B]
    for machine in machines:
        archive.declarer_machine(machine)
    for i in range(NOMBRE_PAGES):
        jour = date(2020, 1, 1) + timedelta(days=i % 2000)
        identifiant = f"{jour.isoformat()}_controle_{i + 1:04d}"
        contenu = page(
            identifiant,
            (1, 1),
            date_mesure=datetime(jour.year, jour.month, jour.day, 9, 30, tzinfo=PARIS),
            titre=f"Contrôle numéro {i}",
            tags=["routine", f"groupe{i % 50}"],
            machine=machines[i % 2],
            mesures=[scalaire(f"grandeur_{j}") for j in range(5)],
        )
        dossier = racine / "pages" / identifiant
        dossier.mkdir()
        (dossier / "page.json").write_text(vers_json(contenu), encoding="utf-8")
    return archive


def test_construction_et_recherche_sur_5000_pages(grande_archive):
    (grande_archive.racine / "index.sqlite").unlink(missing_ok=True)

    debut = time.perf_counter()
    assert len(grande_archive.chercher_identifiants()) == NOMBRE_PAGES
    assert time.perf_counter() - debut < 30

    debut = time.perf_counter()
    trouvees = grande_archive.chercher_identifiants(
        tags=["routine", "groupe7"],
        depuis="2020-06-01",
        jusqua="2025-01-01",
        machine=MACHINE_B,
        texte="contrôle numéro",
    )
    assert time.perf_counter() - debut < 1
    assert trouvees

    debut = time.perf_counter()
    assert grande_archive.chercher_identifiants(texte="numéro 4242")
    assert time.perf_counter() - debut < 1


def test_un_chemin_de_csv_falsifie_ne_sort_pas_de_la_page(arc):
    chemin = arc.racine / "pages" / TEP / "page.json"
    contenu = json.loads(chemin.read_text(encoding="utf-8"))
    contenu["mesures"] = [
        {"nature": "tableau", "nom": "t", "fichier": "../../archive.toml"},
        {"nature": "tableau", "nom": "u", "fichier": "/etc/hostname"},
        {"nature": "tableau", "nom": 3, "fichier": 4},
        "pas une grandeur",
    ]
    chemin.write_text(json.dumps(contenu), encoding="utf-8")
    assert arc.modifications_non_tracees() == [f"pages/{TEP}/page.json"]


def test_le_texte_d_une_annotation_se_cherche():
    page = {
        "onglets": [
            {
                "titre": "Montage",
                "photos": [
                    {
                        "legende": "Vue de face",
                        "annotations": [{"texte": "Fissure du blindage"}],
                    }
                ],
            }
        ]
    }
    assert "Fissure du blindage" in _texte_de_page(page)
