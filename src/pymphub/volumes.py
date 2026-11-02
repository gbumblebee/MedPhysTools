"""Lecture de séries DICOM en volumes aux valeurs physiques, sans interface.

Toutes les formes passent par une même étape : chaque fichier donne des trames (une image, sa
position, sa pente et son ordonnée), puis les trames forment un volume (t, z, y, x).

- Scanner, TEP : `RescaleSlope` et `RescaleIntercept`, propres à chaque coupe.
- RT Dose : les entiers du fichier multipliés par `DoseGridScaling`, avec les coupes données
  par `GridFrameOffsetVector`.
- TEMP (NM) : multi-images, positions déduites du détecteur et de l'espacement entre coupes.
- Multi-images améliorés : tout est dans les groupes fonctionnels, par image.
- 4D : plusieurs images à une même position, ordonnées par leur position temporelle.
"""

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydicom.dataset import Dataset
from scipy.ndimage import affine_transform, map_coordinates

from pymphub.dicom import ErreurDicom, lire_jeux
from pymphub.page import Fichier

_TAILLE_MAX_OCTETS = 8 * 1024**3
_TOLERANCE_POSITION_MM = 1e-3
_TOLERANCE_ESPACEMENT = 0.01
_SEUIL_OBLIQUE = 0.99
_POIDS_RVB = (0.299, 0.587, 0.114)
_UNITES_ECHELLE = {"HU": "HU", "BQML": "Bq/mL", "US": "", "CNTS": "coups", "GY": "Gy"}
_UNITES_MODALITE = {"CT": "HU", "NM": "coups", "PT": "", "RTDOSE": "Gy"}
_UNITES_TEP = {
    "BQML": "Bq/mL",
    "CNTS": "coups",
    "GML": "SUV (g/mL)",
    "NONE": "",
    "PROPCNTS": "coups",
}
PLANS = ("axial", "sagittal", "coronal")
# Reperes de lecture : haut, bas, gauche et droite de chaque coupe, en convention radiologique.
ETIQUETTES_PLAN = {
    "axial": ("A", "P", "D", "G"),
    "sagittal": ("H", "B", "A", "P"),
    "coronal": ("H", "B", "D", "G"),
}


@dataclass
class _Trame:
    """Une image d'un fichier, avec de quoi la placer et la convertir."""

    pixels: np.ndarray
    position: np.ndarray | None
    orientation: np.ndarray | None
    espacement: tuple[float, float]
    pente: float = 1.0
    ordonnee: float = 0.0
    temps: float | None = None
    rang: int = 0


@dataclass
class Volume:
    """Un volume aux valeurs physiques, de forme (t, z, y, x).

    `affine` passe des indices (x, y, z, 1) aux coordonnées patient en mm, repère DICOM LPS.
    Sans géométrie connue, comme une radiographie, seul l'empilement des images a un sens.
    """

    donnees: np.ndarray
    affine: np.ndarray
    unite: str
    modalite: str
    cadre_reference: str | None = None
    geometrie_connue: bool = True
    inverse_monochrome: bool = False
    avertissements: tuple[str, ...] = ()
    temps: tuple[float, ...] = field(default_factory=tuple)

    @property
    def phases(self) -> int:
        return self.donnees.shape[0]

    @property
    def espacement(self) -> tuple[float, float, float]:
        """L'écart entre voxels selon x, y et z, en mm."""
        return tuple(float(np.linalg.norm(self.affine[:3, k])) for k in range(3))

    def valeur(self, t: int, z: int, y: int, x: int) -> float:
        return float(self.donnees[t, z, y, x])

    def vers_patient(self, x: float, y: float, z: float) -> np.ndarray:
        return (self.affine @ np.array([x, y, z, 1.0]))[:3]


# --- Lecture des trames -------------------------------------------------------------------


def _nombre(jeu: Dataset, mot_cle: str, defaut: float) -> float:
    valeur = jeu.get(mot_cle)
    if valeur is None or valeur == "":
        return defaut
    return float(valeur)


def _pixels(jeu: Dataset) -> np.ndarray:
    """Décode les pixels, compressés ou non. Un décodage impossible s'explique."""
    if "PixelData" not in jeu:
        raise ErreurDicom("Ce fichier ne contient pas d'image.")
    try:
        tableau = jeu.pixel_array
    except (ValueError, NotImplementedError, RuntimeError, AttributeError) as erreur:
        syntaxe = jeu.file_meta.get("TransferSyntaxUID", "inconnue")
        raise ErreurDicom(
            f"Image impossible à décoder (syntaxe {syntaxe}) : {erreur}"
        ) from erreur
    if int(jeu.get("SamplesPerPixel", 1)) == 3:
        if str(jeu.get("PhotometricInterpretation", "")).startswith("YBR"):
            from pydicom.pixels import convert_color_space  # noqa: PLC0415

            tableau = convert_color_space(
                tableau, str(jeu.PhotometricInterpretation), "RGB"
            )
        # Image en couleur : seule sa luminance peut se lire comme une valeur.
        tableau = tableau @ np.array(_POIDS_RVB)
    return tableau


def _temps_classique(jeu: Dataset) -> float | None:
    """La position temporelle d'un fichier d'une série 4D, par ordre de préférence."""
    for mot_cle in (
        "TemporalPositionIdentifier",
        "TemporalPositionIndex",
        "TriggerTime",
    ):
        valeur = jeu.get(mot_cle)
        if valeur not in (None, ""):
            return float(valeur)
    for mot_cle in ("AcquisitionTime", "ContentTime"):
        valeur = jeu.get(mot_cle)
        if valeur:
            texte = str(valeur)
            try:
                return (
                    float(texte[:2]) * 3600
                    + float(texte[2:4] or 0) * 60
                    + float(texte[4:] or 0)
                )
            except ValueError:
                continue
    return None


