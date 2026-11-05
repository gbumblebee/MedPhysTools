"""Vérifie l'export du cahier : son contenu en HTML, puis le vrai fichier PDF."""

import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pypdf import PdfReader
from PySide6.QtGui import QColor, QImage

from medphystools import Archive
from medphystools.equations import RESOLUTION_IMPRESSION, dessiner_equation
from medphystools.export import LARGEUR_IMAGE, SansImages, cahier_en_html, page_en_html
from medphystools.incertitudes import Composante
from medphystools.interface.espace_saisie import DescriptionPage, EspaceSaisie
from medphystools.interface.export_pdf import exporter_pdf, largeur_du_texte
from medphystools.interface.fenetre import DescriptionArchive, FenetrePrincipale
from medphystools.page import Identifiant, Onglet, Page, Scalaire

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
MBQ = Composante(type="B", valeur=1.0, unite="MBq", source="certificat")
H = Composante(type="B", valeur=0.1, unite="h", source="horloge")
FORMAT_1 = Path(__file__).parent / "donnees" / "archive_format_1.zip"


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
    monkeypatch.setattr(resultat, "_demander_motif", lambda: "Faute de frappe")
    assert resultat.creer_page(DescriptionPage("Linéarité é±", HEURE, "G.", None, ()))
    return resultat


@pytest.fixture
def complete(espace, tmp_path):
    """Une page avec tous les types d'onglets, saisie comme dans la vraie interface."""
    photo = tmp_path / "montage.png"
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor("#808080"))
    assert image.save(str(photo))
    notes = espace.editeurs()[0]
    notes.ajouter_fichiers([photo])
    scalaires = espace.ajouter_onglet("scalaires")
    ligne = scalaires.lignes[0]
    ligne.nom.setText("activite_ref")
    ligne.valeur.setText("100")
    ligne.unite.setText("MBq")
    ligne.composantes = (MBQ,)
    ligne.commentaire.setText("Valeur de départ")
    scalaires.ligne_modifiee()
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText("t")
    tableau.colonnes[0].nom.setText("heure")
    tableau.colonnes[0].unite.setText("h")
    tableau.colonnes[0].composantes = (H,)
    tableau.colonnes[1].nom.setText("act")
    tableau.colonnes[1].unite.setText("MBq")
    tableau.colonnes[1].composantes = (MBQ,)
    tableau.colonnes[1].commentaire.setText("Lue à l'afficheur")
    tableau.coller("0\t100\n1\t89\n2\t79\n4\t63\n6\t50")
    assert espace.enregistrer_maintenant()
    calculs = espace.ajouter_onglet("calculs")
    calculs.lignes[0].nom.setText("double")
    calculs.lignes[0].formule.setText("activite_ref * 2")
    colonne = calculs.ajouter_ligne()
    colonne.nom.setText("rapport")
    colonne.formule.setText("t.act / activite_ref")
    assert espace.enregistrer_maintenant()
    ajustement = espace.ajouter_onglet("ajustements")
    ajustement.champ_nom.setText("fit")
    ajustement.champ_modele.setCurrentIndex(
        ajustement.champ_modele.findData("decroissance")
    )
    assert ajustement.demander_ajustement(), ajustement.message.text()
    panneau = ajustement.panneau
    panneau.champ_source.setCurrentIndex(0)
    panneau.champ_methode.setCurrentIndex(panneau.champ_methode.findData("maximum"))
    panneau.champ_nom.setText("pic")
    assert panneau.demander_indicateur(), panneau.message.text()
    graphe = espace.ajouter_onglet("graphes")
    panneau_graphe = graphe.panneau
    panneau_graphe.champ_methode.setCurrentIndex(
        panneau_graphe.champ_methode.findData("mediane")
    )
    panneau_graphe.champ_nom.setText("med")
    assert panneau_graphe.demander_indicateur(), panneau_graphe.message.text()
    fichiers = espace.ajouter_onglet("fichiers")
    source = tmp_path / "certificat.txt"
    source.write_text("données", encoding="utf-8")
    fichiers._ajouter_lien(source, copie=True)
    notes.texte.setPlainText(
        "Montage <b>flacon</b>\nPériode : {{fit.T12:valeur}}, référence {{activite_ref}}, "
        "faux {{absent}}"
    )
    assert espace.enregistrer_maintenant()
    assert espace.erreurs == []
    return espace


