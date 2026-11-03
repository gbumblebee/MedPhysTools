"""Export du cahier de labo : une vue d'ensemble lisible sans le logiciel.

Ce module écrit le cahier en HTML, sans aucune dépendance à Qt : page par page, puis onglet par
onglet, dans l'ordre affiché. L'interface fournit les images et produit le PDF. Tout ce que
l'utilisateur a saisi est échappé : un texte ne peut pas être pris pour une mise en forme.
"""

import html
import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from pymphub.ajustements import etat_resultat
from pymphub.archive import Archive
from pymphub.calculs import etat_derivee
from pymphub.equations import RESOLUTION_IMPRESSION
from pymphub.fichiers import ETATS_ALERTE
from pymphub.graphes import empreinte_tableau, reglages_par_defaut
from pymphub.page import (
    Ajustement,
    ColonneCalculee,
    ColonneIdentifiant,
    ColonneIncertitude,
    ColonneNumerique,
    Copie,
    Fichier,
    GrandeurDerivee,
    Identifiant,
    Indicateur,
    OngletDonnees,
    OngletGraphes,
    OngletNotes,
    Page,
    PhotoAnnotee,
    ReferenceFichier,
    ReglagesGraphe,
    Scalaire,
    Tableau,
)
from pymphub.tags import decouper_note, note_en_html

_LIBELLES_METHODES = {
    "minimum": "Minimum",
    "maximum": "Maximum",
    "moyenne": "Moyenne",
    "mediane": "Médiane",
    "percentile": "Percentile",
    "ecart_type": "Écart type",
    "coefficient_variation": "Coefficient de variation",
    "moyenne_quadratique": "Moyenne quadratique",
    "somme": "Somme",
    "etendue": "Étendue",
    "nombre_points": "Nombre de points",
    "integrale": "Intégrale",
    "derivee": "Dérivée",
    "largeur_mi_hauteur": "Largeur à mi-hauteur",
    "abscisse_de_valeur": "Abscisse d'une valeur",
}
_STYLE = """
body { font-family: "Liberation Sans", Arial, Helvetica, sans-serif; font-size: 10pt; }
h1 { font-size: 16pt; }
h2 { font-size: 13pt; margin-top: 14px; }
h3 { font-size: 11pt; margin-top: 10px; }
th { background-color: #eef0f3; }
.discret { color: #5f6b7a; }
.alerte { color: #a4372a; font-weight: 600; }
.calculee { background-color: #f6f7f9; }
"""


class SourceImages(Protocol):
    """Ce que l'interface fournit : chaque méthode renvoie l'adresse de l'image, ou None."""

    def photo(self, page: Page, photo: PhotoAnnotee) -> str | None: ...

    def graphe(self, page: Page, reglages: ReglagesGraphe, k: float) -> str | None: ...


@dataclass(frozen=True)
class SansImages:
    """Écrit le cahier sans image, par exemple pour relire son texte."""

    def photo(self, page: Page, photo: PhotoAnnotee) -> str | None:
        return None

    def graphe(self, page: Page, reglages: ReglagesGraphe, k: float) -> str | None:
        return None


def _e(texte: object) -> str:
    return html.escape(str(texte))


def _nombre(valeur: float) -> str:
    return f"{valeur:.15g}"


def _unite(unite: str) -> str:
    return "" if unite == "1" else f" {unite}"


def _date(valeur: datetime) -> str:
    return valeur.strftime("%d/%m/%Y %H:%M")


def _table(entetes: Sequence[str], lignes: Iterable[Sequence[object]]) -> str:
    tete = "".join(f"<th>{_e(e)}</th>" for e in entetes)
    corps = "".join(
        "<tr>" + "".join(f"<td>{_e(c)}</td>" for c in ligne) + "</tr>"
        for ligne in lignes
    )
    return (
        '<table border="1" cellspacing="0" cellpadding="3" width="100%" '
        'style="border-collapse:collapse">'
        f"<tr>{tete}</tr>{corps}</table>"
        # Qt ignore la marge haute d'un titre qui suit un tableau.
        '<p style="font-size:5pt">&nbsp;</p>'
    )