def _espacement(jeu: Dataset) -> tuple[float, float]:
    """L'écart entre lignes puis entre colonnes, en mm ; 1 si le fichier ne le dit pas."""
    for mot_cle in ("PixelSpacing", "ImagerPixelSpacing", "NominalScannedPixelSpacing"):
        valeur = jeu.get(mot_cle)
        if valeur is not None and len(valeur) == 2:
            return float(valeur[0]), float(valeur[1])
    return 1.0, 1.0


def _vecteur(jeu: Dataset, mot_cle: str, taille: int) -> np.ndarray | None:
    valeur = jeu.get(mot_cle)
    if valeur is None or len(valeur) != taille:
        return None
    return np.array([float(v) for v in valeur])


def _trames_classiques(jeu: Dataset) -> list[_Trame]:
    return [
        _Trame(
            _pixels(jeu),
            _vecteur(jeu, "ImagePositionPatient", 3),
            _vecteur(jeu, "ImageOrientationPatient", 6),
            _espacement(jeu),
            _nombre(jeu, "RescaleSlope", 1.0),
            _nombre(jeu, "RescaleIntercept", 0.0),
            _temps_classique(jeu),
            int(jeu.get("InstanceNumber", 0) or 0),
        )
    ]


def _trames_dose(jeu: Dataset) -> list[_Trame]:
    """RT Dose : dose = valeur stockée × DoseGridScaling, souvent des entiers sur 16 ou 32 bits.

    Les coupes se lisent dans `GridFrameOffsetVector`, relatif à la première image ou absolu :
    seul son écart à la première valeur compte.
    """
    pixels = _pixels(jeu)
    if "DoseGridScaling" not in jeu:
        raise ErreurDicom(
            "Ce fichier de dose n'a pas de DoseGridScaling : valeurs inconnues."
        )
    pente = float(jeu.DoseGridScaling)
    position = _vecteur(jeu, "ImagePositionPatient", 3)
    orientation = _vecteur(jeu, "ImageOrientationPatient", 6)
    espacement = _espacement(jeu)
    if pixels.ndim == 2:
        return [_Trame(pixels, position, orientation, espacement, pente)]
    decalages = jeu.get("GridFrameOffsetVector")
    if decalages is None or len(decalages) != pixels.shape[0]:
        raise ErreurDicom(
            "GridFrameOffsetVector ne correspond pas au nombre d'images de cette dose."
        )
    if position is None or orientation is None:
        raise ErreurDicom("Cette dose n'a pas de position dans le repère du patient.")
    normale = np.cross(orientation[:3], orientation[3:])
    premier = float(decalages[0])
    return [
        _Trame(
            pixels[rang],
            position + (float(decalage) - premier) * normale,
            orientation,
            espacement,
            pente,
        )
        for rang, decalage in enumerate(decalages)
    ]


def _premier(jeu: Dataset, mot_cle: str) -> Dataset | None:
    sequence = jeu.get(mot_cle)
    return sequence[0] if sequence else None


def _dans_les_groupes(
    groupes: Sequence[Dataset | None], mot_cle: str
) -> Dataset | None:
    for groupe in groupes:
        if groupe is not None:
            trouve = _premier(groupe, mot_cle)
            if trouve is not None:
                return trouve
    return None


def _trames_ameliorees(jeu: Dataset) -> list[_Trame]:
    """Multi-images améliorés : position, pente et temps se lisent par image."""
    pixels = _pixels(jeu)
    if pixels.ndim == 2:
        pixels = pixels[np.newaxis]
    commun = _premier(jeu, "SharedFunctionalGroupsSequence")
    par_image = jeu.get("PerFrameFunctionalGroupsSequence")
    trames = []
    for rang in range(pixels.shape[0]):
        propre = par_image[rang] if par_image and rang < len(par_image) else None
        groupes = (propre, commun)
        plan = _dans_les_groupes(groupes, "PlanePositionSequence")
        orientation = _dans_les_groupes(groupes, "PlaneOrientationSequence")
        mesures = _dans_les_groupes(groupes, "PixelMeasuresSequence")
        echelle = _dans_les_groupes(groupes, "PixelValueTransformationSequence")
        contenu = _dans_les_groupes(groupes, "FrameContentSequence")
        temps = None
        if contenu is not None:
            valeur = contenu.get("TemporalPositionIndex")
            if valeur is not None:
                temps = float(valeur)
        trames.append(
            _Trame(
                pixels[rang],
                None if plan is None else _vecteur(plan, "ImagePositionPatient", 3),
                None
                if orientation is None
                else _vecteur(orientation, "ImageOrientationPatient", 6),
                _espacement(mesures if mesures is not None else jeu),
                _nombre(echelle, "RescaleSlope", 1.0) if echelle is not None else 1.0,
                _nombre(echelle, "RescaleIntercept", 0.0)
                if echelle is not None
                else 0.0,
                temps,
                rang,
            )
        )
    return trames


def _pente_nm(jeu: Dataset) -> tuple[float, float]:
    reelle = _premier(jeu, "RealWorldValueMappingSequence")
    if reelle is not None and "RealWorldValueSlope" in reelle:
        return (
            float(reelle.RealWorldValueSlope),
            _nombre(reelle, "RealWorldValueIntercept", 0.0),
        )
    return _nombre(jeu, "RescaleSlope", 1.0), _nombre(jeu, "RescaleIntercept", 0.0)


