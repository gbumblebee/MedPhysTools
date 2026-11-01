"""Dépôt Git d'une archive, géré avec dulwich : rien à installer, Git reste compatible.

Le dépôt n'est ouvert que le temps de chaque opération : sous Windows, un fichier resté
ouvert empêcherait de déplacer l'archive.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dulwich.object_store import iter_tree_contents, tree_lookup_path
from dulwich.objects import Commit
from dulwich.repo import Repo

CONTENU_GITIGNORE = """\
objets/
index.sqlite
index.sqlite-*
pages/*/pieces_jointes/
pages/*/brut/*
!pages/*/brut/*.csv
.*.tmp
"""


def _identite(auteur: str) -> bytes:
    """Construit l'identité Git d'un auteur, à partir de son seul nom."""
    nom = " ".join(auteur.replace("<", " ").replace(">", " ").split()) or "inconnu"
    return f"{nom} <auteur@pymphub.invalid>".encode()


def date_du_commit(commit: Commit) -> datetime:
    """Renvoie la date d'un commit, avec son fuseau horaire."""
    fuseau = timezone(timedelta(seconds=commit.commit_timezone))
    return datetime.fromtimestamp(commit.commit_time, tz=fuseau)


def auteur_du_commit(commit: Commit) -> str:
    """Renvoie le nom de l'auteur d'un commit, sans son adresse."""
    return commit.author.decode("utf-8").rsplit(" <", 1)[0]


class Depot:
    """Le dépôt Git situé à la racine d'une archive."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine

    @classmethod
    def initialiser(cls, racine: Path) -> "Depot":
        """Crée le dépôt et le configure pour rester relisible par Git sous Windows."""
        (racine / ".gitignore").write_bytes(CONTENU_GITIGNORE.encode("utf-8"))
        with Repo.init(str(racine)) as depot:
            configuration = depot.get_config()
            configuration.set((b"core",), b"autocrlf", False)
            configuration.set((b"core",), b"longpaths", True)
            configuration.write_to_path()
        return cls(racine)

    def enregistrer(self, message: str, chemins: Iterable[str], auteur: str) -> str:
        """Crée un commit avec ces fichiers, relatifs à la racine, et renvoie son identifiant.

        Seuls les fichiers nommés entrent dans le commit. Un fichier disparu est retiré
        du dépôt.
        """
        fuseau = datetime.now().astimezone().utcoffset() or timedelta(0)
        decalage = int(fuseau.total_seconds())
        identite = _identite(auteur)
        with Repo(str(self.racine)) as depot:
            arbre = depot.get_worktree()
            arbre.stage(list(chemins))
            identifiant = arbre.commit(
                message=message.encode("utf-8"),
                author=identite,
                committer=identite,
                author_timezone=decalage,
                commit_timezone=decalage,
            )
        return identifiant.decode("ascii")

    def commits(self) -> list[Commit]:
        """Renvoie tous les commits, du plus ancien au plus récent."""
        with Repo(str(self.racine)) as depot:
            return [entree.commit for entree in depot.get_walker()][::-1]

    def contenu(self, commit: str | bytes, chemin: str) -> bytes | None:
        """Renvoie un fichier tel qu'il était à un commit, ou None s'il n'existait pas."""
        identifiant = commit.encode("ascii") if isinstance(commit, str) else commit
        with Repo(str(self.racine)) as depot:
            try:
                arbre = depot[identifiant].tree
                _, objet = tree_lookup_path(
                    depot.object_store.__getitem__, arbre, chemin.encode("utf-8")
                )
            except KeyError:
                return None
            return depot[objet].data

    def contenu_actuel(self, chemin: str) -> bytes | None:
        """Renvoie un fichier tel qu'il est dans le dernier commit."""
        with Repo(str(self.racine)) as depot:
            tete = depot.head()
        return self.contenu(tete, chemin)

    def fichiers_suivis(self) -> dict[str, bytes]:
        """Renvoie le contenu de tous les fichiers du dernier commit."""
        with Repo(str(self.racine)) as depot:
            arbre = depot[depot.head()].tree
            return {
                entree.path.decode("utf-8"): depot[entree.sha].data
                for entree in iter_tree_contents(depot.object_store, arbre)
            }
