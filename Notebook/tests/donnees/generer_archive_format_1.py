"""Génère l'archive de test figée du format 1. Ne pas relancer sans raison.

Cette archive garantit que les futures versions du logiciel relisent encore les archives
existantes. Si le code ne la relit plus, c'est le code qu'il faut corriger, ou une
migration qu'il faut écrire : on ne régénère pas ce fichier.

Usage : uv run python tests/donnees/generer_archive_format_1.py
"""

import sys
import tempfile
import zipfile
from pathlib import Path

ICI = Path(__file__).parent
sys.path.insert(0, str(ICI.parent))

from scenario_activimetre import construire  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as temporaire:
        racine = Path(temporaire) / "ArchiveFormat1"
        construire(racine, Path(temporaire) / "travail")
        with zipfile.ZipFile(
            ICI / "archive_format_1.zip", "w", zipfile.ZIP_DEFLATED
        ) as zip_:
            for chemin in sorted(racine.rglob("*")):
                zip_.write(chemin, chemin.relative_to(racine.parent).as_posix())


if __name__ == "__main__":
    main()
