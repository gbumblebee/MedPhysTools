"""Configuration des tests : l'interface Qt se teste sans écran."""

import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_runtest_teardown() -> None:
    """Libère les cycles d'objets si l'interface a coupé le ramasse-miettes automatique."""
    if not gc.isenabled() and gc.get_count()[0] > gc.get_threshold()[0]:
        gc.collect()
