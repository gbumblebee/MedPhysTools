"""Vérifie les dates d'un tableau : fuseau, écriture à l'écran, fichier et gabarit."""

import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from pymphub.dates import (
    DateInvalide,
    afficher_date,
    ecrire_date_iso,
    lire_date,
    lire_date_iso,
    maintenant,
)
from pymphub.importation import deviner_nature
from pymphub.page import ColonneDate, ColonneNumerique, Composante, Tableau
from pymphub.tableaux import TableauInvalide, ecrire_csv, lire_csv

PARIS = "Europe/Paris"
TOKYO = "Asia/Tokyo"


@pytest.fixture
def fuseau(monkeypatch):
    """Place cet ordinateur dans un fuseau, sans dépendre de celui de la machine de test."""

    def choisir(nom: str) -> None:
        monkeypatch.setenv("TZ", nom)
        time.tzset()

    yield choisir
    monkeypatch.undo()
    os.environ.pop("TZ", None) if "TZ" not in os.environ else None
    time.tzset()


@pytest.mark.parametrize(
    "texte",
    [
        "04/11/2026 14:30:05",
        "4/11/2026 14:30:05",
        "2026-11-04 14:30:05",
        "2026-11-04T14:30:05",
        "  04/11/2026 14:30:05  ",
    ],
)
def test_les_ecritures_usuelles_donnent_la_meme_date(fuseau, texte):
    fuseau(PARIS)
    attendue = datetime(2026, 11, 4, 14, 30, 5, tzinfo=timezone(timedelta(hours=1)))
    assert lire_date(texte) == attendue
    assert lire_date(texte).utcoffset() == timedelta(hours=1)


def test_le_jour_vient_avant_le_mois(fuseau):
    fuseau(PARIS)
    assert lire_date("03/11/2026 10:00").month == 11


def test_les_secondes_sont_facultatives(fuseau):
    fuseau(PARIS)
    assert lire_date("04/11/2026 14:30").second == 0


def test_le_fuseau_suit_le_changement_d_heure(fuseau):
    fuseau(PARIS)
    assert lire_date("15/07/2026 12:00").utcoffset() == timedelta(hours=2)
    assert lire_date("15/12/2026 12:00").utcoffset() == timedelta(hours=1)


@pytest.mark.parametrize(
    "texte",
    [
        "",
        "abc",
        "12.5",
        "2026",
        "20261104",
        "04/11/2026",
        "2026-11-04",
        "32/01/2026 10:00",
    ],
)
def test_ce_qui_n_est_pas_une_date_et_une_heure_est_refuse(texte):
    with pytest.raises(DateInvalide):
        lire_date(texte)


def test_une_formule_doit_indiquer_son_fuseau():
    with pytest.raises(DateInvalide, match="fuseau"):
        lire_date("2026-11-04T14:30:00", fuseau_requis=True)
    assert lire_date("2026-11-04T14:30:00+01:00", fuseau_requis=True).hour == 14


def test_l_ecran_ne_cite_le_fuseau_que_s_il_differe(fuseau):
    fuseau(PARIS)
    ici = datetime(2026, 11, 4, 14, 30, 5, tzinfo=timezone(timedelta(hours=1)))
    ailleurs = datetime(2026, 11, 4, 14, 30, 5, tzinfo=timezone(timedelta(hours=9)))
    assert afficher_date(ici) == "04/11/2026 14:30:05"
    assert afficher_date(ailleurs) == "04/11/2026 14:30:05 +09:00"


@pytest.mark.parametrize("ordinateur", [PARIS, TOKYO])
@pytest.mark.parametrize(
    "iso",
    [
        "2026-11-04T14:30:05+01:00",
        "2026-07-15T14:30:05+02:00",
        "2026-11-04T14:30:05+09:00",
        "2026-11-04T14:30:05.250000+01:00",
    ],
)
def test_relire_ce_qui_est_affiche_redonne_exactement_la_date(fuseau, ordinateur, iso):
    """Sinon, ouvrir une page sur un autre ordinateur changerait les données."""
    fuseau(ordinateur)
    assert ecrire_date_iso(lire_date(afficher_date(lire_date_iso(iso)))) == iso


def test_maintenant_porte_son_fuseau_sans_fraction_de_seconde():
    date = maintenant()
    assert date.tzinfo is not None
    assert date.microsecond == 0


# --- Tableau --------------------------------------------------------------------------


def tableau() -> Tableau:
    return Tableau(
        nom="decroissance",
        fichier="brut/decroissance.csv",
        colonnes=(
            ColonneDate(nom="heure"),
            ColonneNumerique(
                nom="activite",
                unite="MBq",
                composantes=(
                    Composante(source="s", type="B", valeur=1.0, unite="MBq"),
                ),
            ),
        ),
    )


DONNEES = {
    "heure": ["2026-11-04T09:00:00+01:00", "2026-11-04T15:00:00+01:00"],
    "activite": [100.0, 50.0],
}


def test_une_colonne_de_dates_se_retrouve_telle_quelle():
    texte = ecrire_csv(tableau(), DONNEES)
    assert "2026-11-04T09:00:00+01:00,100.0\n" in texte
    relu = lire_csv(tableau(), texte)
    assert relu["heure"].tolist() == DONNEES["heure"]
    assert relu.attrs["unites"] == {"activite": "MBq"}


def test_une_colonne_de_dates_se_convertit_en_une_ligne_de_python():
    relu = lire_csv(tableau(), ecrire_csv(tableau(), DONNEES))
    dates = pd.to_datetime(relu["heure"], utc=True)
    assert (dates.iloc[1] - dates.iloc[0]).total_seconds() == 6 * 3600


@pytest.mark.parametrize("cellule", ["2026-11-04T09:00:00", "demain", "", 3.5], ids=str)
def test_une_cellule_sans_date_complete_est_refusee_a_l_ecriture(cellule):
    with pytest.raises(TableauInvalide, match="heure.*ligne 1"):
        ecrire_csv(tableau(), DONNEES | {"heure": [cellule, DONNEES["heure"][1]]})


def test_une_cellule_sans_fuseau_est_refusee_a_la_lecture():
    texte = "heure,activite\n2026-11-04T09:00:00,100.0\n"
    with pytest.raises(TableauInvalide, match="fuseau"):
        lire_csv(tableau(), texte)


@pytest.mark.parametrize(
    ("valeurs", "nature"),
    [
        (["04/11/2026 14:30", "04/11/2026 15:30:05"], "date"),
        (["2026-11-04 14:30:00", ""], "date"),
        (["04/11/2026 14:30", "abc"], "identifiant"),
        (["04/11/2026", "05/11/2026"], "identifiant"),
        (["12.5", "13"], "numerique"),
        (["007"], "identifiant"),
    ],
)
def test_une_colonne_de_dates_collee_est_reconnue(valeurs, nature):
    assert deviner_nature(valeurs) == nature
