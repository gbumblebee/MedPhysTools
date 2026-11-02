"""Fabrique des fichiers DICOM de test, entièrement fictifs, aux valeurs connues."""

from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import (
    CTImageStorage,
    EnhancedCTImageStorage,
    ExplicitVRLittleEndian,
    NuclearMedicineImageStorage,
    PositronEmissionTomographyImageStorage,
    RTDoseStorage,
    RTStructureSetStorage,
    SecondaryCaptureImageStorage,
    generate_uid,
)

ETUDE = "1.2.826.0.1.3680043.8.498.1"
REPERE = "1.2.826.0.1.3680043.8.498.2"
NOM = "DUPONT^JEAN"
ID_PATIENT = "IPP-123456"


def _jeu(classe: str, modalite: str, serie: str) -> FileDataset:
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = classe
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    jeu = FileDataset("", {}, file_meta=meta, preamble=b"\0" * 128)
    jeu.SOPClassUID = classe
    jeu.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    jeu.StudyInstanceUID = ETUDE
    jeu.SeriesInstanceUID = serie
    jeu.FrameOfReferenceUID = REPERE
    jeu.Modality = modalite
    jeu.PatientName = NOM
    jeu.PatientID = ID_PATIENT
    jeu.PatientBirthDate = "19600215"
    jeu.PatientSex = "M"
    jeu.PatientAddress = "12 rue des Lilas"
    jeu.InstitutionName = "CHU de Nulle-Part"
    jeu.ReferringPhysicianName = "MARTIN^PAUL"
    jeu.OperatorsName = "DURAND^ANNE"
    jeu.AccessionNumber = "ACC-987"
    jeu.StudyID = "ETUDE-42"
    jeu.StudyDescription = "Scanner de Monsieur DUPONT"
    jeu.SeriesDescription = "Coupes DUPONT"
    jeu.ImageComments = "Patient DUPONT, à revoir"
    jeu.StudyDate = "20261114"
    jeu.SeriesDate = "20261114"
    jeu.Manufacturer = "Fabricant fictif"
    jeu.ManufacturerModelName = "Modèle fictif"
    jeu.add_new(0x00091001, "LO", "Privé DUPONT")
    return jeu


def _pixels(jeu: FileDataset, valeurs: np.ndarray, signe: bool = True) -> None:
    jeu.Rows, jeu.Columns = valeurs.shape[-2:]
    jeu.SamplesPerPixel = 1
    jeu.PhotometricInterpretation = "MONOCHROME2"
    octets = valeurs.dtype.itemsize
    jeu.BitsAllocated = octets * 8
    jeu.BitsStored = octets * 8
    jeu.HighBit = octets * 8 - 1
    jeu.PixelRepresentation = 1 if signe else 0
    jeu.PixelData = valeurs.tobytes()


def _geometrie(jeu, position, orientation=(1, 0, 0, 0, 1, 0), pixel=(1.0, 2.0)) -> None:
    jeu.ImagePositionPatient = [float(v) for v in position]
    jeu.ImageOrientationPatient = [float(v) for v in orientation]
    jeu.PixelSpacing = [float(v) for v in pixel]


def valeurs_ct(rang: int, lignes: int = 8, colonnes: int = 10) -> np.ndarray:
    """Une image dont chaque valeur stockée dit sa position : z, y, x se lisent dedans."""
    y, x = np.mgrid[0:lignes, 0:colonnes]
    return (1000 * rang + 10 * y + x).astype(np.int16)


def serie_ct(
    coupes: int = 4,
    pente: float = 2.0,
    ordonnee: float = -1024.0,
    pas: float = 5.0,
    serie: str | None = None,
    origine: tuple[float, float, float] = (-10.0, -20.0, 100.0),
    melanger: bool = True,
    lignes: int = 8,
    colonnes: int = 10,
) -> list[FileDataset]:
    """Un scanner : valeur physique = valeur stockée × pente + ordonnée, à z croissant."""
    uid = serie or generate_uid()
    jeux = []
    for rang in range(coupes):
        jeu = _jeu(CTImageStorage, "CT", uid)
        _pixels(jeu, valeurs_ct(rang, lignes, colonnes))
        _geometrie(jeu, (origine[0], origine[1], origine[2] + rang * pas))
        jeu.RescaleSlope = pente
        jeu.RescaleIntercept = ordonnee
        jeu.RescaleType = "HU"
        jeu.SliceThickness = pas
        jeu.InstanceNumber = rang + 1
        jeu.KVP = 120
        jeux.append(jeu)
    if melanger:
        jeux = jeux[1:] + jeux[:1]
    return jeux


