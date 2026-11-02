"""Vérifie les graphes : données, barres d'erreur, réglages et affichage sans OpenGL."""

import math
import os
import subprocess
import sys
import textwrap
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest
from matplotlib.backend_bases import MouseEvent
from PySide6.QtCore import Qt

from pymphub import Archive
from pymphub.depot import Depot
from pymphub.graphes import (
    Axe,
    GrapheImpossible,
    incertitudes_types,
    preparer_graphe,
    reglages_par_defaut,
)
from pymphub.incertitudes import Composante
from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
from pymphub.interface.fiche_page import FichePage
from pymphub.interface.graphique import ToileGraphe, bornes_log
from pymphub.interface.onglet_graphes import EditeurGraphes
from pymphub.interface.theme import SERIES
from pymphub.page import (
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    OngletGraphes,
    Page,
    ReglagesGraphe,
    Tableau,
)

HEURE = datetime(2026, 11, 14, 9, 30, tzinfo=timezone(timedelta(hours=1)))
CERTIFICAT = Composante(type="B", valeur=1.0, unite="MBq", k=2.0, source="certificat")
HORLOGE = Composante(type="B", valeur=0.1, unite="h", source="horloge")
RELATIVE = Composante(type="B", valeur=2.0, relative=True, source="relative")

RELEVES = Tableau(
    nom="releves",
    fichier="brut/releves.csv",
    colonnes=(
        ColonneIdentifiant(nom="lot"),
        ColonneNumerique(nom="heure", unite="h", composantes=(HORLOGE,)),
        ColonneNumerique(nom="activite", unite="MBq", composantes=(CERTIFICAT,)),
        ColonneNumerique(nom="autre", unite="kBq", composantes=(RELATIVE,)),
        ColonneNumerique(
            nom="temperature",
            unite="degC",
            composantes=(
                Composante(type="B", valeur=0.5, unite="degC", source="sonde"),
            ),
        ),
        ColonneIncertitude(
            nom="u_activite",
            composante_de="activite",
            type="B",
            unite="MBq",
            source="cellule",
        ),
    ),
)
DONNEES = {
    "lot": ["007", "008", "009"],
    "heure": [1.0, 2.0, 4.0],
    "activite": [100.0, 50.0, 25.0],
    "autre": [1000.0, 0.0, 500.0],
    "temperature": [20.0, 21.0, 22.0],
    "u_activite": [0.3, 0.4, 1.2],
}


def page_de_test(arc, onglets=()):
    identifiant = arc.nouvel_identifiant(HEURE, "graphe")
    page = Page(
        id=identifiant,
        titre="Graphe",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=(RELEVES,),
        onglets=onglets,
    )
    arc.creer_page(page, tableaux={"releves": DONNEES})
    return page


@pytest.fixture
def arc(tmp_path):
    return Archive.creer(tmp_path / "A", "CHU X", "Resp.", date(2026, 11, 1))


@pytest.fixture
def cadre():
    return pd.DataFrame(DONNEES)


@pytest.fixture
def page(arc):
    return page_de_test(arc)


def reglages(**champs):
    return ReglagesGraphe(
        **{"tableau": "releves", "x": "heure", "y": ("activite",), **champs}
    )


# --- Valeurs et barres, calculées à la main ----------------------------------------------


def test_les_barres_valent_k_fois_l_incertitude_composee(page, cadre):
    donnees = preparer_graphe(page, {"releves": cadre}, reglages(), k=2.0)
    assert donnees.x.valeurs == (1.0, 2.0, 4.0)
    # heure : u = 0,1 h, donc U = 0,2 h à k = 2.
    assert donnees.x.elargies == pytest.approx((0.2, 0.2, 0.2))
    # activite : certificat u = 1/2 = 0,5 MBq, cellule u = 0,3 : √(0,25 + 0,09) = 0,58310.
    attendues = [2 * math.sqrt(0.25 + u**2) for u in (0.3, 0.4, 1.2)]
    assert donnees.y[0].valeurs == (100.0, 50.0, 25.0)
    assert donnees.y[0].elargies == pytest.approx(attendues)
    assert donnees.y[0].elargies[0] == pytest.approx(1.16619, abs=1e-5)
    assert donnees.avertissements == ()


