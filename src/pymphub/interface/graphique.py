"""Dessin d'un graphe avec matplotlib, en 2D et en 3D, sans aucun appel à OpenGL.

Ce module est lourd à charger : il ne s'importe qu'au moment d'afficher un graphe.
"""

import math
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.backend_bases import MouseEvent
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg,
    NavigationToolbar2QT,
)
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from pymphub.graphes import Axe, DonneesGraphe
from pymphub.interface.theme import BORDURE, SERIES, SURFACE, TEXTE, TEXTE_DISCRET

# Une forme par série : l'identité ne repose jamais sur la seule couleur.
_MARQUEURS = ("o", "s", "^", "D", "v", "P", "X", "*")
_DISTANCE_SURVOL_PIXELS = 14.0
_TAILLE_POLICE = 9
_RESOLUTION_CAPTURE = 200
_FORMAT_CAPTURE = (8.0, 5.0)
_PLANCHER_LOG = 10.0  # Une barre qui passerait sous zéro s'arrête à valeur / 10.


class BarreOutils(NavigationToolbar2QT):
    """La barre d'outils de matplotlib, en français et sans ses réglages de mise en page."""

    toolitems = (
        ("Accueil", "Revenir à la vue d'origine", "home", "home"),
        ("Précédente", "Vue précédente", "back", "back"),
        ("Suivante", "Vue suivante", "forward", "forward"),
        (None, None, None, None),
        ("Déplacer", "Déplacer le graphe à la souris", "move", "pan"),
        ("Zoom", "Zoomer sur un rectangle", "zoom_to_rect", "zoom"),
        (None, None, None, None),
        ("Enregistrer", "Enregistrer l'image du graphe", "filesave", "save_figure"),
    )


def _ecriture_lisible(valeur: float, _position: int) -> str:
    """Écrit une graduation log en clair : 90 plutôt que 9 × 10¹."""
    return f"{valeur:g}"


def _graduer_en_log(axe: object) -> None:
    axe.set_major_formatter(FuncFormatter(_ecriture_lisible))
    axe.set_minor_formatter(FuncFormatter(_ecriture_lisible))


def _nombres(valeurs: Sequence[float]) -> np.ndarray:
    return np.asarray(valeurs, dtype=float)


def _barres(axe: Axe) -> np.ndarray:
    """Les demi-largeurs à tracer : une barre indéterminée n'est simplement pas dessinée."""
    return np.nan_to_num(_nombres(axe.elargies), nan=0.0)


