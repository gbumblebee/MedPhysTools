"""Modèle d'une page : le contenu de `page.json`.

Une page correspond à un CQ. Elle contient des grandeurs, repérées par un nom unique,
et des onglets qui les organisent. Les onglets ne font que référencer les grandeurs :
réorganiser les onglets ne change donc pas la façon de relire une mesure.

Le nom d'une colonne est unique dans son tableau. Une colonne se désigne donc par
« tableau.colonne », par exemple `decroissance.heure`.

Pour ajouter un type d'onglet, il suffit de décrire ses données dans une classe
dérivée de `Onglet` et d'appeler `enregistrer_type_onglet`. Un type inconnu de cette
version du logiciel est conservé tel quel à l'enregistrement.
"""

import math
import statistics
from datetime import date
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    field_validator,
    model_validator,
)

from pymphub.disque import verifier_nom_windows
from pymphub.incertitudes import (
    Budget,
    Composante,
    LigneBudget,
    budget_fige,
    etablir_budget,
)
from pymphub.unites import (
    UniteInvalide,
    UnitesIncompatibles,
    sont_compatibles,
    verifier_unite,
)

VERSION_SCHEMA = 2
MOTIF_ID_PAGE = r"^\d{4}-\d{2}-\d{2}_[a-z0-9]+(-[a-z0-9]+)*_\d{4,}$"
# Format : site.service.modalite.fabricant-modele.numero-serie
MOTIF_MACHINE = r"^[^.\s]+\.[^.\s]+\.[^.\s]+\.[^.\s]+-[^.\s]+\.[^.\s]+$"


def _verifier_nom(nom: str) -> str:
    """Un nom doit pouvoir servir dans un script et dans une formule."""
    if not nom.isidentifier():
        raise ValueError(
            f"Nom « {nom} » invalide : lettres, chiffres et _ seulement, sans espace "
            "et sans chiffre au début."
        )
    return nom


def _verifier_unite(unite: str) -> str:
    verification = verifier_unite(unite)
    if not verification.valide:
        raise UniteInvalide(verification.message)
    return unite


def _verifier_chemin_relatif(chemin: str) -> str:
    """Un chemin stocké dans une page reste à l'intérieur de son dossier."""
    morceaux = PurePosixPath(chemin)
    if not chemin or "\\" in chemin or morceaux.is_absolute() or ".." in morceaux.parts:
        raise ValueError(
            f"Chemin « {chemin} » invalide : relatif à la page et sans « .. »."
        )
    return chemin


def _verifier_csv_brut(chemin: str) -> str:
    _verifier_chemin_relatif(chemin)
    morceaux = PurePosixPath(chemin)
    for partie in morceaux.parts:
        verifier_nom_windows(partie)
    if morceaux.parts[0] != "brut" or morceaux.suffix.lower() != ".csv":
        raise ValueError(
            f"Un tableau est stocké dans brut/*.csv, pas dans « {chemin} »."
        )
    return chemin


def _verifier_piece_jointe(chemin: str) -> str:
    _verifier_chemin_relatif(chemin)
    for partie in PurePosixPath(chemin).parts:
        verifier_nom_windows(partie)
    if PurePosixPath(chemin).parts[0] != "pieces_jointes":
        raise ValueError(
            f"Une pièce jointe est stockée dans pieces_jointes/, pas dans « {chemin} »."
        )
    return chemin


Nom = Annotated[str, AfterValidator(_verifier_nom)]
Unite = Annotated[str, AfterValidator(_verifier_unite)]
Empreinte = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
UidDicom = Annotated[str, Field(pattern=r"^[0-9]+(\.[0-9]+)*$", max_length=64)]
CheminPieceJointe = Annotated[str, AfterValidator(_verifier_piece_jointe)]
Nombre = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class _Modele(BaseModel):
    """Base des modèles : immuables, et tout champ inconnu est refusé."""

    model_config = ConfigDict(frozen=True, extra="forbid")


# --- Grandeurs ------------------------------------------------------------------