def _trames_nm(jeu: Dataset) -> list[_Trame]:
    """TEMP et scintigraphie : positions tirées du détecteur, et vecteurs d'images.

    `SliceVector` donne la coupe de chaque image. `TimeSlotVector` ou `EnergyWindowVector`
    donnent la quatrième dimension d'un examen synchronisé ou à plusieurs fenêtres.
    """
    pixels = _pixels(jeu)
    if pixels.ndim == 2:
        pixels = pixels[np.newaxis]
    detecteur = _premier(jeu, "DetectorInformationSequence")
    source = detecteur if detecteur is not None else jeu
    position = _vecteur(source, "ImagePositionPatient", 3)
    orientation = _vecteur(source, "ImageOrientationPatient", 6)
    pente, ordonnee = _pente_nm(jeu)
    espacement = _espacement(jeu if "PixelSpacing" in jeu else source)
    nombre = pixels.shape[0]
    coupes = jeu.get("SliceVector")
    if coupes is None or len(coupes) != nombre:
        coupes = None
    temps_vecteur = None
    for mot_cle in ("TimeSlotVector", "EnergyWindowVector", "TimeSliceVector"):
        vecteur = jeu.get(mot_cle)
        if vecteur is not None and len(vecteur) == nombre and len(set(vecteur)) > 1:
            temps_vecteur = vecteur
            break
    pas = _nombre(jeu, "SpacingBetweenSlices", _nombre(jeu, "SliceThickness", 1.0))
    geometrie = position is not None and orientation is not None
    normale = np.cross(orientation[:3], orientation[3:]) if geometrie else None
    trames = []
    for rang in range(nombre):
        coupe = int(coupes[rang]) - 1 if coupes is not None else rang
        if geometrie and coupes is None and temps_vecteur is not None:
            # Sans vecteur de coupes, seules les images du premier temps avancent en z.
            coupe = rang // len(set(temps_vecteur))
        trames.append(
            _Trame(
                pixels[rang],
                position + coupe * pas * normale if geometrie else None,
                orientation,
                espacement,
                pente,
                ordonnee,
                float(temps_vecteur[rang]) if temps_vecteur is not None else None,
                rang,
            )
        )
    return trames


def _trames_multi_images(jeu: Dataset) -> list[_Trame]:
    """Multi-images sans vecteur : un film (angiographie, échographie) sans position propre."""
    pixels = _pixels(jeu)
    pente = _nombre(jeu, "RescaleSlope", 1.0)
    ordonnee = _nombre(jeu, "RescaleIntercept", 0.0)
    espacement = _espacement(jeu)
    return [
        _Trame(pixels[rang], None, None, espacement, pente, ordonnee, float(rang), rang)
        for rang in range(pixels.shape[0])
    ]


def _trames_du_fichier(jeu: Dataset) -> list[_Trame]:
    modalite = str(jeu.get("Modality", ""))
    if modalite == "RTDOSE":
        return _trames_dose(jeu)
    if "PerFrameFunctionalGroupsSequence" in jeu or (
        "SharedFunctionalGroupsSequence" in jeu
    ):
        return _trames_ameliorees(jeu)
    if modalite == "NM":
        return _trames_nm(jeu)
    if int(jeu.get("NumberOfFrames", 1) or 1) > 1:
        return _trames_multi_images(jeu)
    return _trames_classiques(jeu)


def _unite(jeu: Dataset) -> str:
    modalite = str(jeu.get("Modality", ""))
    if modalite == "RTDOSE":
        unites = str(jeu.get("DoseUnits", "GY")).upper()
        return {"GY": "Gy", "RELATIVE": "relatif"}.get(unites, unites)
    if modalite == "PT":
        unites = str(jeu.get("Units", "")).upper()
        return _UNITES_TEP.get(unites, unites)
    echelle = jeu.get("RescaleType")
    if echelle:
        return _UNITES_ECHELLE.get(str(echelle).upper(), str(echelle))
    reelle = _premier(jeu, "RealWorldValueMappingSequence")
    if reelle is not None:
        code = _premier(reelle, "MeasurementUnitsCodeSequence")
        if code is not None and code.get("CodeValue"):
            return str(code.CodeValue)
    return _UNITES_MODALITE.get(modalite, "")


# --- Assemblage ---------------------------------------------------------------------------


def _assembler_sans_geometrie(
    trames: list[_Trame], jeu: Dataset, avertissements: list[str]
) -> Volume:
    """Sans position, les images s'empilent dans l'ordre : ce n'est pas un volume physique."""
    trames.sort(key=lambda t: (t.temps if t.temps is not None else 0.0, t.rang))
    formes = {t.pixels.shape for t in trames}
    if len(formes) > 1:
        raise ErreurDicom("Les images de cette série n'ont pas toutes la même taille.")
    donnees = np.stack(
        [
            (t.pixels.astype(np.float64) * t.pente + t.ordonnee).astype(np.float32)
            for t in trames
        ]
    )[np.newaxis]
    ligne, colonne = trames[0].espacement
    affine = np.diag([colonne, ligne, 1.0, 1.0])
    if len(trames) > 1:
        avertissements.append("Images sans position : affichées dans leur ordre.")
    return Volume(
        donnees,
        affine,
        _unite(jeu),
        str(jeu.get("Modality", "OT")),
        None,
        False,
        str(jeu.get("PhotometricInterpretation", "")) == "MONOCHROME1",
        tuple(avertissements),
    )


