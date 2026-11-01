"""Scénario complet d'un CQ d'activimètre, avec des valeurs FICTIVES.

Ces valeurs servent uniquement à vérifier le logiciel : elles ne décrivent aucun appareil
ni aucune mesure réels. Les UID DICOM et les noms de machines sont également inventés.
"""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from pymphub import Archive
from pymphub.gabarit import creer_gabarit
from pymphub.incertitudes import Composante, repetitions
from pymphub.page import Page

FUSEAU = timezone(timedelta(hours=1))
MACHINE = "chu-fictif.medecine-nucleaire.activimetre.fabricant-modele.0001"
ID_PAGE = "2026-11-14_activimetre-linearite_0001"
ID_SUIVANTE = "2026-12-14_activimetre-linearite_0002"
REFERENCE_GABARIT = "activimetre.linearite@1"

MESURES_REPETEES = [100.1, 99.9, 100.2, 99.8, 100.0]
DECROISSANCE = {
    "lot": ["007", "007", "007", "007"],
    "heure": [0.0, 2.0, 4.0, 6.0],
    "activite": [100.0, 79.39, 63.03, 50.04],
    "u_activite": [0.2, 0.2, 0.2, 0.2],
}


def _composante_certificat() -> Composante:
    return Composante(
        type="B",
        valeur=1.0,
        unite="MBq",
        k=2.0,
        source="certificat d'étalonnage fictif",
        erreur_commune="certificat-fictif-001",
    )


def _activite_de_reference() -> dict:
    moyenne, type_a = repetitions(MESURES_REPETEES, "MBq")
    return {
        "nature": "scalaire",
        "nom": "activite_ref",
        "valeur": moyenne,
        "unite": "MBq",
        "composantes": [type_a.model_dump(), _composante_certificat().model_dump()],
    }


def _temperature() -> dict:
    return {
        "nature": "scalaire",
        "nom": "temperature",
        "valeur": 21.5,
        "unite": "degC",
        "composantes": [
            {
                "type": "B",
                "valeur": 0.5,
                "unite": "degC",
                "loi": "rectangulaire",
                "source": "résolution du thermomètre",
            }
        ],
    }


def _lot(valeur: str = "007") -> dict:
    return {"nature": "identifiant", "nom": "lot", "valeur": valeur}


def _tableau() -> dict:
    return {
        "nature": "tableau",
        "nom": "decroissance",
        "fichier": "brut/decroissance.csv",
        "colonnes": [
            {"nature": "identifiant", "nom": "lot"},
            {
                "nature": "numerique",
                "nom": "heure",
                "unite": "h",
                "composantes": [
                    {"type": "B", "valeur": 1.0, "unite": "min", "source": "horloge"}
                ],
            },
            {"nature": "numerique", "nom": "activite", "unite": "MBq"},
            {
                "nature": "incertitude",
                "nom": "u_activite",
                "composante_de": "activite",
                "type": "B",
                "unite": "MBq",
                "source": "afficheur",
            },
        ],
    }


def _serie_dicom() -> dict:
    return {
        "nature": "fichier",
        "nom": "serie_ct",
        "type_fichier": "DICOM",
        "stockage": {
            "mode": "reference_dicom",
            "study_instance_uid": "1.2.826.0.1.3680043.9.9999.1",
            "series_instance_uid": "1.2.826.0.1.3680043.9.9999.1.2",
            "sop_instance_uids": ["1.2.826.0.1.3680043.9.9999.1.2.3"],
            "noeud": "PACS_FICTIF",
            "date": "2026-11-14",
        },
    }


def _onglets(chemin_photo: str | None) -> list[dict]:
    photos = (
        [
            {
                "chemin": chemin_photo,
                "legende": "Flacon dans le puits",
                "annotations": [
                    {
                        "forme": "fleche",
                        "points": [[0.2, 0.2], [0.5, 0.6]],
                        "texte": "source",
                    }
                ],
            }
        ]
        if chemin_photo
        else []
    )
    return [
        {
            "id": "montage",
            "type": "notes",
            "titre": "Montage",
            "texte": "Flacon de Tc-99m au centre du puits.",
            "photos": photos,
        },
        {
            "id": "scalaires",
            "type": "scalaires",
            "titre": "Conditions",
            "grandeurs": ["activite_ref", "temperature", "lot"],
        },
        {
            "id": "releve",
            "type": "tableau",
            "titre": "Relevé",
            "grandeurs": ["decroissance"],
        },
        {
            "id": "courbe",
            "type": "graphes",
            "titre": "Courbe",
            "grandeurs": ["decroissance.heure", "decroissance.activite"],
        },
        {"id": "image", "type": "dicom", "titre": "Image", "grandeurs": ["serie_ct"]},
        {
            "id": "spectre",
            "type": "spectre_gamma",
            "titre": "Onglet d'une version future",
            "pics_keV": [140.5],
        },
    ]