def test_les_barres_suivent_le_facteur_k(page, cadre):
    a_2 = preparer_graphe(page, {"releves": cadre}, reglages(), k=2.0)
    a_3 = preparer_graphe(page, {"releves": cadre}, reglages(), k=3.0)
    assert a_3.y[0].elargies == pytest.approx([u * 1.5 for u in a_2.y[0].elargies])
    assert a_3.y[0].valeurs == a_2.y[0].valeurs
    with pytest.raises(GrapheImpossible, match="positif"):
        preparer_graphe(page, {"releves": cadre}, reglages(), k=0)


def test_l_incertitude_est_la_meme_que_celle_de_l_api(arc, page, cadre):
    lue = arc.page(page.id)
    types = incertitudes_types(RELEVES, cadre, "activite")
    for ligne, u in enumerate(types):
        cellule = lue.cellule("releves", "activite", ligne)
        assert u == pytest.approx(cellule.budget.incertitude_type)


def test_deux_series_partagent_un_axe_dans_la_meme_unite(page, cadre):
    donnees = preparer_graphe(
        page, {"releves": cadre}, reglages(y=("activite", "autre"))
    )
    autre = donnees.y[1]
    assert autre.unite == "MBq"
    assert autre.valeurs == pytest.approx((1.0, 0.0, 0.5))
    # autre : 2 % de 1000 kBq = 20 kBq, soit U = 40 kBq = 0,04 MBq à k = 2.
    assert autre.elargies[0] == pytest.approx(0.04)
    assert math.isnan(autre.elargies[1])
    assert "1 barre(s) d'erreur indéterminée(s)" in donnees.avertissements[0]


def test_des_unites_incompatibles_ne_partagent_pas_un_axe(page, cadre):
    with pytest.raises(GrapheImpossible, match="un seul axe"):
        preparer_graphe(
            page, {"releves": cadre}, reglages(y=("activite", "temperature"))
        )


def test_une_valeur_nulle_est_retiree_d_un_axe_log_et_c_est_dit(page, cadre):
    donnees = preparer_graphe(
        page, {"releves": cadre}, reglages(y=("autre",), echelle_y="log")
    )
    assert donnees.y[0].valeurs[0] == 1000.0
    assert math.isnan(donnees.y[0].valeurs[1])
    assert "1 point(s) à y ≤ 0" in donnees.avertissements[0]


def test_un_x_nul_retire_tout_le_point_d_un_axe_log(page):
    cadre = pd.DataFrame({**DONNEES, "heure": [0.0, 2.0, 4.0]})
    donnees = preparer_graphe(page, {"releves": cadre}, reglages(echelle_x="log"))
    assert math.isnan(donnees.x.valeurs[0])
    assert math.isnan(donnees.y[0].valeurs[0])
    assert donnees.y[0].valeurs[1] == 50.0
    assert "1 point(s) à x ≤ 0" in donnees.avertissements[0]


@pytest.mark.parametrize(
    ("champs", "attendu"),
    [
        ({"tableau": "absent"}, "n'existe plus"),
        ({"x": "absente"}, "n'existe plus"),
        ({"y": ("absente",)}, "n'existe plus"),
        ({"x": "lot"}, "n'existe plus"),
        ({"x": "u_activite"}, "n'existe plus"),
        ({"mode": "3d"}, "axe z"),
        ({"mode": "3d", "z": "absente"}, "n'existe plus"),
        ({"y": tuple(f"c{i}" for i in range(9))}, "n'existe plus"),
    ],
)
def test_des_reglages_impossibles_s_expliquent(page, cadre, champs, attendu):
    with pytest.raises(GrapheImpossible, match=attendu):
        preparer_graphe(page, {"releves": cadre}, reglages(**champs))


def test_un_tableau_vide_s_explique(page, cadre):
    with pytest.raises(GrapheImpossible, match="pas encore de données"):
        preparer_graphe(page, {"releves": cadre.iloc[0:0]}, reglages())


def test_neuf_series_sont_refusees(arc):
    colonnes = tuple(
        ColonneNumerique(nom=f"c{i}", unite="MBq", composantes=(CERTIFICAT,))
        for i in range(10)
    )
    grand = Tableau(nom="t", fichier="brut/t.csv", colonnes=colonnes)
    page = Page(
        id="2026-11-14_large_0001",
        titre="T",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
        mesures=(grand,),
    )
    cadre = pd.DataFrame({f"c{i}": [1.0] for i in range(10)})
    choisis = ReglagesGraphe(
        tableau="t", x="c0", y=tuple(f"c{i}" for i in range(1, 10))
    )
    with pytest.raises(GrapheImpossible, match="8 au plus"):
        preparer_graphe(page, {"t": cadre}, choisis)


