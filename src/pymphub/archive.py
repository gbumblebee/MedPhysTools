"""Archive sur disque : un dossier autonome, copiable sur clé ou sur le réseau.

Ce module crée et ouvre une archive, et lit et écrit ses pages et ses gabarits. Aucun
chemin absolu n'y est stocké : une archive déplacée reste donc utilisable.
"""

import json
import os
import re
import shutil
import tomllib
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import pandas as pd
import tomli_w
from dulwich.objects import Commit
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pymphub.depot import Depot, auteur_du_commit, date_du_commit
from pymphub.disque import (
    chemin_affichable,
    chemin_long,
    ecrire_atomique,
    verifier_nom_windows,
)
from pymphub.fichiers import (
    CacheEmpreintes,
    EtatFichier,
    FichierAltere,
    FichierManquant,
    ReferenceNonResolue,
    chemin_objet,
    copier_dans_le_magasin,
    empreinte_fichier,
    etat_fichier,
    verifier_empreinte,
)
from pymphub.gabarit import Gabarit, depuis_toml, verifier_conformite, vers_toml
from pymphub.historique import (
    NATURE_EXTERNE,
    EntreeHistorique,
    Modification,
    differences,
    lire_message,
    message_commit,
    normaliser_motif,
)
from pymphub.index import (
    Criteres,
    Index,
    PageIllisible,
    ResumePage,
    ValeursPossibles,
)
from pymphub.lecture import PageLue, ResultatsRecherche
from pymphub.page import (
    MOTIF_ID_PAGE,
    MOTIF_MACHINE,
    Copie,
    Fichier,
    Page,
    PieceJointe,
    ReferenceFichier,
    Tableau,
    depuis_json,
    vers_json,
)
from pymphub.tableaux import (
    DonneesTableau,
    TableauInvalide,
    differences_csv,
    ecrire_csv,
    lire_csv,
)

VERSION_FORMAT = 1
_LONGUEUR_MAX_SUJET = 40
_FICHIER_CONFIGURATION = "archive.toml"
_NUMERO_PAGE = re.compile(r"_(\d{4,})$")
_CHAMPS_FIXES = ("id", "date_saisie", "auteur")
# Le commit d'une modification externe ne connaît pas son auteur : il ne faut pas en inventer un.
_AUTEUR_EXTERNE = "hors logiciel"
# Au-delà, un affichage ne calcule pas l'empreinte : il resterait bloqué trop longtemps.
TAILLE_MAX_VERIFICATION = 100 * 1024 * 1024


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
    except ValueError:
        # JSON abîmé, ou enregistré dans un autre encodage qu'UTF-8.
        return ()


def _tableaux_du_json(contenu: bytes) -> list[tuple[str, str]]:
    """Renvoie le nom et le fichier des tableaux d'un `page.json`, même abîmé.

    Cette lecture sert justement quand un fichier a changé hors du logiciel. Elle ne doit
    donc jamais échouer, ni suivre un chemin qui sortirait du dossier de la page.
    """
    try:
        page = json.loads(contenu)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return []
    mesures = page.get("mesures") if isinstance(page, dict) else None
    if not isinstance(mesures, list):
        return []
    tableaux: list[tuple[str, str]] = []
    for grandeur in mesures:
        if not isinstance(grandeur, dict) or grandeur.get("nature") != "tableau":
            continue
        nom, fichier = grandeur.get("nom"), grandeur.get("fichier")
        if not isinstance(nom, str) or not isinstance(fichier, str):
            continue
        morceaux = PurePosixPath(fichier)
        if (
            fichier
            and "\\" not in fichier
            and not morceaux.is_absolute()
            and ".." not in morceaux.parts
        ):
            tableaux.append((nom, fichier))
    return tableaux


def _motif_externe(details: str) -> str:
    """Le motif d'une version créée toute seule : il dit ce qu'on sait, et rien de plus."""
    return (
        "Détecté à l'ouverture : modification hors du logiciel ou enregistrement "
        f"interrompu. {details}"
    )


def _differences_csv_brutes(
    nom: str, ancien: bytes, nouveau: bytes
) -> tuple[Modification, ...]:
    """Compare deux versions d'un CSV, même réenregistré dans un autre encodage par un tableur."""
    return differences_csv(
        nom, ancien.decode("utf-8", "replace"), nouveau.decode("utf-8", "replace")
    )