def page_du_cq(
    mesures: list[dict],
    onglets: list[dict],
    pieces: list[dict] | None = None,
    **champs: object,
) -> Page:
    """Construit la page libre du CQ, avec des dates fixes."""
    return Page.model_validate(
        {
            "id": ID_PAGE,
            "titre": "Linéarité de l'activimètre",
            "description": "Décroissance du Tc-99m (valeurs fictives)",
            "date_mesure": datetime(2026, 11, 14, 9, 30, tzinfo=FUSEAU),
            "date_saisie": datetime(2026, 11, 14, 11, 5, tzinfo=FUSEAU),
            "auteur": "Physicien fictif",
            "machine": MACHINE,
            "tags": ["activimetre", "linearite", "Tc99m"],
            "mesures": mesures,
            "onglets": onglets,
            "pieces_jointes": pieces or [],
        }
        | champs
    )


def construire(racine: Path, travail: Path) -> Archive:
    """Déroule le scénario : page libre, correction, gabarit, seconde page et validation."""
    travail.mkdir(parents=True, exist_ok=True)
    photo = travail / "montage.jpg"
    photo.write_bytes(b"photo fictive")
    export = travail / "export_constructeur.txt"
    export.write_text("export constructeur fictif\n", encoding="utf-8")

    arc = Archive.creer(racine, "CHU fictif", "Responsable fictif", date(2026, 11, 1))
    arc.declarer_machine(MACHINE)

    # 1. Page libre : d'abord les conditions, en brouillon.
    arc.creer_page(
        page_du_cq(
            [_activite_de_reference(), _temperature(), _lot()],
            [o for o in _onglets(None) if o["id"] in ("montage", "scalaires")],
        )
    )

    # 2. On complète le brouillon : tableau, fichiers, photo et autres onglets.
    piece = arc.importer_piece_jointe(ID_PAGE, photo)
    fichier = arc.importer_copie(export, "export", type_fichier="export constructeur")
    mesures = [
        _activite_de_reference(),
        _temperature(),
        _lot(),
        _tableau(),
        fichier.model_dump(),
        _serie_dicom(),
    ]
    onglets = _onglets(piece.chemin)
    pieces = [piece.model_dump()]
    arc.modifier_page(
        page_du_cq(mesures, onglets, pieces), tableaux={"decroissance": DECROISSANCE}
    )

    # 3. Validation, puis correction motivée de la température.
    arc.valider_page(ID_PAGE)
    temperature = _temperature() | {"valeur": 22.0}
    arc.modifier_page(
        page_du_cq(
            [mesures[0], temperature, *mesures[2:]], onglets, pieces, statut="validée"
        ),
        motif="Température mal relevée",
    )

    # 4. Gabarit créé depuis ce qui a été fait, puis page d'origine rattachée.
    gabarit = creer_gabarit(
        arc.lire_page(ID_PAGE),
        "activimetre.linearite",
        ["activite_ref", "lot", "decroissance.heure", "decroissance.activite"],
        description="Variables utiles au post-traitement de la linéarité",
    )
    arc.enregistrer_gabarit(gabarit)
    origine = arc.lire_page(ID_PAGE)
    arc.modifier_page(
        origine.model_copy(update={"gabarit": gabarit.reference}),
        motif="Rattachement au gabarit créé depuis cette page",
    )

    # 5. CQ suivant, créé depuis le gabarit, puis rempli et validé.
    date_suivante = datetime(2026, 12, 14, 9, 30, tzinfo=FUSEAU)
    arc.creer_page(
        Page(
            id=ID_SUIVANTE,
            titre="Linéarité de l'activimètre, décembre",
            date_mesure=date_suivante,
            date_saisie=date_suivante,
            auteur="Physicien fictif",
            machine=MACHINE,
            tags=("activimetre", "linearite"),
            gabarit=gabarit.reference,
        )
    )
    suivante = arc.lire_page(ID_SUIVANTE)
    remplie = Page.model_validate(
        suivante.model_dump()
        | {"mesures": [_activite_de_reference(), _lot("008"), _tableau()]}
    )
    arc.modifier_page(
        remplie, tableaux={"decroissance": dict(DECROISSANCE, lot=["008"] * 4)}
    )
    arc.valider_page(ID_SUIVANTE)
    return arc
