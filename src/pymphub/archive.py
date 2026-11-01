"""Archive sur disque : un dossier autonome, copiable sur clé ou sur le réseau.

Ce module crée et ouvre une archive, et lit et écrit ses pages et ses gabarits. Aucun
chemin absolu n'y est stocké : une archive déplacée reste donc utilisable.
"""

import re
import shutil
import tomllib
import unicodedata
from datetime import date, datetime
from importlib import resources
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pymphub.disque import ecrire_atomique, verifier_nom_windows
from pymphub.gabarit import Gabarit, depuis_toml, verifier_conformite, vers_toml
from pymphub.page import (
    MOTIF_ID_PAGE,
    MOTIF_MACHINE,
    Page,
    depuis_json,
    vers_json,
)

VERSION_FORMAT = 1
_LONGUEUR_MAX_SUJET = 40
_FICHIER_CONFIGURATION = "archive.toml"
_NUMERO_PAGE = re.compile(r"_(\d{4,})$")


class ArchiveIntrouvable(Exception):
    """Le dossier n'existe pas ou n'est pas une archive pymphub."""


class ArchiveInvalide(Exception):
    """Le fichier archive.toml ne peut pas être lu."""


class FormatNonPrisEnCharge(Exception):
    """L'archive utilise un format que ce logiciel ne sait pas lire."""


class PageIntrouvable(KeyError):
    """Aucune page ne porte cet identifiant dans l'archive."""


class PageRefusee(ValueError):
    """La page ne peut pas être enregistrée dans cette archive."""


class GabaritIntrouvable(KeyError):
    """Aucun gabarit ne porte cette référence dans l'archive."""


class Configuration(BaseModel):
    """Le contenu de `archive.toml`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version_format: int = VERSION_FORMAT
    nom: str = Field(min_length=1)
    site: str = Field(min_length=1)
    responsable: str = Field(min_length=1)
    periode_debut: date
    periode_fin: date | None = None
    machines: tuple[str, ...] = ()


def _ecrire_configuration(racine: Path, configuration: Configuration) -> None:
    contenu = configuration.model_dump(exclude_none=True)
    ecrire_atomique(racine / _FICHIER_CONFIGURATION, tomli_w.dumps(contenu))


def _lire_configuration(racine: Path) -> Configuration:
    """Lit `archive.toml` et vérifie d'abord sa version.

    Un format plus récent peut avoir d'autres champs : c'est donc sa version, et non ces
    champs, qui doit motiver le refus.
    """
    chemin = racine / _FICHIER_CONFIGURATION
    if not chemin.is_file():
        raise ArchiveIntrouvable(f"« {racine} » n'est pas une archive pymphub.")
    try:
        contenu = tomllib.loads(chemin.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as erreur:
        raise ArchiveInvalide(
            f"Lecture de « {chemin} » impossible : {erreur}"
        ) from erreur

    version = contenu.get("version_format")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ArchiveInvalide(
            f"Version de format invalide dans « {chemin} » : {version!r}."
        )
    if version > VERSION_FORMAT:
        raise FormatNonPrisEnCharge(
            f"Archive au format {version}, mais ce logiciel lit jusqu'au format "
            f"{VERSION_FORMAT} : mettre à jour pymphub."
        )
    try:
        return Configuration.model_validate(contenu)
    except ValidationError as erreur:
        raise ArchiveInvalide(
            f"Contenu invalide dans « {chemin} » : {erreur}"
        ) from erreur


def _en_sujet(texte: str) -> str:
    """Transforme un texte libre en sujet d'identifiant : minuscules, sans accent."""
    sans_accent = (
        unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    )
    sujet = re.sub(r"[^a-z0-9]+", "-", sans_accent.lower()).strip("-")
    return sujet[:_LONGUEUR_MAX_SUJET].rstrip("-") or "page"


