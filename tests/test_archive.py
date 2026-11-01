"""Vérifie la création et l'ouverture d'une archive, ses pages et ses gabarits."""

import os
import shutil
import tomllib
from datetime import date, datetime, timedelta, timezone

import pytest

from pymphub import archive as module_archive
from pymphub import disque
from pymphub.archive import (
    VERSION_FORMAT,
    Archive,
    ArchiveIntrouvable,
    ArchiveInvalide,
    FormatNonPrisEnCharge,
    GabaritIntrouvable,
    PageIntrouvable,
    PageRefusee,
)
from pymphub.disque import (
    NomInvalide,
    chemin_affichable,
    chemin_long,
    ecrire_atomique,
    prefixer_chemin_windows,
    verifier_nom_windows,
)
from pymphub.gabarit import creer_gabarit
from pymphub.page import Page, depuis_json

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MACHINE = "chu-x.medecine-nucleaire.activimetre.capintec-crc55.12345"
ID_PAGE = "2026-11-14_activimetre-linearite_0007"


def scalaire(nom="activite", unite="MBq"):
    return {
        "nature": "scalaire",
        "nom": nom,
        "valeur": 10.5,
        "unite": unite,
        "composantes": [{"type": "B", "valeur": 1.0, "unite": unite, "source": "s"}],
    }


def page(id_page=ID_PAGE, *mesures, **champs):
    return Page.model_validate(
        {
            "id": id_page,
            "titre": "Linéarité",
            "date_mesure": HEURE,
            "date_saisie": HEURE,
            "auteur": "G.",
            "mesures": mesures,
        }
        | champs
    )


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(
        tmp_path / "ArchiveStage1",
        site="CHU X",
        responsable="G.",
        periode_debut=date(2026, 11, 1),
    )


def test_creation_de_l_arborescence(arc):
    for dossier in ("normes", "gabarits", "objets", "pages"):
        assert (arc.racine / dossier).is_dir()
    assert (arc.racine / "archive.toml").is_file()
    assert (arc.racine / "FORMAT.md").is_file()


def test_archive_toml_est_lisible(arc):
    contenu = tomllib.loads((arc.racine / "archive.toml").read_text(encoding="utf-8"))
    assert contenu["nom"] == "ArchiveStage1"
    assert contenu["site"] == "CHU X"
    assert contenu["version_format"] == VERSION_FORMAT
    assert contenu["periode_debut"] == date(2026, 11, 1)
    assert "periode_fin" not in contenu


def test_format_md_annonce_la_version_du_format(arc):
    texte = (arc.racine / "FORMAT.md").read_text(encoding="utf-8")
    assert f"Version du format : {VERSION_FORMAT}" in texte


def test_creation_refusee_si_le_dossier_existe(tmp_path):
    (tmp_path / "A").mkdir()
    with pytest.raises(FileExistsError):
        Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1))


def test_creation_interrompue_ne_laisse_pas_d_archive(tmp_path, monkeypatch):
    def echec(*_):
        raise OSError("disque plein")

    monkeypatch.setattr(module_archive, "_ecrire_configuration", echec)
    with pytest.raises(OSError):
        Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1))
    assert os.listdir(tmp_path) == []

    monkeypatch.undo()
    assert (
        Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1)).identifiants() == []
    )


def test_le_reste_d_une_coupure_de_courant_ne_bloque_pas_la_creation(tmp_path):
    reste = tmp_path / ".A.creation"
    reste.mkdir()
    (reste / "archive.toml").write_text("incomplet", encoding="utf-8")

    arc = Archive.creer(tmp_path / "A", "s", "r", date(2026, 1, 1))
    assert arc.configuration.nom == "A"
    assert os.listdir(tmp_path) == ["A"]


@pytest.mark.parametrize("nom", ["CON", "aux.txt", "archive:1", "archive."])
def test_nom_d_archive_invalide_sous_windows_refuse(tmp_path, nom):
    with pytest.raises(NomInvalide):
        Archive.creer(tmp_path / nom, "s", "r", date(2026, 1, 1))


def test_une_archive_deplacee_s_ouvre_toujours(arc, tmp_path):
    arc.declarer_machine(MACHINE)
    arc.creer_page(page(machine=MACHINE))
    arc.enregistrer_gabarit(creer_gabarit(page(ID_PAGE, scalaire()), "g", ["activite"]))

    destination = tmp_path / "cle_usb" / "copie"
    destination.parent.mkdir()
    shutil.move(arc.racine, destination)

    deplacee = Archive(destination)
    assert deplacee.identifiants() == [ID_PAGE]
    assert deplacee.lire_page(ID_PAGE).machine == MACHINE
    assert deplacee.gabarits() == ["g@1"]


