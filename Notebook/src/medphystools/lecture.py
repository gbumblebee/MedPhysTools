"""Lecture d'une archive depuis un script ou un notebook, sans interface.

Usage visé :

    from medphystools import Archive

    p = Archive("D:/ArchiveStage1").page("2026-11-14_activimetre-linearite_0007")
    p.mesures["activite"].valeur        # valeur brute, unité d'origine
    p.mesures["activite"].en("mCi")     # convertie, avec son incertitude
    p.mesures["activite"].incertitude   # objet GTC, pour propager dans un calcul
    p.mesures["activite"].budget        # budget complet, ligne par ligne
    p.tableau("decroissance")           # DataFrame pandas, unités dans attrs["unites"]
    p.fichiers["serie_ct"]              # chemin vérifié par empreinte
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, overload

import pandas as pd
from GTC.lib import UncertainReal

from medphystools.historique import EntreeHistorique
from medphystools.incertitudes import Budget, InfluencesCommunes, etablir_budget
from medphystools.page import (
    ColonneCalculee,
    ColonneNumerique,
    Fichier,
    GrandeurDerivee,
    Identifiant,
    Indicateur,
    Page,
    Scalaire,
    Tableau,
)

if TYPE_CHECKING:
    from medphystools.archive import Archive


class MesureLue:
    """Une valeur numérique relue, avec son unité et son incertitude.

    Pour une même mesure, utiliser le même objet : GTC tient ainsi compte de la
    corrélation quand elle apparaît plusieurs fois dans un calcul.
    """

    def __init__(
        self,
        nom: str,
        budget: Budget,
        horodatage: datetime | None = None,
        commentaire: str = "",
    ) -> None:
        self.nom = nom
        self.budget = budget
        self.horodatage = horodatage
        self.commentaire = commentaire

    @property
    def valeur(self) -> float:
        """La valeur, dans l'unité de cette mesure."""
        return self.budget.valeur

    @property
    def unite(self) -> str:
        """L'unité de cette mesure."""
        return self.budget.unite

    @property
    def incertitude(self) -> UncertainReal:
        """L'objet GTC de la mesure, à utiliser pour propager dans un calcul."""
        return self.budget.gtc

    def en(self, unite: str) -> MesureLue:
        """Renvoie la même mesure dans une autre unité, sans perdre les corrélations."""
        return MesureLue(
            self.nom, self.budget.en(unite), self.horodatage, self.commentaire
        )

    def elargie(self, k: float = 2.0) -> float:
        """Incertitude élargie U = k · u, dans l'unité de la mesure."""
        return self.budget.elargie(k)

    def elargie_relative(self, k: float = 2.0) -> float | None:
        """Incertitude élargie en % de la valeur ; None si la valeur est nulle."""
        return self.budget.elargie_relative(k)

    def __float__(self) -> float:
        return self.valeur

    def __repr__(self) -> str:
        return (
            f"<{self.nom} = {self.valeur:g} ± {self.elargie():g} {self.unite} (k = 2)>"
        )


class Mesures(Mapping[str, "MesureLue | Identifiant | Tableau | Fichier"]):
    """Les grandeurs d'une page, repérées par leur nom.

    Un scalaire est renvoyé sous forme de `MesureLue`, toujours le même objet pour une
    même page. Deux mesures qui partagent un repère d'erreur commune partagent cette erreur
    dans un calcul. Un résultat de calcul est relu tel qu'il a été enregistré : pour GTC, il
    devient une mesure indépendante. Les autres grandeurs sont renvoyées telles que décrites
    dans la page.
    """

    def __init__(self, page: Page, communes: InfluencesCommunes | None = None) -> None:
        self._page = page
        self._communes = communes or InfluencesCommunes()
        self._lues: dict[str, MesureLue] = {}

    def __getitem__(self, nom: str) -> MesureLue | Identifiant | Tableau | Fichier:
        if nom in self._lues:
            return self._lues[nom]
        grandeur = self._page.grandeur(nom)
        if isinstance(grandeur, Scalaire):
            budget = etablir_budget(
                grandeur.valeur, grandeur.unite, grandeur.composantes, self._communes
            )
            self._lues[nom] = MesureLue(
                nom, budget, grandeur.horodatage, grandeur.commentaire
            )
            return self._lues[nom]
        if isinstance(grandeur, (GrandeurDerivee, Indicateur)):
            self._lues[nom] = MesureLue(
                nom, grandeur.budget(), commentaire=grandeur.commentaire
            )
            return self._lues[nom]
        return grandeur

    def __iter__(self) -> Iterator[str]:
        return (grandeur.nom for grandeur in self._page.mesures)

    def __len__(self) -> int:
        return len(self._page.mesures)


class FichiersLus(Mapping[str, Path]):
    """Les fichiers bruts d'une page : l'accès à un fichier vérifie son empreinte.

    Lister les fichiers ou tester leur présence ne lit aucun fichier brut.
    """

    def __init__(self, archive: Archive, page: Page) -> None:
        self._archive = archive
        self._page = page

    def _noms(self) -> list[str]:
        return [g.nom for g in self._page.mesures if isinstance(g, Fichier)]

    def __getitem__(self, nom: str) -> Path:
        if nom not in self._noms():
            raise KeyError(f"Aucun fichier « {nom} » dans la page « {self._page.id} ».")
        return self._archive.chemin_fichier(self._page.id, nom)

    def __contains__(self, nom: object) -> bool:
        return nom in self._noms()

    def __iter__(self) -> Iterator[str]:
        return iter(self._noms())

    def __len__(self) -> int:
        return len(self._noms())


