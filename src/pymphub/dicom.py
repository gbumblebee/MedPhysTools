"""Import de fichiers DICOM dans une archive : pseudonymisation et stockage.

Par défaut, chaque fichier est pseudonymisé : les balises identifiantes sont retirées, ses UID
et ses pixels restent identiques. Un fichier désigné comme fantôme est conservé tel quel.

Une série devient un seul fichier zip du magasin `objets/`, compressé sans perte, dont les
fichiers portent des noms neutres : le nom d'origine, qui contient parfois celui du patient,
n'atteint jamais l'archive. Un texte incrusté dans l'image n'est pas détecté : cette limite
doit rester affichée à l'import.
"""

import hashlib
import io
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pydicom
from pydicom.dataset import Dataset
from pydicom.errors import InvalidDicomError

from pymphub.archive import Archive
from pymphub.disque import chemin_long
from pymphub.edition import nom_libre
from pymphub.fichiers import FichierManquant
from pymphub.page import DicomImporte, Fichier, ReferenceDicom

LIMITE_TEXTE_INCRUSTE = (
    "Limite : un texte incrusté dans l'image, comme un nom sur une échographie ou une "
    "capture d'écran, n'est pas détecté. Vérifier ces images avant de les importer."
)
MESSAGE_TEXTE_DECLARE = "Ces images déclarent un texte incrusté : les vérifier."
VALEUR_PSEUDONYME = "ANONYME"
METHODE_PSEUDONYMISATION = "pymphub : balises identifiantes retirées, UID conservés"

# Balises identifiantes retirées, en plus de tout ce qui est de type « nom de personne »,
# des balises privées, des surcouches d'image et des courbes.
BALISES_RETIREES: tuple[str, ...] = (
    "AccessionNumber",
    "AcquisitionComments",
    "AdditionalPatientHistory",
    "AdmissionID",
    "AdmittingDiagnosesCodeSequence",
    "AdmittingDiagnosesDescription",
    "Allergies",
    "BranchOfService",
    "ContentDescription",
    "CountryOfResidence",
    "CurrentPatientLocation",
    "DoseComment",
    "EthnicGroup",
    "ImageComments",
    "ImagingServiceRequestComments",
    "InstitutionAddress",
    "InstitutionCodeSequence",
    "InstitutionName",
    "InstitutionalDepartmentName",
    "IssuerOfPatientID",
    "MedicalAlerts",
    "MedicalRecordLocator",
    "MilitaryRank",
    "Occupation",
    "OperatorIdentificationSequence",
    "OtherPatientIDs",
    "OtherPatientIDsSequence",
    "OtherPatientNames",
    "PatientAddress",
    "PatientAge",
    "PatientBirthDate",
    "PatientBirthName",
    "PatientBirthTime",
    "PatientComments",
    "PatientInsurancePlanCodeSequence",
    "PatientMotherBirthName",
    "PatientReligiousPreference",
    "PatientSex",
    "PatientSexNeutered",
    "PatientSize",
    "PatientState",
    "PatientTelephoneNumbers",
    "PatientTransportArrangements",
    "PatientWeight",
    "PerformedProcedureStepDescription",
    "PerformedProcedureStepID",
    "PerformingPhysicianIdentificationSequence",
    "PhysiciansOfRecordIdentificationSequence",
    "PhysiciansReadingStudyIdentificationSequence",
    "PregnancyStatus",
    "ProtocolName",
    "RTPlanDescription",
    "RTPlanLabel",
    "RTPlanName",
    "ROIDescription",
    "ROIObservationDescription",
    "ReasonForStudy",
    "ReasonForTheRequestedProcedure",
    "ReferencedPatientPhotoSequence",
    "ReferencedPatientSequence",
    "ReferringPhysicianAddress",
    "ReferringPhysicianIdentificationSequence",
    "ReferringPhysicianTelephoneNumbers",
    "RegionOfResidence",
    "RequestAttributesSequence",
    "RequestedProcedureComments",
    "RequestedProcedureDescription",
    "RequestedProcedureID",
    "RequestingService",
    "ScheduledProcedureStepDescription",
    "ScheduledProcedureStepID",
    "SeriesDescription",
    "SmokingStatus",
    "SpecialNeeds",
    "StationName",
    "StudyComments",
    "StudyDescription",
    "StudyID",
    "StructureSetDescription",
    "StructureSetName",
    "TextComments",
    "TextString",
    "VisitComments",
)
# Ces balises sont obligatoires dans le fichier : elles restent, avec une valeur neutre.
_BALISES_NEUTRALISEES = {
    "PatientName": VALEUR_PSEUDONYME,
    "PatientID": VALEUR_PSEUDONYME,
    "StructureSetLabel": "STRUCTURES",
}
_META_RETIREES = (
    "SourceApplicationEntityTitle",
    "SendingApplicationEntityTitle",
    "ReceivingApplicationEntityTitle",
    "PrivateInformationCreatorUID",
    "PrivateInformation",
)
_MOTS_FANTOME = ("fantome", "fantôme", "phantom", "test", "cq", "qc", "qa", "cq_")
_LIBELLES_PATIENT = {
    "PatientName": "nom du patient",
    "PatientID": "identifiant du patient",
    "PatientBirthDate": "date de naissance",
    "PatientAddress": "adresse",
    "OtherPatientIDs": "autres identifiants",
    "PatientTelephoneNumbers": "téléphone",
}


