"""Configuration des tests : l'interface Qt se teste sans écran."""

import gc
import os
import tempfile

import pytest
from menus_reels import Menus

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Les tests ne doivent jamais toucher aux préférences réelles de l'utilisateur.
os.environ["MEDPHYSNOTEBOOK_CONFIG"] = tempfile.mkdtemp(prefix="medphysnotebook-tests-")


@pytest.fixture
def menus():
    """Relève les menus de clic droit réellement ouverts pendant un test."""
    resultat = Menus()
    yield resultat
    resultat.arreter()


def pytest_runtest_teardown() -> None:
    """Libère les cycles d'objets si l'interface a coupé le ramasse-miettes automatique."""
    if not gc.isenabled() and gc.get_count()[0] > gc.get_threshold()[0]:
        gc.collect()
