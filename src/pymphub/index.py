"""Index de recherche SQLite : un simple cache des fichiers `page.json`.

Les fichiers restent la seule source de vérité : l'index peut être supprimé à tout
moment, il se reconstruit à la recherche suivante. Il est mis à jour dès que le dernier
commit de l'archive n'est plus celui qu'il a mémorisé. Seules les pages dont le contenu
a changé sont alors relues.

Le contenu des pages est lu directement en JSON : une page d'un ancien format, ou
devenue invalide, reste ainsi repérable et n'empêche pas de chercher dans les autres.
"""

import contextlib
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

VERSION_INDEX = 2

_SCHEMA = """
CREATE TABLE meta (cle TEXT PRIMARY KEY, valeur TEXT NOT NULL);
CREATE TABLE pages (
    numero INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    empreinte TEXT NOT NULL,
    titre TEXT NOT NULL,
    date_mesure TEXT NOT NULL,
    instant REAL NOT NULL,
    date_locale TEXT NOT NULL,
    gabarit TEXT,
    gabarit_nom TEXT,
    machine TEXT,
    statut TEXT,
    auteur TEXT
);
CREATE INDEX pages_date ON pages (date_locale);
CREATE INDEX pages_ordre ON pages (instant DESC, id DESC);
CREATE TABLE tags (
    numero INTEGER NOT NULL REFERENCES pages (numero) ON DELETE CASCADE,
    tag TEXT NOT NULL COLLATE NOCASE
);
CREATE INDEX tags_tag ON tags (tag, numero);
CREATE INDEX tags_numero ON tags (numero);
CREATE TABLE illisibles (id TEXT PRIMARY KEY, empreinte TEXT NOT NULL, erreur TEXT NOT NULL);
CREATE VIRTUAL TABLE recherche USING fts5(texte, tokenize = "unicode61 remove_diacritics 2");
"""


class CritereInvalide(ValueError):
    """Un critère de recherche ne peut pas être interprété."""


@dataclass(frozen=True)
class Criteres:
    """Les critères d'une recherche : tous doivent être respectés.

    `depuis` et `jusqua` sont inclus, et comparés à la date de la mesure dans son propre
    fuseau. `gabarit` accepte une référence (`nom@1`) ou un nom seul, pour toutes ses
    versions. `mode_tags` vaut « et » (tous les tags) ou « ou » (au moins un).
    """

    tags: Sequence[str] = ()
    mode_tags: Literal["et", "ou"] = "et"
    depuis: str | date | datetime | None = None
    jusqua: str | date | datetime | None = None
    gabarit: str | None = None
    machine: str | None = None
    statut: str | None = None
    auteur: str | None = None
    texte: str | None = None


@dataclass(frozen=True)
class ResumePage:
    """Ce qu'il faut pour afficher une page dans une liste, sans la relire."""

    id: str
    titre: str
    date_mesure: str
    machine: str | None
    statut: str | None
    auteur: str | None
    gabarit: str | None


@dataclass(frozen=True)
class ValeursPossibles:
    """Les valeurs présentes dans l'archive pour chaque filtre."""

    gabarits: tuple[str, ...]
    machines: tuple[str, ...]
    auteurs: tuple[str, ...]
    statuts: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PageIllisible:
    """Une page que l'index n'a pas pu lire."""

    id: str
    erreur: str


def _textes(valeur: Any) -> Iterator[str]:  # noqa: ANN401 - contenu JSON quelconque
    """Parcourt le contenu JSON à indexer et donne tous ses textes."""
    if isinstance(valeur, str):
        yield valeur
    elif isinstance(valeur, list):
        for element in valeur:
            yield from _textes(element)


def _texte_de_grandeur(grandeur: dict[str, Any]) -> Iterator[str]:
    for champ in ("nom", "commentaire", "type_fichier", "formule", "modele"):
        yield from _textes(grandeur.get(champ))
    if grandeur.get("nature") == "identifiant":
        yield from _textes(grandeur.get("valeur"))
    for composante in grandeur.get("composantes", []):
        yield from _textes(composante.get("source"))
    for colonne in grandeur.get("colonnes", []):
        yield from _textes(colonne.get("nom"))
        yield from _textes(colonne.get("commentaire"))
    stockage = grandeur.get("stockage")
    if isinstance(stockage, dict):
        for champ in ("nom_origine", "description", "chemin", "noeud"):
            yield from _textes(stockage.get(champ))