class Scalaire(_Modele):
    """Une valeur numérique, avec son unité et son incertitude, toutes deux obligatoires."""

    nature: Literal["scalaire"] = "scalaire"
    nom: Nom
    valeur: Nombre
    unite: Unite
    composantes: tuple[Composante, ...]
    horodatage: AwareDatetime | None = None
    commentaire: str = ""
    # Valeurs brutes de mesures répétées, dont `valeur` est la moyenne (format 2).
    repetitions: tuple[Nombre, ...] | None = None

    @model_validator(mode="after")
    def _verifier_budget(self) -> "Scalaire":
        self.budget()
        if self.repetitions is not None:
            if len(self.repetitions) < 2:
                raise ValueError("Il faut au moins deux valeurs répétées.")
            if not math.isclose(
                self.valeur,
                statistics.fmean(self.repetitions),
                rel_tol=1e-9,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    f"« {self.nom} » : la valeur doit être la moyenne de ses répétitions."
                )
        return self

    def budget(self) -> Budget:
        """Établit le budget d'incertitude de la valeur."""
        return etablir_budget(self.valeur, self.unite, self.composantes)


class Identifiant(_Modele):
    """Un texte conservé à l'identique : numéro de série, de lot, de certificat…"""

    nature: Literal["identifiant"] = "identifiant"
    nom: Nom
    valeur: str = Field(min_length=1)
    commentaire: str = ""


class ColonneNumerique(_Modele):
    """Une colonne de valeurs numériques, dont l'unité est obligatoire.

    `composantes` s'applique à toutes les cellules de la colonne. Une incertitude
    propre à chaque cellule se déclare par une `ColonneIncertitude`.
    """

    nature: Literal["numerique"] = "numerique"
    nom: Nom
    unite: Unite
    composantes: tuple[Composante, ...] = ()
    commentaire: str = ""


class ColonneIdentifiant(_Modele):
    """Une colonne de textes, jamais convertis en nombres."""

    nature: Literal["identifiant"] = "identifiant"
    nom: Nom
    commentaire: str = ""


class ColonneIncertitude(_Modele):
    """Une colonne donnant, cellule par cellule, une composante d'une colonne numérique.

    Les champs ont le même sens que dans `Composante`. La cellule en donne la valeur.
    """

    nature: Literal["incertitude"] = "incertitude"
    nom: Nom
    composante_de: Nom
    type: Literal["A", "B"]
    unite: Unite | None = None
    relative: bool = False
    loi: Literal["normale", "rectangulaire", "triangulaire"] = "normale"
    k: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    ddl: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    source: str = Field(min_length=1)
    erreur_commune: str | None = Field(default=None, min_length=1)
    commentaire: str = ""

    @model_validator(mode="after")
    def _verifier_composante(self) -> "ColonneIncertitude":
        self.composante(0.0)
        return self

    def composante(self, valeur_cellule: float) -> Composante:
        """Construit la composante correspondant à une cellule."""
        return Composante(
            type=self.type,
            valeur=valeur_cellule,
            unite=self.unite,
            relative=self.relative,
            loi=self.loi,
            k=self.k,
            ddl=self.ddl,
            source=self.source,
            erreur_commune=self.erreur_commune,
        )


Colonne = Annotated[
    ColonneNumerique | ColonneIdentifiant | ColonneIncertitude,
    Field(discriminator="nature"),
]


class Tableau(_Modele):
    """Un tableau de valeurs, stocké en CSV dans `brut/` et décrit ici."""

    nature: Literal["tableau"] = "tableau"
    nom: Nom
    fichier: Annotated[str, AfterValidator(_verifier_csv_brut)]
    colonnes: tuple[Colonne, ...] = Field(min_length=1)
    commentaire: str = ""

    @model_validator(mode="after")
    def _verifier_incertitudes(self) -> "Tableau":
        noms = [colonne.nom for colonne in self.colonnes]
        doublons = sorted({nom for nom in noms if noms.count(nom) > 1})
        if doublons:
            raise ValueError(
                f"Colonnes répétées dans le tableau « {self.nom} » : {', '.join(doublons)}."
            )

        numeriques = {
            c.nom: c for c in self.colonnes if isinstance(c, ColonneNumerique)
        }
        cellules = [c for c in self.colonnes if isinstance(c, ColonneIncertitude)]

        for colonne in cellules:
            cible = numeriques.get(colonne.composante_de)
            if cible is None:
                raise ValueError(
                    f"La colonne « {colonne.nom} » se rapporte à « {colonne.composante_de} », "
                    "qui n'est pas une colonne numérique de ce tableau."
                )
            if colonne.unite is not None and not sont_compatibles(
                colonne.unite, cible.unite
            ):
                raise UnitesIncompatibles(
                    f"Colonne « {colonne.nom} » en {colonne.unite}, "
                    f"incompatible avec « {cible.nom} » en {cible.unite}."
                )

        avec_cellules = {c.composante_de for c in cellules}
        for colonne in numeriques.values():
            if not colonne.composantes and colonne.nom not in avec_cellules:
                raise ValueError(
                    f"La colonne « {colonne.nom} » n'a pas d'incertitude déclarée."
                )
            for composante in colonne.composantes:
                if not composante.relative and not sont_compatibles(
                    composante.unite, colonne.unite
                ):
                    raise UnitesIncompatibles(
                        f"Composante « {composante.source} » en {composante.unite}, "
                        f"incompatible avec la colonne « {colonne.nom} » en {colonne.unite}."
                    )
        return self