def construire_volume(jeux: Sequence[Dataset]) -> Volume:
    """Construit un volume aux valeurs physiques. Lève `ErreurDicom` en l'expliquant."""
    jeux = [j for j in jeux if "PixelData" in j]
    if not jeux:
        raise ErreurDicom("Cette série ne contient pas d'image.")
    premier = jeux[0]
    lignes = int(premier.get("Rows", 0))
    colonnes = int(premier.get("Columns", 0))
    images = sum(int(j.get("NumberOfFrames", 1) or 1) for j in jeux)
    if lignes * colonnes * images * 4 > _TAILLE_MAX_OCTETS:
        raise ErreurDicom(
            f"Série trop grande pour la mémoire : {images} images de {colonnes} × {lignes}."
        )
    trames: list[_Trame] = []
    for jeu in jeux:
        trames.extend(_trames_du_fichier(jeu))
    avertissements: list[str] = []
    if any(t.position is None or t.orientation is None for t in trames):
        return _assembler_sans_geometrie(trames, premier, avertissements)

    orientation = trames[0].orientation
    if any(not np.allclose(t.orientation, orientation, atol=1e-3) for t in trames):
        raise ErreurDicom(
            "Les images de cette série n'ont pas toutes la même orientation."
        )
    formes = {t.pixels.shape for t in trames}
    if len(formes) > 1:
        raise ErreurDicom("Les images de cette série n'ont pas toutes la même taille.")
    ligne_dir, colonne_dir = orientation[:3], orientation[3:]
    normale = np.cross(ligne_dir, colonne_dir)
    groupes: dict[float, list[_Trame]] = {}
    for trame in trames:
        cle = round(float(trame.position @ normale) / _TOLERANCE_POSITION_MM)
        groupes.setdefault(cle, []).append(trame)
    cles = sorted(groupes)
    nombres = {len(groupes[c]) for c in cles}
    if len(nombres) > 1:
        raise ErreurDicom(
            "Série 4D incomplète : toutes les coupes n'ont pas le même nombre de phases."
        )
    phases = nombres.pop()
    forme = trames[0].pixels.shape
    donnees = np.empty((phases, len(cles), *forme), dtype=np.float32)
    temps_premiere: tuple[float, ...] = ()
    for z, cle in enumerate(cles):
        groupe = sorted(
            groupes[cle],
            key=lambda t: (t.temps if t.temps is not None else 0.0, t.rang),
        )
        if z == 0 and phases > 1:
            temps_premiere = tuple(
                float(t.temps if t.temps is not None else rang)
                for rang, t in enumerate(groupe)
            )
        for t, trame in enumerate(groupe):
            # En double pendant le calcul : un entier sur 32 bits dépasse la précision de 32 bits.
            donnees[t, z] = (
                trame.pixels.astype(np.float64) * trame.pente + trame.ordonnee
            )

    positions = np.array([c * _TOLERANCE_POSITION_MM for c in cles])
    if len(positions) > 1:
        ecarts = np.diff(positions)
        pas = float(np.median(ecarts))
        if np.max(np.abs(ecarts - pas)) > _TOLERANCE_ESPACEMENT * pas:
            avertissements.append(
                "L'espacement entre les coupes n'est pas régulier : le volume utilise sa valeur "
                "médiane, les positions intermédiaires sont approchées."
            )
    else:
        pas = _nombre(premier, "SliceThickness", 1.0) or 1.0
    espace_ligne, espace_colonne = trames[0].espacement
    origine = groupes[cles[0]][0].position
    affine = np.eye(4)
    affine[:3, 0] = ligne_dir * espace_colonne
    affine[:3, 1] = colonne_dir * espace_ligne
    affine[:3, 2] = normale * pas
    affine[:3, 3] = origine
    volume = Volume(
        donnees,
        affine,
        _unite(premier),
        str(premier.get("Modality", "OT")),
        str(premier.get("FrameOfReferenceUID", "")) or None,
        True,
        str(premier.get("PhotometricInterpretation", "")) == "MONOCHROME1",
        tuple(avertissements),
        temps_premiere,
    )
    if _est_oblique(volume):
        volume.avertissements = (
            *volume.avertissements,
            "Acquisition oblique : les coupes affichées suivent les axes de l'image, "
            "et non exactement ceux du patient.",
        )
    return volume


# --- Coupes -------------------------------------------------------------------------------


@dataclass(frozen=True)
class Repere:
    """Comment lire une coupe dans le tableau (z, y, x) : axes et sens."""

    axe_fixe: int
    axe_lignes: int
    axe_colonnes: int
    inverser_lignes: bool
    inverser_colonnes: bool


def _axes_patient(volume: Volume) -> dict[int, tuple[int, float]]:
    """Pour chaque axe du patient (L, P, S), l'axe du tableau le plus proche et son signe."""
    directions = volume.affine[:3, :3] / np.array(volume.espacement)
    restant = [0, 1, 2]
    resultat: dict[int, tuple[int, float]] = {}
    for axe_patient in np.argsort(-np.max(np.abs(directions), axis=1)):
        meilleur = max(restant, key=lambda k: abs(directions[axe_patient, k]))
        restant.remove(meilleur)
        # Les indices voxels x, y, z sont les axes 2, 1, 0 du tableau.
        resultat[int(axe_patient)] = (
            2 - meilleur,
            float(directions[axe_patient, meilleur]),
        )
    return resultat


