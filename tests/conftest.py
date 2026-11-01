"""Configuration des tests : l'interface Qt se teste sans écran."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
