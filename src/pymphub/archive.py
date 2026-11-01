"""Archive sur disque : un dossier autonome, copiable sur clé ou sur le réseau.

Ce module crée et ouvre une archive, et lit et écrit ses pages et ses gabarits. Aucun
chemin absolu n'y est stocké : une archive déplacée reste donc utilisable.
"""

import json
import re
import shutil
import tomllib
import unicodedata
from datetime import date, datetime
from importlib import resources
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pymphub.depot import Depot, auteur_du_commit, date_du_commit
from pymphub.disque import (
    chemin_affichable,
    chemin_long,
    ecrire_atomique,
    verifier_nom_windows,
)
from pymphub.gabarit import Gabarit, depuis_toml, verifier_conformite, vers_toml
from pymphub.historique import (
    EntreeHistorique,
    Modification,
    differences,
    lire_message,
    message_commit,
    normaliser_motif,
)
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
_CHAMPS_FIXES = ("id", "date_saisie", "auteur")


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
        raise ArchiveIntrouvable(
            f"« {chemin_affichable(racine)} » n'est pas une archive pymphub."
        )
    try:
        contenu = tomllib.loads(chemin.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as erreur:
        raise ArchiveInvalide(
            f"Lecture de « {chemin_affichable(chemin)} » impossible : {erreur}"
        ) from erreur

    version = contenu.get("version_format")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ArchiveInvalide(
            f"Version de format invalide dans « {chemin_affichable(chemin)} » : {version!r}."
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
            f"Contenu invalide dans « {chemin_affichable(chemin)} » : {erreur}"
        ) from erreur


def _chemin_page_json(id_page: str) -> str:
    """Chemin de `page.json` relatif à la racine, tel que Git le connaît."""
    return f"pages/{id_page}/page.json"


def _comparer_versions(
    ancien: bytes | None, nouveau: bytes | None
) -> tuple[Modification, ...]:
    """Compare deux versions brutes d'une page, éventuellement illisibles après une reprise."""
    if ancien is None or nouveau is None:
        return ()
    try:
        return differences(json.loads(ancien), json.loads(nouveau))
    except json.JSONDecodeError:
        return ()


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
        self.racine = chemin_long(chemin)
        self.configuration = _lire_configuration(self.racine)
        self._depot = Depot(self.racine)

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
        racine = chemin_long(chemin)
        verifier_nom_windows(racine.name)
        if racine.exists():
            raise FileExistsError(f"« {chemin_affichable(racine)} » existe déjà.")

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
            Depot.initialiser(temporaire).enregistrer(
                message_commit("archive", sujet=f"création de l'archive {racine.name}"),
                [".gitignore", _FICHIER_CONFIGURATION, "FORMAT.md"],
                responsable,
            )
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
        self._depot.enregistrer(
            message_commit("archive", sujet=f"machine déclarée : {machine}"),
            [_FICHIER_CONFIGURATION],
            self.configuration.responsable,
        )

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
        self._depot.enregistrer(
            message_commit("gabarit", sujet=f"gabarit ajouté : {gabarit.reference}"),
            [f"gabarits/{chemin.name}"],
            self.configuration.responsable,
        )

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

    def _exiger_page_suivie(self, id_page: str) -> None:
        """Refuse d'écraser une page dont le fichier diffère du dernier commit.

        L'écraser effacerait la trace d'une modification faite hors du logiciel, ou
        d'un enregistrement interrompu avant son commit.
        """
        chemin = self._dossier_page(id_page) / "page.json"
        if chemin.read_bytes() != self._depot.contenu_actuel(
            _chemin_page_json(id_page)
        ):
            raise PageRefusee(
                f"La page « {id_page} » a changé hors du logiciel ou sans commit : "
                "l'examiner, puis utiliser enregistrer_reprise."
            )

    def creer_page(self, page: Page, auteur: str | None = None) -> Path:
        """Enregistre une nouvelle page, en brouillon, et renvoie son dossier."""
        if page.statut != "brouillon":
            raise PageRefusee(
                "Une nouvelle page est un brouillon : la valider ensuite."
            )
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
        self._depot.enregistrer(
            message_commit("création", page.id),
            [_chemin_page_json(page.id)],
            auteur or page.auteur,
        )
        return dossier

    def modifier_page(
        self, page: Page, motif: str | None = None, auteur: str | None = None
    ) -> bool:
        """Enregistre une nouvelle version d'une page et renvoie True si elle a changé.

        Un brouillon s'enregistre sans motif. Une page validée exige un motif et passe
        au statut « corrigée ». Sans changement, aucun commit n'est créé.
        """
        ancienne = self.lire_page(page.id)
        for champ in _CHAMPS_FIXES:
            if getattr(page, champ) != getattr(ancienne, champ):
                raise PageRefusee(
                    f"Le champ « {champ} » d'une page n'est pas modifiable."
                )
        if page.statut != ancienne.statut:
            raise PageRefusee(
                "Le statut ne se modifie pas ici : utiliser valider_page."
            )
        self._exiger_page_suivie(page.id)
        if page == ancienne:
            return False

        motif = normaliser_motif(motif)
        if ancienne.statut == "brouillon":
            nature = "enregistrement"
        else:
            if motif is None:
                raise PageRefusee(
                    "Une page validée ne se modifie que par une correction motivée."
                )
            nature = "correction"
            page = page.model_copy(update={"statut": "corrigée"})
        self._verifier_page(page)

        self._ecrire_et_enregistrer(page, ancienne, nature, motif, auteur)
        return True

    def valider_page(self, id_page: str, auteur: str | None = None) -> Page:
        """Fait passer un brouillon au statut « validée » et renvoie la page validée."""
        ancienne = self.lire_page(id_page)
        if ancienne.statut != "brouillon":
            raise PageRefusee(f"La page « {id_page} » est déjà {ancienne.statut}.")
        self._exiger_page_suivie(id_page)
        page = ancienne.model_copy(update={"statut": "validée"})
        self._verifier_page(page)
        self._ecrire_et_enregistrer(page, ancienne, "validation", None, auteur)
        return page

    def _ecrire_et_enregistrer(
        self,
        page: Page,
        ancienne: Page,
        nature: str,
        motif: str | None,
        auteur: str | None,
    ) -> None:
        modifications = differences(
            json.loads(vers_json(ancienne)), json.loads(vers_json(page))
        )
        ecrire_atomique(self._dossier_page(page.id) / "page.json", vers_json(page))
        self._depot.enregistrer(
            message_commit(nature, page.id, motif, modifications),
            [_chemin_page_json(page.id)],
            auteur or page.auteur,
        )

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

    # --- Historique ------------------------------------------------------------

    def _entrees(
        self, id_page: str | None = None, nature: str | None = None
    ) -> list[EntreeHistorique]:
        entrees: list[EntreeHistorique] = []
        for commit in self._depot.commits():
            infos = lire_message(commit.message.decode("utf-8"))
            page = infos.get("Page")
            if page is None or (id_page is not None and page != id_page):
                continue
            if nature is not None and infos.get("Nature") != nature:
                continue
            chemin = _chemin_page_json(page)
            ancien = (
                self._depot.contenu(commit.parents[0], chemin)
                if commit.parents
                else None
            )
            nouveau = self._depot.contenu(commit.id, chemin)
            entrees.append(
                EntreeHistorique(
                    commit=commit.id.decode("ascii"),
                    date=date_du_commit(commit),
                    auteur=auteur_du_commit(commit),
                    nature=infos.get("Nature", ""),
                    page=page,
                    motif=infos.get("Motif"),
                    modifications=_comparer_versions(ancien, nouveau),
                )
            )
        return entrees

    def historique(self, id_page: str) -> list[EntreeHistorique]:
        """Renvoie la chronologie d'une page, de sa création à sa dernière version."""
        self._dossier_page(id_page)
        entrees = self._entrees(id_page)
        if not entrees:
            raise PageIntrouvable(f"Aucun historique pour la page « {id_page} ».")
        return entrees

    def corrections(self, id_page: str | None = None) -> list[EntreeHistorique]:
        """Renvoie les corrections motivées d'une page, ou de toute l'archive."""
        return self._entrees(id_page, nature="correction")

    def version_page(self, id_page: str, commit: str) -> Page:
        """Relit une page telle qu'elle était à un commit de son historique."""
        self._dossier_page(id_page)
        contenu = self._depot.contenu(commit, _chemin_page_json(id_page))
        if contenu is None:
            raise PageIntrouvable(f"La page « {id_page} » n'existe pas à ce commit.")
        return depuis_json(contenu.decode("utf-8"))

    # --- Modifications hors du logiciel ----------------------------------------

    def modifications_non_tracees(self) -> list[str]:
        """Liste les fichiers suivis qui diffèrent du dernier commit.

        Cela révèle une modification faite à la main ou un enregistrement interrompu.
        """
        suivis = self._depot.fichiers_suivis()
        sur_disque = [
            self.racine / nom
            for nom in (_FICHIER_CONFIGURATION, "FORMAT.md", ".gitignore")
        ]
        sur_disque += self.racine.glob("gabarits/*.toml")
        sur_disque += self.racine.glob("pages/*/page.json")

        ecarts = set(suivis) - {
            c.relative_to(self.racine).as_posix() for c in sur_disque
        }
        for chemin in sur_disque:
            relatif = chemin.relative_to(self.racine).as_posix()
            if not chemin.is_file() or chemin.read_bytes() != suivis.get(relatif):
                ecarts.add(relatif)
        return sorted(ecarts)

    def enregistrer_reprise(self, motif: str, auteur: str | None = None) -> list[str]:
        """Enregistre l'état actuel des fichiers non tracés, avec un motif obligatoire.

        Un commit est créé par fichier, pour rattacher chacun à sa page dans l'historique.
        L'ancienne version reste lisible dans Git. Renvoie les fichiers enregistrés.
        """
        motif_normalise = normaliser_motif(motif)
        if motif_normalise is None:
            raise PageRefusee("Une reprise exige un motif.")
        fichiers = self.modifications_non_tracees()
        for fichier in fichiers:
            parties = fichier.split("/")
            page = parties[1] if parties[0] == "pages" else None
            sujet = None if page else f"reprise : {fichier}"
            self._depot.enregistrer(
                message_commit("reprise", page, motif_normalise, sujet=sujet),
                [fichier],
                auteur or self.configuration.responsable,
            )
        return fichiers