def html_de(espace, k=2.0):
    return page_en_html(espace.archive, espace.page.id, SansImages(), k)


def test_la_page_donne_ses_informations(complete):
    texte = html_de(complete)
    for attendu in (
        "Linéarité é±",
        complete.page.id,
        "brouillon",
        "14/11/2026 09:30",
        "Page non validée",
    ):
        assert attendu in texte


def test_les_onglets_suivent_l_ordre_affiche(complete):
    texte = html_de(complete)
    positions = [
        texte.index(f"<h2>{onglet.titre}</h2>") for onglet in complete.page.onglets
    ]
    assert positions == sorted(positions)
    assert len(positions) == 7


def test_les_notes_affichent_les_valeurs_a_la_place_des_tags(complete):
    texte = html_de(complete)
    assert "{{fit" not in texte
    assert "100 ± 2 MBq (k = 2)" in texte
    assert "{{absent ?}}" in texte
    assert "&lt;b&gt;flacon&lt;/b&gt;" in texte
    assert "<b>flacon" not in texte


def test_les_scalaires_donnent_valeur_incertitude_et_source(complete):
    texte = html_de(complete)
    assert "activite_ref" in texte
    assert "Valeur de départ" in texte
    assert "certificat" in texte


def test_le_tableau_est_entier_avec_sa_colonne_calculee(complete):
    texte = html_de(complete)
    assert "act (MBq)" in texte
    assert "rapport, calculée" in texte
    assert "Lue à l&#x27;afficheur" in texte
    assert "rapport = t.act / activite_ref" in texte
    assert "0.5 ± " in texte
    # Aucune limite de lignes : c'est la copie papier.
    assert texte.count(">89</td>") == 1


def test_un_grand_tableau_n_est_pas_tronque(espace):
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText("g")
    tableau.colonnes[0].nom.setText("x")
    tableau.colonnes[0].unite.setText("h")
    tableau.colonnes[0].composantes = (H,)
    tableau.colonnes[1].nom.setText("y")
    tableau.colonnes[1].unite.setText("MBq")
    tableau.colonnes[1].composantes = (MBQ,)
    tableau.coller("\n".join(f"{i}\t{i + 1000}" for i in range(600)))
    assert espace.enregistrer_maintenant()
    texte = html_de(espace)
    assert ">1000</td>" in texte
    assert ">1599</td>" in texte


def test_les_calculs_donnent_formule_et_resultat(complete):
    texte = html_de(complete)
    assert "activite_ref * 2" in texte
    assert "200 ± 4 MBq" in texte
    assert "Colonne de t" in texte


def test_l_ajustement_donne_coefficients_correlation_et_methode(complete):
    texte = html_de(complete)
    assert "Ajustement fit" in texte
    assert "Corrélation" in texte
    assert "1.000" in texte
    assert "Moindres carrés" in texte
    assert "χ² réduit" in texte
    assert "<th>Coefficient</th>" in texte
    # Le tableau de la courbe ne sert qu'au tracé : il ne remplit pas le cahier.
    assert "Tableau fit_courbe" not in texte


def test_les_indicateurs_sont_dans_leur_onglet(complete):
    texte = html_de(complete)
    assert "Maximum" in texte
    assert "Médiane" in texte
    assert "0 ± 0.2 h" in texte


def test_le_facteur_k_s_applique_partout(complete):
    texte = html_de(complete, k=3.0)
    assert "100 ± 3 MBq (k = 3)" in texte
    assert "200 ± 6 MBq" in texte
    assert "U (k = 3)" in texte


def test_les_fichiers_et_les_pieces_jointes_sont_listes(complete):
    texte = html_de(complete)
    assert "certificat.txt" in texte
    assert "Pièces jointes" in texte
    assert "montage.png" in texte


def test_un_fichier_manquant_est_signale(complete):
    page = complete.archive.lire_page(complete.page.id)
    piece = page.pieces_jointes[0]
    (complete.archive.racine / "pages" / page.id / piece.chemin).unlink()
    texte = html_de(complete)
    assert "Fichiers à vérifier" in texte
    assert "manquant" in texte