def _texte_de_page(page: dict[str, Any]) -> str:
    """Rassemble le texte libre d'une page. Les cellules des tableaux n'y entrent pas."""
    morceaux: list[str] = []
    for champ in (
        "id",
        "titre",
        "description",
        "site",
        "machine",
        "gabarit",
        "tags",
        "equipements",
    ):
        morceaux.extend(_textes(page.get(champ)))
    for grandeur in page.get("mesures", []):
        morceaux.extend(_texte_de_grandeur(grandeur))
    for piece in page.get("pieces_jointes", []):
        morceaux.extend(_textes(piece.get("commentaire")))
    for onglet in page.get("onglets", []):
        for champ in ("titre", "texte"):
            morceaux.extend(_textes(onglet.get(champ)))
        for photo in onglet.get("photos", []):
            morceaux.extend(_textes(photo.get("legende")))
            for annotation in photo.get("annotations", []):
                morceaux.extend(_textes(annotation.get("texte")))
    return "\n".join(morceaux)


def _requete_plein_texte(texte: str) -> str | None:
    """Traduit un texte libre en requête : tous les mots, au début d'un mot indexé.

    Chaque mot est mis entre guillemets : la saisie ne peut donc pas être interprétée
    comme une commande de recherche.
    """
    mots = re.findall(r"\w+", texte)
    if not mots:
        return None
    return " ".join(f'"{mot}"*' for mot in mots)


def _en_date(valeur: str | date | datetime | None, nom: str) -> str | None:
    """Ramène un critère de date à une date ISO, dans le fuseau de la mesure."""
    if valeur is None:
        return None
    if isinstance(valeur, datetime):
        return valeur.date().isoformat()
    if isinstance(valeur, date):
        return valeur.isoformat()
    try:
        return datetime.fromisoformat(valeur).date().isoformat()
    except ValueError as erreur:
        raise CritereInvalide(
            f"Date « {valeur} » invalide pour « {nom} » : format attendu AAAA-MM-JJ."
        ) from erreur