# --- Réglages par défaut ------------------------------------------------------------------


def test_les_anciennes_references_servent_de_reglages(page):
    onglet = OngletGraphes(
        id="g", type="graphes", grandeurs=("releves.heure", "releves.autre")
    )
    assert reglages_par_defaut(page, onglet) == ReglagesGraphe(
        tableau="releves", x="heure", y=("autre",)
    )


def test_sans_reference_les_premieres_colonnes_numeriques_sont_proposees(page):
    onglet = OngletGraphes(id="g", type="graphes")
    assert reglages_par_defaut(page, onglet) == ReglagesGraphe(
        tableau="releves", x="heure", y=("activite",)
    )


def test_sans_tableau_rien_n_est_propose():
    page = Page(
        id="2026-11-14_vide_0001",
        titre="V",
        date_mesure=HEURE,
        date_saisie=HEURE,
        auteur="G.",
    )
    assert reglages_par_defaut(page, OngletGraphes(id="g", type="graphes")) is None


# --- Dessin -------------------------------------------------------------------------------


@pytest.fixture
def toile(qtbot):
    resultat = ToileGraphe()
    qtbot.addWidget(resultat)
    resultat.resize(700, 500)
    return resultat


def segments_des_barres(conteneur):
    """Renvoie les barres en x et en y d'une série dessinée."""
    return [collection.get_segments() for collection in conteneur.lines[2]]


def test_les_barres_dessinees_correspondent_au_tableau(page, cadre, toile):
    toile.tracer(preparer_graphe(page, {"releves": cadre}, reglages(), k=3.0))
    barres_x, barres_y = segments_des_barres(toile.axes.containers[0])
    for rang, (x, y, u) in enumerate(
        zip((1.0, 2.0, 4.0), (100.0, 50.0, 25.0), (0.3, 0.4, 1.2))
    ):
        U_y = 3 * math.sqrt(0.25 + u**2)
        assert barres_y[rang][0] == pytest.approx((x, y - U_y))
        assert barres_y[rang][1] == pytest.approx((x, y + U_y))
        assert barres_x[rang][0] == pytest.approx((x - 0.3, y))
        assert barres_x[rang][1] == pytest.approx((x + 0.3, y))


def test_decocher_les_barres_les_retire(page, cadre, toile):
    toile.tracer(preparer_graphe(page, {"releves": cadre}, reglages(barres=False)))
    assert toile.axes.containers[0].lines[2] == ()


def test_les_echelles_et_les_libelles_sont_appliques(page, cadre, toile):
    toile.tracer(
        preparer_graphe(
            page, {"releves": cadre}, reglages(echelle_x="log", echelle_y="log")
        )
    )
    assert toile.axes.get_xscale() == "log"
    assert toile.axes.get_yscale() == "log"
    assert toile.axes.get_xlabel() == "heure (h)"
    assert toile.axes.get_ylabel() == "activite (MBq)"
    assert toile.axes.get_legend() is None


def test_les_graduations_log_s_ecrivent_en_clair(page, cadre, toile):
    toile.tracer(preparer_graphe(page, {"releves": cadre}, reglages(echelle_y="log")))
    toile.canevas.draw()
    axe = toile.axes.yaxis
    assert axe.get_major_formatter()(100.0, 0) == "100"
    assert axe.get_minor_formatter()(90.0, 0) == "90"
    assert all(t.get_color() != "black" for t in axe.get_ticklabels(minor=True))


def test_la_barre_d_outils_est_en_francais_sans_boutons_inutiles(toile):
    libelles = [a.toolTip() for a in toile.barre_outils.actions() if a.toolTip()]
    assert "Revenir à la vue d'origine" in libelles
    assert "Enregistrer l'image du graphe" in libelles
    assert not any("Configure" in t or "Edit" in t or "Reset" in t for t in libelles)