def test_aucun_chemin_absolu_n_est_stocke(arc, tmp_path):
    arc.creer_page(page())
    for chemin in arc.racine.rglob("*"):
        if chemin.is_file():
            assert str(tmp_path).encode() not in chemin.read_bytes()


def test_dossier_ordinaire_refuse(tmp_path):
    with pytest.raises(ArchiveIntrouvable):
        Archive(tmp_path)


def test_dossier_absent_refuse(tmp_path):
    with pytest.raises(ArchiveIntrouvable):
        Archive(tmp_path / "absent")


def ecrire_configuration(arc, texte):
    (arc.racine / "archive.toml").write_text(texte, encoding="utf-8")


def test_format_trop_recent_refuse_meme_avec_des_champs_inconnus(arc):
    ecrire_configuration(
        arc, f"version_format = {VERSION_FORMAT + 1}\nchamp_futur = 1\n"
    )
    with pytest.raises(FormatNonPrisEnCharge, match="mettre à jour"):
        Archive(arc.racine)


@pytest.mark.parametrize(
    "texte",
    [
        "ceci n'est pas du TOML",
        'nom = "sans version"\n',
        'version_format = "1"\n',
        "version_format = 0\n",
        "version_format = true\n",
        "version_format = 1\n",
    ],
)
def test_configuration_illisible_refusee(arc, texte):
    ecrire_configuration(arc, texte)
    with pytest.raises(ArchiveInvalide):
        Archive(arc.racine)


def test_ecrire_et_relire_une_page(arc):
    originale = page(ID_PAGE, scalaire(), tags=["activimetre"])
    dossier = arc.creer_page(originale)
    assert dossier == arc.racine / "pages" / ID_PAGE
    assert arc.lire_page(ID_PAGE) == originale
    assert Archive(arc.racine).lire_page(ID_PAGE) == originale


def test_page_json_est_en_utf8_avec_des_fins_de_ligne_lf(arc):
    dossier = arc.creer_page(page())
    octets = (dossier / "page.json").read_bytes()
    assert "Linéarité".encode() in octets
    assert b"\r\n" not in octets


def test_creer_une_page_existante_est_refuse(arc):
    arc.creer_page(page())
    with pytest.raises(PageRefusee, match="existe déjà"):
        arc.creer_page(page(titre="Autre"))
    assert arc.lire_page(ID_PAGE).titre == "Linéarité"


def test_modifier_une_page(arc):
    arc.creer_page(page())
    arc.modifier_page(page(titre="Nouveau titre"))
    assert arc.lire_page(ID_PAGE).titre == "Nouveau titre"


def test_modifier_une_page_absente_est_refuse(arc):
    with pytest.raises(PageIntrouvable):
        arc.modifier_page(page())


def test_lire_une_page_absente_est_refuse(arc):
    with pytest.raises(PageIntrouvable):
        arc.lire_page(ID_PAGE)


@pytest.mark.parametrize(
    "identifiant", ["../../secret", "a/b", "", "CON", ID_PAGE + "/.."]
)
def test_un_identifiant_mal_forme_ne_sort_pas_de_l_archive(arc, identifiant):
    with pytest.raises(PageIntrouvable):
        arc.lire_page(identifiant)


def test_un_dossier_contenant_une_autre_page_est_signale(arc):
    dossier = arc.creer_page(page())
    autre = "2026-11-14_autre_0008"
    dossier.rename(arc.racine / "pages" / autre)
    with pytest.raises(ArchiveInvalide, match=autre):
        arc.lire_page(autre)


def test_identifiants_tries(arc):
    arc.creer_page(page("2026-11-15_b_0002"))
    arc.creer_page(page("2026-11-14_a_0001"))
    (arc.racine / "pages" / "dossier_sans_page").mkdir()
    assert arc.identifiants() == ["2026-11-14_a_0001", "2026-11-15_b_0002"]


def test_nouvel_identifiant_incremente_le_numero(arc):
    premier = arc.nouvel_identifiant(HEURE, "Activimètre linéarité")
    assert premier == "2026-11-14_activimetre-linearite_0001"
    arc.creer_page(page(premier))
    second = arc.nouvel_identifiant(HEURE, "Scanner")
    assert second == "2026-11-14_scanner_0002"


def test_nouvel_identifiant_suit_le_plus_grand_numero(arc):
    arc.creer_page(page("2026-11-14_a_0041"))
    arc.creer_page(page("2026-11-13_b_0003"))
    assert arc.nouvel_identifiant(HEURE, "c").endswith("_0042")