class Copie(_Modele):
    """Fichier copié dans le magasin `objets/` de l'archive."""

    mode: Literal["copie"] = "copie"
    empreinte: Empreinte
    nom_origine: str = Field(min_length=1)


class ReferenceFichier(_Modele):
    """Fichier laissé à son emplacement : disque ou partage réseau."""

    mode: Literal["reference_fichier"] = "reference_fichier"
    chemin: str = Field(min_length=1)
    empreinte: Empreinte
    taille: int = Field(ge=0)
    date: AwareDatetime


class ReferenceDicom(_Modele):
    """Série DICOM identifiée par ses UID, jamais par un identifiant patient.

    L'empreinte des pixels est calculée à l'analyse : elle peut donc être absente.
    """

    mode: Literal["reference_dicom"] = "reference_dicom"
    study_instance_uid: UidDicom
    series_instance_uid: UidDicom
    sop_instance_uids: tuple[UidDicom, ...] = Field(min_length=1)
    noeud: str = Field(min_length=1)
    date: date
    description: str = ""
    empreinte_pixels: Empreinte | None = None


class Fichier(_Modele):
    """Un fichier brut : DICOM, image, export constructeur."""

    nature: Literal["fichier"] = "fichier"
    nom: Nom
    stockage: Annotated[
        Copie | ReferenceFichier | ReferenceDicom, Field(discriminator="mode")
    ]
    type_fichier: str = ""
    commentaire: str = ""


class LigneDerivee(_Modele):
    """Une ligne du budget d'un calcul, dans l'unité de son résultat."""

    source: str
    type: str
    loi: str
    incertitude_type: float = Field(ge=0, allow_inf_nan=False)
    ddl: float | None = None
    contribution_pourcent: float = Field(ge=0, allow_inf_nan=False)


class GrandeurDerivee(_Modele):
    """Le résultat d'un calcul sur d'autres grandeurs, avec sa formule.

    Les valeurs brutes restent des grandeurs à part : une dérivée ne les remplace jamais.
    Son résultat est figé à l'enregistrement. `ddl` à None signifie des degrés de liberté
    infinis. Une composante d'erreur commune y apparaît une seule fois.
    """

    nature: Literal["derivee"] = "derivee"
    nom: Nom
    formule: str = Field(min_length=1)
    valeur: Nombre
    unite: Unite
    incertitude_type: float = Field(ge=0, allow_inf_nan=False)
    ddl: float | None = Field(default=None, gt=0)
    budget_calcul: tuple[LigneDerivee, ...] = ()
    commentaire: str = ""

    def budget(self) -> Budget:
        """Le budget enregistré, sans refaire le calcul."""
        return budget_fige(
            self.valeur,
            self.unite,
            self.incertitude_type,
            self.ddl,
            [LigneBudget(**ligne.model_dump()) for ligne in self.budget_calcul],
        )


class ColonneCalculee(_Modele):
    """Une colonne calculée ligne par ligne à partir de colonnes de tableaux.

    Elle s'affiche dans chaque tableau de `tableaux`, mais reste une grandeur à part : les
    fichiers CSV des valeurs brutes ne changent jamais. Une ligne sans résultat vaut None.
    Le budget d'une ligne se refait à la demande, à partir de la formule.
    """

    nature: Literal["colonne_calculee"] = "colonne_calculee"
    nom: Nom
    formule: str = Field(min_length=1)
    unite: Unite
    tableaux: tuple[Nom, ...] = Field(min_length=1)
    valeurs: tuple[Nombre | None, ...] = Field(min_length=1)
    incertitudes_types: tuple[Annotated[float, Field(ge=0)] | None, ...]
    commentaire: str = ""

    @model_validator(mode="after")
    def _verifier_longueurs(self) -> "ColonneCalculee":
        if len(self.valeurs) != len(self.incertitudes_types):
            raise ValueError(
                f"Colonne « {self.nom} » : autant de valeurs que d'incertitudes attendues."
            )
        return self