def test_plusieurs_series_ont_une_legende_et_des_formes_distinctes(page, cadre, toile):
    toile.tracer(
        preparer_graphe(page, {"releves": cadre}, reglages(y=("activite", "autre")))
    )
    textes = [t.get_text() for t in toile.axes.get_legend().get_texts()]
    assert textes == ["activite (MBq)", "autre (MBq)"]
    assert toile.axes.get_ylabel() == "MBq"
    marqueurs = {c.lines[0].get_marker() for c in toile.axes.containers}
    couleurs = {c.lines[0].get_color() for c in toile.axes.containers}
    assert len(marqueurs) == 2
    assert len(couleurs) == 2


def test_une_serie_garde_sa_couleur_selon_sa_place(page, cadre, toile):
    toile.tracer(preparer_graphe(page, {"releves": cadre}, reglages()))
    assert toile.axes.containers[0].lines[0].get_color() == SERIES[0]


def test_relier_les_points_est_un_choix(page, cadre, toile):
    toile.tracer(preparer_graphe(page, {"releves": cadre}, reglages(relier=False)))
    assert toile.axes.containers[0].lines[0].get_linestyle() == "None"


def test_le_survol_donne_la_valeur_et_son_incertitude(page, cadre, toile, qtbot):
    toile.show()
    qtbot.waitExposed(toile)
    toile.tracer(preparer_graphe(page, {"releves": cadre}, reglages()))
    toile.canevas.draw()
    px, py = toile.axes.transData.transform((2.0, 50.0))
    toile.survoler(MouseEvent("motion_notify_event", toile.canevas, px, py))
    assert toile.lecture.text() == (
        "activite, ligne 2 : heure = 2 h, activite = 50 ± 1.3 MBq (k = 2)"
    )
    toile.survoler(MouseEvent("motion_notify_event", toile.canevas, px + 60, py + 60))
    assert toile.lecture.text() == ""


def test_un_graphe_3d(page, cadre, toile):
    toile.tracer(
        preparer_graphe(
            page,
            {"releves": cadre},
            reglages(mode="3d", y=("activite",), z="temperature", surface=True),
        )
    )
    toile.canevas.draw()
    assert toile.axes.name == "3d"
    assert toile.axes.get_zlabel() == "temperature (degC)"
    assert toile.message.text() == ""


def test_un_graphe_3d_log_annonce_le_logarithme(page, cadre, toile):
    toile.tracer(
        preparer_graphe(
            page,
            {"releves": cadre},
            reglages(mode="3d", z="temperature", echelle_z="log", echelle_y="log"),
        )
    )
    toile.canevas.draw()
    assert toile.axes.get_zlabel() == "log₁₀ temperature (degC)"
    assert toile.axes.get_ylabel() == "log₁₀ activite (MBq)"


def test_une_barre_log_3d_n_est_pas_symetrique_et_reste_definie():
    centre, basse, haute = bornes_log(Axe("a", "", (100.0, 1.0), (90.0, 5.0)))
    assert centre == pytest.approx((2.0, 0.0))
    assert basse[0] == pytest.approx(1.0)  # 100 − 90 = 10, soit un facteur 10.
    assert haute[0] == pytest.approx(math.log10(190) - 2)
    # 1 − 5 passerait sous zéro : la barre s'arrête à valeur / 10.
    assert basse[1] == pytest.approx(1.0)
    assert haute[1] == pytest.approx(math.log10(6))


def test_une_surface_impossible_garde_les_points(page, toile):
    alignes = pd.DataFrame({**DONNEES, "heure": [1.0, 1.0, 1.0]})
    toile.tracer(
        preparer_graphe(
            page,
            {"releves": alignes},
            reglages(mode="3d", z="temperature", surface=True),
        )
    )
    toile.canevas.draw()
    assert toile.axes.name == "3d"


def test_un_message_remplace_le_graphe_puis_le_graphe_revient(page, cadre, toile):
    toile.signaler("Explication")
    assert toile.message.text() == "Explication"
    assert toile.canevas.isHidden()
    toile.tracer(preparer_graphe(page, {"releves": cadre}, reglages()))
    assert not toile.canevas.isHidden()
    assert toile.message.isHidden()