class Archive:
    """Une archive ouverte."""

    def __init__(self, chemin: str | Path) -> None:
        self.racine = Path(chemin)
        self.configuration = _lire_configuration(self.racine)

    @classmethod
    def creer(
        cls,
        chemin: str | Path,
        site: str,
        responsable: str,
        periode_debut: date,
        periode_fin: date | None = None,
    ) -> "Archive":
        """Crée une archive dans un nouveau dossier, dont le nom devient celui de l'archive.

        L'archive est préparée dans un dossier temporaire puis renommée : une création
        interrompue ne laisse donc pas d'archive à moitié construite.
        """
        racine = Path(chemin)
        verifier_nom_windows(racine.name)
        if racine.exists():
            raise FileExistsError(f"« {racine} » existe déjà.")

        configuration = Configuration(
            nom=racine.name,
            site=site,
            responsable=responsable,
            periode_debut=periode_debut,
            periode_fin=periode_fin,
        )
        racine.parent.mkdir(parents=True, exist_ok=True)
        temporaire = racine.with_name(f".{racine.name}.creation")
        # Reste éventuel d'une création interrompue par une coupure de courant.
        shutil.rmtree(temporaire, ignore_errors=True)
        temporaire.mkdir()
        try:
            for dossier in ("normes", "gabarits", "objets", "pages"):
                (temporaire / dossier).mkdir()
            _ecrire_configuration(temporaire, configuration)
            format_md = (
                resources.files("pymphub")
                .joinpath("FORMAT.md")
                .read_text(encoding="utf-8")
            )
            ecrire_atomique(temporaire / "FORMAT.md", format_md)
            temporaire.rename(racine)
        except BaseException:
            shutil.rmtree(temporaire, ignore_errors=True)
            raise
        return cls(racine)

    # --- Machines --------------------------------------------------------------

    def declarer_machine(self, machine: str) -> None:
        """Ajoute une machine à `archive.toml`, si elle n'y est pas déjà."""
        if not re.fullmatch(MOTIF_MACHINE, machine):
            raise ValueError(
                f"Machine « {machine} » invalide : le format attendu est "
                "site.service.modalite.fabricant-modele.numero-serie."
            )
        if machine in self.configuration.machines:
            return
        machines = (*self.configuration.machines, machine)
        nouvelle = self.configuration.model_copy(update={"machines": machines})
        _ecrire_configuration(self.racine, nouvelle)
        self.configuration = nouvelle

    # --- Gabarits --------------------------------------------------------------

    def _chemin_gabarit(self, reference: str) -> Path:
        return self.racine / "gabarits" / verifier_nom_windows(f"{reference}.toml")

    def enregistrer_gabarit(self, gabarit: Gabarit) -> None:
        """Enregistre un gabarit. Une version existante ne peut pas changer de contenu."""
        chemin = self._chemin_gabarit(gabarit.reference)
        if chemin.exists():
            if self.lire_gabarit(gabarit.reference) != gabarit:
                raise FileExistsError(
                    f"Le gabarit {gabarit.reference} existe déjà avec un autre contenu : "
                    "créer une nouvelle version."
                )
            return
        ecrire_atomique(chemin, vers_toml(gabarit))

    def lire_gabarit(self, reference: str) -> Gabarit:
        """Relit un gabarit d'après sa référence, par exemple `activimetre.linearite@1`."""
        chemin = self._chemin_gabarit(reference)
        if not chemin.is_file():
            raise GabaritIntrouvable(f"Aucun gabarit « {reference} » dans l'archive.")
        return depuis_toml(chemin.read_text(encoding="utf-8"))

    def gabarits(self) -> list[str]:
        """Liste les références des gabarits de l'archive."""
        return sorted(
            f.name.removesuffix(".toml")
            for f in (self.racine / "gabarits").glob("*.toml")
        )

    # --- Pages -----------------------------------------------------------------

    def _dossier_page(self, id_page: str) -> Path:
        """Renvoie le dossier d'une page, après avoir vérifié la forme de l'identifiant."""
        if not re.fullmatch(MOTIF_ID_PAGE, id_page):
            raise PageIntrouvable(f"Identifiant de page invalide : « {id_page} ».")
        return self.racine / "pages" / verifier_nom_windows(id_page)

    def identifiants(self) -> list[str]:
        """Liste les identifiants des pages de l'archive."""
        return sorted(
            d.name
            for d in (self.racine / "pages").iterdir()
            if (d / "page.json").is_file()
        )

    def nouvel_identifiant(self, date_mesure: datetime, sujet: str) -> str:
        """Propose l'identifiant suivant : date, sujet sans accent, numéro d'ordre.

        Le numéro suit le plus grand numéro de l'archive. Il n'est réservé qu'à la
        création de la page.
        """
        numeros = [
            int(trouve.group(1))
            for dossier in (self.racine / "pages").iterdir()
            if (trouve := _NUMERO_PAGE.search(dossier.name))
        ]
        numero = max(numeros, default=0) + 1
        return f"{date_mesure.date().isoformat()}_{_en_sujet(sujet)}_{numero:04d}"

    def _verifier_page(self, page: Page) -> None:
        """Vérifie ce qui dépend de l'archive : machine déclarée et respect du gabarit."""
        if page.machine is not None and page.machine not in self.configuration.machines:
            raise PageRefusee(
                f"Machine « {page.machine} » non déclarée dans l'archive : la déclarer d'abord."
            )
        if page.gabarit is None:
            return
        try:
            gabarit = self.lire_gabarit(page.gabarit)
        except GabaritIntrouvable as erreur:
            raise PageRefusee(erreur.args[0]) from erreur

        # Une variable absente reste à remplir dans un brouillon : seules les erreurs de
        # saisie le bloquent. Une page validée doit être complète.
        bloquants = [
            ecart
            for ecart in verifier_conformite(page, gabarit)
            if ecart.probleme != "absente" or page.statut != "brouillon"
        ]
        if bloquants:
            details = " ".join(ecart.message for ecart in bloquants)
            raise PageRefusee(f"Page non conforme à {page.gabarit} : {details}")

    def creer_page(self, page: Page) -> Path:
        """Enregistre une nouvelle page et renvoie son dossier."""
        self._verifier_page(page)
        dossier = self._dossier_page(page.id)
        try:
            dossier.mkdir()
        except FileExistsError as erreur:
            raise PageRefusee(f"La page « {page.id} » existe déjà.") from erreur
        try:
            ecrire_atomique(dossier / "page.json", vers_json(page))
        except BaseException:
            dossier.rmdir()
            raise
        return dossier

    def modifier_page(self, page: Page) -> None:
        """Remplace le contenu d'une page existante."""
        self._verifier_page(page)
        dossier = self._dossier_page(page.id)
        if not (dossier / "page.json").is_file():
            raise PageIntrouvable(f"Aucune page « {page.id} » dans l'archive.")
        ecrire_atomique(dossier / "page.json", vers_json(page))

    def lire_page(self, id_page: str) -> Page:
        """Relit une page d'après son identifiant."""
        chemin = self._dossier_page(id_page) / "page.json"
        if not chemin.is_file():
            raise PageIntrouvable(f"Aucune page « {id_page} » dans l'archive.")
        page = depuis_json(chemin.read_text(encoding="utf-8"))
        if page.id != id_page:
            raise ArchiveInvalide(
                f"Le dossier « {id_page} » contient la page « {page.id} »."
            )
        return page