def _alerte(texte: str) -> str:
    return f'<p class="alerte">{_e(texte)}</p>'


def _sources(budget_lignes: Iterable[object]) -> str:
    return " ; ".join(ligne.source for ligne in budget_lignes)


class _Ecrivain:
    """Écrit une page du cahier, et note ce que ses onglets ont déjà montré."""

    def __init__(
        self, archive: Archive, page: Page, images: SourceImages, k: float
    ) -> None:
        self.archive = archive
        self.page = page
        self.images = images
        self.k = k
        self.montrees: set[str] = set()

    def lire_tableau(self, nom: str) -> object:
        return self.archive.lire_tableau(self.page.id, nom)

    # --- Grandeurs ------------------------------------------------------------------------

    def scalaires(self, grandeurs: Sequence[object]) -> str:
        lignes = []
        for g in grandeurs:
            self.montrees.add(g.nom)
            if isinstance(g, Identifiant):
                lignes.append((g.nom, g.valeur, "", "", "", g.commentaire))
                continue
            budget = g.budget()
            relative = budget.elargie_relative(self.k)
            lignes.append(
                (
                    g.nom,
                    _nombre(g.valeur),
                    g.unite,
                    f"{budget.elargie(self.k):.2g}",
                    "—" if relative is None else f"{relative:.2g} %",
                    " · ".join(filter(None, (g.commentaire, _sources(budget.lignes)))),
                )
            )
        return _table(
            (
                "Nom",
                "Valeur",
                "Unité",
                f"U (k = {self.k:g})",
                "U relative",
                "Commentaire, sources",
            ),
            lignes,
        )

    def tableau(self, tableau: Tableau) -> str:
        """Écrit toutes les lignes : comme sur un cahier papier, rien n'est tronqué."""
        self.montrees.add(tableau.nom)
        parties = [f"<h3>Tableau {_e(tableau.nom)}</h3>"]
        if tableau.commentaire:
            parties.append(f'<p class="discret">{_e(tableau.commentaire)}</p>')
        try:
            donnees = self.lire_tableau(tableau.nom)
        except (ValueError, KeyError, OSError) as erreur:
            parties.append(_alerte(f"Tableau illisible : {erreur}"))
            return "".join(parties)
        calculees = [
            g
            for g in self.page.mesures
            if isinstance(g, ColonneCalculee) and tableau.nom in g.tableaux
        ]
        entetes = [
            c.nom
            if isinstance(c, ColonneIdentifiant) or c.unite in (None, "1")
            else f"{c.nom} ({c.unite})"
            for c in tableau.colonnes
        ]
        entetes += [
            f"{c.nom}{'' if c.unite == '1' else f' ({c.unite})'}, calculée"
            for c in calculees
        ]
        lignes = []
        for rang in range(len(donnees)):
            ligne: list[object] = []
            for colonne in tableau.colonnes:
                valeur = donnees[colonne.nom].iloc[rang]
                ligne.append(
                    valeur
                    if isinstance(colonne, ColonneIdentifiant)
                    else f"{float(valeur):.6g}"
                    if getattr(colonne, "formule", None)
                    else _nombre(float(valeur))
                )
            for calculee in calculees:
                if rang < len(calculee.valeurs) and calculee.valeurs[rang] is not None:
                    ligne.append(
                        f"{calculee.valeurs[rang]:.6g} ± "
                        f"{self.k * calculee.incertitudes_types[rang]:.2g}"
                    )
                else:
                    ligne.append("—")
            lignes.append(ligne)
        parties.append(_table(entetes, lignes))
        details = []
        for colonne in tableau.colonnes:
            morceaux = []
            if isinstance(colonne, ColonneNumerique) and colonne.composantes:
                morceaux.append(
                    "incertitude : "
                    + " ; ".join(
                        f"{c.valeur:g}{' %' if c.relative else _unite(c.unite or '1')}, "
                        f"{c.loi}, {c.source}"
                        for c in colonne.composantes
                    )
                )
            if isinstance(colonne, ColonneIncertitude):
                morceaux.append(
                    f"incertitude de {colonne.composante_de}, {colonne.source}"
                )
            if colonne.commentaire:
                morceaux.append(colonne.commentaire)
            if morceaux:
                details.append(f"{colonne.nom} : {' · '.join(morceaux)}")
        for calculee in calculees:
            details.append(f"{calculee.nom} = {calculee.formule}")
        if details:
            parties.append(
                '<p class="discret">' + "<br>".join(_e(d) for d in details) + "</p>"
            )
        return "".join(parties)

    def calculs(self, grandeurs: Sequence[object]) -> str:
        lignes = []
        alertes = []
        for g in grandeurs:
            self.montrees.add(g.nom)
            etat = etat_derivee(self.page, g, self.lire_tableau)
            if etat is not None:
                alertes.append(_alerte(f"{g.nom} : {etat}"))
            if isinstance(g, ColonneCalculee):
                resultat = f"Colonne de {', '.join(g.tableaux)}"
            else:
                budget = g.budget()
                resultat = (
                    f"{g.valeur:.6g} ± {budget.elargie(self.k):.2g}{_unite(g.unite)}"
                )
            lignes.append((g.nom, g.formule, resultat, g.commentaire))
        return _table(
            ("Nom", "Formule", f"Résultat (k = {self.k:g})", "Commentaire"), lignes
        ) + "".join(alertes)

    def indicateurs(self, indicateurs: Sequence[Indicateur]) -> str:
        if not indicateurs:
            return ""
        lignes = []
        alertes = []
        for i in indicateurs:
            self.montrees.add(i.nom)
            etat = etat_resultat(self.page, i, self.lire_tableau)
            if etat is not None:
                alertes.append(_alerte(f"{i.nom} : {etat}"))
            libelle = _LIBELLES_METHODES[i.methode]
            if i.parametre is not None:
                libelle = f"{libelle} ({i.parametre:g})"
            position = "—"
            if i.position is not None:
                u = i.incertitude_position
                position = f"{i.position:.6g}"
                if u:
                    position += f" ± {self.k * u:.2g}"
                position += _unite(i.unite_position or "1")
            source = "fonction ajustée" if i.source == "ajustement" else "points"
            lignes.append(
                [
                    i.nom,
                    libelle,
                    f"{source}, {i.tableau}.{i.y}, de {i.de:.6g} à {i.a:.6g}",
                    position,
                    f"{i.valeur:.6g} ± {self.k * i.incertitude_type:.2g}{_unite(i.unite)}",
                ]
            )
        entetes = ["Nom", "Indicateur", "Données", "En x =", "Résultat"]
        # Une colonne vide prendrait de la place aux résultats.
        if any(i.commentaire for i in indicateurs):
            entetes.append("Commentaire")
            for ligne, i in zip(lignes, indicateurs):
                ligne.append(i.commentaire)
        return "<h3>Indicateurs</h3>" + _table(entetes, lignes) + "".join(alertes)

    def ajustement(self, a: Ajustement) -> str:
        self.montrees.update((a.nom, a.courbe))
        chi2 = "" if a.chi2_reduit is None else f", χ² réduit = {a.chi2_reduit:.3g}"
        parties = [
            f"<h3>Ajustement {_e(a.nom)}</h3>",
            f"<p>{_e(a.modele)} : {_e(a.y)} = {_e(a.formule)}, en fonction de "
            f"{_e(a.x)}, sur {_e(a.tableau)}, {a.points} points{_e(chi2)}.</p>",
            f'<p class="discret">{_e(a.methode)}</p>',
        ]
        etat = etat_resultat(self.page, a, self.lire_tableau)
        if etat is not None:
            parties.append(_alerte(f"{a.nom} : {etat}"))
        parties.append(
            _table(
                ("Coefficient", "Valeur", f"U (k = {self.k:g})", "U relative", "Unité"),
                [
                    (
                        p.nom,
                        f"{p.valeur:.6g}",
                        f"{self.k * p.incertitude_type:.2g}",
                        "—"
                        if p.valeur == 0
                        else f"{100 * self.k * p.incertitude_type / abs(p.valeur):.2g} %",
                        p.unite,
                    )
                    for p in a.parametres
                ],
            )
        )
        noms = [p.nom for p in a.parametres]
        parties.append(
            _table(
                ("Corrélation", *noms),
                [
                    (nom, *(f"{r:.3f}" for r in ligne))
                    for nom, ligne in zip(noms, a.correlation)
                ],
            )
        )
        parties.append(
            self._image_graphe(
                ReglagesGraphe(
                    tableau=a.tableau,
                    x=a.x,
                    y=(a.y,),
                    courbes=(a.nom,),
                    relier=False,
                )
            )
        )
        return "".join(parties)

    def _image_graphe(self, reglages: ReglagesGraphe) -> str:
        adresse = self.images.graphe(self.page, reglages, self.k)
        return (
            f'<p align="center"><img src="{_e(adresse)}" width="{LARGEUR_IMAGE}"></p>'
            if adresse
            else ""
        )

    # --- Onglets --------------------------------------------------------------------------

    def _remarque_capture(self, photo: PhotoAnnotee) -> str:
        capture = photo.capture
        if capture is None:
            return ""
        try:
            empreinte = empreinte_tableau(
                self.page, capture.tableau, self.lire_tableau(capture.tableau)
            )
        except (ValueError, KeyError, OSError):
            return f"Capture : le tableau « {capture.tableau} » n'est plus lisible."
        if empreinte != capture.empreinte_donnees:
            return f"Capture ancienne : « {capture.tableau} » a changé depuis."
        return ""

    def notes(self, onglet: OngletNotes, etats: dict[str, str]) -> str:
        parties = []
        if onglet.texte:
            parties.append(
                note_en_html(
                    decouper_note(onglet.texte, self.page, self.lire_tableau, self.k),
                    resolution=RESOLUTION_IMPRESSION,
                )
            )
        for photo in onglet.photos:
            nom = photo.chemin.removeprefix("pieces_jointes/")
            adresse = self.images.photo(self.page, photo)
            if adresse:
                parties.append(
                    f'<p align="center"><img src="{_e(adresse)}" '
                    f'width="{LARGEUR_IMAGE}"></p>'
                )
            legende = photo.legende or nom
            parties.append(f'<p align="center" class="discret">{_e(legende)}</p>')
            for annotation in photo.annotations:
                if annotation.texte:
                    parties.append(
                        f'<p align="center" class="discret">{_e(annotation.texte)}</p>'
                    )
            if etats.get(photo.chemin) in ETATS_ALERTE:
                parties.append(_alerte(f"{nom} : fichier {etats[photo.chemin]}."))
            remarque = self._remarque_capture(photo)
            if remarque:
                parties.append(_alerte(remarque))
        return "".join(parties)

    def _grandeurs_de(self, onglet: OngletDonnees) -> list[object]:
        par_nom = {g.nom: g for g in self.page.mesures}
        return [par_nom[n] for n in onglet.grandeurs if n in par_nom]

    def fichiers(self, fichiers: Sequence[Fichier], etats: dict[str, str]) -> str:
        lignes = []
        for f in fichiers:
            self.montrees.add(f.nom)
            stockage = f.stockage
            if f.dicom is not None:
                nature, detail = "Copie DICOM", f.dicom.resume
            elif isinstance(stockage, Copie):
                nature, detail = "Copie", stockage.nom_origine
            elif isinstance(stockage, ReferenceFichier):
                nature, detail = "Référence fichier", stockage.chemin
            else:
                nature = "Référence DICOM"
                detail = f"{stockage.noeud} — {stockage.series_instance_uid}"
            lignes.append((f.nom, nature, detail, etats.get(f.nom, "—"), f.commentaire))
        return _table(("Nom", "Stockage", "Détail", "État", "Commentaire"), lignes)

    def onglet(self, onglet: object, etats: dict[str, str]) -> str:
        titre = f"<h2>{_e(onglet.titre or onglet.type)}</h2>"
        if isinstance(onglet, OngletNotes):
            return titre + self.notes(onglet, etats)
        if isinstance(onglet, OngletGraphes):
            reglages = reglages_par_defaut(self.page, onglet)
            par_nom = {g.nom: g for g in self.page.mesures}
            indicateurs = [
                par_nom[n]
                for n in onglet.indicateurs
                if isinstance(par_nom.get(n), Indicateur)
            ]
            image = self._image_graphe(reglages) if reglages is not None else ""
            return titre + image + self.indicateurs(indicateurs)
        if isinstance(onglet, OngletDonnees):
            grandeurs = self._grandeurs_de(onglet)
            parties = [titre]
            scalaires = [g for g in grandeurs if isinstance(g, (Scalaire, Identifiant))]
            if scalaires:
                parties.append(self.scalaires(scalaires))
            calculs = [
                g
                for g in grandeurs
                if isinstance(g, (GrandeurDerivee, ColonneCalculee))
            ]
            if calculs:
                parties.append(self.calculs(calculs))
            ajustements = [g for g in grandeurs if isinstance(g, Ajustement)]
            for a in ajustements:
                parties.append(self.ajustement(a))
            courbes = {a.courbe for a in ajustements}
            parties.extend(
                self.tableau(g)
                for g in grandeurs
                if isinstance(g, Tableau) and g.nom not in courbes
            )
            parties.append(
                self.indicateurs([g for g in grandeurs if isinstance(g, Indicateur)])
            )
            fichiers = [g for g in grandeurs if isinstance(g, Fichier)]
            if fichiers:
                parties.append(self.fichiers(fichiers, etats))
            return "".join(parties)
        contenu = json.dumps(
            onglet.model_dump(mode="json"), ensure_ascii=False, indent=1
        )
        return (
            titre
            + f'<p class="discret">Type d\'onglet « {_e(onglet.type)} », inconnu de cette '
            "version : son contenu est donné tel quel.</p>"
            + f"<pre>{_e(contenu)}</pre>"
        )

    # --- Page -----------------------------------------------------------------------------

    def _reste(self, etats: dict[str, str]) -> str:
        """Montre ce qu'aucun onglet n'affiche : le cahier ne doit rien omettre."""
        courbes = {g.courbe for g in self.page.mesures if isinstance(g, Ajustement)}
        reste = [
            g
            for g in self.page.mesures
            if g.nom not in self.montrees and g.nom not in courbes
        ]
        parties = []
        scalaires = [g for g in reste if isinstance(g, (Scalaire, Identifiant))]
        if scalaires:
            parties.append(self.scalaires(scalaires))
        calculs = [
            g for g in reste if isinstance(g, (GrandeurDerivee, ColonneCalculee))
        ]
        if calculs:
            parties.append(self.calculs(calculs))
        for g in reste:
            if isinstance(g, Ajustement):
                parties.append(self.ajustement(g))
            elif isinstance(g, Tableau):
                parties.append(self.tableau(g))
        indicateurs = [
            g
            for g in self.page.mesures
            if isinstance(g, Indicateur) and g.nom not in self.montrees
        ]
        parties.append(self.indicateurs(indicateurs))
        fichiers = [g for g in reste if isinstance(g, Fichier)]
        if fichiers:
            parties.append(self.fichiers(fichiers, etats))
        contenu = "".join(parties)
        return f"<h2>Autres grandeurs</h2>{contenu}" if contenu else ""

    def _pieces(self, etats: dict[str, str]) -> str:
        if not self.page.pieces_jointes:
            return ""
        lignes = [
            (
                p.chemin.removeprefix("pieces_jointes/"),
                etats.get(p.chemin, "—"),
                f"{p.empreinte[:16]}…",
                p.commentaire,
            )
            for p in self.page.pieces_jointes
        ]
        return "<h2>Pièces jointes</h2>" + _table(
            ("Fichier", "État", "Empreinte SHA-256", "Commentaire"), lignes
        )

    def _historique(self) -> str:
        try:
            entrees = self.archive.historique(self.page.id)
        except (KeyError, ValueError, OSError):
            return ""
        lignes = [
            (
                _date(e.date),
                e.auteur,
                e.nature,
                e.motif or "",
                ", ".join(m.champ for m in e.modifications[:_CHAMPS_MAX])
                + (" …" if len(e.modifications) > _CHAMPS_MAX else ""),
            )
            for e in entrees
            if e.nature != "enregistrement"
        ]
        if not lignes:
            return ""
        return "<h2>Historique</h2>" + _table(
            ("Date", "Auteur", "Nature", "Motif", "Champs"), lignes
        )

    def ecrire(self) -> str:
        page = self.page
        informations = [
            ("Identifiant", page.id),
            ("Statut", page.statut),
            ("Date de mesure", _date(page.date_mesure)),
            ("Date de saisie", _date(page.date_saisie)),
            ("Auteur", page.auteur),
            ("Machine", page.machine or "—"),
            ("Site", page.site or "—"),
            ("Gabarit", page.gabarit or "Page libre"),
            ("Tags", ", ".join(page.tags) or "—"),
            ("Description", page.description or "—"),
        ]
        etats = self.archive.etats_fichiers(page)
        a_signaler = [f"{c} ({e})" for c, e in etats.items() if e in ETATS_ALERTE]
        parties = [
            f'<h1 style="page-break-before:always">{_e(page.titre)}</h1>',
            '<table cellpadding="1">'
            + "".join(
                f"<tr><td><b>{_e(libelle)}</b>&nbsp;&nbsp;</td><td>{_e(valeur)}</td></tr>"
                for libelle, valeur in informations
            )
            + "</table>",
        ]
        if page.statut == "brouillon":
            parties.append(_alerte("Page non validée : ceci est un brouillon."))
        if a_signaler:
            parties.append(_alerte("Fichiers à vérifier : " + " ; ".join(a_signaler)))
        parties.extend(self.onglet(onglet, etats) for onglet in page.onglets)
        parties.append(self._reste(etats))
        parties.append(self._pieces(etats))
        parties.append(self._historique())
        return "".join(parties)