def test_un_resultat_perime_est_signale(complete):
    page = complete.archive.lire_page(complete.page.id)
    complete.archive.modifier_page(
        page,
        tableaux={
            "t": {
                "heure": [0.0, 1.0, 2.0, 4.0, 6.0],
                "act": [90.0, 89.0, 79.0, 63.0, 50.0],
            }
        },
    )
    texte = html_de(complete)
    assert "À refaire" in texte
    assert "à recalculer" in texte


def test_l_historique_des_corrections_garde_son_motif(espace):
    assert espace.demander_validation()
    espace.champ_titre.setText("Autre titre")
    espace.champ_titre.textEdited.emit("Autre titre")
    assert espace.demander_correction()
    texte = html_de(espace)
    assert "Historique" in texte
    assert "Faute de frappe" in texte
    assert "correction" in texte
    assert "Page non validée" not in texte


def test_une_grandeur_hors_de_tout_onglet_reste_dans_le_cahier(arc):
    identifiant = arc.nouvel_identifiant(HEURE, "orpheline")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Orpheline",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(
                Scalaire(nom="seule", valeur=1.0, unite="MBq", composantes=(MBQ,)),
                Identifiant(nom="lot", valeur="007"),
            ),
        )
    )
    texte = page_en_html(arc, identifiant, SansImages())
    assert "Autres grandeurs" in texte
    assert "seule" in texte
    assert "007" in texte


def test_un_onglet_inconnu_donne_son_contenu_tel_quel(arc):
    identifiant = arc.nouvel_identifiant(HEURE, "futur")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Futur",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            onglets=(Onglet(id="o", type="spectre", titre="Spectre", pics=[140.5]),),
        )
    )
    texte = page_en_html(arc, identifiant, SansImages())
    assert "inconnu de cette version" in texte
    assert "140.5" in texte


def test_le_texte_saisi_ne_devient_jamais_du_html(arc):
    identifiant = arc.nouvel_identifiant(HEURE, "piege")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="<script>x</script>",
            description="<img src=x>",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="<b>G.</b>",
        )
    )
    texte = page_en_html(arc, identifiant, SansImages())
    for interdit in ("<script>", "<img src=x>", "<b>G."):
        assert interdit not in texte


def test_une_page_illisible_n_arrete_pas_le_cahier(arc):
    bonne = arc.nouvel_identifiant(HEURE, "bonne")
    arc.creer_page(
        Page(id=bonne, titre="Bonne", date_mesure=HEURE, date_saisie=HEURE, auteur="G.")
    )
    mauvaise = arc.nouvel_identifiant(HEURE, "mauvaise")
    dossier = arc.racine / "pages" / mauvaise
    dossier.mkdir()
    (dossier / "page.json").write_text("{pas du json", encoding="utf-8")
    texte = cahier_en_html(arc, [bonne, mauvaise])
    assert "Bonne" in texte
    assert "Page illisible" in texte
    assert mauvaise in texte


def test_le_sommaire_liste_les_pages_et_l_interruption_arrete_l_export(arc):
    identifiants = []
    for numero in range(3):
        identifiant = arc.nouvel_identifiant(HEURE, f"page{numero}")
        arc.creer_page(
            Page(
                id=identifiant,
                titre=f"Page {numero}",
                date_mesure=HEURE,
                date_saisie=HEURE,
                auteur="G.",
            )
        )
        identifiants.append(identifiant)
    texte = cahier_en_html(arc, identifiants, maintenant=HEURE)
    assert "Cahier de labo : A" in texte
    assert "Exporté le 14/11/2026 09:30" in texte
    assert "Sommaire" in texte
    assert all(i in texte for i in identifiants)
    assert texte.count("page-break-before:always") == 3
    vus = []
    assert (
        cahier_en_html(arc, identifiants, avancer=lambda r: vus.append(r) or r < 2)
        is None
    )
    assert vus == [1, 2]


def test_exporter_ne_modifie_rien_dans_l_archive(complete, tmp_path):
    arc = complete.archive
    avant = arc._depot.tete()
    assert exporter_pdf(arc, [complete.page.id], tmp_path / "s.pdf")
    assert arc._depot.tete() == avant
    assert arc.modifications_non_tracees() == []


# --- Le vrai PDF --------------------------------------------------------------------------


def texte_du_pdf(chemin):
    lecteur = PdfReader(str(chemin))
    texte = "\n".join(page.extract_text() for page in lecteur.pages)
    return lecteur, texte.replace("\t", " ")