def _est_oblique(volume: Volume) -> bool:
    return any(
        abs(signe) < _SEUIL_OBLIQUE for _axe, signe in _axes_patient(volume).values()
    )


def repere_du_plan(volume: Volume, plan: str) -> Repere:
    """Oriente une coupe en convention radiologique : la droite du patient à gauche.

    axial : A en haut, droite du patient à gauche. sagittal : tête en haut, A à gauche.
    coronal : tête en haut, droite du patient à gauche.
    """
    if not volume.geometrie_connue:
        return Repere(0, 1, 2, False, False)
    axes = _axes_patient(volume)
    lr, pa, si = axes[0], axes[1], axes[2]
    if plan == "axial":
        return Repere(si[0], pa[0], lr[0], pa[1] < 0, lr[1] < 0)
    if plan == "sagittal":
        return Repere(lr[0], si[0], pa[0], si[1] > 0, pa[1] < 0)
    if plan == "coronal":
        return Repere(pa[0], si[0], lr[0], si[1] > 0, lr[1] < 0)
    raise ErreurDicom(f"Plan « {plan} » inconnu.")


def coupe(volume: Volume, plan: str, indice: int, t: int = 0) -> np.ndarray:
    """Extrait une coupe : ses lignes vont du haut vers le bas de l'affichage."""
    repere = repere_du_plan(volume, plan)
    image = np.take(volume.donnees[t], indice, axis=repere.axe_fixe)
    # Après np.take, les deux axes restants gardent leur ordre relatif.
    if repere.axe_lignes > repere.axe_colonnes:
        image = image.T
    if repere.inverser_lignes:
        image = image[::-1, :]
    if repere.inverser_colonnes:
        image = image[:, ::-1]
    return image


def nombre_de_coupes(volume: Volume, plan: str) -> int:
    return volume.donnees.shape[1 + repere_du_plan(volume, plan).axe_fixe]


def taille_pixel(volume: Volume, plan: str) -> tuple[float, float]:
    """La taille d'un pixel de la coupe, en mm : hauteur puis largeur."""
    repere = repere_du_plan(volume, plan)
    pas = volume.espacement[::-1]
    return pas[repere.axe_lignes], pas[repere.axe_colonnes]


def voxel_du_pixel(
    volume: Volume, plan: str, indice: int, ligne: float, colonne: float
) -> tuple[float, float, float]:
    """Passe d'un pixel de la coupe à l'indice (z, y, x) du tableau."""
    repere = repere_du_plan(volume, plan)
    forme = volume.donnees.shape[1:]
    voxel = [0.0, 0.0, 0.0]
    voxel[repere.axe_fixe] = float(indice)
    voxel[repere.axe_lignes] = (
        forme[repere.axe_lignes] - 1 - ligne if repere.inverser_lignes else ligne
    )
    voxel[repere.axe_colonnes] = (
        forme[repere.axe_colonnes] - 1 - colonne
        if repere.inverser_colonnes
        else colonne
    )
    return tuple(voxel)


def pixel_du_voxel(
    volume: Volume, plan: str, voxel: Sequence[float]
) -> tuple[float, float, float]:
    """Passe d'un indice (z, y, x) au pixel de la coupe : indice de coupe, ligne, colonne."""
    repere = repere_du_plan(volume, plan)
    forme = volume.donnees.shape[1:]
    ligne = voxel[repere.axe_lignes]
    colonne = voxel[repere.axe_colonnes]
    if repere.inverser_lignes:
        ligne = forme[repere.axe_lignes] - 1 - ligne
    if repere.inverser_colonnes:
        colonne = forme[repere.axe_colonnes] - 1 - colonne
    return float(voxel[repere.axe_fixe]), float(ligne), float(colonne)


def fenetre_automatique(volume: Volume, t: int = 0) -> tuple[float, float]:
    """Propose un fenêtrage : du 1er au 99e percentile, sur un sous-échantillon pour aller vite."""
    donnees = volume.donnees[t]
    pas = max(1, round((donnees.size / 2_000_000) ** (1 / 3)))
    echantillon = donnees[::pas, ::pas, ::pas]
    bas, haut = np.nanpercentile(echantillon, (1, 99))
    if not bas < haut:
        bas, haut = float(np.nanmin(donnees)), float(np.nanmax(donnees))
    if not bas < haut:
        haut = bas + 1.0
    return float(bas), float(haut)


def reechantillonner_sur_coupe(
    source: Volume, reference: Volume, plan: str, indice: int, t: int = 0
) -> np.ndarray:
    """Lit un volume sur la grille d'une coupe d'un autre, par leurs coordonnées patient.

    Sert à superposer une dose à un scanner : leurs grilles diffèrent. Hors de la source, la
    valeur est `nan`.
    """
    forme_coupe = coupe(reference, plan, indice).shape
    lignes, colonnes = np.mgrid[0 : forme_coupe[0], 0 : forme_coupe[1]]
    repere = repere_du_plan(reference, plan)
    forme = reference.donnees.shape[1:]
    indices = [np.zeros_like(lignes, dtype=np.float64) for _ in range(3)]
    indices[repere.axe_fixe] += indice
    indices[repere.axe_lignes] = (
        forme[repere.axe_lignes] - 1 - lignes if repere.inverser_lignes else lignes
    ).astype(np.float64)
    indices[repere.axe_colonnes] = (
        forme[repere.axe_colonnes] - 1 - colonnes
        if repere.inverser_colonnes
        else colonnes
    ).astype(np.float64)
    points = np.stack(
        (
            indices[2].ravel(),
            indices[1].ravel(),
            indices[0].ravel(),
            np.ones(lignes.size),
        )
    )
    voxels = np.linalg.inv(source.affine) @ reference.affine @ points
    t_source = min(t, source.phases - 1)
    return map_coordinates(
        source.donnees[t_source],
        (voxels[2], voxels[1], voxels[0]),
        order=1,
        cval=np.nan,
        prefilter=False,
    ).reshape(lignes.shape)