@pytest.mark.parametrize(
    ("sujet", "attendu"),
    [
        ("Contrôle n°3 : TEP/TDM", "controle-n3-tep-tdm"),
        ("CON", "con"),
        ("!!!", "page"),
        ("", "page"),
        ("x" * 100, "x" * 40),
        ("a" * 39 + " b", "a" * 39),
    ],
)
def test_le_sujet_donne_un_identifiant_valide_et_relisible(arc, sujet, attendu):
    identifiant = arc.nouvel_identifiant(HEURE, sujet)
    assert identifiant == f"2026-11-14_{attendu}_0001"
    verifier_nom_windows(identifiant)
    arc.creer_page(page(identifiant))
    assert arc.lire_page(identifiant).id == identifiant


def test_machine_non_declaree_refusee(arc):
    with pytest.raises(PageRefusee, match="non déclarée"):
        arc.creer_page(page(machine=MACHINE))
    assert arc.identifiants() == []


def test_declarer_une_machine(arc):
    arc.declarer_machine(MACHINE)
    arc.declarer_machine(MACHINE)
    assert Archive(arc.racine).configuration.machines == (MACHINE,)
    arc.creer_page(page(machine=MACHINE))


def test_machine_mal_formee_refusee(arc):
    with pytest.raises(ValueError, match="site.service"):
        arc.declarer_machine("capintec")


@pytest.fixture
def gabarit():
    return creer_gabarit(
        page(ID_PAGE, scalaire()), "activimetre.linearite", ["activite"]
    )


def test_enregistrer_et_relire_un_gabarit(arc, gabarit):
    arc.enregistrer_gabarit(gabarit)
    assert (arc.racine / "gabarits" / "activimetre.linearite@1.toml").is_file()
    assert arc.lire_gabarit("activimetre.linearite@1") == gabarit
    assert arc.gabarits() == ["activimetre.linearite@1"]


def test_reenregistrer_un_gabarit_identique_est_sans_effet(arc, gabarit):
    arc.enregistrer_gabarit(gabarit)
    arc.enregistrer_gabarit(gabarit)
    assert arc.gabarits() == [gabarit.reference]


def test_une_version_de_gabarit_ne_change_pas_de_contenu(arc, gabarit):
    arc.enregistrer_gabarit(gabarit)
    autre = creer_gabarit(
        page(ID_PAGE, scalaire("temperature", "K")), gabarit.nom, ["temperature"]
    )
    with pytest.raises(FileExistsError, match="nouvelle version"):
        arc.enregistrer_gabarit(autre)
    assert arc.lire_gabarit(gabarit.reference) == gabarit


def test_gabarit_absent_signale(arc):
    with pytest.raises(GabaritIntrouvable):
        arc.lire_gabarit("absent@1")


def test_page_rattachee_a_un_gabarit_absent_refusee(arc):
    with pytest.raises(PageRefusee, match="absent@1"):
        arc.creer_page(page(gabarit="absent@1"))


def test_un_brouillon_peut_avoir_des_variables_a_remplir(arc, gabarit):
    arc.enregistrer_gabarit(gabarit)
    arc.creer_page(page(gabarit=gabarit.reference))
    assert arc.lire_page(ID_PAGE).mesures == ()


def test_une_page_validee_doit_etre_complete(arc, gabarit):
    arc.enregistrer_gabarit(gabarit)
    arc.creer_page(page(gabarit=gabarit.reference))
    with pytest.raises(PageRefusee, match="activite"):
        arc.valider_page(ID_PAGE)
    assert arc.lire_page(ID_PAGE).statut == "brouillon"

    arc.modifier_page(page(ID_PAGE, scalaire(), gabarit=gabarit.reference))
    assert arc.valider_page(ID_PAGE).statut == "validée"


def test_une_unite_incoherente_avec_le_gabarit_bloque_meme_un_brouillon(arc, gabarit):
    arc.enregistrer_gabarit(gabarit)
    incoherente = page(ID_PAGE, scalaire(unite="mm"), gabarit=gabarit.reference)
    with pytest.raises(PageRefusee, match="incompatible"):
        arc.creer_page(incoherente)
    assert arc.identifiants() == []

    arc.creer_page(page(gabarit=gabarit.reference))
    with pytest.raises(PageRefusee, match="incompatible"):
        arc.modifier_page(incoherente)
    assert arc.lire_page(ID_PAGE).mesures == ()