class ErreurDicom(ValueError):
    """Ce fichier DICOM ne peut pas être lu ou importé : le message explique pourquoi."""


class LectureAnnulee(Exception):  # noqa: N818 - ce n'est pas une erreur
    """La lecture n'est plus utile, car l'utilisateur est passé à autre chose."""


# --- Pseudonymisation ---------------------------------------------------------------------


def _neutraliser(jeu: Dataset) -> None:
    """Retire les balises identifiantes d'un niveau du fichier, et de ceux qu'il contient."""
    for mot_cle in BALISES_RETIREES:
        if mot_cle in jeu:
            del jeu[mot_cle]
    for mot_cle, valeur in _BALISES_NEUTRALISEES.items():
        if mot_cle in jeu:
            setattr(jeu, mot_cle, valeur)
    # Surcouches d'image (60xx) et courbes (50xx) : elles peuvent porter du texte.
    for balise in [e.tag for e in jeu if e.tag.group in range(0x5000, 0x50FF, 2)]:
        del jeu[balise]
    for balise in [e.tag for e in jeu if e.tag.group in range(0x6000, 0x60FF, 2)]:
        del jeu[balise]
    jeu.remove_private_tags()
    for element in list(jeu):
        if element.VR == "PN" and element.keyword not in _BALISES_NEUTRALISEES:
            del jeu[element.tag]
        elif element.VR == "SQ":
            for sous_jeu in element.value:
                _neutraliser(sous_jeu)


def pseudonymiser(jeu: Dataset) -> Dataset:
    """Retire d'un fichier ce qui identifie le patient. Les UID et les pixels ne changent pas."""
    _neutraliser(jeu)
    jeu.PatientName = VALEUR_PSEUDONYME
    jeu.PatientID = VALEUR_PSEUDONYME
    jeu.PatientIdentityRemoved = "YES"
    jeu.DeidentificationMethod = METHODE_PSEUDONYMISATION
    # Le préambule et ces champs d'en-tête sont libres : ils pourraient contenir n'importe quoi.
    jeu.preamble = b"\0" * 128
    meta = getattr(jeu, "file_meta", None)
    if meta is not None:
        for mot_cle in _META_RETIREES:
            if mot_cle in meta:
                del meta[mot_cle]
    return jeu


def balises_patient(jeu: Dataset) -> list[str]:
    """Liste ce qui ressemble à l'identité d'un patient, pour alerter avant une copie telle quelle.

    Un nom qui évoque clairement un fantôme ou un contrôle de qualité n'alerte pas. Seuls les
    libellés sont renvoyés : les valeurs ne doivent pas se retrouver dans un message.
    """
    trouvees: list[str] = []
    for mot_cle, libelle in _LIBELLES_PATIENT.items():
        valeur = str(jeu.get(mot_cle, "") or "").strip()
        if not valeur or valeur.upper() == VALEUR_PSEUDONYME:
            continue
        if mot_cle in ("PatientName", "PatientID") and any(
            mot in valeur.lower() for mot in _MOTS_FANTOME
        ):
            continue
        trouvees.append(libelle)
    return trouvees


def empreinte_des_pixels(jeux: Iterable[Dataset]) -> str | None:
    """Résume les pixels d'une série, dans l'ordre des SOP Instance UID.

    Elle ne dépend pas des balises : un PACS ou la pseudonymisation peuvent les réécrire sans
    la changer. Une série sans pixels, comme un RTSTRUCT, n'en a pas.
    """
    calcul = hashlib.sha256()
    avec_pixels = False
    for jeu in sorted(jeux, key=lambda j: str(j.SOPInstanceUID)):
        avec_pixels = _ajouter_pixels(calcul, jeu) or avec_pixels
    return calcul.hexdigest() if avec_pixels else None