class Index:
    """L'index de recherche d'une archive."""

    def __init__(self, chemin: Path, dossier_pages: Path) -> None:
        self.chemin = chemin
        self.dossier_pages = dossier_pages

    def _connexion(self) -> sqlite3.Connection:
        connexion = sqlite3.connect(str(self.chemin))
        connexion.execute("PRAGMA foreign_keys = ON")
        return connexion

    def _creer(self) -> None:
        """Repart d'un index vide, en remplaçant un fichier absent, ancien ou abîmé."""
        self.chemin.unlink(missing_ok=True)
        with contextlib.closing(self._connexion()) as connexion:
            connexion.executescript(_SCHEMA)
            connexion.execute(
                "INSERT INTO meta VALUES ('version', ?)", (str(VERSION_INDEX),)
            )
            connexion.commit()

    def _lire_meta(self, cle: str) -> str | None:
        """Lit une information de l'index ; None si le fichier est absent ou abîmé."""
        if not self.chemin.is_file():
            return None
        try:
            with contextlib.closing(self._connexion()) as connexion:
                ligne = connexion.execute(
                    "SELECT valeur FROM meta WHERE cle = ?", (cle,)
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        return ligne[0] if ligne else None

    def est_a_jour(self, commit: str) -> bool:
        """Indique si l'index correspond à ce commit, et à ce format d'index."""
        return (
            self._lire_meta("version") == str(VERSION_INDEX)
            and self._lire_meta("commit") == commit
        )

    def synchroniser(self, commit: str) -> None:
        """Met l'index en accord avec les fichiers, puis mémorise le commit."""
        if self._lire_meta("version") != str(VERSION_INDEX):
            self._creer()

        with contextlib.closing(self._connexion()) as connexion, connexion:
            connues = dict(connexion.execute("SELECT id, empreinte FROM pages"))
            connues.update(connexion.execute("SELECT id, empreinte FROM illisibles"))

            presentes: set[str] = set()
            for fichier in self.dossier_pages.glob("*/page.json"):
                id_page = fichier.parent.name
                presentes.add(id_page)
                contenu = fichier.read_bytes()
                empreinte = hashlib.sha256(contenu).hexdigest()
                if connues.get(id_page) != empreinte:
                    self._retirer(connexion, id_page)
                    self._ajouter(connexion, id_page, contenu, empreinte)

            for id_page in set(connues) - presentes:
                self._retirer(connexion, id_page)

            connexion.execute(
                "INSERT OR REPLACE INTO meta VALUES ('commit', ?)", (commit,)
            )

    @staticmethod
    def _retirer(connexion: sqlite3.Connection, id_page: str) -> None:
        connexion.execute("DELETE FROM illisibles WHERE id = ?", (id_page,))
        ligne = connexion.execute(
            "SELECT numero FROM pages WHERE id = ?", (id_page,)
        ).fetchone()
        if ligne:
            connexion.execute("DELETE FROM recherche WHERE rowid = ?", ligne)
            connexion.execute("DELETE FROM pages WHERE numero = ?", ligne)

    @staticmethod
    def _ajouter(
        connexion: sqlite3.Connection, id_page: str, contenu: bytes, empreinte: str
    ) -> None:
        try:
            page = json.loads(contenu)
            if page["id"] != id_page:
                raise ValueError(f"le fichier contient la page « {page['id']} »")
            mesure = datetime.fromisoformat(page["date_mesure"])
            gabarit = page.get("gabarit")
            ligne = (
                id_page,
                empreinte,
                str(page.get("titre", "")),
                page["date_mesure"],
                mesure.timestamp(),
                mesure.date().isoformat(),
                gabarit,
                gabarit.split("@")[0] if gabarit else None,
                page.get("machine"),
                page.get("statut"),
                page.get("auteur"),
            )
            texte = _texte_de_page(page)
            tags = [tag for tag in page.get("tags", []) if isinstance(tag, str)]
        except (ValueError, KeyError, TypeError, AttributeError) as erreur:
            connexion.execute(
                "INSERT INTO illisibles VALUES (?, ?, ?)",
                (id_page, empreinte, f"{type(erreur).__name__} : {erreur}"),
            )
            return

        curseur = connexion.execute(
            "INSERT INTO pages (id, empreinte, titre, date_mesure, instant, date_locale,"
            " gabarit, gabarit_nom,"
            " machine, statut, auteur) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ligne,
        )
        numero = curseur.lastrowid
        connexion.execute(
            "INSERT INTO recherche (rowid, texte) VALUES (?, ?)", (numero, texte)
        )
        connexion.executemany(
            "INSERT INTO tags VALUES (?, ?)", [(numero, tag) for tag in tags]
        )

    def chercher(self, criteres: Criteres) -> list[str]:
        """Renvoie les identifiants des pages qui respectent tous les critères."""
        return [resume.id for resume in self.chercher_resumes(criteres)]

    def chercher_resumes(self, criteres: Criteres) -> list[ResumePage]:
        """Renvoie les pages qui respectent tous les critères, résumées pour une liste."""
        if criteres.mode_tags not in ("et", "ou"):
            raise CritereInvalide(
                f"Mode de tags « {criteres.mode_tags} » : « et » ou « ou » attendu."
            )

        conditions: list[str] = []
        parametres: list[str | int] = []

        def ajouter(condition: str, *valeurs: str | int) -> None:
            conditions.append(condition)
            parametres.extend(valeurs)

        debut = _en_date(criteres.depuis, "depuis")
        fin = _en_date(criteres.jusqua, "jusqua")
        if debut is not None:
            ajouter("p.date_locale >= ?", debut)
        if fin is not None:
            ajouter("p.date_locale <= ?", fin)
        if criteres.gabarit is not None:
            colonne = "p.gabarit" if "@" in criteres.gabarit else "p.gabarit_nom"
            ajouter(f"{colonne} = ?", criteres.gabarit)
        if criteres.machine is not None:
            ajouter("p.machine = ?", criteres.machine)
        if criteres.statut is not None:
            ajouter("p.statut = ?", criteres.statut)
        if criteres.auteur is not None:
            ajouter("p.auteur = ?", criteres.auteur)

        tags_uniques = list(dict.fromkeys(tag.casefold() for tag in criteres.tags))
        if tags_uniques:
            marques = ", ".join("?" for _ in tags_uniques)
            minimum = len(tags_uniques) if criteres.mode_tags == "et" else 1
            ajouter(
                "p.numero IN (SELECT numero FROM tags WHERE tag IN "
                f"({marques}) GROUP BY numero HAVING COUNT(DISTINCT tag COLLATE NOCASE) >= ?)",
                *tags_uniques,
                minimum,
            )

        if criteres.texte is not None:
            requete = _requete_plein_texte(criteres.texte)
            if requete is not None:
                ajouter(
                    "p.numero IN (SELECT rowid FROM recherche WHERE recherche MATCH ?)",
                    requete,
                )

        ou = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with contextlib.closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT p.id, p.titre, p.date_mesure, p.machine, p.statut, p.auteur, p.gabarit"
                f" FROM pages AS p {ou} ORDER BY p.instant DESC, p.id DESC",
                parametres,
            ).fetchall()
        return [ResumePage(*ligne) for ligne in lignes]

    def valeurs_possibles(self) -> ValeursPossibles:
        """Renvoie les valeurs présentes dans l'archive, pour proposer des filtres."""

        def distinctes(connexion: sqlite3.Connection, colonne: str) -> tuple[str, ...]:
            lignes = connexion.execute(
                f"SELECT DISTINCT {colonne} FROM pages"
                f" WHERE {colonne} IS NOT NULL ORDER BY {colonne}"
            )
            return tuple(ligne[0] for ligne in lignes)

        with contextlib.closing(self._connexion()) as connexion:
            return ValeursPossibles(
                gabarits=distinctes(connexion, "gabarit"),
                machines=distinctes(connexion, "machine"),
                auteurs=distinctes(connexion, "auteur"),
                statuts=distinctes(connexion, "statut"),
                tags=tuple(
                    ligne[0]
                    for ligne in connexion.execute(
                        "SELECT DISTINCT tag FROM tags ORDER BY tag COLLATE NOCASE"
                    )
                ),
            )

    def pages_illisibles(self) -> list[PageIllisible]:
        """Liste les pages que l'index n'a pas pu lire."""
        with contextlib.closing(self._connexion()) as connexion:
            lignes = connexion.execute(
                "SELECT id, erreur FROM illisibles ORDER BY id"
            ).fetchall()
        return [PageIllisible(*ligne) for ligne in lignes]

    def contenu(self) -> dict[str, list[tuple[Any, ...]]]:
        """Renvoie le contenu logique de l'index, indépendant de l'ordre de construction."""
        with contextlib.closing(self._connexion()) as connexion:
            return {
                "pages": connexion.execute(
                    "SELECT id, empreinte, titre, date_mesure, instant, date_locale,"
                    " gabarit, gabarit_nom,"
                    " machine, statut, auteur FROM pages ORDER BY id"
                ).fetchall(),
                "tags": connexion.execute(
                    "SELECT p.id, t.tag FROM tags AS t JOIN pages AS p USING (numero)"
                    " ORDER BY p.id, t.tag"
                ).fetchall(),
                "recherche": connexion.execute(
                    "SELECT p.id, r.texte FROM recherche AS r"
                    " JOIN pages AS p ON p.numero = r.rowid ORDER BY p.id"
                ).fetchall(),
                "illisibles": connexion.execute(
                    "SELECT id, empreinte, erreur FROM illisibles ORDER BY id"
                ).fetchall(),
                "meta": connexion.execute("SELECT * FROM meta ORDER BY cle").fetchall(),
            }