def test_un_graphe_s_affiche_sans_opengl(tmp_path):
    """Dans un processus neuf : aucun module OpenGL n'est chargé, et des pixels sont dessinés."""
    code = textwrap.dedent(
        """
        import sys
        import pandas as pd
        from PySide6.QtWidgets import QApplication
        from pymphub.graphes import Axe, DonneesGraphe
        from pymphub.interface.graphique import ToileGraphe
        from pymphub.page import ReglagesGraphe

        app = QApplication([])
        axe = Axe("a", "MBq", (1.0, 2.0, 3.0), (0.1, 0.1, 0.1))
        for mode in ("2d", "3d"):
            r = ReglagesGraphe(tableau="t", mode=mode, x="a", y=("b",), z="c")
            d = DonneesGraphe(r, 2.0, axe, (axe,), axe, ())
            toile = ToileGraphe()
            toile.resize(500, 400)
            toile.tracer(d)
            toile.canevas.draw()
            image = toile.canevas.grab().toImage()
            couleurs = {
                image.pixelColor(x, y).rgb()
                for x in range(0, image.width(), 7)
                for y in range(0, image.height(), 7)
            }
            assert len(couleurs) > 3, mode
        interdits = [m for m in sys.modules if "opengl" in m.lower()]
        assert interdits == [], interdits
        print("ok")
        """
    )
    resultat = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "QT_QPA_PLATFORM": "offscreen",
            "QT_OPENGL": "software",
        },
    )
    assert resultat.returncode == 0, resultat.stderr
    assert resultat.stdout.strip() == "ok"


def test_importer_pymphub_ne_charge_pas_matplotlib():
    code = (
        "import sys, pymphub, pymphub.graphes, pymphub.archive; "
        "assert not [m for m in sys.modules if m.startswith(('matplotlib', 'PySide6'))]"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)


def test_ouvrir_une_page_sans_afficher_de_graphe_ne_charge_pas_matplotlib(tmp_path):
    code = textwrap.dedent(
        f"""
        import os, sys
        from datetime import date, datetime, timezone
        from PySide6.QtWidgets import QApplication
        from pymphub import Archive
        from pymphub.interface.espace_saisie import DescriptionPage, EspaceSaisie
        app = QApplication([])
        arc = Archive.creer(r"{tmp_path / "B"}", "S", "R", date(2026, 11, 1))
        e = EspaceSaisie(); e.installer(arc)
        e.creer_page(DescriptionPage("T", datetime.now(timezone.utc), "G", None, ()))
        o = e.ajouter_onglet("graphes")
        e.onglets.setCurrentIndex(0)
        e.ouvrir_page(e.page.id)
        assert not [m for m in sys.modules if m.startswith("matplotlib")]
        print("ok", flush=True)
        os._exit(0)
        """
    )
    resultat = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    assert resultat.stdout.strip() == "ok", resultat.stderr


# --- Onglet ------------------------------------------------------------------------------


@pytest.fixture
def espace(qtbot, arc, monkeypatch):
    resultat = EspaceSaisie()
    qtbot.addWidget(resultat)
    resultat.installer(arc)
    resultat.erreurs = []
    monkeypatch.setattr(resultat, "afficher_erreur", resultat.erreurs.append)
    monkeypatch.setattr(resultat, "_confirmer", lambda *_: True)
    monkeypatch.setattr(resultat, "_demander_motif", lambda: "Motif")
    page = page_de_test(arc)
    assert resultat.ouvrir_page(page.id)
    return resultat


@pytest.fixture
def editeur(espace):
    resultat = espace.ajouter_onglet("graphes")
    assert isinstance(resultat, EditeurGraphes)
    return resultat


def relue(espace):
    return espace.archive.lire_page(espace.page.id)


def cocher(editeur, *noms):
    for rang in range(editeur.liste_y.count()):
        element = editeur.liste_y.item(rang)
        element.setCheckState(
            Qt.CheckState.Checked if element.text() in noms else Qt.CheckState.Unchecked
        )


def test_seules_les_colonnes_numeriques_sont_proposees(editeur):
    proposes = [editeur.champ_x.itemText(i) for i in range(editeur.champ_x.count())]
    assert proposes == ["heure", "activite", "autre", "temperature"]