def test_le_pdf_contient_le_cahier(complete, tmp_path):
    chemin = tmp_path / "cahier.pdf"
    assert exporter_pdf(complete.archive, [complete.page.id], chemin)
    assert chemin.read_bytes().startswith(b"%PDF")
    lecteur, texte = texte_du_pdf(chemin)
    assert len(lecteur.pages) >= 2
    for attendu in ("Cahier de labo", "Linéarité", "activite_ref", "Corrélation", "±"):
        assert attendu in texte
    images = sum(len(page.images) for page in lecteur.pages)
    # La photo, le graphe de l'ajustement et celui de l'onglet de graphes.
    assert images >= 3
    assert lecteur.metadata.title == "Cahier de labo : A"
    assert not list(tmp_path.glob(".*.tmp"))


def test_les_images_tiennent_dans_la_largeur_de_la_page():
    """Une image plus large que le texte serait coupée à droite, sans message."""
    assert LARGEUR_IMAGE <= largeur_du_texte()
    # Et elles ne sont pas réduites pour rien : le graphe doit rester lisible.
    assert LARGEUR_IMAGE >= 0.9 * largeur_du_texte()


def test_le_texte_d_un_titre_n_est_ecrit_qu_une_fois(complete, tmp_path):
    """Un gras simulé écrirait chaque titre deux fois, et le rendrait pâteux à l'écran."""
    chemin = tmp_path / "cahier.pdf"
    assert exporter_pdf(complete.archive, [complete.page.id], chemin)
    _lecteur, texte = texte_du_pdf(chemin)
    assert texte.count("Cahier de labo : A") == 1


def test_un_export_interrompu_ne_laisse_pas_de_fichier(complete, tmp_path):
    chemin = tmp_path / "cahier.pdf"
    assert not exporter_pdf(
        complete.archive, [complete.page.id], chemin, avancer=lambda _r: False
    )
    assert not chemin.exists()


def test_un_dossier_impossible_s_explique(complete, tmp_path):
    with pytest.raises(OSError):
        exporter_pdf(
            complete.archive, [complete.page.id], tmp_path / "absent" / "c.pdf"
        )


def test_l_archive_figee_du_format_1_s_exporte(tmp_path):
    with zipfile.ZipFile(FORMAT_1) as archive_zip:
        archive_zip.extractall(tmp_path)
    arc = Archive(tmp_path / "ArchiveFormat1")
    chemin = tmp_path / "ancien.pdf"
    assert exporter_pdf(arc, arc.identifiants(), chemin)
    _lecteur, texte = texte_du_pdf(chemin)
    assert "Linéarité" in texte


# --- Interface ----------------------------------------------------------------------------


@pytest.fixture
def fenetre(qtbot, tmp_path, monkeypatch):
    resultat = FenetrePrincipale()
    qtbot.addWidget(resultat)
    monkeypatch.setattr(resultat, "_confirmer_export_archive", lambda _ids: True)
    resultat.creer_archive(
        DescriptionArchive(tmp_path / "F", "S", "R", date(2026, 11, 1))
    )
    yield resultat
    resultat.espace_archive.arreter()


def test_le_menu_exporte_tout_le_cahier_du_plus_ancien_au_plus_recent(
    fenetre, tmp_path, monkeypatch
):
    arc = fenetre.archive
    ids = []
    for jours, titre in ((1, "Récent"), (0, "Ancien")):
        identifiant = arc.nouvel_identifiant(HEURE + timedelta(days=jours), titre)
        arc.creer_page(
            Page(
                id=identifiant,
                titre=titre,
                date_mesure=HEURE + timedelta(days=jours),
                date_saisie=HEURE,
                auteur="G.",
            )
        )
        ids.append(identifiant)
    cible = tmp_path / "sans_extension"
    monkeypatch.setattr(fenetre, "_choisir_fichier_pdf", lambda _nom: str(cible))
    assert fenetre.demander_export_cahier()
    fichier = tmp_path / "sans_extension.pdf"
    assert fichier.is_file()
    _lecteur, texte = texte_du_pdf(fichier)
    assert texte.index("Ancien") < texte.index("Récent")
    assert str(fichier) in fenetre.statusBar().currentMessage()