def valeur_au_point(volume: Volume, point: np.ndarray, t: int = 0) -> float:
    """Lit un volume en un point du patient, en mm. Hors du volume, la valeur est `nan`."""
    x, y, z, _ = np.linalg.inv(volume.affine) @ np.array([*point, 1.0])
    return float(
        map_coordinates(
            volume.donnees[min(t, volume.phases - 1)],
            ([z], [y], [x]),
            order=1,
            cval=np.nan,
            prefilter=False,
        )[0]
    )


# --- Structures RT ------------------------------------------------------------------------


@dataclass(frozen=True)
class Structure:
    """Une structure d'un RTSTRUCT : ses contours sont en mm, dans le repère du patient."""

    numero: int
    nom: str
    couleur: tuple[int, int, int]
    contours: tuple[np.ndarray, ...]


_COULEUR_DEFAUT = (255, 196, 0)


def lire_structures(jeu: Dataset) -> tuple[Structure, ...]:
    """Relit les structures d'un RTSTRUCT. Lève `ErreurDicom` si ce n'en est pas un."""
    if str(jeu.get("Modality", "")) != "RTSTRUCT" or "ROIContourSequence" not in jeu:
        raise ErreurDicom("Ce fichier n'est pas un RTSTRUCT.")
    noms = {
        int(roi.ROINumber): str(roi.get("ROIName", f"ROI {roi.ROINumber}"))
        for roi in jeu.get("StructureSetROISequence", [])
    }
    structures = []
    for roi in jeu.ROIContourSequence:
        numero = int(roi.ReferencedROINumber)
        couleur = roi.get("ROIDisplayColor")
        contours = []
        for contour in roi.get("ContourSequence", []):
            donnees = contour.get("ContourData")
            if not donnees:
                continue
            contours.append(np.array([float(v) for v in donnees]).reshape(-1, 3))
        structures.append(
            Structure(
                numero,
                noms.get(numero, f"ROI {numero}"),
                tuple(int(c) for c in couleur) if couleur else _COULEUR_DEFAUT,
                tuple(contours),
            )
        )
    return tuple(structures)


def segments_sur_coupe(
    structure: Structure, volume: Volume, plan: str, indice: int
) -> list[np.ndarray]:
    """Trace une structure sur une coupe : des lignes brisées en (ligne, colonne) de pixel.

    Un contour dans le plan de la coupe se dessine en entier. Un contour qui la traverse donne
    les segments où il entre et sort de la structure, ce qui permet de lire en sagittal et en
    coronal des contours dessinés en axial.
    """
    inverse = np.linalg.inv(volume.affine)
    traces: list[np.ndarray] = []
    repere = repere_du_plan(volume, plan)
    forme = volume.donnees.shape[1:]
    for contour in structure.contours:
        if len(contour) < 2:
            continue
        points = (inverse @ np.c_[contour, np.ones(len(contour))].T)[:3].T
        # Indices voxels (x, y, z) vers les axes (z, y, x) du tableau.
        voxels = points[:, ::-1]
        distance = voxels[:, repere.axe_fixe] - indice
        lignes = voxels[:, repere.axe_lignes]
        colonnes = voxels[:, repere.axe_colonnes]
        if repere.inverser_lignes:
            lignes = forme[repere.axe_lignes] - 1 - lignes
        if repere.inverser_colonnes:
            colonnes = forme[repere.axe_colonnes] - 1 - colonnes
        plan_points = np.c_[lignes, colonnes]
        if np.all(np.abs(distance) <= 0.5):
            traces.append(np.vstack((plan_points, plan_points[:1])))
            continue
        # Points où le contour fermé traverse le plan.
        suivants = np.roll(np.arange(len(distance)), -1)
        traverses = []
        for a, b in zip(range(len(distance)), suivants):
            if (distance[a] < 0) != (distance[b] < 0):
                fraction = distance[a] / (distance[a] - distance[b])
                traverses.append(
                    plan_points[a] + fraction * (plan_points[b] - plan_points[a])
                )
        if len(traverses) < 2:
            continue
        traverses_tableau = np.array(traverses)
        principal = np.argmax(np.ptp(traverses_tableau, axis=0))
        tries = traverses_tableau[np.argsort(traverses_tableau[:, principal])]
        # Le long d'une droite, on entre puis on sort de la structure, deux à deux.
        for debut in range(0, len(tries) - 1, 2):
            traces.append(tries[debut : debut + 2])
    return traces