def _decrire_reference(chemin: str) -> ReferenceFichier:
    """Décrit un fichier externe : chemin, empreinte, taille et date de modification."""
    acces = chemin_long(chemin)
    empreinte = empreinte_fichier(acces)
    etat = acces.stat()
    return ReferenceFichier(
        chemin=chemin,
        empreinte=empreinte,
        taille=etat.st_size,
        date=datetime.fromtimestamp(etat.st_mtime, tz=timezone.utc),
    )


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
        self._index = Index(self.racine / "index.sqlite", self.racine / "pages")
        self.cache_empreintes = CacheEmpreintes(
            self.racine / "objets" / ".cache_empreintes.json"
        )

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

        Le numéro suit le plus grand numéro de l'archive, y compris ceux des pages
        supprimées, que l'historique garde : une nouvelle page ne doit jamais hériter de
        la chronologie d'une ancienne. Il n'est réservé qu'à la création de la page.
        """
        noms = [dossier.name for dossier in (self.racine / "pages").iterdir()]
        noms += [
            infos["Page"]
            for commit in self._depot.commits()
            if "Page" in (infos := lire_message(commit.message.decode("utf-8")))
        ]
        numeros = [
            int(trouve.group(1)) for nom in noms if (trouve := _NUMERO_PAGE.search(nom))
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
        """Refuse d'écraser une page dont les fichiers diffèrent du dernier commit.

        Les écraser effacerait la trace d'une modification faite hors du logiciel, ou
        d'un enregistrement interrompu avant son commit.
        """
        for relatif, contenu in self._depot.fichiers_suivis(
            f"pages/{id_page}/"
        ).items():
            chemin = self.racine / relatif
            if not chemin.is_file() or chemin.read_bytes() != contenu:
                raise PageRefusee(
                    f"La page « {id_page} » a changé hors du logiciel ou sans commit : "
                    "l'examiner, puis utiliser enregistrer_reprise."
                )

    def _preparer_tableaux(
        self, page: Page, tableaux: Mapping[str, DonneesTableau] | None
    ) -> dict[str, str]:
        """Vérifie les tableaux d'une page et renvoie les fichiers CSV à écrire.

        Un tableau sans nouvelles données doit déjà exister sur le disque et respecter
        sa description. Renvoie, pour chaque fichier à écrire, son chemin dans la page
        et son texte.
        """
        tableaux = tableaux or {}
        declares = {g.nom: g for g in page.mesures if isinstance(g, Tableau)}
        inconnus = sorted(set(tableaux) - set(declares))
        if inconnus:
            raise PageRefusee(
                f"Tableaux non déclarés dans la page : {', '.join(inconnus)}."
            )

        dossier = self._dossier_page(page.id)
        textes: dict[str, str] = {}
        for nom, tableau in declares.items():
            try:
                if nom in tableaux:
                    textes[tableau.fichier] = ecrire_csv(tableau, tableaux[nom])
                    continue
                chemin = dossier / tableau.fichier
                if not chemin.is_file():
                    raise PageRefusee(f"Le tableau « {nom} » n'a pas de données.")
                lire_csv(tableau, chemin.read_text(encoding="utf-8"))
            except TableauInvalide as erreur:
                raise PageRefusee(str(erreur)) from erreur
        return textes

    def _ecrire_tableaux(self, id_page: str, textes: Mapping[str, str]) -> None:
        dossier = self._dossier_page(id_page)
        for relatif, texte in textes.items():
            chemin = dossier / relatif
            chemin.parent.mkdir(parents=True, exist_ok=True)
            ecrire_atomique(chemin, texte)

    def creer_page(
        self,
        page: Page,
        auteur: str | None = None,
        tableaux: Mapping[str, DonneesTableau] | None = None,
    ) -> Path:
        """Enregistre une nouvelle page, en brouillon, et renvoie son dossier."""
        if page.statut != "brouillon":
            raise PageRefusee(
                "Une nouvelle page est un brouillon : la valider ensuite."
            )
        self._verifier_page(page)
        dossier = self._dossier_page(page.id)
        if dossier.exists():
            raise PageRefusee(f"La page « {page.id} » existe déjà.")
        textes = self._preparer_tableaux(page, tableaux)

        dossier.mkdir()
        try:
            self._ecrire_tableaux(page.id, textes)
            ecrire_atomique(dossier / "page.json", vers_json(page))
        except BaseException:
            shutil.rmtree(dossier, ignore_errors=True)
            raise
        self._depot.enregistrer(
            message_commit("création", page.id),
            [_chemin_page_json(page.id), *(f"pages/{page.id}/{r}" for r in textes)],
            auteur or page.auteur,
        )
        return dossier

    def _modifications_tableaux(
        self, page: Page, textes: Mapping[str, str]
    ) -> tuple[Modification, ...]:
        """Compare les nouveaux fichiers CSV à ceux du disque, cellule par cellule."""
        dossier = self._dossier_page(page.id)
        modifications: list[Modification] = []
        for grandeur in page.mesures:
            if isinstance(grandeur, Tableau) and grandeur.fichier in textes:
                chemin = dossier / grandeur.fichier
                if chemin.is_file():
                    modifications.extend(
                        differences_csv(
                            grandeur.nom,
                            chemin.read_text(encoding="utf-8"),
                            textes[grandeur.fichier],
                        )
                    )
        return tuple(modifications)

    def modifier_page(
        self,
        page: Page,
        motif: str | None = None,
        auteur: str | None = None,
        tableaux: Mapping[str, DonneesTableau] | None = None,
    ) -> bool:
        """Enregistre une nouvelle version d'une page et renvoie True si elle a changé.

        `tableaux` donne les nouvelles données des tableaux modifiés. Un brouillon
        s'enregistre sans motif. Une page validée exige un motif et passe au statut
        « corrigée ». Sans changement, aucun commit n'est créé.
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

        textes = self._preparer_tableaux(page, tableaux)
        dossier = self._dossier_page(page.id)
        textes = {
            relatif: texte
            for relatif, texte in textes.items()
            if not (dossier / relatif).is_file()
            or (dossier / relatif).read_text(encoding="utf-8") != texte
        }
        if page == ancienne and not textes:
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

        modifications = differences(
            json.loads(vers_json(ancienne)), json.loads(vers_json(page))
        ) + self._modifications_tableaux(page, textes)

        # Un tableau retiré de la page quitte aussi le dépôt ; Git garde son contenu.
        fichiers_gardes = {g.fichier for g in page.mesures if isinstance(g, Tableau)}
        retires = [
            g.fichier
            for g in ancienne.mesures
            if isinstance(g, Tableau) and g.fichier not in fichiers_gardes
        ]

        self._ecrire_tableaux(page.id, textes)
        ecrire_atomique(dossier / "page.json", vers_json(page))
        for relatif in retires:
            (dossier / relatif).unlink(missing_ok=True)
        self._depot.enregistrer(
            message_commit(nature, page.id, motif, modifications),
            [
                _chemin_page_json(page.id),
                *(f"pages/{page.id}/{r}" for r in (*textes, *retires)),
            ],
            auteur or page.auteur,
        )
        return True

    def valider_page(self, id_page: str, auteur: str | None = None) -> Page:
        """Fait passer un brouillon au statut « validée » et renvoie la page validée."""
        ancienne = self.lire_page(id_page)
        if ancienne.statut != "brouillon":
            raise PageRefusee(f"La page « {id_page} » est déjà {ancienne.statut}.")
        self._exiger_page_suivie(id_page)
        page = ancienne.model_copy(update={"statut": "validée"})
        self._verifier_page(page)
        modifications = differences(
            json.loads(vers_json(ancienne)), json.loads(vers_json(page))
        )
        ecrire_atomique(self._dossier_page(id_page) / "page.json", vers_json(page))
        self._depot.enregistrer(
            message_commit("validation", id_page, None, modifications),
            [_chemin_page_json(id_page)],
            auteur or page.auteur,
        )
        return page

    def _ignorer_corbeille(self) -> list[str]:
        """Ajoute `corbeille/` au `.gitignore` d'une archive créée avant son existence.

        Renvoie les fichiers à enregistrer dans le commit.
        """
        chemin = self.racine / ".gitignore"
        texte = chemin.read_text(encoding="utf-8") if chemin.is_file() else ""
        if "corbeille/" in texte.splitlines():
            return []
        ecrire_atomique(chemin, f"{texte}corbeille/\n")
        return [".gitignore"]

    def supprimer_page(
        self, id_page: str, motif: str | None = None, auteur: str | None = None
    ) -> Path:
        """Retire une page de l'archive et renvoie le dossier où elle est conservée.

        Un brouillon se supprime sans motif ; une page validée ou corrigée en exige un.
        Le dossier entier part dans `corbeille/<id>/` : ce qui est hors de Git (pièces
        jointes, fichiers déposés) n'est donc pas perdu, et le renommage se fait d'un seul
        coup. Git garde `page.json` et les tableaux. `objets/` n'est pas touché : une
        autre page peut utiliser les mêmes fichiers. Le numéro de la page ne resservira pas.
        """
        page = self.lire_page(id_page)
        motif = normaliser_motif(motif)
        if page.statut != "brouillon" and motif is None:
            raise PageRefusee(
                f"Une page {page.statut} ne se supprime qu'avec un motif."
            )
        self._exiger_page_suivie(id_page)

        cible = self.racine / "corbeille" / id_page
        if cible.exists():
            raise PageRefusee(
                f"« corbeille/{id_page} » existe déjà : l'examiner avant de supprimer."
            )
        suivis = list(self._depot.fichiers_suivis(f"pages/{id_page}/"))
        cible.parent.mkdir(exist_ok=True)
        gitignore = self._ignorer_corbeille()
        self._dossier_page(id_page).rename(cible)
        self._depot.enregistrer(
            message_commit("suppression", id_page, motif),
            [*suivis, *gitignore],
            auteur or page.auteur,
        )
        return cible

    def emplacement_page(self, id_page: str) -> Path:
        """Renvoie le dossier où une page est réellement stockée."""
        return self._dossier_page(id_page)

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

    # --- Tableaux ----------------------------------------------------------------

    def _tableau_de(self, page: Page, nom: str) -> Tableau:
        grandeur = page.grandeur(nom)
        if not isinstance(grandeur, Tableau):
            raise ValueError(f"« {nom} » n'est pas un tableau.")
        return grandeur

    def lire_tableau(self, id_page: str, nom: str) -> pd.DataFrame:
        """Relit un tableau d'une page, avec ses unités dans `attrs["unites"]`."""
        tableau = self._tableau_de(self.lire_page(id_page), nom)
        chemin = self._dossier_page(id_page) / tableau.fichier
        if not chemin.is_file():
            raise FichierManquant(f"Fichier introuvable : « {tableau.fichier} ».")
        return lire_csv(tableau, chemin.read_text(encoding="utf-8"))

    def version_tableau(self, id_page: str, nom: str, commit: str) -> pd.DataFrame:
        """Relit un tableau tel qu'il était à un commit de l'historique."""
        tableau = self._tableau_de(self.version_page(id_page, commit), nom)
        contenu = self._depot.contenu(commit, f"pages/{id_page}/{tableau.fichier}")
        if contenu is None:
            raise FichierManquant(f"« {tableau.fichier} » n'existe pas à ce commit.")
        return lire_csv(tableau, contenu.decode("utf-8"))

    # --- Fichiers bruts ---------------------------------------------------------

    def importer_copie(
        self,
        source: str | Path,
        nom: str,
        type_fichier: str = "",
        commentaire: str = "",
    ) -> Fichier:
        """Copie un fichier dans `objets/` et renvoie la grandeur à ajouter à une page."""
        origine = chemin_long(source)
        empreinte = copier_dans_le_magasin(self.racine / "objets", origine)
        return Fichier(
            nom=nom,
            stockage=Copie(empreinte=empreinte, nom_origine=origine.name),
            type_fichier=type_fichier,
            commentaire=commentaire,
        )

    def referencer_fichier(
        self,
        source: str | Path,
        nom: str,
        type_fichier: str = "",
        commentaire: str = "",
    ) -> Fichier:
        """Référence un fichier sans le copier, et renvoie la grandeur à ajouter à une page."""
        return Fichier(
            nom=nom,
            stockage=_decrire_reference(chemin_affichable(os.path.abspath(source))),
            type_fichier=type_fichier,
            commentaire=commentaire,
        )

    def chemin_fichier(self, id_page: str, nom: str) -> Path:
        """Renvoie le chemin d'un fichier brut, après avoir vérifié son empreinte.

        Lève `FichierManquant` ou `FichierAltere` sans rien écrire dans l'archive. Une
        référence DICOM ne se résout pas encore : l'accès au PACS n'existe pas en v1.
        """
        grandeur = self.lire_page(id_page).grandeur(nom)
        if not isinstance(grandeur, Fichier):
            raise ValueError(f"« {nom} » n'est pas un fichier.")
        stockage = grandeur.stockage
        if isinstance(stockage, Copie):
            cible = chemin_objet(self.racine / "objets", stockage.empreinte)
            return verifier_empreinte(cible, stockage.empreinte)
        if isinstance(stockage, ReferenceFichier):
            return verifier_empreinte(chemin_long(stockage.chemin), stockage.empreinte)
        raise ReferenceNonResolue(
            f"« {nom} » est une référence DICOM : récupérer la série "
            f"{stockage.series_instance_uid} sur {stockage.noeud}."
        )

    def importer_piece_jointe(
        self, id_page: str, source: str | Path, nom_fichier: str | None = None
    ) -> PieceJointe:
        """Copie une pièce jointe dans la page et renvoie sa description.

        Une pièce jointe existante ne peut pas être remplacée par un autre contenu.
        """
        origine = chemin_long(source)
        nom_fichier = verifier_nom_windows(nom_fichier or origine.name)
        dossier = self._dossier_page(id_page)
        if not (dossier / "page.json").is_file():
            raise PageIntrouvable(f"Aucune page « {id_page} » dans l'archive.")

        empreinte = empreinte_fichier(origine)
        cible = dossier / "pieces_jointes" / nom_fichier
        if cible.exists() and empreinte_fichier(cible) != empreinte:
            raise FileExistsError(
                f"La pièce jointe « {nom_fichier} » existe déjà avec un autre contenu."
            )
        if not cible.exists():
            cible.parent.mkdir(exist_ok=True)
            temporaire = cible.with_name(f".{nom_fichier}.tmp")
            try:
                shutil.copyfile(origine, temporaire)
                verifier_empreinte(temporaire, empreinte)
                os.replace(temporaire, cible)
            except BaseException:
                temporaire.unlink(missing_ok=True)
                raise
        return PieceJointe(chemin=f"pieces_jointes/{nom_fichier}", empreinte=empreinte)

    def joindre_fichier(self, id_page: str, source: str | Path) -> PieceJointe:
        """Copie un fichier dans les pièces jointes, sans jamais refuser son nom.

        Deux fichiers différents portent souvent le même nom, comme `image.png`. Le second
        devient alors `image_2.png`. Un fichier déjà joint n'est pas copié à nouveau.
        """
        origine = chemin_long(source)
        verifier_nom_windows(origine.name)
        dossier = self._dossier_page(id_page) / "pieces_jointes"
        empreinte = empreinte_fichier(origine)
        nom = origine.name
        numero = 1
        while (dossier / nom).exists() and empreinte_fichier(
            dossier / nom
        ) != empreinte:
            numero += 1
            nom = f"{origine.stem}_{numero}{origine.suffix}"
        return self.importer_piece_jointe(id_page, origine, nom)

    def emplacement_piece_jointe(self, id_page: str, chemin: str) -> Path:
        """Renvoie où se trouve une pièce jointe, sans vérifier son contenu."""
        return self._dossier_page(id_page) / chemin

    def emplacement_fichier(self, fichier: Fichier) -> Path | None:
        """Renvoie où se trouve un fichier brut. Une référence DICOM n'a pas d'emplacement."""
        stockage = fichier.stockage
        if isinstance(stockage, Copie):
            return chemin_objet(self.racine / "objets", stockage.empreinte)
        if isinstance(stockage, ReferenceFichier):
            return chemin_long(stockage.chemin)
        return None

    def etat_piece_jointe(
        self, id_page: str, piece: PieceJointe, taille_max: int | None = None
    ) -> EtatFichier:
        """Dit si une pièce jointe correspond à son empreinte."""
        chemin = self.emplacement_piece_jointe(id_page, piece.chemin)
        return etat_fichier(chemin, piece.empreinte, taille_max)

    def etat_fichier_brut(
        self, fichier: Fichier, taille_max: int | None = None
    ) -> EtatFichier | None:
        """Dit si un fichier brut correspond à son empreinte ; None pour une référence DICOM."""
        chemin = self.emplacement_fichier(fichier)
        if chemin is None:
            return None
        # Une grosse série déjà vérifiée n'a pas à être relue pour afficher son état.
        connue = self.cache_empreintes.connue(chemin)
        if connue is not None:
            return "intact" if connue == fichier.stockage.empreinte else "altéré"
        return etat_fichier(chemin, fichier.stockage.empreinte, taille_max)

    def etats_fichiers(
        self, page: Page, taille_max: int | None = TAILLE_MAX_VERIFICATION
    ) -> dict[str, EtatFichier]:
        """Vérifie les fichiers d'une page, pour les signaler à l'affichage.

        La clé est le nom d'un fichier brut ou le chemin d'une pièce jointe. Rien n'est écrit.
        """
        etats: dict[str, EtatFichier] = {
            piece.chemin: self.etat_piece_jointe(page.id, piece, taille_max)
            for piece in page.pieces_jointes
        }
        for grandeur in page.mesures:
            if isinstance(grandeur, Fichier):
                etat = self.etat_fichier_brut(grandeur, taille_max)
                if etat is not None:
                    etats[grandeur.nom] = etat
        return etats

    def chemin_piece_jointe(self, id_page: str, chemin: str) -> Path:
        """Renvoie le chemin d'une pièce jointe, après avoir vérifié son empreinte."""
        for piece in self.lire_page(id_page).pieces_jointes:
            if piece.chemin == chemin:
                return verifier_empreinte(
                    self._dossier_page(id_page) / chemin, piece.empreinte
                )
        raise KeyError(f"Aucune pièce jointe « {chemin} » dans la page « {id_page} ».")

    def enregistrer_fichier_modifie(
        self, id_page: str, designation: str, motif: str, auteur: str | None = None
    ) -> Page:
        """Enregistre le nouvel état d'un fichier lourd qui a changé hors du logiciel.

        `designation` est le nom d'une grandeur en référence fichier, ou le chemin d'une
        pièce jointe. Le motif est obligatoire, même pour un brouillon. Le commit garde
        le nom du fichier, son ancienne et sa nouvelle empreinte. Un objet du magasin ne
        se remplace pas : il doit être restauré.
        """
        if normaliser_motif(motif) is None:
            raise PageRefusee("L'enregistrement d'un fichier modifié exige un motif.")
        page = self.lire_page(id_page)
        nouvelle = self._page_avec_fichier_actualise(page, designation)
        self.modifier_page(nouvelle, motif, auteur)
        return self.lire_page(id_page)

    def _page_avec_fichier_actualise(self, page: Page, designation: str) -> Page:
        for piece in page.pieces_jointes:
            if piece.chemin == designation:
                empreinte = empreinte_fichier(self._dossier_page(page.id) / designation)
                actualisee = piece.model_copy(update={"empreinte": empreinte})
                pieces = tuple(
                    actualisee if p.chemin == designation else p
                    for p in page.pieces_jointes
                )
                return page.model_copy(update={"pieces_jointes": pieces})

        grandeur = page.grandeur(designation)
        stockage = grandeur.stockage if isinstance(grandeur, Fichier) else None
        if isinstance(stockage, Copie):
            raise FichierAltere(
                f"« {designation} » est une copie du magasin : la restaurer depuis une "
                "sauvegarde au lieu de la remplacer."
            )
        if not isinstance(stockage, ReferenceFichier):
            raise ValueError(f"« {designation} » n'est pas une référence de fichier.")
        actualisee = grandeur.model_copy(
            update={"stockage": _decrire_reference(stockage.chemin)}
        )
        mesures = tuple(actualisee if g.nom == designation else g for g in page.mesures)
        return page.model_copy(update={"mesures": mesures})

    # --- Recherche --------------------------------------------------------------

    def page(self, id_page: str) -> PageLue:
        """Relit une page pour un script : `Archive(chemin).page(id).mesures["activite"]`."""
        return PageLue(self, self.lire_page(id_page))

    def chercher(
        self,
        tags: Sequence[str] = (),
        mode_tags: Literal["et", "ou"] = "et",
        depuis: str | date | datetime | None = None,
        jusqua: str | date | datetime | None = None,
        gabarit: str | None = None,
        machine: str | None = None,
        statut: str | None = None,
        auteur: str | None = None,
        texte: str | None = None,
    ) -> ResultatsRecherche:
        """Cherche des pages et les renvoie de la plus récente à la plus ancienne.

        Voir `Index.chercher` pour le détail des critères. Chaque page n'est relue
        qu'au moment d'y accéder.
        """
        return ResultatsRecherche(
            self,
            self.chercher_identifiants(
                tags, mode_tags, depuis, jusqua, gabarit, machine, statut, auteur, texte
            ),
        )

    def _index_synchronise(self) -> Index:
        """Renvoie l'index, après l'avoir mis à jour si le dernier commit a changé."""
        tete = self._depot.tete()
        if not self._index.est_a_jour(tete):
            self._index.synchroniser(tete)
        return self._index

    def index_a_jour(self) -> bool:
        """Indique si l'index correspond au dernier commit de l'archive."""
        return self._index.est_a_jour(self._depot.tete())

    def chercher_identifiants(
        self,
        tags: Sequence[str] = (),
        mode_tags: Literal["et", "ou"] = "et",
        depuis: str | date | datetime | None = None,
        jusqua: str | date | datetime | None = None,
        gabarit: str | None = None,
        machine: str | None = None,
        statut: str | None = None,
        auteur: str | None = None,
        texte: str | None = None,
    ) -> list[str]:
        """Cherche des pages et renvoie leurs identifiants, de la plus récente à la plus ancienne.

        Voir `Criteres` pour le détail des critères.
        """
        return self._index_synchronise().chercher(
            Criteres(
                tags, mode_tags, depuis, jusqua, gabarit, machine, statut, auteur, texte
            )
        )

    def chercher_resumes(self, criteres: Criteres) -> list[ResumePage]:
        """Cherche des pages et les renvoie résumées, sans relire leurs fichiers."""
        return self._index_synchronise().chercher_resumes(criteres)

    def valeurs_possibles(self) -> ValeursPossibles:
        """Renvoie les valeurs présentes dans l'archive, pour proposer des filtres."""
        return self._index_synchronise().valeurs_possibles()

    def pages_illisibles(self) -> list[PageIllisible]:
        """Liste les pages que la recherche n'a pas pu lire."""
        return self._index_synchronise().pages_illisibles()

    def contenu_index(self) -> dict[str, list[tuple[Any, ...]]]:
        """Renvoie le contenu de l'index, pour le comparer à une reconstruction."""
        return self._index_synchronise().contenu()

    # --- Historique ------------------------------------------------------------

    def _modifications_csv_du_commit(
        self, commit: Commit, page: str, contenu_page: bytes | None
    ) -> tuple[Modification, ...]:
        """Compare les tableaux d'une page entre un commit et son parent."""
        if contenu_page is None or not commit.parents:
            return ()
        modifications: list[Modification] = []
        for nom, fichier in _tableaux_du_json(contenu_page):
            chemin = f"pages/{page}/{fichier}"
            ancien = self._depot.contenu(commit.parents[0], chemin)
            nouveau = self._depot.contenu(commit.id, chemin)
            if ancien is not None and nouveau is not None:
                modifications.extend(_differences_csv_brutes(nom, ancien, nouveau))
        return tuple(modifications)

    def _entrees(
        self, id_page: str | None = None, natures: Sequence[str] | None = None
    ) -> list[EntreeHistorique]:
        entrees: list[EntreeHistorique] = []
        for commit in self._depot.commits():
            infos = lire_message(commit.message.decode("utf-8"))
            page = infos.get("Page")
            if page is None or (id_page is not None and page != id_page):
                continue
            if natures is not None and infos.get("Nature") not in natures:
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
                    modifications=_comparer_versions(ancien, nouveau)
                    + self._modifications_csv_du_commit(
                        commit, page, nouveau or ancien
                    ),
                )
            )
        return entrees

    def precharger_historique(self) -> None:
        """Lit l'historique à l'avance, à appeler depuis un fil de travail."""
        self._depot.commits()

    def historique(self, id_page: str) -> list[EntreeHistorique]:
        """Renvoie la chronologie d'une page, de sa création à sa dernière version."""
        self._dossier_page(id_page)
        entrees = self._entrees(id_page)
        if not entrees:
            raise PageIntrouvable(f"Aucun historique pour la page « {id_page} ».")
        return entrees

    def corrections(self, id_page: str | None = None) -> list[EntreeHistorique]:
        """Renvoie les corrections d'une page ou de toute l'archive, y compris celles à la main."""
        return self._entrees(id_page, ("correction", NATURE_EXTERNE))

    def modifications_externes(
        self, id_page: str | None = None
    ) -> list[EntreeHistorique]:
        """Renvoie les versions créées toutes seules après une modification hors du logiciel."""
        return self._entrees(id_page, (NATURE_EXTERNE,))

    def version_page(self, id_page: str, commit: str) -> Page:
        """Relit une page telle qu'elle était à un commit de son historique."""
        self._dossier_page(id_page)
        contenu = self._depot.contenu(commit, _chemin_page_json(id_page))
        if contenu is None:
            raise PageIntrouvable(f"La page « {id_page} » n'existe pas à ce commit.")
        return depuis_json(contenu.decode("utf-8"))

    # --- Modifications hors du logiciel ----------------------------------------

    def _tableaux_declares_sur_disque(self) -> list[Path]:
        """Liste les CSV déclarés par les `page.json` du disque, même non encore suivis."""
        return [
            page_json.parent / fichier
            for page_json in self.racine.glob("pages/*/page.json")
            for _, fichier in _tableaux_du_json(page_json.read_bytes())
        ]

    def modifications_non_tracees(self) -> list[str]:
        """Liste les fichiers suivis qui diffèrent du dernier commit.

        Cela révèle une modification faite à la main ou un enregistrement interrompu.
        Les fichiers déposés dans `brut/` mais pas encore déclarés ne sont pas concernés.
        """
        suivis = self._depot.fichiers_suivis()
        sur_disque = [
            self.racine / nom
            for nom in (_FICHIER_CONFIGURATION, "FORMAT.md", ".gitignore")
        ]
        sur_disque += self.racine.glob("gabarits/*.toml")
        sur_disque += self.racine.glob("pages/*/page.json")
        sur_disque += self._tableaux_declares_sur_disque()

        ecarts = set(suivis) - {
            c.relative_to(self.racine).as_posix() for c in sur_disque
        }
        ecarts = {
            r
            for r in ecarts
            if not (self.racine / r).is_file()
            or (self.racine / r).read_bytes() != suivis[r]
        }
        for chemin in sur_disque:
            relatif = chemin.relative_to(self.racine).as_posix()
            if chemin.is_file():
                if chemin.read_bytes() != suivis.get(relatif):
                    ecarts.add(relatif)
            elif relatif in suivis:
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

    def _decrire_fichier_externe(self, relatif: str, base: str = "") -> str:
        """Nomme un fichier changé, avec sa date : le commit date seulement la détection."""
        chemin = self.racine / relatif
        nom = relatif.removeprefix(base)
        if not chemin.is_file():
            return f"{nom} supprimé"
        date_fichier = datetime.fromtimestamp(chemin.stat().st_mtime).astimezone()
        return f"{nom} daté du {date_fichier:%d/%m/%Y %H:%M}"

    def _modifications_sur_disque(
        self, id_page: str, fichiers: Sequence[str]
    ) -> tuple[Modification, ...]:
        """Compare à son dernier commit une page changée hors du logiciel."""
        chemin = self.racine / _chemin_page_json(id_page)
        ancien = self._depot.contenu_actuel(_chemin_page_json(id_page))
        nouveau = chemin.read_bytes() if chemin.is_file() else None
        modifications = list(_comparer_versions(ancien, nouveau))
        for nom, fichier in _tableaux_du_json(nouveau or ancien or b""):
            relatif = f"pages/{id_page}/{fichier}"
            avant = self._depot.contenu_actuel(relatif)
            if relatif in fichiers and avant is not None:
                if (self.racine / relatif).is_file():
                    apres = (self.racine / relatif).read_bytes()
                    modifications.extend(_differences_csv_brutes(nom, avant, apres))
        return tuple(modifications)

    def enregistrer_modifications_externes(self) -> list[str]:
        """Crée une nouvelle version de tout fichier suivi qui a changé hors du logiciel.

        Les fichiers sont enregistrés tels quels, sans jamais être réécrits, et leur ancienne
        version reste lisible dans Git. Une page donne un seul commit, même si son `page.json`
        et ses tableaux ont changé ensemble. Le motif est écrit par le logiciel : il ne peut
        pas distinguer une modification à la main d'un enregistrement interrompu. Renvoie les
        fichiers enregistrés.

        Ouvrir une archive depuis un script n'appelle jamais cette méthode : la relire n'écrit
        rien. L'interface l'appelle à chaque ouverture.
        """
        fichiers = self.modifications_non_tracees()
        par_page: dict[str, list[str]] = {}
        for fichier in fichiers:
            parties = fichier.split("/")
            cle = parties[1] if parties[0] == "pages" and len(parties) > 2 else ""
            if cle:
                par_page.setdefault(cle, []).append(fichier)
            else:
                self._depot.enregistrer(
                    message_commit(
                        NATURE_EXTERNE,
                        motif=_motif_externe(self._decrire_fichier_externe(fichier)),
                        sujet=f"{NATURE_EXTERNE} : {fichier}",
                    ),
                    [fichier],
                    _AUTEUR_EXTERNE,
                )
        for id_page, chemins in par_page.items():
            details = ", ".join(
                self._decrire_fichier_externe(c, f"pages/{id_page}/") for c in chemins
            )
            self._depot.enregistrer(
                message_commit(
                    NATURE_EXTERNE,
                    id_page,
                    _motif_externe(details),
                    self._modifications_sur_disque(id_page, chemins),
                ),
                chemins,
                _AUTEUR_EXTERNE,
            )
        return fichiers