def dose_rt(
    facteur: float = 0.001,
    decalages: tuple[float, ...] = (0.0, 5.0, 10.0),
    bits: int = 32,
    origine: tuple[float, float, float] = (-10.0, -20.0, 100.0),
) -> FileDataset:
    """Une dose : les pixels sont des entiers, à multiplier par DoseGridScaling."""
    jeu = _jeu(RTDoseStorage, "RTDOSE", generate_uid())
    z, y, x = np.mgrid[0 : len(decalages), 0:6, 0:7]
    entiers = (100000 * (z + 1) + 100 * y + x).astype(
        np.uint32 if bits == 32 else np.uint16
    )
    if bits == 16:
        entiers = (1000 * (z + 1) + 10 * y + x).astype(np.uint16)
    _pixels(jeu, entiers, signe=False)
    jeu.NumberOfFrames = len(decalages)
    _geometrie(jeu, origine, pixel=(4.0, 3.0))
    jeu.GridFrameOffsetVector = [float(v) for v in decalages]
    jeu.FrameIncrementPointer = 0x3004000C
    jeu.DoseGridScaling = facteur
    jeu.DoseUnits = "GY"
    jeu.DoseType = "PHYSICAL"
    jeu.DoseSummationType = "PLAN"
    return jeu


def serie_tep(pentes: tuple[float, ...] = (0.5, 2.0)) -> list[FileDataset]:
    """Une TEP : chaque coupe a sa propre pente, et les valeurs sont en Bq/mL."""
    uid = generate_uid()
    jeux = []
    for rang, pente in enumerate(pentes):
        jeu = _jeu(PositronEmissionTomographyImageStorage, "PT", uid)
        _pixels(jeu, np.full((4, 5), 100 * (rang + 1), dtype=np.int16))
        _geometrie(jeu, (0.0, 0.0, rang * 3.0), pixel=(2.0, 2.0))
        jeu.RescaleSlope = pente
        jeu.RescaleIntercept = 0.0
        jeu.Units = "BQML"
        jeu.CorrectedImage = ["DECY", "ATTN"]
        jeu.InstanceNumber = rang + 1
        jeux.append(jeu)
    return jeux


def temp_nm(coupes: int = 3, temps: int = 1) -> FileDataset:
    """Une TEMP multi-images : positions tirées du détecteur, avec une pente réelle."""
    jeu = _jeu(NuclearMedicineImageStorage, "NM", generate_uid())
    images = coupes * temps
    z = np.array(
        [[(c + 1) * 10 + t for t in range(temps)] for c in range(coupes)]
    ).ravel()
    _pixels(
        jeu,
        np.stack([np.full((4, 4), v, dtype=np.uint16) for v in z]),
        signe=False,
    )
    jeu.NumberOfFrames = images
    jeu.PixelSpacing = [4.0, 4.0]
    jeu.SpacingBetweenSlices = 4.0
    detecteur = Dataset()
    detecteur.ImagePositionPatient = [0.0, 0.0, 0.0]
    detecteur.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    jeu.DetectorInformationSequence = Sequence([detecteur])
    jeu.SliceVector = [c + 1 for c in range(coupes) for _ in range(temps)]
    if temps > 1:
        jeu.TimeSlotVector = [t + 1 for _ in range(coupes) for t in range(temps)]
    echelle = Dataset()
    echelle.RealWorldValueSlope = 2.5
    echelle.RealWorldValueIntercept = 0.0
    code = Dataset()
    code.CodeValue = "Bq/ml"
    code.CodeMeaning = "Bq/ml"
    code.CodingSchemeDesignator = "UCUM"
    echelle.MeasurementUnitsCodeSequence = Sequence([code])
    jeu.RealWorldValueMappingSequence = Sequence([echelle])
    return jeu


def serie_4d_classique(phases: int = 3, coupes: int = 2) -> list[FileDataset]:
    """Une série 4D en fichiers séparés : plusieurs images à une même position, dans le désordre."""
    uid = generate_uid()
    jeux = []
    for t in reversed(range(phases)):
        for rang in range(coupes):
            jeu = _jeu(CTImageStorage, "CT", uid)
            _pixels(jeu, np.full((3, 3), 100 * t + rang, dtype=np.int16))
            _geometrie(jeu, (0.0, 0.0, rang * 2.0), pixel=(1.0, 1.0))
            jeu.RescaleSlope = 1.0
            jeu.RescaleIntercept = 0.0
            jeu.TemporalPositionIdentifier = t + 1
            jeu.NumberOfTemporalPositions = phases
            jeu.InstanceNumber = t * coupes + rang + 1
            jeux.append(jeu)
    return jeux