def volume_pour_structures(structures: Sequence[Structure]) -> Volume:
    """Prépare un volume vide autour de structures, pour les voir sans leur image."""
    points = [c for s in structures for c in s.contours]
    if not points:
        raise ErreurDicom("Ce RTSTRUCT ne contient aucun contour.")
    tous = np.vstack(points)
    marge = 10.0
    bas = tous.min(axis=0) - marge
    haut = tous.max(axis=0) + marge
    pas = np.maximum((haut - bas) / 128.0, 0.5)
    # Les contours sont souvent dessinés coupe à coupe : on garde leur espacement si possible.
    z = np.unique(np.round(tous[:, 2], 3))
    if len(z) > 1:
        pas[2] = float(np.median(np.diff(z)))
    taille = np.ceil((haut - bas) / pas).astype(int) + 1
    affine = np.diag([pas[0], pas[1], pas[2], 1.0])
    affine[:3, 3] = bas
    return Volume(
        np.zeros((1, taille[2], taille[1], taille[0]), dtype=np.float32),
        affine,
        "",
        "RTSTRUCT",
        avertissements=(
            "Aucune image dans ce RTSTRUCT : les structures s'affichent seules.",
        ),
    )


# --- Vue 3D, par le calcul seul -----------------------------------------------------------


@dataclass(frozen=True)
class Volume3D:
    """Un volume réduit et isotrope : il suffit à une vue 3D, et se tourne vite."""

    donnees: np.ndarray
    pas_mm: float
    centre_patient: np.ndarray


def preparer_3d(volume: Volume, t: int = 0, taille: int = 96) -> Volume3D:
    """Réduit un volume à un cube, avec les axes du patient : (S, P, L), tête en premier.

    Sans OpenGL : tout le calcul reste sur le processeur, sur un petit volume.
    """
    etendue = np.array(volume.donnees.shape[1:][::-1]) * np.array(volume.espacement)
    # La diagonale : le volume tient dans le cube sous tous les angles, sans être coupé.
    pas = float(np.linalg.norm(etendue) / taille)
    # Du cube (s, p, l) en mm, centré, vers les voxels (z, y, x) du tableau.
    centre_voxel = (np.array(volume.donnees.shape[1:][::-1]) - 1) / 2
    centre_patient = volume.vers_patient(*centre_voxel)
    inverse = np.linalg.inv(volume.affine)[:3, :3]
    cube_vers_lps = np.array(
        [[0.0, 0.0, pas], [0.0, pas, 0.0], [-pas, 0.0, 0.0]]
    )  # indices (s, p, l) : s descend depuis la tête.
    vers_xyz = inverse @ cube_vers_lps
    vers_zyx = vers_xyz[::-1]
    centre_cube = np.array([(taille - 1) / 2] * 3)
    decalage = centre_voxel[::-1] - vers_zyx @ centre_cube
    reduit = affine_transform(
        volume.donnees[t],
        vers_zyx,
        offset=decalage,
        output_shape=(taille, taille, taille),
        order=1,
        cval=np.nan,
        prefilter=False,
    )
    return Volume3D(
        np.nan_to_num(reduit, nan=float(np.nanmin(volume.donnees[t]))),
        pas,
        centre_patient,
    )