def test_ecriture_interrompue_laisse_l_ancien_fichier_intact(arc, monkeypatch):
    dossier = arc.creer_page(page())

    def echec(*_):
        raise OSError("coupure")

    monkeypatch.setattr(disque.os, "replace", echec)
    with pytest.raises(OSError):
        arc.modifier_page(page(titre="Nouveau titre"))

    monkeypatch.undo()
    assert arc.lire_page(ID_PAGE).titre == "Linéarité"
    assert [f.name for f in dossier.iterdir()] == ["page.json"]


def test_echec_a_la_creation_ne_laisse_pas_de_dossier(arc, monkeypatch):
    def echec(*_):
        raise OSError("coupure")

    monkeypatch.setattr(disque.os, "replace", echec)
    with pytest.raises(OSError):
        arc.creer_page(page())
    monkeypatch.undo()
    assert not (arc.racine / "pages" / ID_PAGE).exists()


def test_ecrire_atomique_remplace_le_contenu(tmp_path):
    chemin = tmp_path / "f.txt"
    ecrire_atomique(chemin, "a\n")
    ecrire_atomique(chemin, "é\n")
    assert chemin.read_bytes() == "é\n".encode()
    assert os.listdir(tmp_path) == ["f.txt"]


@pytest.mark.parametrize(
    "nom",
    [
        "",
        ".",
        "..",
        "CON",
        "con",
        "PRN.txt",
        "aux.tar.gz",
        "NUL",
        "COM1",
        "LPT9.log",
        "a<b",
        "a>b",
        "a:b",
        'a"b',
        "a/b",
        "a\\b",
        "a|b",
        "a?b",
        "a*b",
        "a\tb",
        "fin.",
        "fin ",
        "x" * 256,
    ],
)
def test_nom_invalide_sous_windows_refuse(nom):
    with pytest.raises(NomInvalide):
        verifier_nom_windows(nom)


@pytest.mark.parametrize(
    "nom",
    [
        "page.json",
        "activimetre.linearite@1.toml",
        "CONTROLE",
        "COM10",
        "été.txt",
        ID_PAGE,
    ],
)
def test_nom_valide_sous_windows_accepte(nom):
    assert verifier_nom_windows(nom) == nom


def test_relire_la_page_ecrite_sans_passer_par_l_archive(arc):
    dossier = arc.creer_page(page(ID_PAGE, scalaire()))
    relue = depuis_json((dossier / "page.json").read_text(encoding="utf-8"))
    assert relue.grandeur("activite").valeur == 10.5


# --- Chemins de plus de 260 caractères --------------------------------------------


@pytest.mark.parametrize(
    ("chemin", "attendu"),
    [
        (r"D:\ArchiveStage1", r"\\?\D:\ArchiveStage1"),
        ("D:/ArchiveStage1/pages", r"\\?\D:\ArchiveStage1\pages"),
        (r"D:\a\..\b", r"\\?\D:\b"),
        (r"\\serveur\partage\Archive", r"\\?\UNC\serveur\partage\Archive"),
        (r"\\?\D:\deja", r"\\?\D:\deja"),
        (r"\\?\UNC\serveur\partage", r"\\?\UNC\serveur\partage"),
    ],
)
def test_prefixe_des_chemins_longs_windows(chemin, attendu):
    assert prefixer_chemin_windows(chemin) == attendu


@pytest.mark.parametrize(
    ("chemin", "attendu"),
    [
        (r"\\?\D:\ArchiveStage1", r"D:\ArchiveStage1"),
        (r"\\?\UNC\serveur\partage\A", r"\\serveur\partage\A"),
        ("/home/a", "/home/a"),
    ],
)
def test_chemin_affichable_retire_le_prefixe(chemin, attendu):
    assert chemin_affichable(chemin) == attendu


@pytest.mark.parametrize("chemin", [r"D:\a\b", r"\\serveur\partage\a"])
def test_le_prefixe_est_reversible(chemin):
    assert chemin_affichable(prefixer_chemin_windows(chemin)) == chemin


def test_hors_windows_le_chemin_reste_inchange(tmp_path):
    if os.name != "nt":
        assert chemin_long(tmp_path / "a") == tmp_path / "a"


def test_une_archive_au_chemin_tres_long_fonctionne(tmp_path):
    profond = tmp_path
    for i in range(6):
        profond = profond / f"{i}{'d' * 60}"
    arc = Archive.creer(profond / "ArchiveStage1", "s", "r", date(2026, 1, 1))
    dossier = arc.creer_page(page(ID_PAGE, scalaire()))
    assert len(str(dossier / "page.json")) > 260
    assert Archive(profond / "ArchiveStage1").lire_page(ID_PAGE).grandeur("activite")
    assert arc.modifications_non_tracees() == []