def test_annuler_le_choix_du_fichier_ne_fait_rien(fenetre, monkeypatch):
    arc = fenetre.archive
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant, titre="P", date_mesure=HEURE, date_saisie=HEURE, auteur="G."
        )
    )
    monkeypatch.setattr(fenetre, "_choisir_fichier_pdf", lambda _nom: "")
    assert not fenetre.demander_export_cahier()


def test_une_archive_vide_s_explique(fenetre, monkeypatch):
    erreurs = []
    monkeypatch.setattr(fenetre, "afficher_erreur", erreurs.append)
    assert not fenetre.demander_export_cahier()
    assert "aucune page" in erreurs[0]


def test_le_menu_n_est_actif_qu_avec_une_archive(qtbot, fenetre):
    autre = FenetrePrincipale()
    qtbot.addWidget(autre)
    assert not autre.action_exporter.isEnabled()
    assert fenetre.action_exporter.isEnabled()


def test_le_bouton_de_la_fiche_exporte_la_page_au_k_choisi(
    fenetre, tmp_path, monkeypatch, qtbot
):
    arc = fenetre.archive
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant,
            titre="Seule page",
            date_mesure=HEURE,
            date_saisie=HEURE,
            auteur="G.",
            mesures=(
                Scalaire(nom="activite", valeur=100.0, unite="MBq", composantes=(MBQ,)),
            ),
        )
    )
    fiche = fenetre.espace_archive.fiche
    assert not fiche.bouton_exporter.isEnabled()
    fiche.afficher(arc, identifiant)
    assert fiche.bouton_exporter.isEnabled()
    fiche.champ_k.setValue(3.0)
    cible = tmp_path / "page.pdf"
    monkeypatch.setattr(fenetre, "_choisir_fichier_pdf", lambda _nom: str(cible))
    fiche.bouton_exporter.click()
    _lecteur, texte = texte_du_pdf(cible)
    assert "Seule page" in texte
    assert "k = 3" in texte


def test_une_erreur_d_ecriture_s_explique(fenetre, tmp_path, monkeypatch):
    arc = fenetre.archive
    identifiant = arc.nouvel_identifiant(HEURE, "p")
    arc.creer_page(
        Page(
            id=identifiant, titre="P", date_mesure=HEURE, date_saisie=HEURE, auteur="G."
        )
    )
    erreurs = []
    monkeypatch.setattr(fenetre, "afficher_erreur", erreurs.append)
    monkeypatch.setattr(
        fenetre, "_choisir_fichier_pdf", lambda _nom: str(tmp_path / "absent" / "c.pdf")
    )
    assert not fenetre.demander_export_cahier()
    assert "Export impossible" in erreurs[0]


def test_les_titres_et_l_incertitude_calculee_passent_dans_le_pdf(espace):
    tableau = espace.ajouter_onglet("tableau")
    tableau.champ_nom.setText("comptage")
    n, u = tableau.colonnes[:2]
    n.nom.setText("N")
    n.unite.setText("1")
    u.nom.setText("u_N")
    u.nature.setCurrentIndex(u.nature.findData("incertitude_calculee"))
    u.cible.setCurrentText("N")
    u.formule.setText("sqrt(N)")
    tableau.ecrire_cellules([["1000"]], 0, 0)
    espace.editeurs()[0].texte.setPlainText("# Montage\nsuite")
    espace.enregistrer_maintenant()
    assert espace.erreurs == []
    texte = html_de(espace)
    assert "font-size:14pt;font-weight:700" in texte
    assert ">Montage</div>" in texte
    assert "# Montage" not in texte
    assert ">31.6228</td>" in texte
    assert "incertitude de N, Calculée : sqrt(N)" in texte


def test_une_equation_passe_dans_le_pdf(espace, tmp_path):
    espace.editeurs()[0].texte.setPlainText("Loi : $x^2$")
    espace.enregistrer_maintenant()
    texte = html_de(espace)
    assert '<img src="data:image/png;base64,' in texte
    # Résolution d'impression : l'image a plus de pixels que sa taille d'affichage.
    image = dessiner_equation("x^2", 10.0, RESOLUTION_IMPRESSION)
    assert image.adresse() in texte
    chemin = tmp_path / "cahier.pdf"
    assert exporter_pdf(espace.archive, [espace.page.id], chemin)
    assert chemin.read_bytes().startswith(b"%PDF")