def _rotation(azimut: float, elevation: float) -> np.ndarray:
    a, e = math.radians(azimut), math.radians(elevation)
    tourne_s = np.array(
        [[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]
    )
    tourne_l = np.array(
        [[math.cos(e), -math.sin(e), 0], [math.sin(e), math.cos(e), 0], [0, 0, 1]]
    )
    return tourne_l @ tourne_s


def rendre_3d(
    cube: Volume3D,
    azimut: float,
    elevation: float,
    bas: float,
    haut: float,
    mode: str = "transparence",
) -> np.ndarray:
    """Rend le cube vu de face, tourné : une image de 0 à 1, sur le processeur seul.

    `mip` garde le maximum le long de chaque rayon. `transparence` compose du fond vers l'avant,
    avec une opacité qui suit la valeur : ce qui est faible laisse voir ce qui est derrière.
    """
    donnees = np.clip((cube.donnees - bas) / max(haut - bas, 1e-12), 0.0, 1.0)
    taille = donnees.shape[0]
    centre = np.array([(taille - 1) / 2] * 3)
    matrice = _rotation(azimut, elevation)
    tourne = affine_transform(
        donnees,
        matrice,
        offset=centre - matrice @ centre,
        order=1,
        cval=0.0,
        prefilter=False,
    )
    # La profondeur est l'axe p (A → P) : on regarde le patient de face.
    if mode == "mip":
        return tourne.max(axis=1)
    image = np.zeros((taille, taille), dtype=np.float32)
    transmis = np.ones((taille, taille), dtype=np.float32)
    for profondeur in range(taille):
        tranche = tourne[:, profondeur, :]
        opacite = np.clip(tranche * 0.18, 0.0, 1.0)
        image += transmis * opacite * tranche
        transmis *= 1.0 - opacite
    return np.clip(image, 0.0, 1.0)


def projeter_3d(
    cube: Volume3D, points_patient: np.ndarray, azimut: float, elevation: float
) -> np.ndarray:
    """Place des points du patient sur l'image 3D : colonnes (ligne, colonne), pour tracer."""
    taille = cube.donnees.shape[0]
    centre = (taille - 1) / 2
    relatifs = (points_patient - cube.centre_patient) / cube.pas_mm
    # LPS vers (s, p, l), comme dans `preparer_3d`.
    indices = np.c_[-relatifs[:, 2], relatifs[:, 1], relatifs[:, 0]]
    # Le rendu lit le volume par `matrice` : un point suit donc la rotation inverse.
    tournes = indices @ _rotation(azimut, elevation)
    return np.c_[tournes[:, 0] + centre, tournes[:, 2] + centre]


# --- Ce qu'il faut afficher pour une série ------------------------------------------------


@dataclass
class ContenuSerie:
    """Une série prête à afficher, avec ce qui partage son repère : image, dose, structures."""

    volume: Volume
    structures: tuple[Structure, ...] = ()
    dose: Volume | None = None
    balises: list[tuple[str, str]] = field(default_factory=list)
    remarques: tuple[str, ...] = ()


_SANS_IMAGE = ("RTSTRUCT", "RTPLAN", "RTRECORD", "SR", "PR", "REG", "KO")


def assembler_contenu(
    fichier: Fichier,
    voisins: Sequence[Fichier],
    emplacement: Callable[[Fichier], Path | None],
) -> ContenuSerie:
    """Prépare l'affichage d'une série : seuls les fichiers de même repère se superposent.

    Choisir une dose ou des structures les montre sur l'image de même repère, si la page en
    contient une. Deux séries de repères différents ne sont jamais superposées : leurs
    coordonnées ne se correspondent pas.
    """

    def jeux_de(cible: Fichier) -> list[Dataset]:
        chemin = emplacement(cible)
        if chemin is None:
            raise ErreurDicom("Cette série n'est pas disponible sur cet ordinateur.")
        return lire_jeux(chemin)

    if fichier.dicom is None:
        raise ErreurDicom("Ce fichier n'a pas de copie DICOM à afficher.")
    modalite = fichier.dicom.modalite
    cadre = fichier.dicom.frame_of_reference_uid
    proches = [
        v
        for v in voisins
        if v.nom != fichier.nom
        and v.dicom is not None
        and cadre is not None
        and v.dicom.frame_of_reference_uid == cadre
    ]
    images = [v for v in proches if v.dicom.modalite not in (*_SANS_IMAGE, "RTDOSE")]
    doses = [v for v in proches if v.dicom.modalite == "RTDOSE"]
    remarques: list[str] = []

    jeux_choisis = jeux_de(fichier)
    if not jeux_choisis:
        raise ErreurDicom("Cette série est vide.")
    balises = balises_principales(jeux_choisis[0])

    structures: list[Structure] = []
    a_lire = [fichier] if modalite == "RTSTRUCT" else []
    a_lire += [v for v in proches if v.dicom.modalite == "RTSTRUCT"]
    for source in a_lire:
        for jeu in jeux_choisis if source is fichier else jeux_de(source):
            structures.extend(lire_structures(jeu))

    dose: Volume | None = None
    if modalite == "RTDOSE" and images:
        volume = construire_volume(jeux_de(images[0]))
        dose = construire_volume(jeux_choisis)
        remarques.append(
            f"Dose affichée sur l'image « {images[0].nom} », de même repère."
        )
    elif modalite == "RTSTRUCT":
        if images:
            volume = construire_volume(jeux_de(images[0]))
            remarques.append(f"Structures affichées sur l'image « {images[0].nom} ».")
        else:
            volume = volume_pour_structures(structures)
    else:
        volume = construire_volume(jeux_choisis)
        if doses and modalite != "RTDOSE":
            dose = construire_volume(jeux_de(doses[0]))
            remarques.append(f"Dose superposée : « {doses[0].nom} », de même repère.")
    return ContenuSerie(volume, tuple(structures), dose, balises, tuple(remarques))


# --- Balises principales ------------------------------------------------------------------

_BALISES_AFFICHEES: tuple[tuple[str, str], ...] = (
    ("Modality", "Modalité"),
    ("Manufacturer", "Fabricant"),
    ("ManufacturerModelName", "Modèle"),
    ("SoftwareVersions", "Version logicielle"),
    ("Rows", "Lignes"),
    ("Columns", "Colonnes"),
    ("NumberOfFrames", "Images"),
    ("PixelSpacing", "Taille de pixel (mm)"),
    ("SliceThickness", "Épaisseur de coupe (mm)"),
    ("SpacingBetweenSlices", "Espacement entre coupes (mm)"),
    ("KVP", "kV"),
    ("XRayTubeCurrent", "mA"),
    ("ExposureTime", "Temps d'exposition (ms)"),
    ("ConvolutionKernel", "Filtre"),
    ("RescaleType", "Type d'échelle"),
    ("RescaleSlope", "Pente"),
    ("RescaleIntercept", "Ordonnée"),
    ("Units", "Unités"),
    ("CorrectedImage", "Corrections"),
    ("DecayCorrection", "Correction de décroissance"),
    ("DoseUnits", "Unité de dose"),
    ("DoseType", "Type de dose"),
    ("DoseSummationType", "Sommation"),
    ("DoseGridScaling", "DoseGridScaling"),
    ("BitsStored", "Bits stockés"),
    ("MagneticFieldStrength", "Champ (T)"),
    ("EchoTime", "TE (ms)"),
    ("RepetitionTime", "TR (ms)"),
    ("SeriesInstanceUID", "UID de la série"),
    ("FrameOfReferenceUID", "UID du repère"),
)


def balises_principales(jeu: Dataset) -> list[tuple[str, str]]:
    """Les balises techniques à afficher : une liste fermée, qui n'identifie personne.

    Un fichier conservé tel quel ne montre donc pas non plus l'identité d'un patient.
    """
    resultat = []
    for mot_cle, libelle in _BALISES_AFFICHEES:
        valeur = jeu.get(mot_cle)
        if valeur not in (None, ""):
            resultat.append((libelle, str(valeur)))
    syntaxe = getattr(getattr(jeu, "file_meta", None), "TransferSyntaxUID", None)
    if syntaxe is not None:
        resultat.append(("Syntaxe de transfert", syntaxe.name))
    return resultat