class ParametreAjuste(_Modele):
    """Un coefficient d'ajustement, dans l'unité que lui donnent les colonnes ajustées."""

    nom: Nom
    valeur: Nombre
    incertitude_type: float = Field(ge=0, allow_inf_nan=False)
    unite: Unite


class Ajustement(_Modele):
    """Un modèle ajusté sur deux colonnes d'un tableau.

    `correlation` est la matrice de corrélation des coefficients, dans leur ordre. Tout calcul
    ultérieur repart de ces valeurs. `courbe` nomme le tableau qui garde la fonction
    suréchantillonnée : il sert seulement à la retracer. `empreinte_donnees` permet de voir
    que les points ont changé depuis l'ajustement.
    """

    nature: Literal["ajustement"] = "ajustement"
    nom: Nom
    modele: str = Field(min_length=1)
    formule: str = Field(min_length=1)
    tableau: Nom
    x: Nom
    y: Nom
    unite_x: Unite
    unite_y: Unite
    x_min: Nombre
    x_max: Nombre
    points: int = Field(gt=0)
    parametres: tuple[ParametreAjuste, ...] = Field(min_length=1)
    correlation: tuple[tuple[float, ...], ...]
    chi2_reduit: float | None = None
    methode: str = Field(min_length=1)
    empreinte_donnees: Empreinte
    courbe: Nom
    commentaire: str = ""

    @model_validator(mode="after")
    def _verifier_correlation(self) -> "Ajustement":
        taille = len(self.parametres)
        if len(self.correlation) != taille or any(
            len(ligne) != taille for ligne in self.correlation
        ):
            raise ValueError(
                f"Ajustement « {self.nom} » : la matrice de corrélation doit être "
                f"{taille} × {taille}."
            )
        return self

    def covariance(self) -> tuple[tuple[float, ...], ...]:
        """Refait la matrice de covariance des coefficients, à partir de ce qui est gardé."""
        u = [parametre.incertitude_type for parametre in self.parametres]
        return tuple(
            tuple(r * u[i] * u[j] for j, r in enumerate(ligne))
            for i, ligne in enumerate(self.correlation)
        )


class Indicateur(_Modele):
    """Un résultat lu sur une courbe : extremum, moyenne, intégrale ou dérivée.

    `source` dit s'il vient des points du tableau ou de la fonction ajustée. `de` et `a`
    donnent l'intervalle utilisé. `position` est l'abscisse d'un extremum, ou celle demandée
    pour une dérivée.
    """

    nature: Literal["indicateur"] = "indicateur"
    nom: Nom
    methode: Literal["minimum", "maximum", "moyenne", "integrale", "derivee"]
    source: Literal["points", "ajustement"]
    tableau: Nom
    x: Nom
    y: Nom
    ajustement: Nom | None = None
    de: Nombre
    a: Nombre
    position: Nombre | None = None
    valeur: Nombre
    incertitude_type: float = Field(ge=0, allow_inf_nan=False)
    unite: Unite
    empreinte_donnees: Empreinte
    commentaire: str = ""

    def budget(self) -> Budget:
        """Le résultat enregistré, comme une mesure indépendante pour un calcul."""
        return budget_fige(self.valeur, self.unite, self.incertitude_type, None, ())


Grandeur = Annotated[
    Scalaire
    | Identifiant
    | Tableau
    | Fichier
    | GrandeurDerivee
    | ColonneCalculee
    | Ajustement
    | Indicateur,
    Field(discriminator="nature"),
]


# --- Pièces jointes et annotations ----------------------------------------------


class PieceJointe(_Modele):
    """Photo, PDF ou note scannée de `pieces_jointes/`, hors de Git."""

    chemin: CheminPieceJointe
    empreinte: Empreinte
    commentaire: str = ""


class Annotation(_Modele):
    """Annotation d'une photo, enregistrée à part : l'image n'est jamais modifiée.

    Les coordonnées vont de 0 à 1 : elles ne dépendent pas de la résolution de l'image.
    """

    forme: Literal["point", "fleche", "rectangle", "ellipse", "trace"]
    points: tuple[tuple[float, float], ...] = Field(min_length=1)
    texte: str = ""

    @field_validator("points")
    @classmethod
    def _verifier_points(
        cls, points: tuple[tuple[float, float], ...]
    ) -> tuple[tuple[float, float], ...]:
        if not all(0 <= x <= 1 and 0 <= y <= 1 for x, y in points):
            raise ValueError("Les coordonnées d'une annotation vont de 0 à 1.")
        return points


