"""Refait les logos à partir de `logo.svg` : uv run python outils/generer_logo.py.

L'application lit `logo.png`, et le raccourci Windows `logo.ico`. Pour changer de logo,
remplacer `logo.svg` puis lancer ce script. Pillow vient avec matplotlib.
"""

import io
import os
from pathlib import Path

from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

DOSSIER = Path(__file__).parent.parent / "src" / "pymphub" / "interface" / "ressources"
TAILLES_ICO = (16, 24, 32, 48, 64, 128, 256)


def rendre(moteur: QSvgRenderer, cote: int) -> Image.Image:
    image = QImage(cote, cote, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    peintre = QPainter(image)
    moteur.render(peintre)
    peintre.end()
    octets = QByteArray()
    tampon = QBuffer(octets)
    tampon.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(tampon, "PNG")
    return Image.open(io.BytesIO(bytes(octets)))


def main() -> None:
    _application = QGuiApplication([])
    moteur = QSvgRenderer(str(DOSSIER / "logo.svg"))
    if not moteur.isValid():
        raise SystemExit("logo.svg est illisible.")
    rendre(moteur, 512).save(DOSSIER / "logo.png")
    rendre(moteur, 256).save(DOSSIER / "logo.ico", sizes=[(t, t) for t in TAILLES_ICO])


if __name__ == "__main__":
    main()