class PageLue:
    """Une page relue depuis une archive, telle qu'elle était à sa lecture."""

    def __init__(self, archive: Archive, page: Page) -> None:
        self._archive = archive
        self.contenu = page
        self.id = page.id
        self.titre = page.titre
        self.description = page.description
        self.date_mesure = page.date_mesure
        self.date_saisie = page.date_saisie
        self.auteur = page.auteur
        self.gabarit = page.gabarit
        self.site = page.site
        self.machine = page.machine
        self.equipements = page.equipements
        self.tags = page.tags
        self.statut = page.statut
        self.onglets = page.onglets
        self._communes = InfluencesCommunes()
        self.mesures = Mesures(page, self._communes)
        self.fichiers = FichiersLus(archive, page)

    def tableau(self, nom: str) -> pd.DataFrame:
        """Relit un tableau en DataFrame, avec ses unités dans `attrs["unites"]`.

        Une colonne d'identifiants reste du texte. Les colonnes n'ont pas forcément toutes
        la même longueur : une cellule vide vaut `nan` dans une colonne de nombres, `None`
        dans une colonne de textes ou de dates. Les colonnes calculées suivent les colonnes
        brutes, avec `nan` pour une ligne sans résultat. Leurs incertitudes se lisent dans
        `mesures`. Le DataFrame a autant de lignes que sa colonne la plus longue.
        """
        donnees = self._archive.lire_tableau(self.id, nom)
        calculees = [
            g
            for g in self.contenu.mesures
            if isinstance(g, ColonneCalculee) and nom in g.tableaux
        ]
        lignes = max((len(g.valeurs) for g in calculees), default=0)
        if lignes > len(donnees):
            unites = donnees.attrs["unites"]
            donnees = donnees.reindex(range(lignes))
            donnees.attrs["unites"] = unites
        for grandeur in calculees:
            valeurs = [float("nan") if v is None else v for v in grandeur.valeurs]
            valeurs += [float("nan")] * (len(donnees) - len(valeurs))
            donnees[grandeur.nom] = valeurs
            donnees.attrs["unites"][grandeur.nom] = grandeur.unite
        return donnees

    def cellule(self, tableau: str, colonne: str, ligne: int) -> MesureLue:
        """Renvoie une cellule numérique avec son incertitude.

        `ligne` est l'indice de la ligne dans le DataFrame, à partir de 0. Les composantes
        de la colonne et celles propres à la cellule sont combinées. Chaque appel crée
        une nouvelle mesure, indépendante de la précédente pour GTC, sauf pour ses erreurs
        communes.
        """
        description = self.contenu.grandeur(tableau)
        if not isinstance(description, Tableau):
            raise ValueError(f"« {tableau} » n'est pas un tableau.")
        colonnes = {c.nom: c for c in description.colonnes}
        if colonne not in colonnes:
            raise KeyError(f"Aucune colonne « {colonne} » dans « {tableau} ».")
        cible = colonnes[colonne]
        if not isinstance(cible, ColonneNumerique):
            raise ValueError(f"« {tableau}.{colonne} » n'a pas d'incertitude à donner.")

        donnees = self.tableau(tableau)
        valeur = float(donnees[colonne].iloc[ligne])
        if math.isnan(valeur):
            raise ValueError(f"La cellule {tableau}.{colonne}[{ligne}] est vide.")
        composantes = description.composantes_de_ligne(
            cible, lambda nom: float(donnees[nom].iloc[ligne])
        )
        budget = etablir_budget(valeur, cible.unite, composantes, self._communes)
        return MesureLue(f"{tableau}.{colonne}[{ligne}]", budget)

    def historique(self) -> list[EntreeHistorique]:
        """Renvoie la chronologie de la page, de sa création à sa dernière version."""
        return self._archive.historique(self.id)

    def corrections(self) -> list[EntreeHistorique]:
        """Renvoie les corrections motivées de la page."""
        return self._archive.corrections(self.id)

    def __repr__(self) -> str:
        return f"<Page {self.id} : {self.titre} ({self.statut})>"


class ResultatsRecherche(Sequence[PageLue]):
    """Les pages trouvées, relues seulement quand on y accède.

    Une page illisible n'empêche donc pas d'accéder aux autres : l'erreur n'apparaît
    qu'à l'accès à cette page. `identifiants` les liste sans en relire aucune.
    """

    def __init__(self, archive: Archive, identifiants: list[str]) -> None:
        self._archive = archive
        self.identifiants = identifiants
        self._lues: dict[str, PageLue] = {}

    def _page(self, identifiant: str) -> PageLue:
        if identifiant not in self._lues:
            self._lues[identifiant] = self._archive.page(identifiant)
        return self._lues[identifiant]

    @overload
    def __getitem__(self, indice: int) -> PageLue: ...

    @overload
    def __getitem__(self, indice: slice) -> list[PageLue]: ...

    def __getitem__(self, indice: int | slice) -> PageLue | list[PageLue]:
        if isinstance(indice, slice):
            return [self._page(i) for i in self.identifiants[indice]]
        return self._page(self.identifiants[indice])

    def __len__(self) -> int:
        return len(self.identifiants)

    def __repr__(self) -> str:
        return f"<{len(self)} page(s) : {self.identifiants}>"