class PhotoAnnotee(_Modele):
    """Une pièce jointe affichée dans un onglet de notes, avec ses annotations."""

    chemin: CheminPieceJointe
    legende: str = ""
    annotations: tuple[Annotation, ...] = ()


# --- Onglets --------------------------------------------------------------------


class Onglet(BaseModel):
    """Un onglet : base de tous les types, et forme d'un type inconnu.

    Les champs supplémentaires sont conservés, pour qu'une version plus ancienne du
    logiciel ne perde pas les données d'une version plus récente.
    """

    model_config = ConfigDict(frozen=True, extra="allow")

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    titre: str = ""

    def grandeurs_referencees(self) -> list[str]:
        """Grandeurs (`nom`) ou colonnes (`tableau.colonne`) dont dépend cet onglet."""
        return []

    def pieces_jointes_referencees(self) -> list[str]:
        """Chemins des pièces jointes dont dépend cet onglet."""
        return []


class OngletDonnees(Onglet):
    """Un onglet qui affiche des grandeurs de la page."""

    grandeurs: tuple[str, ...] = ()

    def grandeurs_referencees(self) -> list[str]:
        return list(self.grandeurs)


class ReglagesGraphe(_Modele):
    """Ce qu'un onglet de graphes trace. Le facteur k n'en fait pas partie : il s'affiche.

    Les colonnes sont celles d'un seul tableau : leurs lignes se correspondent. Un nom
    absent n'invalide pas la page, pour que renommer une colonne ne bloque aucune saisie :
    le graphe demande alors de choisir une autre colonne.
    """

    tableau: str = Field(min_length=1)
    mode: Literal["2d", "3d"] = "2d"
    x: str = Field(min_length=1)
    y: tuple[str, ...] = Field(min_length=1)
    z: str | None = None
    echelle_x: Literal["lineaire", "log"] = "lineaire"
    echelle_y: Literal["lineaire", "log"] = "lineaire"
    echelle_z: Literal["lineaire", "log"] = "lineaire"
    barres: bool = True
    relier: bool = True
    surface: bool = False
    # Noms des ajustements dont la courbe se superpose aux points, en 2D.
    courbes: tuple[str, ...] = ()


class OngletGraphes(OngletDonnees):
    """Un onglet qui trace des colonnes d'un tableau, selon des réglages gardés dans la page."""

    reglages: ReglagesGraphe | None = None


class OngletNotes(Onglet):
    """Un onglet de commentaires et de photos annotées."""

    texte: str = ""
    photos: tuple[PhotoAnnotee, ...] = ()

    def pieces_jointes_referencees(self) -> list[str]:
        return [photo.chemin for photo in self.photos]


_TYPES_ONGLET: dict[str, type[Onglet]] = {}


def enregistrer_type_onglet(nom_type: str, classe: type[Onglet]) -> None:
    """Déclare un type d'onglet. C'est le seul endroit à modifier pour en ajouter un."""
    _TYPES_ONGLET[nom_type] = classe


def retirer_type_onglet(nom_type: str) -> None:
    """Retire un type d'onglet déclaré."""
    del _TYPES_ONGLET[nom_type]


enregistrer_type_onglet("notes", OngletNotes)
enregistrer_type_onglet("scalaires", OngletDonnees)
enregistrer_type_onglet("tableau", OngletDonnees)
enregistrer_type_onglet("fichiers", OngletDonnees)
enregistrer_type_onglet("dicom", OngletDonnees)
enregistrer_type_onglet("graphes", OngletGraphes)


def _lire_onglet(onglet: Any) -> Any:  # noqa: ANN401 - donnée brute validée par pydantic
    """Choisit la classe d'un onglet d'après son type ; un type inconnu reste un `Onglet`."""
    if isinstance(onglet, dict):
        return _TYPES_ONGLET.get(onglet.get("type"), Onglet).model_validate(onglet)
    return onglet


# --- Page -----------------------------------------------------------------------


