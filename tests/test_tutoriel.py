"""Vérifie que le tutoriel de lecture tourne encore : il cite beaucoup de l'API publique."""

import json
import os
import subprocess
import sys
from pathlib import Path

DOSSIER = Path(__file__).parent.parent / "docs" / "tutoriel"
CAHIER = DOSSIER / "lire_une_archive.ipynb"


def test_le_tutoriel_s_execute_du_debut_a_la_fin():
    cellules = json.loads(CAHIER.read_text(encoding="utf-8"))["cells"]
    code = "\n\n".join(
        "".join(c["source"]) for c in cellules if c["cell_type"] == "code"
    )
    assert "creer_archive_demo" in code
    resultat = subprocess.run(
        [sys.executable, "-c", code],
        cwd=DOSSIER,
        env={**os.environ, "MPLBACKEND": "Agg", "PYTHONPATH": str(DOSSIER)},
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert resultat.returncode == 0, resultat.stderr


def test_le_tutoriel_est_enregistre_avec_ses_resultats_et_sans_erreur():
    cellules = json.loads(CAHIER.read_text(encoding="utf-8"))["cells"]
    sorties = [
        sortie for c in cellules if c["cell_type"] == "code" for sortie in c["outputs"]
    ]
    assert sorties
    assert not [s for s in sorties if s["output_type"] == "error"]