def test_les_reglages_se_retrouvent_apres_reouverture(espace, editeur):
    editeur.champ_x.setCurrentText("heure")
    cocher(editeur, "activite", "autre")
    editeur.log_y.setChecked(True)
    editeur.relier.setChecked(False)
    editeur.barres.setChecked(False)
    assert espace.enregistrer_maintenant()

    onglet = relue(espace).onglets[-1]
    assert onglet.reglages == ReglagesGraphe(
        tableau="releves",
        x="heure",
        y=("activite", "autre"),
        echelle_y="log",
        barres=False,
        relier=False,
    )

    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert isinstance(relu, EditeurGraphes)
    assert relu.champ_x.currentText() == "heure"
    assert relu._colonnes_y_cochees() == ("activite", "autre")
    assert relu.log_y.isChecked()
    assert not relu.relier.isChecked()
    assert not relu.barres.isChecked()
    assert relu._lire_reglages() == onglet.reglages
    assert not espace.modifications_en_attente()

    relu_disque = Archive(espace.archive.racine).lire_page(espace.page.id)
    assert relu_disque.onglets[-1].reglages == onglet.reglages


def test_les_reglages_3d_se_retrouvent_apres_reouverture(espace, editeur):
    editeur.champ_mode.setCurrentIndex(1)
    editeur.champ_y_3d.setCurrentText("activite")
    editeur.champ_z.setCurrentText("temperature")
    editeur.log_z.setChecked(True)
    editeur.surface.setChecked(True)
    assert espace.enregistrer_maintenant()
    assert espace.ouvrir_page(espace.page.id)
    relu = espace.editeurs()[-1]
    assert relu.reglages.mode == "3d"
    assert relu.champ_mode.currentData() == "3d"
    assert relu.champ_z.currentText() == "temperature"
    assert relu.log_z.isChecked()
    assert relu.surface.isChecked()
    assert not espace.modifications_en_attente()


def test_passer_en_3d_propose_une_colonne_libre_pour_z(espace, editeur):
    cocher(editeur, "autre")
    editeur.champ_x.setCurrentText("heure")
    editeur.champ_mode.setCurrentIndex(1)
    assert editeur.champ_y_3d.currentText() == "autre"
    assert editeur.champ_z.currentText() == "activite"
    assert editeur.reglages.z == "activite"
    assert editeur.reglages.y == ("autre",)


