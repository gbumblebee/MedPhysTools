# MedPhysTools

Free and open-source tools for the daily work of medical physicists.

The first and only application so far is **MedPhysNotebook**, a local laboratory notebook for quality-control measurements. It comes with a Python API, so you can read your data back and post-process it with your own code.

> **French only for now.** The interface, the documentation and the Python names (`Archive`, `mesures`, `incertitude`…) only exist in French. Translations do not exist yet.

> **Early-stage project.** It is developed on Linux, and has not yet been tried on a real Windows computer. DICOM import has only been tried on synthetic files. This is not a certified medical device: check its results independently before relying on them. Patient dosimetry is out of scope.

## What MedPhysNotebook does

- **Records measurements** in free-form pages with tabs: values, tables, notes, photos, attachments, DICOM series, with optional templates.
- **Handles uncertainties and units**: GUM uncertainty budgets (with [GTC](https://github.com/MSLNZ/GTC)), including errors shared between measurements, and unit checks (with [pint](https://github.com/hgrecco/pint)).
- **Calculates**: formulas, derivatives and integrals of columns, curve fits, 2D and 3D graphs.
- **Keeps raw data traceable**: an archive is a self-contained folder with a full version history, and any change to a validated page requires a written reason.
- **Pseudonymises DICOM** files on import, and includes a viewer.
- **Finds and shares**: search by tag, date, machine or text, and export the notebook to PDF.

It needs no server, no database to install and no open network port, so that it can suit a hospital computer.

## Getting started

You need [uv](https://docs.astral.sh/uv/) and Python 3.12 or later. The code is in the `Notebook` folder.

```bash
git clone https://github.com/gbumblebee/MedPhysTools.git
cd MedPhysTools/Notebook
uv sync
uv run medphysnotebook
```

On Windows, after `uv sync`, you can also double-click `lanceurs\windows\medphysnotebook.vbs`.

## Examples

### In the application: a routine check

A typical session for a dose calibrator (*activimètre*) linearity check:

1. Create or open an archive, for example one per training placement.
2. Add a page, then tag it `activimetre`.
3. Type a decay table, or import it from a spreadsheet or instrument export.
4. Declare the uncertainty components, such as the source certificate or the display resolution.
5. Fit a curve, add a graph, write your notes, and validate the page.
6. Export the page to PDF. Later corrections stay visible in the history, with their reason.

### In Python: read a page back

All the examples below use real output from the fictional archive built by the tutorial.

```python
from medphystools import Archive

archive = Archive("D:/ArchiveStage1")

# Find pages by tag and date, most recent first
pages = archive.chercher(tags=["activimetre"], depuis="2026-11-01")
print(pages.identifiants)
# ['2026-11-21_linearite_0002', '2026-11-14_decroissance-tc99m_0001']

page = archive.page("2026-11-14_decroissance-tc99m_0001")
```

### Use a value with its uncertainty

```python
activity = page.mesures["activite_ref"]

print(activity)                # <activite_ref = 100 ± 2.06204 MBq (k = 2)>
print(activity.valeur)         # 100.0
print(activity.unite)          # MBq
print(activity.elargie(2))     # 2.062037827005121, the expanded uncertainty at k = 2
print(activity.en("mCi"))      # <activite_ref = 2.7027 ± 0.0557308 mCi (k = 2)>
```

### Get a table as a pandas DataFrame

```python
table = page.tableau("decroissance")
print(table[["heure", "activite"]].head(3))
#    heure  activite
# 0    0.0     100.0
# 1    1.0      89.1
# 2    2.0      79.4

print(table.attrs["unites"]["activite"])  # MBq
```

### Calculate with correlated uncertainties

Both activities below share the same source certificate. Its error cancels out in their ratio, and the calculation takes this into account:

```python
from medphystools.calculs import calculer


def read_table(name):
    return archive.lire_tableau(page.id, name)


ratio = calculer(page.contenu, "activite_ref / activite_fin", read_table)
print(f"{ratio.valeur:.4f} ± {ratio.elargie(2):.4f} (k = 2)")
# 1.9841 ± 0.0186 (k = 2)
```

### Review the history of a page

```python
for entry in page.historique():
    print(entry.nature, "|", entry.motif)
# création | None
# enregistrement | None
# enregistrement | None
# validation | None
# correction | Erreur de recopie sur l'activité finale
```

## Learn more

All of these are in French:

- [Tutorial notebook: reading an archive from Python](Notebook/docs/tutoriel/lire_une_archive.ipynb)
- [Archive file format](Notebook/src/medphystools/FORMAT.md)
- [Uncertainty calculation method](Notebook/docs/incertitudes.md)
- [Custom imports for instrument exports](Notebook/docs/imports-personnalises.md)
- [Full specification and roadmap](Notebook/docs/cahier-des-charges.md)

## Repository layout

```text
MedPhysTools/
├── LICENSE
├── README.md
└── Notebook/     MedPhysNotebook: application, Python package, tests and documentation
```

## License

[MIT](LICENSE)