def _ajouter_pixels(calcul: "hashlib._Hash", jeu: Dataset) -> bool:
    """Ajoute les pixels d'un fichier au calcul. Indique s'il en contenait."""
    trouve = False
    for mot_cle in ("PixelData", "FloatPixelData", "DoubleFloatPixelData"):
        if mot_cle in jeu:
            calcul.update(bytes(jeu[mot_cle].value))
            trouve = True
    return trouve


# --- Lecture des fichiers à importer ------------------------------------------------------


def trouver_fichiers(chemins: Iterable[str | Path]) -> list[Path]:
    """Déroule des fichiers et des dossiers en une liste de fichiers."""
    trouves: list[Path] = []
    for chemin in chemins:
        source = Path(chemin)
        if source.is_dir():
            trouves.extend(sorted(p for p in source.rglob("*") if p.is_file()))
        else:
            trouves.append(source)
    return trouves


def _lire_entete(chemin: Path) -> Dataset | None:
    """Lit l'en-tête d'un fichier. Renvoie None si ce n'est pas un fichier DICOM utilisable."""
    try:
        jeu = pydicom.dcmread(chemin_long(chemin), stop_before_pixels=True)
    except (InvalidDicomError, OSError, ValueError, EOFError):
        return None
    if "SeriesInstanceUID" not in jeu or "SOPInstanceUID" not in jeu:
        return None
    return jeu


@dataclass(frozen=True)
class SerieTrouvee:
    """Les fichiers d'une même série, prêts à importer."""

    series_instance_uid: str
    modalite: str
    chemins: tuple[Path, ...]
    entete: Dataset


@dataclass(frozen=True)
class ExamenFichiers:
    """Ce que contient une sélection de fichiers, sans rien écrire."""

    series: tuple[SerieTrouvee, ...]
    # Seul leur nombre est gardé : leur nom pourrait être celui d'un patient.
    ignores: int
    balises_patient: tuple[str, ...]


def _modalite(jeu: Dataset) -> str:
    """La modalité, réduite à ce qui peut servir dans un nom de grandeur ou de fichier."""
    texte = "".join(
        c for c in str(jeu.get("Modality", "") or "") if c.isascii() and c.isalnum()
    )
    return texte[:16] or "OT"


def examiner(chemins: Iterable[str | Path]) -> ExamenFichiers:
    """Regroupe les fichiers par série et repère ce qui ressemble à un patient."""
    groupes: dict[str, list[tuple[Path, Dataset]]] = {}
    ignores = 0
    balises: dict[str, None] = {}
    for chemin in trouver_fichiers(chemins):
        entete = _lire_entete(chemin)
        if entete is None:
            ignores += 1
            continue
        groupes.setdefault(str(entete.SeriesInstanceUID), []).append((chemin, entete))
        balises.update(dict.fromkeys(balises_patient(entete)))
    series = []
    for uid, membres in groupes.items():
        # L'ordre des SOP Instance UID : celui de l'empreinte des pixels et du zip.
        membres.sort(key=lambda membre: str(membre[1].SOPInstanceUID))
        series.append(
            SerieTrouvee(
                uid,
                _modalite(membres[0][1]),
                tuple(chemin for chemin, _entete in membres),
                membres[0][1],
            )
        )
    return ExamenFichiers(tuple(series), ignores, tuple(balises))


# --- Import -------------------------------------------------------------------------------


@dataclass
class ResultatImportDicom:
    """Ce qu'a donné un import. Les noms des fichiers d'origine n'y figurent jamais."""

    fichiers: list[Fichier] = field(default_factory=list)
    ignores: int = 0
    avertissements: list[str] = field(default_factory=list)