def test_les_champs_suivent_le_type_de_graphe(editeur, espace, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    assert editeur.liste_y.isVisible()
    assert not editeur.champ_z.isVisible()
    assert not editeur.surface.isVisible()
    editeur.champ_mode.setCurrentIndex(1)
    assert not editeur.liste_y.isVisible()
    assert editeur.champ_z.isVisible()
    assert editeur.surface.isVisible()


def test_changer_k_ne_cree_aucun_commit(espace, editeur, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    assert espace.enregistrer_maintenant()
    tete = Depot(espace.archive.racine).tete()
    avant = editeur.toile.donnees.y[0].elargies
    editeur.champ_k.setValue(3.0)
    assert not espace.modifications_en_attente()
    assert not espace._minuteur.isActive()
    assert espace.enregistrer_maintenant()
    assert Depot(espace.archive.racine).tete() == tete
    assert editeur.toile.donnees.k == 3.0
    assert editeur.toile.donnees.y[0].elargies == pytest.approx(
        [u * 1.5 for u in avant]
    )


def test_regarder_un_ancien_onglet_ne_modifie_pas_la_page(arc, espace, qtbot):
    page = page_de_test(
        arc,
        onglets=(
            OngletGraphes(
                id="g", type="graphes", grandeurs=("releves.heure", "releves.activite")
            ),
        ),
    )
    assert espace.ouvrir_page(page.id)
    espace.show()
    qtbot.waitExposed(espace)
    editeur = espace.editeurs()[0]
    assert isinstance(editeur, EditeurGraphes)
    assert editeur.toile.donnees.x.nom == "heure"
    assert editeur.toile.donnees.y[0].nom == "activite"
    assert not espace.modifications_en_attente()


def test_regler_une_page_validee_demande_un_motif(espace, editeur):
    assert espace.enregistrer_maintenant()
    assert espace.demander_validation()
    relu = espace.editeurs()[-1]
    relu.log_y.setChecked(True)
    assert espace.modifications_en_attente()
    assert not espace._minuteur.isActive()
    assert espace.demander_correction()
    assert relue(espace).statut == "corrigée"
    assert relue(espace).onglets[-1].reglages.echelle_y == "log"


def test_rien_de_coche_s_explique_et_garde_les_reglages(espace, editeur, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    editeur.barres.setChecked(False)
    assert espace.enregistrer_maintenant()
    cocher(editeur)
    assert editeur.toile.message.text() == "Cocher au moins une colonne pour l'axe y."
    assert editeur.reglages.y == ("activite",)
    assert espace.enregistrer_maintenant()
    assert relue(espace).onglets[-1].reglages.y == ("activite",)


def test_renommer_une_colonne_ne_bloque_pas_la_saisie(arc, espace, editeur, qtbot):
    """Le graphe s'explique, et la page continue de s'enregistrer."""
    espace.show()
    qtbot.waitExposed(espace)
    editeur.barres.setChecked(False)
    assert espace.enregistrer_maintenant()
    page = relue(espace)
    assert page.onglets[-1].reglages.y == ("activite",)
    nouvelle = RELEVES.model_copy(
        update={
            "colonnes": tuple(
                c.model_copy(update={"nom": "activite_2"})
                if c.nom == "activite"
                else c.model_copy(update={"composante_de": "activite_2"})
                if isinstance(c, ColonneIncertitude)
                else c
                for c in RELEVES.colonnes
            )
        }
    )
    donnees = dict(DONNEES)
    donnees["activite_2"] = donnees.pop("activite")
    arc.modifier_page(
        page.model_copy(update={"mesures": (nouvelle,)}),
        tableaux={"releves": donnees},
    )
    assert relue(espace).grandeur("releves").colonnes[2].nom == "activite_2"
    espace.ouvrir_page(espace.page.id)
    relu = next(e for e in espace.editeurs() if isinstance(e, EditeurGraphes))
    espace.onglets.setCurrentWidget(relu)
    assert "« activite » n'existe plus" in relu.toile.message.text()


def test_le_graphe_se_met_a_jour_apres_un_enregistrement(espace, editeur, qtbot):
    espace.show()
    qtbot.waitExposed(espace)
    assert editeur.toile.donnees.x.nom == "heure"
    espace.page = relue(espace)
    editeur.enregistre(espace.page)
    assert editeur.toile.donnees is not None
    assert editeur.champ_tableau.currentText() == "releves"


def test_sans_tableau_l_onglet_explique_quoi_faire(qtbot, arc, monkeypatch):
    espace = EspaceSaisie()
    qtbot.addWidget(espace)
    espace.installer(arc)
    assert espace.creer_page(DescriptionPage("Vide", HEURE, "G.", None, ()))
    editeur = espace.ajouter_onglet("graphes")
    espace.show()
    qtbot.waitExposed(espace)
    assert "d'abord un tableau" in editeur.toile.message.text()
    assert editeur.valeur().reglages is None
    assert espace.enregistrer_maintenant()


# --- Fiche -------------------------------------------------------------------------------


@pytest.fixture
def fiche(qtbot):
    resultat = FichePage()
    qtbot.addWidget(resultat)
    return resultat


def test_la_fiche_trace_les_graphes_et_suit_son_facteur_k(arc, fiche):
    page = page_de_test(
        arc,
        onglets=(
            OngletGraphes(
                id="g",
                type="graphes",
                titre="Décroissance",
                reglages=ReglagesGraphe(tableau="releves", x="heure", y=("activite",)),
            ),
        ),
    )
    fiche.afficher(arc, page.id)
    toile, _reglages = fiche.graphes[0]
    assert toile.donnees.k == 2.0
    avant = toile.donnees.y[0].elargies
    fiche.champ_k.setValue(4.0)
    assert fiche.graphes[0][0].donnees.y[0].elargies == pytest.approx(
        [u * 2 for u in avant]
    )
    fiche.champ_k.setValue(2.0)


def test_une_page_sans_graphe_n_en_affiche_pas(arc, page, fiche):
    fiche.afficher(arc, page.id)
    assert fiche.graphes == []
    fiche.vider()
    assert fiche.graphes == []


def test_la_fiche_explique_un_graphe_devenu_impossible(arc, fiche):
    page = page_de_test(
        arc,
        onglets=(
            OngletGraphes(
                id="g",
                type="graphes",
                reglages=ReglagesGraphe(
                    tableau="releves", x="absente", y=("activite",)
                ),
            ),
        ),
    )
    fiche.afficher(arc, page.id)
    assert "« absente » n'existe plus" in fiche.graphes[0][0].message.text()