def serie_4d_amelioree(phases: int = 2, coupes: int = 3) -> FileDataset:
    """Un seul fichier amélioré, 4D : position, échelle et temps se lisent image par image."""
    jeu = _jeu(EnhancedCTImageStorage, "CT", generate_uid())
    images = []
    groupes = []
    for t in range(phases):
        for rang in range(coupes):
            images.append(np.full((3, 4), 100 * t + 10 * rang, dtype=np.int16))
            groupe = Dataset()
            plan = Dataset()
            plan.ImagePositionPatient = [0.0, 0.0, rang * 2.5]
            groupe.PlanePositionSequence = Sequence([plan])
            contenu = Dataset()
            contenu.TemporalPositionIndex = t + 1
            groupe.FrameContentSequence = Sequence([contenu])
            echelle = Dataset()
            echelle.RescaleSlope = 2.0
            echelle.RescaleIntercept = -10.0
            echelle.RescaleType = "HU"
            groupe.PixelValueTransformationSequence = Sequence([echelle])
            groupes.append(groupe)
    _pixels(jeu, np.stack(images))
    jeu.NumberOfFrames = len(images)
    jeu.PerFrameFunctionalGroupsSequence = Sequence(groupes)
    commun = Dataset()
    orientation = Dataset()
    orientation.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    commun.PlaneOrientationSequence = Sequence([orientation])
    mesures = Dataset()
    mesures.PixelSpacing = [1.5, 0.5]
    mesures.SliceThickness = 2.5
    commun.PixelMeasuresSequence = Sequence([mesures])
    jeu.SharedFunctionalGroupsSequence = Sequence([commun])
    return jeu


def capture_ecran() -> FileDataset:
    """Une capture d'écran en couleur : sans position, et où un texte peut être incrusté."""
    jeu = _jeu(SecondaryCaptureImageStorage, "OT", generate_uid())
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    image[..., 0] = 255
    jeu.Rows, jeu.Columns = 4, 4
    jeu.SamplesPerPixel = 3
    jeu.PhotometricInterpretation = "RGB"
    jeu.PlanarConfiguration = 0
    jeu.BitsAllocated = 8
    jeu.BitsStored = 8
    jeu.HighBit = 7
    jeu.PixelRepresentation = 0
    jeu.PixelData = image.tobytes()
    jeu.BurnedInAnnotation = "YES"
    return jeu


def cercle(z: float, rayon: float, centre=(0.0, 0.0), points: int = 64) -> list[float]:
    angles = np.linspace(0.0, 2 * np.pi, points, endpoint=False)
    valeurs = np.c_[
        centre[0] + rayon * np.cos(angles),
        centre[1] + rayon * np.sin(angles),
        np.full(points, z),
    ]
    return [float(v) for v in valeurs.ravel()]


def structures_rt(contours: dict[str, tuple] | None = None) -> FileDataset:
    """Un RTSTRUCT : des contours fermés, dessinés coupe à coupe."""
    jeu = _jeu(RTStructureSetStorage, "RTSTRUCT", generate_uid())
    jeu.StructureSetLabel = "Plan DUPONT"
    jeu.StructureSetName = "Structures de DUPONT"
    jeu.ReviewerName = "MARTIN^PAUL"
    contours = contours or {
        "PTV": ((255, 0, 0), {100.0: 8.0, 105.0: 8.0, 110.0: 8.0}),
        "Coeur": ((0, 255, 0), {105.0: 3.0}),
    }
    rois = []
    rois_contours = []
    for numero, (nom, (couleur, tranches)) in enumerate(contours.items(), start=1):
        roi = Dataset()
        roi.ROINumber = numero
        roi.ROIName = nom
        roi.ReferencedFrameOfReferenceUID = REPERE
        rois.append(roi)
        contenu = Dataset()
        contenu.ReferencedROINumber = numero
        contenu.ROIDisplayColor = list(couleur)
        sequence = []
        for z, rayon in tranches.items():
            contour = Dataset()
            contour.ContourGeometricType = "CLOSED_PLANAR"
            donnees = cercle(z, rayon)
            contour.NumberOfContourPoints = len(donnees) // 3
            contour.ContourData = donnees
            sequence.append(contour)
        contenu.ContourSequence = Sequence(sequence)
        rois_contours.append(contenu)
    jeu.StructureSetROISequence = Sequence(rois)
    jeu.ROIContourSequence = Sequence(rois_contours)
    repere = Dataset()
    repere.FrameOfReferenceUID = REPERE
    jeu.ReferencedFrameOfReferenceSequence = Sequence([repere])
    del jeu.FrameOfReferenceUID
    return jeu


def ecrire(
    jeux: list[FileDataset] | FileDataset, dossier: Path, nom="DUPONT_Jean"
) -> list[Path]:
    """Écrit des fichiers sous un nom qui identifie le patient, comme dans la vraie vie."""
    dossier.mkdir(parents=True, exist_ok=True)
    liste = jeux if isinstance(jeux, list) else [jeux]
    chemins = []
    for rang, jeu in enumerate(liste):
        chemin = dossier / f"{nom}_{rang}.dcm"
        jeu.save_as(chemin, enforce_file_format=True)
        chemins.append(chemin)
    return chemins