def bornes_log(axe: Axe) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Passe un axe en log10 pour la 3D : valeurs, barre basse et barre haute.

    L'échelle log de matplotlib n'est pas fiable en 3D : on trace donc le logarithme, et
    l'axe l'annonce. Une barre log n'est pas symétrique.
    """
    valeurs = _nombres(axe.valeurs)
    barres = _barres(axe)
    basses = valeurs - barres
    basses = np.where(basses > 0, basses, valeurs / _PLANCHER_LOG)
    centre = np.log10(valeurs)
    return centre, centre - np.log10(basses), np.log10(valeurs + barres) - centre


def _style_axes(axes: Axes) -> None:
    axes.set_facecolor(SURFACE)
    axes.tick_params(colors=TEXTE_DISCRET, labelsize=_TAILLE_POLICE)
    axes.xaxis.label.set_color(TEXTE)
    axes.yaxis.label.set_color(TEXTE)
    axes.xaxis.label.set_size(_TAILLE_POLICE)
    axes.yaxis.label.set_size(_TAILLE_POLICE)


class ToileGraphe(QWidget):
    """Affiche un graphe, ou un message s'il ne peut pas être tracé."""

    def __init__(self, interactif: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.donnees: DonneesGraphe | None = None
        self.figure = Figure(facecolor=SURFACE, layout="constrained")
        self.canevas = FigureCanvasQTAgg(self.figure)
        self.axes: Axes | None = None
        self.message = QLabel("")
        self.message.setObjectName("alerte")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setVisible(False)
        self.lecture = QLabel("")
        self.lecture.setObjectName("discret")
        self.lecture.setTextFormat(Qt.TextFormat.PlainText)

        disposition = QVBoxLayout(self)
        disposition.setContentsMargins(0, 0, 0, 0)
        disposition.addWidget(self.message)
        if interactif:
            self.barre_outils = BarreOutils(self.canevas, self)
            disposition.addWidget(self.barre_outils)
            self.canevas.mpl_connect("motion_notify_event", self.survoler)
        else:
            # Dans une fiche, la molette doit continuer à faire défiler la page.
            self.canevas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        disposition.addWidget(self.canevas, stretch=1)
        if interactif:
            disposition.addWidget(self.lecture)

    def enregistrer_image(self, chemin: Path) -> None:
        """Écrit le graphe en image, assez nette pour l'imprimer.

        Son format est fixe : il ne doit pas dépendre de la taille de la fenêtre à cet instant.
        """
        taille = self.figure.get_size_inches().copy()
        self.figure.set_size_inches(*_FORMAT_CAPTURE, forward=False)
        try:
            self.figure.savefig(chemin, dpi=_RESOLUTION_CAPTURE, facecolor=SURFACE)
        finally:
            self.figure.set_size_inches(*taille, forward=False)
            self.canevas.draw_idle()

    def signaler(self, message: str) -> None:
        """Remplace le graphe par une explication."""
        self.donnees = None
        self.axes = None
        self.figure.clear()
        self.canevas.draw_idle()
        self.canevas.setVisible(False)
        self.message.setText(message)
        self.message.setVisible(True)

    def tracer(self, donnees: DonneesGraphe) -> None:
        """Dessine le graphe et annonce ce qui n'a pas pu y figurer."""
        self.donnees = donnees
        self.figure.clear()
        if donnees.reglages.mode == "3d":
            self.axes = self._tracer_3d(donnees)
            self.lecture.setText("Glisser sur le graphe pour le faire tourner.")
        else:
            self.axes = self._tracer_2d(donnees)
            self.lecture.setText("")
        self.canevas.setVisible(True)
        self.message.setText(" ".join(donnees.avertissements))
        self.message.setVisible(bool(donnees.avertissements))
        self.canevas.draw_idle()

    def _tracer_2d(self, donnees: DonneesGraphe) -> Axes:
        reglages = donnees.reglages
        axes = self.figure.add_subplot()
        _style_axes(axes)
        x = _nombres(donnees.x.valeurs)
        for rang, serie in enumerate(donnees.y):
            axes.errorbar(
                x,
                _nombres(serie.valeurs),
                xerr=_barres(donnees.x) if reglages.barres else None,
                yerr=_barres(serie) if reglages.barres else None,
                color=SERIES[rang],
                ecolor=SERIES[rang],
                marker=_MARQUEURS[rang],
                markersize=7,
                markeredgecolor=SURFACE,
                markeredgewidth=1,
                linestyle="-" if reglages.relier else "none",
                linewidth=1.5,
                elinewidth=1,
                capsize=3,
                label=serie.libelle,
            )
        for rang, courbe in enumerate(donnees.courbes, start=len(donnees.y)):
            couleur = SERIES[rang % len(SERIES)]
            abscisses, valeurs = _nombres(courbe.x), _nombres(courbe.y)
            bande = _nombres(courbe.elargies)
            axes.plot(
                abscisses,
                valeurs,
                color=couleur,
                linewidth=1.5,
                label=f"Ajustement {courbe.nom}",
            )
            axes.fill_between(
                abscisses, valeurs - bande, valeurs + bande, color=couleur, alpha=0.15
            )
        for repere in donnees.reperes:
            if repere.x is None:
                axes.axhline(repere.y, color=TEXTE_DISCRET, linewidth=1, linestyle="--")
                axes.annotate(
                    repere.nom,
                    xy=(1.0, repere.y),
                    xycoords=("axes fraction", "data"),
                    xytext=(-4, 3),
                    textcoords="offset points",
                    ha="right",
                    color=TEXTE,
                    fontsize=_TAILLE_POLICE,
                )
            else:
                axes.plot(
                    [repere.x],
                    [repere.y],
                    marker="o",
                    markersize=11,
                    markerfacecolor="none",
                    markeredgecolor=TEXTE,
                    linestyle="none",
                )
                axes.annotate(
                    repere.nom,
                    xy=(repere.x, repere.y),
                    xytext=(8, 8),
                    textcoords="offset points",
                    color=TEXTE,
                    fontsize=_TAILLE_POLICE,
                )
        axes.set_xlabel(donnees.x.libelle)
        # Une seule série : le libellé de l'axe suffit, une légende le répéterait.
        if len(donnees.y) == 1 and not donnees.courbes:
            axes.set_ylabel(donnees.y[0].libelle)
        else:
            unite = donnees.y[0].unite
            axes.set_ylabel("" if unite in ("", "1") else unite)
            legende = axes.legend(frameon=False, fontsize=_TAILLE_POLICE)
            for texte in legende.get_texts():
                texte.set_color(TEXTE)
        if reglages.echelle_x == "log":
            axes.set_xscale("log", nonpositive="clip")
            _graduer_en_log(axes.xaxis)
        if reglages.echelle_y == "log":
            axes.set_yscale("log", nonpositive="clip")
            _graduer_en_log(axes.yaxis)
        # Les graduations secondaires d'un axe log gardent la couleur des autres.
        axes.tick_params(which="both", colors=TEXTE_DISCRET, labelsize=_TAILLE_POLICE)
        axes.grid(True, color=BORDURE, linewidth=0.7)
        axes.set_axisbelow(True)
        for cote, bord in axes.spines.items():
            bord.set_color(BORDURE)
            bord.set_visible(cote in ("left", "bottom"))
        return axes

    def _axe_3d(self, axe: Axe, echelle: str) -> tuple[np.ndarray, object, str]:
        """Valeurs, barres et libellé d'un axe 3D, en logarithme si demandé."""
        if echelle != "log":
            return _nombres(axe.valeurs), _barres(axe), axe.libelle
        centre, basse, haute = bornes_log(axe)
        return centre, np.vstack((basse, haute)), f"log₁₀ {axe.libelle}"

    def _tracer_3d(self, donnees: DonneesGraphe) -> Axes:
        reglages = donnees.reglages
        axes = self.figure.add_subplot(projection="3d")
        _style_axes(axes)
        axes.zaxis.label.set_color(TEXTE)
        axes.zaxis.label.set_size(_TAILLE_POLICE)
        x, barres_x, libelle_x = self._axe_3d(donnees.x, reglages.echelle_x)
        y, barres_y, libelle_y = self._axe_3d(donnees.y[0], reglages.echelle_y)
        z, barres_z, libelle_z = self._axe_3d(donnees.z, reglages.echelle_z)
        if reglages.surface:
            self._tracer_surface(axes, x, y, z)
        axes.errorbar(
            x,
            y,
            z,
            xerr=barres_x if reglages.barres else None,
            yerr=barres_y if reglages.barres else None,
            zerr=barres_z if reglages.barres else None,
            color=SERIES[0],
            ecolor=SERIES[0],
            marker=_MARQUEURS[0],
            markersize=5,
            linestyle="-" if reglages.relier and not reglages.surface else "none",
            linewidth=1.5,
            elinewidth=1,
            capsize=2,
        )
        axes.set_xlabel(libelle_x)
        axes.set_ylabel(libelle_y)
        axes.set_zlabel(libelle_z)
        return axes

    def _tracer_surface(
        self, axes: Axes, x: np.ndarray, y: np.ndarray, z: np.ndarray
    ) -> None:
        """Dessine une surface si les points s'y prêtent ; sinon, les points restent seuls."""
        valides = np.isfinite(x) & np.isfinite(y) & np.isfinite(z)
        try:
            axes.plot_trisurf(
                x[valides], y[valides], z[valides], color=SERIES[0], alpha=0.25
            )
        except (ValueError, RuntimeError):
            self.donnees = _avec_avertissement(
                self.donnees,
                "Surface impossible : il faut au moins trois points non alignés.",
            )

    def survoler(self, evenement: MouseEvent) -> None:
        """Donne la valeur et l'incertitude du point le plus proche de la souris."""
        donnees = self.donnees
        if (
            donnees is None
            or donnees.reglages.mode != "2d"
            or self.axes is None
            or evenement.inaxes is not self.axes
        ):
            return
        meilleur: tuple[float, Axe, int] | None = None
        x = donnees.x.valeurs
        for serie in donnees.y:
            for rang, (vx, vy) in enumerate(zip(x, serie.valeurs)):
                if math.isnan(vx) or math.isnan(vy):
                    continue
                px, py = self.axes.transData.transform((vx, vy))
                distance = math.hypot(px - evenement.x, py - evenement.y)
                if meilleur is None or distance < meilleur[0]:
                    meilleur = (distance, serie, rang)
        if meilleur is None or meilleur[0] > _DISTANCE_SURVOL_PIXELS:
            self.lecture.setText("")
            return
        _distance, serie, rang = meilleur
        self.lecture.setText(
            f"{serie.nom}, ligne {rang + 1} : "
            f"{donnees.x.nom} = {_ecrire(x[rang], donnees.x)}, "
            f"{serie.nom} = {_ecrire(serie.valeurs[rang], serie, rang)} "
            f"(k = {donnees.k:g})"
        )


def _ecrire(valeur: float, axe: Axe, rang: int | None = None) -> str:
    unite = "" if axe.unite in ("", "1") else f" {axe.unite}"
    if rang is None or math.isnan(axe.elargies[rang]):
        return f"{valeur:.6g}{unite}"
    return f"{valeur:.6g} ± {axe.elargies[rang]:.2g}{unite}"


def _avec_avertissement(donnees: DonneesGraphe, message: str) -> DonneesGraphe:
    return DonneesGraphe(
        donnees.reglages,
        donnees.k,
        donnees.x,
        donnees.y,
        donnees.z,
        (*donnees.avertissements, message),
        donnees.courbes,
        donnees.reperes,
    )
