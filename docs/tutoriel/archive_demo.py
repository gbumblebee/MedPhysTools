"""Fabrique l'archive de démonstration du tutoriel, avec des valeurs entièrement fictives.

Ce fichier ne sert qu'au tutoriel : il n'est pas utile pour lire votre propre archive.
"""

import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import CTImageStorage, ExplicitVRLittleEndian, generate_uid

from medphystools import Archive
from medphystools.ajustements import MODELES, creer_ajustement, creer_indicateur, lire_points
from medphystools.calculs import creer_resultat
from medphystools.dicom import importer_series
from medphystools.gabarit import creer_gabarit
from medphystools.incertitudes import Composante, repetitions
from medphystools.page import (
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Identifiant,
    OngletNotes,
    Page,
    Scalaire,
    Tableau,
)

PARIS = timezone(timedelta(hours=1))
CERTIFICAT = Composante(
    type="B",
    valeur=2.0,
    relative=True,
    k=2.0,
    source="certificat de la source",
    erreur_commune="CERT-114",
)
AFFICHEUR = Composante(type="B", valeur=0.2, unite="MBq", source="afficheur")
HORLOGE = Composante(type="B", valeur=0.02, unite="h", source="horloge")


def _coupes_de_scanner(dossier: Path) -> list[Path]:
    """Quatre coupes de scanner fictives, avec une pente et une ordonnée connues."""
    serie = generate_uid()
    chemins = []
    for rang in range(4):
        meta = FileMetaDataset()
        meta.MediaStorageSOPClassUID = CTImageStorage
        meta.MediaStorageSOPInstanceUID = generate_uid()
        meta.TransferSyntaxUID = ExplicitVRLittleEndian
        jeu = FileDataset("", {}, file_meta=meta, preamble=b"\0" * 128)
        jeu.SOPClassUID = CTImageStorage
        jeu.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
        jeu.StudyInstanceUID = "1.2.826.0.1.3680043.8.498.10"
        jeu.SeriesInstanceUID = serie
        jeu.FrameOfReferenceUID = "1.2.826.0.1.3680043.8.498.11"
        jeu.Modality = "CT"
        jeu.PatientName = "FANTOME^DEMO"
        jeu.StudyDate = "20261114"
        jeu.ImagePositionPatient = [0.0, 0.0, 2.5 * rang]
        jeu.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        jeu.PixelSpacing = [1.0, 1.0]
        jeu.SliceThickness = 2.5
        jeu.InstanceNumber = rang + 1
        jeu.RescaleSlope = 1.0
        jeu.RescaleIntercept = -1024.0
        jeu.RescaleType = "HU"
        valeurs = np.full((16, 16), 1024 + 10 * rang, dtype=np.int16)
        jeu.Rows, jeu.Columns = valeurs.shape
        jeu.SamplesPerPixel = 1
        jeu.PhotometricInterpretation = "MONOCHROME2"
        jeu.BitsAllocated = 16
        jeu.BitsStored = 16
        jeu.HighBit = 15
        jeu.PixelRepresentation = 1
        jeu.PixelData = valeurs.tobytes()
        chemin = dossier / f"coupe{rang}.dcm"
        jeu.save_as(chemin)
        chemins.append(chemin)
    return chemins