def _ecrire_membre(archive_zip: zipfile.ZipFile, rang: int, contenu: bytes) -> None:
    # La date est fixe : la même série donne le même zip, que le magasin ne garde qu'une fois.
    info = zipfile.ZipInfo(f"{rang:05d}.dcm", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    archive_zip.writestr(info, contenu)


def _contenu_du_fichier(jeu: Dataset, chemin: Path, mode: str) -> bytes:
    if mode == "fantome":
        return chemin_long(chemin).read_bytes()
    tampon = io.BytesIO()
    pseudonymiser(jeu).save_as(tampon, enforce_file_format=True)
    return tampon.getvalue()


def _relire(chemin: Path) -> Dataset:
    try:
        return pydicom.dcmread(chemin_long(chemin))
    except (InvalidDicomError, OSError, ValueError, EOFError) as erreur:
        # Le message d'origine pourrait citer le chemin, donc le nom d'un patient.
        raise ErreurDicom(
            f"Un fichier de la série n'a pas pu être relu ({type(erreur).__name__})."
        ) from erreur


def _importer_une_serie(
    archive: Archive,
    serie: SerieTrouvee,
    mode: str,
    nom: str,
    un_fichier_fait: Callable[[], None],
) -> Fichier:
    """Écrit la série dans un zip, un fichier à la fois : la mémoire ne suit pas sa taille."""
    calcul = hashlib.sha256()
    avec_pixels = False
    avertissements: list[str] = []
    nom_zip = f"{serie.modalite}_{len(serie.chemins)}_fichiers.dcm.zip"
    with tempfile.TemporaryDirectory() as dossier:
        temporaire = Path(dossier) / nom_zip
        with zipfile.ZipFile(temporaire, "w", zipfile.ZIP_DEFLATED) as archive_zip:
            for rang, chemin in enumerate(serie.chemins, start=1):
                jeu = _relire(chemin)
                if (
                    str(jeu.get("BurnedInAnnotation", "")).upper() == "YES"
                    and MESSAGE_TEXTE_DECLARE not in avertissements
                ):
                    avertissements.append(MESSAGE_TEXTE_DECLARE)
                avec_pixels = _ajouter_pixels(calcul, jeu) or avec_pixels
                _ecrire_membre(
                    archive_zip, rang, _contenu_du_fichier(jeu, chemin, mode)
                )
                un_fichier_fait()
        fichier = archive.importer_copie(temporaire, nom, "dicom")
    entete = serie.entete
    return fichier.model_copy(
        update={
            "dicom": DicomImporte(
                mode=mode,
                modalite=serie.modalite,
                study_instance_uid=str(entete.StudyInstanceUID),
                series_instance_uid=serie.series_instance_uid,
                frame_of_reference_uid=_cadre_de_reference(entete),
                fichiers=len(serie.chemins),
                empreinte_pixels=calcul.hexdigest() if avec_pixels else None,
                avertissements=tuple(avertissements),
            )
        }
    )


def _cadre_de_reference(jeu: Dataset) -> str | None:
    """Le repère commun à plusieurs séries : il relie une dose et des structures à une image."""
    uid = jeu.get("FrameOfReferenceUID")
    if uid:
        return str(uid)
    references = jeu.get("ReferencedFrameOfReferenceSequence")
    if references and references[0].get("FrameOfReferenceUID"):
        return str(references[0].FrameOfReferenceUID)
    return None


def importer_series(
    archive: Archive,
    chemins: Iterable[str | Path],
    mode: str = "pseudonymise",
    noms_pris: Iterable[str] = (),
    avancer: Callable[[int, int], None] | None = None,
) -> ResultatImportDicom:
    """Importe des fichiers DICOM : une grandeur par série, à ajouter ensuite à une page.

    Par défaut, tout est pseudonymisé. Avec `mode="fantome"`, les fichiers restent identiques.
    Lève `ErreurDicom` si aucun fichier n'est utilisable.
    """
    return importer_examen(archive, examiner(chemins), mode, noms_pris, avancer)


def importer_examen(
    archive: Archive,
    examen: ExamenFichiers,
    mode: str = "pseudonymise",
    noms_pris: Iterable[str] = (),
    avancer: Callable[[int, int], None] | None = None,
) -> ResultatImportDicom:
    """Importe des séries déjà examinées, par exemple après une confirmation de l'utilisateur."""
    if mode not in ("pseudonymise", "fantome"):
        raise ErreurDicom(f"Mode d'import « {mode} » inconnu.")
    if not examen.series:
        raise ErreurDicom(
            f"Aucun fichier DICOM utilisable parmi la sélection ({examen.ignores} ignoré(s))."
        )
    resultat = ResultatImportDicom(ignores=examen.ignores)
    pris = set(noms_pris)
    total = sum(len(serie.chemins) for serie in examen.series)
    faits = 0

    def un_fichier_fait() -> None:
        nonlocal faits
        faits += 1
        if avancer is not None:
            avancer(faits, total)

    for serie in examen.series:
        nom = nom_libre(f"dicom_{serie.modalite}", pris)
        pris.add(nom)
        try:
            fichier = _importer_une_serie(archive, serie, mode, nom, un_fichier_fait)
        except OSError as erreur:
            raise ErreurDicom(
                f"Copie impossible : {type(erreur).__name__}."
            ) from erreur
        resultat.fichiers.append(fichier)
    if mode == "fantome" and examen.balises_patient:
        resultat.avertissements.append(
            "Conservés tels quels alors qu'ils ressemblent à un patient : "
            + ", ".join(examen.balises_patient)
            + "."
        )
    return resultat


def _date_de_serie(jeu: Dataset) -> date:
    for mot_cle in ("SeriesDate", "StudyDate", "ContentDate", "AcquisitionDate"):
        texte = str(jeu.get(mot_cle, "") or "")
        if len(texte) == 8 and texte.isdigit():
            return datetime.strptime(texte, "%Y%m%d").date()
    return date.today()


def creer_references(
    chemins: Iterable[str | Path],
    noeud: str,
    noms_pris: Iterable[str] = (),
) -> ResultatImportDicom:
    """Décrit des séries par leurs seuls UID, sans copier de fichier ni de pixel dans l'archive.

    Sert pour une série qui reste sur un PACS. Aucune donnée identifiante n'est reprise.
    """
    examen = examiner(chemins)
    if not examen.series:
        raise ErreurDicom(
            f"Aucun fichier DICOM utilisable parmi la sélection ({examen.ignores} ignoré(s))."
        )
    resultat = ResultatImportDicom(ignores=examen.ignores)
    pris = set(noms_pris)
    for serie in examen.series:
        calcul = hashlib.sha256()
        avec_pixels = False
        uids = []
        for chemin in serie.chemins:
            jeu = _relire(chemin)
            uids.append(str(jeu.SOPInstanceUID))
            avec_pixels = _ajouter_pixels(calcul, jeu) or avec_pixels
        nom = nom_libre(f"reference_{serie.modalite}", pris)
        pris.add(nom)
        resultat.fichiers.append(
            Fichier(
                nom=nom,
                type_fichier="dicom",
                stockage=ReferenceDicom(
                    study_instance_uid=str(serie.entete.StudyInstanceUID),
                    series_instance_uid=serie.series_instance_uid,
                    sop_instance_uids=tuple(uids),
                    noeud=noeud,
                    date=_date_de_serie(serie.entete),
                    empreinte_pixels=calcul.hexdigest() if avec_pixels else None,
                ),
            )
        )
    return resultat


# --- Relecture ----------------------------------------------------------------------------


def lire_serie(
    chemin: Path, annule: Callable[[], bool] | None = None
) -> Iterator[Dataset]:
    """Relit une série du magasin un fichier à la fois : seul le fichier en cours reste en mémoire.

    `annule` est consulté avant chaque fichier : changer de série arrête la lecture en cours.
    """
    try:
        with zipfile.ZipFile(chemin_long(chemin)) as archive_zip:
            for nom in sorted(archive_zip.namelist()):
                if annule is not None and annule():
                    raise LectureAnnulee
                yield pydicom.dcmread(io.BytesIO(archive_zip.read(nom)))
    except FileNotFoundError as erreur:
        raise FichierManquant("La série est introuvable dans le magasin.") from erreur
    except (zipfile.BadZipFile, InvalidDicomError, EOFError) as erreur:
        raise ErreurDicom(f"Série illisible : {erreur}") from erreur


def lire_entete_de_serie(chemin: Path) -> Dataset | None:
    """Relit les balises du premier fichier d'une série, sans garder ses pixels."""
    try:
        with zipfile.ZipFile(chemin_long(chemin)) as archive_zip:
            noms = sorted(archive_zip.namelist())
            if not noms:
                return None
            return pydicom.dcmread(
                io.BytesIO(archive_zip.read(noms[0])), stop_before_pixels=True
            )
    except FileNotFoundError as erreur:
        raise FichierManquant("La série est introuvable dans le magasin.") from erreur
    except (zipfile.BadZipFile, InvalidDicomError, EOFError) as erreur:
        raise ErreurDicom(f"Série illisible : {erreur}") from erreur


def lire_jeux(chemin: Path) -> list[Dataset]:
    """Relit toute une série du magasin d'un coup : à réserver aux petites séries."""
    return list(lire_serie(chemin))
