# Phase 0 — Fondations : découpage en étapes

**Objectif :** un cœur sans interface comprenant le format `page.json`, les gabarits TOML, GTC, pint, Git, l'index SQLite et l'API `Archive()`.

**Critère de passage :** une page se relit en une ligne de Python.

Référence : [cahier des charges](cahier-des-charges.md), section 14.

## Règles

- Chaque étape dépend de la précédente.
- Une étape n'est terminée que si ses critères sont couverts par des tests et si `uv run pytest` et `uv run ruff check .` passent.
- Le développement se fait sous Fedora. Les tests Windows auront lieu à l'arrivée du poste, vers mi-décembre 2026.

## Étapes

### 0. Socle

**Contenu :** arborescence du paquet, pytest et ruff, fichier `LICENSE` MIT et licence dans `pyproject.toml`.

**Validée quand :**
- les tests passent sous Fedora ;
- `import pymphub` ne charge pas Qt.

### 1. Unités

**Contenu :** registre pint unique, avec HU et mCi.

**Validée quand :**
- la conversion MBq ↔ mCi est exacte ;
- convertir une longueur en activité échoue ;
- la saisie d'origine reste intacte.

### 2. Incertitudes

**Contenu :** composantes GUM (type, loi, degrés de liberté, source) converties vers GTC.

**Validée quand :**
- un budget calculé à la main est retrouvé ;
- U s'affiche en absolu et en relatif ;
- k change l'affichage, pas les données ;
- une conversion avec décalage (°C ↔ K) ne décale pas l'incertitude.

### 3. Modèle de page

**Contenu :** modèles pydantic pour la page et les trois types de grandeurs (scalaire, tableau, fichier), version du schéma.

**Validée quand :**
- l'aller-retour JSON est identique ;
- une date sans fuseau et un identifiant machine mal formé sont refusés ;
- `007` et `10.50` restent exacts en identifiant, et un nombre peut devenir `10.5`.

### 4. Gabarits TOML

**Contenu :** chargement, version, unités autorisées, plages plausibles, page libre.

**Validée quand :**
- une unité incohérente est bloquée ;
- une valeur hors plage donne une alerte, sans blocage.

### 5. Archive sur disque

**Contenu :** création et ouverture, `archive.toml` (écrit avec tomli-w), `FORMAT.md`, identifiants de pages, écriture atomique.

**Validée quand :**
- une archive déplacée s'ouvre toujours ;
- un format trop récent est refusé ;
- les identifiants respectent les règles de nommage Windows, vérifiables sous Fedora.

### 6. Traçabilité Git

**Contenu :** dulwich, commits de création, de correction et de validation, historique par champ.

**Validée quand :**
- une correction sans motif est refusée ;
- l'ancienne valeur est retrouvée ;
- les corrections d'une page et de toute l'archive sont retrouvées, avec champs et motif ;
- `git log` et `git fsck` standard fonctionnent.

### 7. Fichiers bruts

**Contenu :** magasin `objets/` dédupliqué, tableaux CSV, référence fichier, schéma de la référence DICOM.

**Validée quand :**
- un même fichier importé deux fois n'est stocké qu'une fois ;
- un fichier altéré ou manquant est signalé à l'accès, sans rien écrire dans l'archive ;
- l'enregistrement du remplacement d'un fichier lourd crée un commit avec son nom, l'ancienne et la nouvelle empreinte ;
- une colonne d'identifiants du CSV n'est pas convertie en nombres (`007` ne devient pas `7`).

### 8. Index SQLite

**Contenu :** recherche plein texte et filtres combinables.

**Validée quand :**
- supprimé, l'index se reconstruit à l'identique ;
- un index périmé est détecté, en comparant le commit `HEAD` à celui mémorisé dans l'index ;
- la recherche reste rapide sur 5 000 pages synthétiques.

### 9. API `Archive()`

**Contenu :** `page`, `chercher`, `mesures`, `.en()`, `.incertitude`, `tableau`, `fichiers`.

**Validée quand :**
- `Archive(chemin).page(id)` relit une page ;
- l'exemple de la section 7 du cahier des charges s'exécute tel quel comme test ;
- une colonne d'identifiants reste du texte dans le DataFrame renvoyé par `tableau`.

### 10. Sortie de phase

**Contenu :** scénario complet sur un CQ d'activimètre, archive de test v1 figée, matrice exigences ↔ tests.

**Validée quand :** un script neuf relit la page en une ligne sur l'archive copiée ailleurs, sous Fedora.

**Vérification à part, à l'arrivée du poste Windows :** même scénario sous Windows, puis Python embarqué sur un vrai PC d'hôpital dès que possible (antivirus, exécution du `.bat`, chemins longs et réseau).

## Hors phase 0

- **Interface et analyse :** Qt, modules, normes et rapports LaTeX.
- **DICOM :** `pydicom` n'est pas dans les dépendances, donc ni empreinte des pixels ni compression. Seul le schéma de la référence DICOM est prévu.
- **Intégrité et exports :** la vérification globale et l'export `.eln` arrivent en phase 3. La phase 0 vérifie seulement l'empreinte à l'accès à un fichier.
- **Section 16 du cahier des charges :** ni registre des équipements ni journal machine, seulement des références stables dans le format pour les ajouter plus tard.