def _page_de_decroissance(archive: Archive, dossier: Path) -> str:
    heure = datetime(2026, 11, 14, 9, 30, tzinfo=PARIS)
    moyenne, type_a = repetitions([100.2, 99.7, 100.1, 100.4, 99.6], "MBq")
    scalaires = (
        Scalaire(
            nom="activite_ref",
            valeur=moyenne,
            unite="MBq",
            composantes=(type_a, CERTIFICAT, AFFICHEUR),
            repetitions=(100.2, 99.7, 100.1, 100.4, 99.6),
            commentaire="Mesure de référence, répétée cinq fois",
        ),
        Scalaire(
            nom="activite_fin",
            valeur=50.3,
            unite="MBq",
            composantes=(CERTIFICAT, AFFICHEUR),
            horodatage=heure + timedelta(hours=6),
        ),
        Scalaire(
            nom="T12",
            valeur=6.01,
            unite="h",
            composantes=(Composante(type="B", valeur=0.01, unite="h", source="ICRP"),),
        ),
        Identifiant(nom="lot", valeur="007"),
    )
    decroissance = Tableau(
        nom="decroissance",
        fichier="brut/decroissance.csv",
        commentaire="Suivi au cours de la journée",
        colonnes=(
            ColonneIdentifiant(nom="flacon"),
            ColonneNumerique(nom="heure", unite="h", composantes=(HORLOGE,)),
            ColonneNumerique(nom="activite", unite="MBq", composantes=(CERTIFICAT,)),
            ColonneIncertitude(
                nom="u_lecture",
                composante_de="activite",
                type="B",
                unite="MBq",
                source="lecture, cellule par cellule",
            ),
        ),
    )
    comptage = Tableau(
        nom="comptage",
        fichier="brut/comptage.csv",
        colonnes=(
            ColonneNumerique(nom="N", unite="1"),
            ColonneIncertitude(
                nom="u_N",
                composante_de="N",
                type="B",
                unite="1",
                source="Calculée : sqrt(N)",
                formule="sqrt(N)",
            ),
        ),
    )
    donnees = {
        "decroissance": {
            "flacon": ["007", "007", "007", "007", "008"],
            "heure": [0.0, 1.0, 2.0, 4.0, 8.0],
            "activite": [100.0, 89.1, 79.4, 63.0, 39.8],
            "u_lecture": [0.5, 0.5, 0.4, 0.3, 0.2],
        },
        "comptage": {"N": [1000.0, 1210.0, 961.0]},
    }
    identifiant = archive.nouvel_identifiant(heure, "decroissance Tc99m")
    page = Page(
        id=identifiant,
        titre="Décroissance du Tc-99m",
        description="Exemple du tutoriel",
        date_mesure=heure,
        date_saisie=heure,
        auteur="G. Exemple",
        machine="chu.medecine-nucleaire.activimetre.fabricant-modele.0001",
        tags=("activimetre", "Tc99m"),
        mesures=(*scalaires, decroissance, comptage),
        onglets=(
            OngletNotes(
                id="onglet_notes",
                type="notes",
                titre="Notes",
                texte="# Montage\nActivité de référence : {{activite_ref}}",
            ),
        ),
    )
    archive.declarer_machine(page.machine)
    archive.creer_page(page, tableaux=donnees)

    # Résultats de calcul, comme ceux de l'onglet Calculs.
    lire = lambda nom: archive.lire_tableau(identifiant, nom)  # noqa: E731
    page = archive.lire_page(identifiant)
    for nom, formule in (
        ("rapport", "activite_ref / activite_fin"),
        ("activite_relative", "decroissance.activite / activite_ref"),
        ("d_activite", "derivee(decroissance.activite, decroissance.heure)"),
        ("P_activite", "primitive(decroissance.activite, decroissance.heure)"),
    ):
        resultat = creer_resultat(page, nom, formule, lire)
        page = page.model_copy(update={"mesures": (*page.mesures, resultat)})
    archive.modifier_page(page)

    # Ajustement et indicateur, comme ceux des onglets du même nom.
    page = archive.lire_page(identifiant)
    points = lire_points(page, "decroissance", "heure", "activite", lire)
    ajustement, _ = creer_ajustement(
        points, "fit", MODELES["exponentielle"].formule, "exponentielle"
    )
    maximum = creer_indicateur(
        page, "pic", "maximum", "points", "decroissance", "heure", "activite", lire
    )
    # La série DICOM et un fichier joint.
    releve = dossier / "releve.txt"
    releve.write_text("Relevé de l'activimètre\n", encoding="utf-8")
    fichier = archive.importer_copie(releve, "releve", "texte")
    serie = importer_series(archive, _coupes_de_scanner(dossier), noms_pris=("releve",))
    piece = archive.importer_piece_jointe(identifiant, releve, "releve.txt")
    page = page.model_copy(
        update={
            "mesures": (*page.mesures, ajustement, maximum, fichier, *serie.fichiers),
            "pieces_jointes": (piece,),
        }
    )
    archive.modifier_page(page)

    # Validation, puis une correction motivée : elle se relit dans l'historique.
    archive.valider_page(identifiant)
    page = archive.lire_page(identifiant)
    corrigee = tuple(
        g.model_copy(update={"valeur": 50.4}) if g.nom == "activite_fin" else g
        for g in page.mesures
    )
    archive.modifier_page(
        page.model_copy(update={"mesures": corrigee}),
        motif="Erreur de recopie sur l'activité finale",
    )
    return identifiant


def _deuxieme_page(archive: Archive) -> str:
    """Une autre page, mesurée avec la même source : même certificat, même repère."""
    heure = datetime(2026, 11, 21, 9, 30, tzinfo=PARIS)
    identifiant = archive.nouvel_identifiant(heure, "linearite")
    archive.creer_page(
        Page(
            id=identifiant,
            titre="Linéarité de l'activimètre",
            date_mesure=heure,
            date_saisie=heure,
            auteur="G. Exemple",
            tags=("activimetre",),
            mesures=(
                Scalaire(
                    nom="activite_ref",
                    valeur=80.0,
                    unite="MBq",
                    composantes=(CERTIFICAT, AFFICHEUR),
                ),
            ),
        )
    )
    return identifiant


def creer_archive_demo() -> tuple[Path, str]:
    """Crée l'archive dans un dossier temporaire ; renvoie son chemin et la page principale."""
    dossier = Path(tempfile.mkdtemp(prefix="medphysnotebook_tutoriel_"))
    archive = Archive.creer(
        dossier / "archive", "CHU fictif", "Resp.", date(2026, 11, 1)
    )
    identifiant = _page_de_decroissance(archive, dossier)
    _deuxieme_page(archive)
    page = archive.lire_page(identifiant)
    archive.enregistrer_gabarit(
        creer_gabarit(page, "decroissance", ["activite_ref", "decroissance"])
    )
    return dossier / "archive", identifiant