LARGEUR_IMAGE = 620
_CHAMPS_MAX = 4


def page_en_html(
    archive: Archive, id_page: str, images: SourceImages, k: float = 2.0
) -> str:
    """Écrit une page du cahier. Une page illisible est signalée, sans arrêter le cahier."""
    try:
        page = archive.lire_page(id_page)
    except (ValueError, KeyError, OSError) as erreur:
        return f'<h1 style="page-break-before:always">{_e(id_page)}</h1>' + _alerte(
            f"Page illisible : {erreur}"
        )
    return _Ecrivain(archive, page, images, k).ecrire()


def cahier_en_html(
    archive: Archive,
    identifiants: Sequence[str],
    images: SourceImages | None = None,
    k: float = 2.0,
    maintenant: datetime | None = None,
    avancer: Callable[[int], bool] | None = None,
) -> str | None:
    """Écrit le cahier : une couverture, un sommaire, puis chaque page.

    Les pages suivent l'ordre donné. `avancer` reçoit le nombre de pages écrites, et peut
    interrompre l'export en renvoyant False : la fonction renvoie alors None.
    """
    images = images or SansImages()
    configuration = archive.configuration
    date_export = maintenant or datetime.now().astimezone()
    sommaire = []
    for identifiant in identifiants:
        try:
            page = archive.lire_page(identifiant)
            sommaire.append(
                (_date(page.date_mesure), page.titre, page.statut, identifiant)
            )
        except (ValueError, KeyError, OSError):
            sommaire.append(("—", "Page illisible", "—", identifiant))
    pages = []
    for rang, identifiant in enumerate(identifiants, start=1):
        pages.append(page_en_html(archive, identifiant, images, k))
        if avancer is not None and not avancer(rang):
            return None
    return (
        f"<html><head><style>{_STYLE}</style></head><body>"
        f"<h1>Cahier de labo : {_e(configuration.nom)}</h1>"
        f"<p>Site : {_e(configuration.site)}<br>"
        f"Responsable : {_e(configuration.responsable)}<br>"
        f"Exporté le {_date(date_export)}, avec pymphub. "
        f"Incertitudes élargies à k = {k:g}.</p>"
        '<p class="discret">Cette copie est faite pour être relue sans le logiciel. '
        "L'archive reste la référence : ses fichiers, son historique et ses empreintes.</p>"
        "<h2>Sommaire</h2>"
        + _table(("Date", "Titre", "Statut", "Identifiant"), sommaire)
        + "".join(pages)
        + "</body></html>"
    )