class Page(_Modele):
    """Le contenu de `page.json`."""

    version_schema: int = VERSION_SCHEMA
    id: str = Field(pattern=MOTIF_ID_PAGE)
    titre: str = Field(min_length=1)
    description: str = ""
    date_mesure: AwareDatetime
    date_saisie: AwareDatetime
    auteur: str = Field(min_length=1)
    gabarit: str | None = Field(default=None, pattern=r"^[a-z0-9_.-]+@\d+$")
    site: str | None = None
    machine: str | None = Field(default=None, pattern=MOTIF_MACHINE)
    equipements: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    statut: Literal["brouillon", "validée", "corrigée"] = "brouillon"
    mesures: tuple[Grandeur, ...] = ()
    onglets: tuple[SerializeAsAny[Onglet], ...] = ()
    pieces_jointes: tuple[PieceJointe, ...] = ()

    @field_validator("version_schema")
    @classmethod
    def _verifier_version(cls, version: int) -> int:
        if not 1 <= version <= VERSION_SCHEMA:
            raise ValueError(
                f"Page au format {version}, mais ce logiciel lit jusqu'au format "
                f"{VERSION_SCHEMA} : mettre à jour pymphub."
            )
        return version

    @field_validator("onglets", mode="before")
    @classmethod
    def _lire_onglets(cls, onglets: Any) -> Any:  # noqa: ANN401 - donnée brute validée par pydantic
        if isinstance(onglets, (list, tuple)):
            return tuple(_lire_onglet(onglet) for onglet in onglets)
        return onglets

    @model_validator(mode="after")
    def _verifier_coherence(self) -> "Page":
        noms = [grandeur.nom for grandeur in self.mesures]
        doublons = sorted({nom for nom in noms if noms.count(nom) > 1})
        if doublons:
            raise ValueError(
                f"Noms présents plusieurs fois dans la page : {', '.join(doublons)}."
            )

        fichiers = [g.fichier for g in self.mesures if isinstance(g, Tableau)]
        doublons = sorted({f for f in fichiers if fichiers.count(f) > 1})
        if doublons:
            raise ValueError(f"Fichiers de tableaux partagés : {', '.join(doublons)}.")

        tableaux = {g.nom: g for g in self.mesures if isinstance(g, Tableau)}
        for grandeur in self.mesures:
            if not isinstance(grandeur, ColonneCalculee):
                continue
            for nom_tableau in grandeur.tableaux:
                tableau = tableaux.get(nom_tableau)
                if tableau and any(c.nom == grandeur.nom for c in tableau.colonnes):
                    raise ValueError(
                        f"Le tableau « {nom_tableau} » a déjà une colonne "
                        f"« {grandeur.nom} »."
                    )

        ids = [onglet.id for onglet in self.onglets]
        doublons = sorted({i for i in ids if ids.count(i) > 1})
        if doublons:
            raise ValueError(f"Identifiants d'onglets répétés : {', '.join(doublons)}.")

        references = set(noms) | {
            f"{grandeur.nom}.{colonne.nom}"
            for grandeur in self.mesures
            if isinstance(grandeur, Tableau)
            for colonne in grandeur.colonnes
        }
        chemins = {piece.chemin for piece in self.pieces_jointes}
        for onglet in self.onglets:
            for nom in onglet.grandeurs_referencees():
                if nom not in references:
                    raise ValueError(
                        f"L'onglet « {onglet.id} » référence « {nom} », absent de la page."
                    )
            for chemin in onglet.pieces_jointes_referencees():
                if chemin not in chemins:
                    raise ValueError(
                        f"L'onglet « {onglet.id} » affiche « {chemin} », non déclarée."
                    )

        return self

    def grandeur(
        self, nom: str
    ) -> (
        Scalaire
        | Identifiant
        | Tableau
        | Fichier
        | GrandeurDerivee
        | ColonneCalculee
        | Ajustement
        | Indicateur
    ):
        """Renvoie une grandeur d'après son nom, quel que soit son onglet."""
        for grandeur in self.mesures:
            if grandeur.nom == nom:
                return grandeur
        raise KeyError(f"Aucune grandeur « {nom} » dans la page « {self.id} ».")


def vers_json(page: Page) -> str:
    """Écrit une page en JSON lisible, en UTF-8 et avec un retour à la ligne final.

    Une page est toujours écrite au format courant : le fichier annonce ainsi ce qu'il
    peut contenir. Une page d'un format antérieur passe au format courant à sa modification.
    """
    actuelle = page.model_copy(update={"version_schema": VERSION_SCHEMA})
    return actuelle.model_dump_json(indent=2) + "\n"


def depuis_json(texte: str) -> Page:
    """Relit une page depuis son JSON, en vérifiant toutes ses règles."""
    return Page.model_validate_json(texte)
