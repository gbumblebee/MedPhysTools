# Cahier des charges — Hub du physicien médical

Nov 1, 2026 · @gbumblebee

## 1. Contexte, objectifs et périmètre

Le logiciel est un outil personnel, local et portable, qui fait gagner du temps sur l'analyse des CQ en trois gestes : saisir vite des données expérimentales, les archiver de façon fiable, les analyser automatiquement avec rapport PDF.

**Contexte.** Développé pendant les stages DQPRM, d'abord pour un seul utilisateur. À terme (plusieurs années), il pourra être partagé pour homogénéiser les pratiques en France.

**Objectifs mesurables de la première version**

- Saisir une série de mesures (scalaires, tableaux, fichiers DICOM, lecture de fichier .csv excel ou autre tableur (fonction cutsom pouvant être codé par l'utilisateur dans un fichier dédier) avec incertitudes en moins de temps qu'avec un tableur.
- Retrouver toute mesure par tag et par date en quelques secondes.
- Relire n'importe quelle page d'archive depuis un script Python en une ligne.
- Produire un rapport PDF de CQ avec statut conforme / non conforme proposé et sources des normes citées.

**Inclus**

- Cahier de labo : pages de mesures libres organisées en onglets, gabarits facultatifs, incertitudes GUM, unités, photos et pièces jointes. Incertitude présenté avec élargissement k = 2 (parametre par défaut pouvant être changé dans l'affichage à tout moment) en absolu et en relatif.
- Archive versionnée par stage (ArchiveStage1, ArchiveStage2…).
- Hub de modules d'analyse : radiologie, médecine nucléaire, radiothérapie.
- Référentiel de normes paramétrable, avec sources BibTeX.
- Rapports LaTeX éditables puis compilés en PDF.
- Les rapport contiendront en référence : les articles scientifiques utilisés pour les méthodes, les normes réglementaires ainsi que le nom des archives avec données brutes utilisés pour réaliser les calculs (localisation sur fichier local et si DICOM, identification de ce dernier pour le retrouver facilement sur le PACS ou DACS). Le dernier point doit permettre de retrouver rapidement les données d'entrées.
- Les outils d'analyse pourront être dans une fenetre sous forme de plusieurs onglet si nécessaire (par exemple un onglet visualisation DICOM, un autre pour contourer si pas de courage automatique, un pour afficher les graphes et vérification visuel etc etc ...)

**Exclu pour l'instant**

- Dosimétrie patient (relèverait de l'article 5(5) du MDR).
- Connexion directe au PACS : l'import est manuel, mais l'architecture prévoit une source « PACS » plus tard.
- Multi-utilisateur simultané, serveur, interface tablette.
- Outils serveur existants (QATrack+, eLabFTW) : non installables sur un PC d'hôpital ; QATrack+ reste un débouché possible pour le suivi temporel.

**Ce qu'on ne recode pas.** Les bibliothèques Python existantes (pylinac, hazen, pynemaiqpet, GTC, pydicom…) sont des dépendances appelées par les modules ; on écrit le cœur, l'interface et la « colle ».

## 2. Principes directeurs et contraintes

La contrainte dominante est le PC d'hôpital : Windows, sans droits administrateur, réseau filtré par pare-feu. Tout le reste en découle.

**Contraintes techniques**

| Contrainte | Conséquence de conception |
| --- | --- |
| Pas de droits admin | Distribution portable : Python embarqué + dépendances dans un dossier, lancé par un .bat |
| Pare-feu, pas de serveur | Aucune base serveur, aucun port réseau ouvert ; SQLite et fichiers |
| Changement de poste et de stage | Une archive = un dossier autonome, copiable sur clé ou réseau |
| Archive par stage | ArchiveStage1, ArchiveStage2… ; le logiciel ouvre une archive comme un projet |
| PACS plus tard | Les entrées passent par une couche « source de données » abstraite (dossier aujourd'hui, PACS demain) |
| Chemins de plus de 260 caractères | Préfixe `\\?\` appliqué à l'accès au disque, sans droits administrateur et jamais stocké dans l'archive ; `core.longpaths` activé dans le dépôt Git |
| Windows en priorité | Développement multiplateforme, d'abord sous Fedora ; tests de compatibilité dès l'arrivée d'un poste Windows (vers mi-décembre 2026), puis systématiques |

**Principes**

1. **Données brutes intouchables.** Une valeur saisie ou un fichier importé n'est jamais modifié ; toute correction crée une nouvelle version tracée.
2. **Le cœur ignore l'interface.** Archive, mesures et modules fonctionnent sans Qt, en ligne de commande et dans un notebook.
3. **Un module = une fonction pure.** Entrées typées en paramètres, résultats en sortie, aucun effet de bord caché.
4. **Lisible par un physicien.** Code en Python simple, noms explicites, commentaires et docstrings en français, pas de magie (métaclasses, décorateurs complexes) dans le code des modules.
5. **Formats ouverts et lisibles.** JSON, TOML, CSV, BibTeX, LaTeX : tout reste exploitable sans le logiciel.
6. **Peu de dépendances, versions figées.** Chaque dépendance doit se justifier ; les versions sont verrouillées pour que l'archive se relise dans 10 ans.

## 3. Architecture générale

Le logiciel est un paquet Python unique : un cœur sans interface, une application Qt qui l'utilise, et des modules d'analyse branchés dessus. Les modules renvoient leurs résultats au cœur, qui seul écrit dans l'archive.

```
Interface Qt (PySide6), sans logique métier
  Archive (recherche, historique) | Saisie (onglets, tableaux, DICOM) | Hub (formulaires générés)
      │ appelle                                                            │ exécute
      ▼                                                                    ▼
Cœur pymphub (Python pur, testable sans Qt)    ◄── importe ──   Modules (dossiers)
  Pages et archive : page.json, empreintes                        Radiologie
  Mesures : GTC (GUM) + pint (unités)                             Médecine nucléaire
  Normes : TOML + BibTeX datés                                    Radiothérapie
  Rapports : Jinja2 → LaTeX → PDF                                     │ appellent
      ▲ importe            │ lit / écrit                              ▼
Sources de données         ▼                                  Bibliothèques libres
  dossier déposé à la    ArchiveStageN/ sur disque              pylinac, hazen, PyMedPhys
  main (v1)                pages/ + brut/ intouchable           pynemaiqpet, OpenIQ-CT
  PACS (plus tard)         Git : historique motivé
                           index.sqlite (cache)
```

Remplacer l'interface, ajouter un module ou brancher le PACS ne touche à aucune autre couche.

## 4. Modèle de données

L'unité de base est la **page** : un dossier qui contient un fichier `page.json`, les fichiers bruts et les pièces jointes. Une archive est un dossier de pages, plus un index de recherche reconstructible.

**Arborescence d'une archive**

```
ArchiveStage1/
  archive.toml                # nom, site, période, responsable, version du format, identifiants machines
  normes/                     # référentiel figé utilisé par cette archive (section 9)
  gabarits/                   # gabarits de pages utilisés (copie figée)
  objets/                     # magasin dédupliqué des fichiers copiés, nommés par empreinte
    3f/a9c2...e1.dcm.zip
  pages/
    2026-11-14_activimetre-linearite_0007/
      page.json               # métadonnées + mesures + incertitudes + références des fichiers bruts
      brut/                   # tableaux CSV décrits dans page.json (dans Git) ; fichiers déposés avant import (hors Git)
      pieces_jointes/         # photos, PDF constructeur, notes scannées
      analyses/               # une sous-page par exécution de module
        2026-11-15T10-02_activimetre.linearite_v1.2/
          resultats.json
          rapport.tex / rapport.pdf
  index.sqlite                # cache de recherche, supprimable et reconstructible
  .git/                       # historique (section 5)
```

**Contenu d'une page**

| Champ | Type | Exemple | Modifiable |
| --- | --- | --- | --- |
| id | texte unique | 2026-11-14\_activimetre-linearite\_0007 | non |
| gabarit (facultatif) | nom + version | activimetre.linearite@1 | rattachement tracé |
| titre, description libre | texte | « Linéarité Capintec, décroissance Tc-99m » | oui, tracé |
| date et heure de la mesure | ISO 8601 + fuseau | 2026-11-14T09:30+01:00 | oui, tracé |
| date de saisie, auteur | auto | 2026-11-14T11:05, G. | non |
| site, machine, équipement de mesure | références | CHU X / activimètre n° série | oui, tracé |
| tags | liste | \[activimetre, linearite, Tc99m\] | oui, tracé |
| onglets | liste : identifiant, type, titre, contenu | tableau « Relevés », notes « Montage » | oui, tracé |
| mesures | liste de grandeurs (ci-dessous) |  | correction = nouvelle version |
| fichiers | liste : mode de stockage + empreinte SHA-256 | objets/3f/a9… ou UID de série DICOM | non |
| statut | brouillon / validée / corrigée | validée | tracé |

**Une page par CQ, divisée en onglets.** Une page libre est le mode normal : elle ne suppose aucun gabarit. Ses onglets sont de types variés (notes et photos annotées, scalaires, tableau, DICOM, graphes, fichiers) et on en ajoute autant que nécessaire pendant le stage. Chaque grandeur a un nom unique dans la page et chaque colonne un nom unique dans son tableau (`decroissance.heure`), indépendamment des onglets : les réorganiser ne casse donc ni un script ni une analyse.

**Ajouter un type d'onglet.** Un nouveau type se déclare à un seul endroit, sans modifier les autres types. Le cœur décrit ses données, l'interface fournit son affichage. Un type inconnu de la version du logiciel utilisée reste lisible et ses données sont conservées intactes à l'enregistrement.

**Une grandeur mesurée**

- **Scalaire** : valeur saisie, unité saisie, incertitudes, horodatage, commentaire.
- **Vecteur ou tableau** : colonnes nommées, chacune avec son unité ; stocké en CSV dans `brut/` et décrit dans `page.json`. Exemple : couples (heure, activité) d'une décroissance.
- **Identifiant** : texte conservé à l'identique (numéro de série, de lot, de certificat).
- **Fichier** : DICOM, image, export constructeur ; stocké en copie ou en référence, avec empreinte et type (voir « Stockage des fichiers bruts »).

**Incertitudes (GUM).** La méthode et ses hypothèses sont détaillées dans `docs/incertitudes.md`. Chaque grandeur porte une liste de composantes, chacune avec : type A ou B, valeur, loi (normale, rectangulaire, triangulaire…), degrés de liberté, source (« résolution afficheur », « certificat d'étalonnage n° … ») et, si besoin, un repère d'erreur commune à plusieurs mesures, comme le numéro du certificat. La propagation dans les calculs utilise GTC ; le stockage garde les composantes, pas seulement l'incertitude combinée, pour pouvoir refaire le budget.

**Nombres et identifiants.** Une grandeur numérique est stockée comme un nombre : `10.50` peut devenir `10.5`. Un identifiant (numéro de série, de lot, de certificat…) est toujours stocké comme du texte, sans aucune conversion : `10.50` et `007` restent `10.50` et `007`, y compris dans les colonnes de tableaux CSV.

**Déclaration obligatoire.** Toute valeur numérique, scalaire ou dans un tableau, déclare son unité et son incertitude. Dans un tableau, l'unité se déclare par colonne, et l'incertitude par colonne ou par cellule. Une grandeur sans dimension déclare l'unité `1`, et une valeur exacte déclare explicitement une incertitude nulle.

**Syntaxe des unités.** Les unités s'écrivent dans la syntaxe de MetroloPy (`MBq`, `kBq/mL`, `kg/m**3`, `mA*s`, `um`), pour permettre son adoption plus tard sans convertir les archives. La syntaxe est vérifiée pendant la frappe et une correction est suggérée quand elle est fausse (`Bq/ml` → `Bq/mL`). Les unités absentes de MetroloPy (`Ci`, `HU`, `cps`, `lp`) sont définies dans un catalogue unique.

**Unités.** Valeur et unité saisies sont conservées, au sens de la règle ci-dessus. Les conversions (MBq ↔ mCi, mm ↔ cm) se font à l'affichage et dans les calculs, avec contrôle de cohérence dimensionnelle (bibliothèque pint). Une saisie incohérente avec le gabarit (une longueur là où on attend une activité) est bloquée. En page libre, aucune unité attendue n'est imposée.

**Gabarits.** Un gabarit sert à automatiser le post-traitement des CQ : il liste les variables utiles à l'analyse (nom, nature, colonnes, unité attendue), jamais leurs données. Il se crée après coup depuis une page libre, en choisissant ces variables. Il est écrit en TOML et versionné, et une page garde la version du gabarit auquel elle est rattachée. Une page créée depuis un gabarit part vide : les variables restent à remplir, avec leur unité et leur incertitude. Le hub recherche ensuite les valeurs dans l'archive à partir de ces noms.

**Stockage des fichiers bruts : copie ou référence.** Chaque fichier brut est enregistré selon l'un des trois modes ci-dessous, choisi explicitement à l'import. Le but est d'éviter les copies inutiles sur une carrière entière sans jamais perdre la capacité de retrouver et vérifier une donnée.

| Mode | Contenu dans `page.json` | Usage |
| --- | --- | --- |
| Copie | empreinte SHA-256 → fichier dans `objets/` | petits fichiers, saisies, exports constructeurs, données fondant un rapport validé |
| Référence fichier | chemin (disque, partage réseau) + empreinte + taille + date | gros volumes déjà stockés ailleurs de façon fiable |
| Référence DICOM | StudyInstanceUID, SeriesInstanceUID, liste des SOPInstanceUID, nœud DICOM (AE title), date, description + empreinte des pixels | séries récupérées sur PACS ou DACS |

1. **Déduplication.** Le magasin `objets/` est adressé par empreinte : une série utilisée par plusieurs pages n'est stockée qu'une fois.
2. **Compression sans perte.** Les DICOM copiés sont compressés sans perte (archive zip ou syntaxe de transfert DICOM sans perte) ; jamais de compression avec perte.
3. **Empreinte calculée à l'analyse**, même en mode référence. Pour le DICOM, l'empreinte porte sur les données de pixels, car un PACS peut réécrire certaines balises sans toucher l'image.
4. **Identification DICOM par UID**, jamais par chemin ou identifiant patient : c'est ce qui permettra de rejouer automatiquement la requête quand l'accès PACS sera branché.
5. **Copie à la validation.** À la validation d'un rapport, le logiciel propose de copier dans `objets/` toutes les entrées encore en mode référence. Par défaut, tout ce qui fonde un rapport validé est copié.
6. **Références surveillées.** La vérification d'intégrité tente de résoudre chaque référence et signale celles devenues introuvables (purge PACS, migration, changement d'hôpital).

## 5. Traçabilité et versionnage

Chaque archive est un dépôt Git géré par le logiciel : toute création ou modification de page produit un commit dont le message contient obligatoirement la raison. L'utilisateur n'a jamais besoin de taper une commande Git.

**Règles**

1. **Création d'une page** : commit automatique « création ». Tant que la page est en brouillon, chaque enregistrement produit un commit automatique, sans motif.
2. **Modification d'une page validée** : le logiciel demande un motif (champ obligatoire), puis commit « correction : \<motif> ». L'ancienne valeur reste lisible dans l'historique et affichée barrée dans l'interface, comme une correction sur un cahier papier. Git est le seul historique des corrections ; le message de commit nomme donc la page, les champs modifiés et le motif, pour retrouver facilement où chaque correction a été faite, page par page ou sur toute l'archive.
3. **Validation** : passage brouillon → validée, commit signé du nom de l'utilisateur. Une page validée ne se modifie plus que par correction motivée.
4. **Fichiers bruts** : trop lourds pour Git (DICOM), ils restent hors du dépôt, mais leur empreinte SHA-256 est versionnée dans `page.json`. Une commande « vérifier l'intégrité » recalcule toutes les empreintes, signale tout fichier modifié ou manquant et toute référence (fichier ou DICOM) devenue introuvable. Seuls les fichiers légers (`page.json`, tableaux CSV) sont dans Git ; `objets/` et `pieces_jointes/` restent hors du dépôt. Tout changement d'un fichier lourd laisse une trace dans un commit : nom du fichier, ancienne et nouvelle empreinte.
5. **Analyses** : chaque exécution de module est enregistrée avec la version du module, la version du logiciel, la version des normes, les empreintes des entrées et l'assignation des entrées (automatique ou manuelle). Une analyse est donc rejouable à l'identique.

**Historique lisible.** L'interface affiche pour chaque page une chronologie : date, auteur, champ modifié, ancienne valeur, nouvelle valeur, motif.

**Git en Python pur.** Le logiciel utilise dulwich, qui implémente Git en Python pur : rien à installer ni à faire autoriser par la DSI, et les dépôts restent lisibles par Git standard.

**Hors périmètre v1** : horodatage certifié par un tiers et signature électronique qualifiée. Le format est prévu pour les ajouter plus tard.

## 6. Saisie (cahier de labo) et calculs rapides

La saisie se fait directement sur ordinateur, en page libre par défaut ou à partir d'un gabarit ; la retranscription d'une feuille papier suit le même chemin, avec la feuille scannée jointe.

**Fonctions de saisie**

- Page libre : ajout à tout moment d'un onglet (notes et photos, scalaires, tableau, DICOM, graphes, fichiers).
- Export en gabarit : depuis une page, choix des variables utiles au post-traitement.
- Nouvelle page depuis un gabarit : le formulaire affiche les grandeurs attendues et leurs unités.
- Saisie de tableaux dans une grille type tableur, avec collage depuis Excel et import CSV.
- Mesures répétées : saisie de n valeurs ou sélection de valeurs répétées dans un tableau → moyenne et incertitude type A calculées automatiquement.
- Horodatage de la mesure distinct de l'horodatage de saisie ; bouton « maintenant ».
- Pièces jointes par glisser-déposer : photos (vignettes visibles dans la page), PDF, exports constructeurs.
- Annotation des photos : les annotations sont enregistrées à part, l'image d'origine n'est jamais modifiée.
- Association manuelle fichier ↔ entrée attendue : on dépose les fichiers dans `brut/` puis on choisit, pour chaque entrée du gabarit, le fichier correspondant.
- Visualiseur DICOM polyvalent (CT, dose, TEP, TEMP, etc.) : défilement des coupes, fenêtrage, valeurs mises à l'échelle, lecture des principales balises.
- Contrôles à la saisie : unité et incertitude obligatoires, syntaxe de l'unité vérifiée en direct avec suggestion, unité incompatible avec le gabarit quand la page en a un.

**Calculs rapides (post-traitement léger)**

- Calculatrice d'expressions sur les grandeurs de la page, avec propagation GUM automatique : `A0 * exp(-ln(2) * t / T12)` donne valeur, incertitude élargie et budget.
- Graphes rapides : une colonne contre une autre, échelle log, graphes 3D.
- Ajustements (lmfit) : modèles usuels ou personnalisés, paramètres avec incertitudes et covariances, évaluation de la fonction ajustée avec son incertitude.
- Indicateurs usuels, sur les points ou sur la fonction ajustée : minimum, maximum, moyenne, dérivée, intégrale, etc.
- Les calculs rapides sont enregistrés comme grandeurs dérivées, avec leur formule, jamais à la place des valeurs brutes.

**Ce que la saisie ne fait pas** : les analyses lourdes (images, protocoles complets) relèvent des modules du hub (section 8).

## 7. Recherche et lecture Python de l'archive

La recherche se fait par tag, date, gabarit, machine et texte libre ; un script tiers relit n'importe quelle page en une ligne, sans passer par l'interface.

**Recherche**

- Index SQLite construit à partir des `page.json` (recherche plein texte intégrée à SQLite).
- Filtres combinables : tags (et / ou), intervalle de dates, gabarit, machine, statut, auteur.
- L'index n'est qu'un cache : supprimé, il se reconstruit à l'ouverture de l'archive. Les fichiers restent la seule source de vérité.

**API Python (usage visé)**

```python
from pymphub import Archive

Archive("D:/ArchiveStage1").page("2026-11-14_activimetre-linearite_0007")   # relire une page en une ligne

arc = Archive("D:/ArchiveStage1")
pages = arc.chercher(tags=["activimetre"], depuis="2026-11-01")
p = pages[0]
p.mesures["activite"].valeur        # valeur brute, unité d'origine
p.mesures["activite"].en("MBq")     # convertie
p.mesures["activite"].incertitude   # objet GTC, budget complet
p.tableau("decroissance")           # DataFrame pandas avec unités
p.fichiers["serie_ct"]              # chemin vérifié par empreinte
```

**Exports**

- CSV et JSON par page ou par sélection.
- Export .eln (format RO-Crate du consortium ELN) pour l'interopérabilité future avec eLabFTW ou d'autres cahiers de labo.
- Export vers un outil de suivi temporel (QATrack+ ou autre) : prévu comme module séparé, non prioritaire.

## 8. Hub : contrat d'un module

Un module est un dossier autonome : une fonction Python d'analyse aux entrées typées, une fiche descriptive, un gabarit de rapport et des tests. Le hub le découvre, génère son formulaire Qt à partir des types, l'exécute et archive le résultat.

**Structure d'un module**

```
modules/
  radiologie/
    scanner/
      uniformite_bruit/
        module.toml        # identité, domaine, version, normes concernées
        analyse.py         # la fonction analyser(...)
        rapport.tex.j2     # gabarit LaTeX du rapport
        README.md          # méthode, références, limites
        tests/             # données de référence + résultats attendus
  medecine_nucleaire/
  radiotherapie/
```

**module.toml**

```toml
id = "radiologie.scanner.uniformite_bruit"
nom = "Scanner — uniformité et bruit"
version = "1.0.0"
auteur = "G."
normes = ["ansm.scanner.uniformite", "ansm.scanner.bruit"]   # clés du référentiel (section 9)
dependances = ["pydicom>=2.4"]
```

**analyse.py (forme imposée)**

```python
from pymphub.types import SerieDICOM, Grandeur, Resultats

def analyser(serie: SerieDICOM,
             diametre_roi_mm: float = 20.0,
             coupe_centrale: int | None = None) -> Resultats:
    """Uniformité et bruit sur fantôme eau.

    Méthode : 5 ROI (centre + 4 périphériques), écart des moyennes
    au centre et écart type au centre. Référence : décision ANSM ...
    """
    ...
    return Resultats(
        grandeurs={"uniformite_HU": Grandeur(...), "bruit_HU": Grandeur(...)},
        figures={"rois": fig},
    )
```

**Règles du contrat**

1. Les annotations de type définissent le formulaire : `float` → champ numérique, `SerieDICOM` → sélecteur de fichiers, `Literal[...]` → liste déroulante, `Grandeur` → valeur + unité + incertitude. La génération utilise magicgui.
2. La fonction est pure : elle ne lit que ses entrées, n'écrit rien sur disque, ne connaît pas Qt ni l'archive. Le hub se charge de l'archivage.
3. Elle renvoie un objet `Resultats` : grandeurs avec incertitudes, figures matplotlib, tableaux, messages d'avertissement.
4. La conformité n'est pas calculée dans le module : le hub compare les grandeurs aux normes déclarées (section 9).
5. Un module qui enveloppe une bibliothèque existante (pylinac, hazen, pynemaiqpet) suit le même contrat : il convertit les entrées, appelle la bibliothèque, traduit la sortie en `Resultats`.
6. Les tests du dossier `tests/` doivent passer pour qu'un module soit marqué « validé » dans le hub.

**Découverte et exécution**

- Le hub parcourt `modules/` au démarrage et construit le menu à trois niveaux : domaine → sous-domaine → module.
- Exécution : choix d'une ou plusieurs pages sources dans l'archive → assignation des entrées obligatoires → lancement → résultats affichés → enregistrement dans `analyses/` de la page.
- Assignation : le hub lit les entrées dans la page si elle a un gabarit. Sinon, l'utilisateur les assigne à la main (par exemple un tableau, puis ses colonnes X et Y), avec contrôle des unités. Le hub fait cette recherche et passe les valeurs à `analyser()`, qui reste une fonction pure.
- Ajouter un module = copier un dossier modèle, écrire `analyser()`, relancer le hub. Aucune modification du cœur.
- Un module peut aussi s'exécuter en ligne de commande ou dans un notebook, avec la même fonction.

## 9. Référentiel des normes : TOML + BibTeX

Les tolérances vivent dans un fichier TOML, les textes qui les fondent dans un fichier BibTeX ; chaque critère pointe vers sa référence BibTeX et porte ses dates de validité. Le rapport cite ainsi automatiquement la décision applicable à la date de la mesure.

**Fichiers**

```
referentiel_normes/
  references.bib     # décisions ANSM, arrêtés, normes CEI, rapports AAPM/IPEM...
  criteres.toml      # tolérances, chacune liée à une clé BibTeX
  CHANGELOG.md       # ce qui a changé et pourquoi
```

**references.bib (exemple de forme, contenu à renseigner)**

```bibtex
@legislation{ansm_scanner_v1,
  title       = {Décision fixant les modalités du contrôle de qualité des scanographes},
  author      = {{ANSM}},
  date        = {AAAA-MM-JJ},
  url         = {https://...},
  note        = {Publiée au JO du ...},
  keywords    = {radiologie, scanner}
}
```

**criteres.toml (exemple de forme, valeurs à renseigner)**

```toml
[[critere]]
cle        = "ansm.scanner.uniformite"
grandeur   = "uniformite_HU"          # nom de grandeur renvoyé par le module
unite      = "HU"
type       = "absolu"                 # absolu | relatif_reference | intervalle
limite_max = 0.0                      # à renseigner depuis le texte
source     = "ansm_scanner_v1"        # clé BibTeX
article    = "§ ..."                  # point précis du texte
valide_depuis = 2000-01-01
valide_jusqua = ""                    # vide = en vigueur
commentaire = "Contrôle interne trimestriel"
```

**Règles**

1. **Versions datées.** Quand un texte change, on n'écrase pas le critère : on ferme l'ancien (`valide_jusqua`) et on en ajoute un nouveau avec sa nouvelle clé BibTeX. Un CQ de 2026 reste évalué avec la norme de 2026.
2. **Prise en compte de l'incertitude.** Le statut proposé distingue : conforme, non conforme, et « indéterminé » quand l'intervalle valeur ± U chevauche la limite. La règle de décision appliquée (acceptation simple ou bande de garde) est un paramètre explicite, imprimé dans le rapport.
3. **Statut proposé, jamais imposé.** Le statut est optionnel et doit être approuvé manuellement (section 10).
4. **Figé par archive.** À la création d'une archive, le référentiel est copié dans `normes/` ; les mises à jour sont importées explicitement, avec commit. On sait toujours quelle version a servi.
5. **Validation du fichier.** Au chargement, le logiciel vérifie que chaque `source` existe dans le .bib, que les unités sont compatibles avec la grandeur, et que deux critères de même clé ne se chevauchent pas dans le temps.
6. **Partage.** Le référentiel est un dépôt indépendant : à terme, il peut devenir une ressource commune maintenue par plusieurs physiciens.

**Dans le rapport** : chaque ligne de résultat affiche la valeur, U (k = 2), la limite, le statut et la citation `\cite{ansm_scanner_v1}` ; la bibliographie complète est générée en fin de rapport par biblatex.

## 10. Rapports LaTeX et validation manuelle

Chaque analyse produit un fichier `.tex` lisible et modifiable, compilé en PDF ; le physicien peut l'annoter avant de valider, et la version validée est figée dans l'archive.

**Chaîne de production**

1. Le module fournit les résultats ; le hub remplit son gabarit `rapport.tex.j2` (moteur Jinja2, délimiteurs adaptés pour ne pas entrer en conflit avec les accolades LaTeX).
2. Le gabarit hérite d'une classe commune `pymphub-rapport.cls` : en-tête (site, machine, date, auteur), tableau de résultats, figures, budget d'incertitude, bibliographie.
3. Compilation avec LuaLaTeX de TeX Live portable + biblatex.
4. Le `.tex` et le PDF sont enregistrés dans le dossier d'analyse de la page.

**Contenu minimal d'un rapport**

- Identification : site, machine, équipement de mesure, date de mesure, page source, auteur.
- Méthode : nom et version du module, paramètres utilisés.
- Résultats : valeur, U (k = 2), limite, statut proposé, citation de la norme.
- Figures (ROI, courbes) et budget d'incertitude en annexe.
- Traçabilité : versions du logiciel, du module, du référentiel, empreintes des entrées.
- Zone « Remarques du physicien » éditable.
- Bloc de validation : nom, date, décision.

**Validation manuelle**

- Le statut automatique apparaît comme « proposé ». Le physicien accepte ou modifie chaque statut ; une modification exige un motif.
- Les remarques ajoutées à la main dans le .tex sont conservées : le hub ne régénère jamais un rapport validé, il crée une nouvelle version.
- À la validation : recompilation, commit, et le PDF devient la pièce de référence.

## 11. Interface Qt

Une seule application de bureau PySide6 à trois espaces (Archive, Saisie, Hub) partageant l'archive ouverte ; aucune logique métier dans le code de l'interface.

| Espace | Rôle | Éléments principaux |
| --- | --- | --- |
| Archive | Retrouver et consulter | Filtres tags / dates / gabarit / machine, liste de pages, fiche page, historique, vérification d'intégrité |
| Saisie | Créer et corriger des pages | Onglets extensibles, grille tableur, photos annotées, visualiseur DICOM, calculatrice GUM, graphes 2D et 3D, export en gabarit |
| Hub | Analyser | Menu domaine → sous-domaine → module, formulaire généré, résultats, rapport, validation |

**Choix techniques**

- PySide6 (licence LGPL) plutôt que PyQt6 (GPL), pour garder le choix de licence du projet.
- magicgui pour générer les formulaires des modules depuis les annotations de type.
- pyqtgraph pour l'affichage d'images DICOM et de courbes interactives rapides ; matplotlib pour les graphes 3D et les figures des rapports.
- Lancement d'outils externes (3D Slicer, ImageJ/Fiji) en sous-processus quand un module en a besoin.

**Ergonomie**

- Style sobre et moderne ; priorité à la praticité et à une bonne ergonomie.
- Raccourcis clavier pour la saisie en série ; tabulation dans l'ordre de la page.
- Affichage systématique de l'unité et de l'incertitude à côté de chaque valeur.
- Valeurs corrigées affichées barrées avec info-bulle du motif.
- Interface en français ; libellés des gabarits et modules en français.

**Évolution possible** : le cœur étant indépendant, une interface web pourra être ajoutée plus tard pour un usage partagé, sans réécrire les modules.

## 12. Pile technique et intégration de l'existant

Python 3.12, distribué en version portable, avec un petit nombre de dépendances choisies pour leur stabilité ; les outils libres de physique médicale sont appelés par les modules, jamais copiés.

**Cœur et interface**

| Besoin | Choix | Licence |
| --- | --- | --- |
| Incertitudes GUM | GTC (GUM Tree Calculator) | MIT |
| Unités | pint | BSD |
| Validation des structures (page, gabarit) | pydantic | MIT |
| Lecture et écriture TOML / BibTeX | tomllib (inclus) + tomli-w / bibtexparser | — + MIT / MIT |
| Tableaux | numpy, pandas | BSD |
| Ajustements (à partir de la phase 1) | lmfit | BSD-3-Clause |
| DICOM | pydicom | MIT |
| Interface | PySide6, magicgui, pyqtgraph, matplotlib | LGPL / MIT / MIT / PSF |
| Versionnage | dulwich (compatible Git) | Apache-2.0 |
| Rapports | Jinja2 + LuaLaTeX (TeX Live portable) | BSD / LPPL |
| Recherche | SQLite (inclus) | domaine public |
| Environnement | uv pour figer les versions | Apache-2.0 / MIT |

**Bibliothèques de physique médicale à envelopper en modules**

| Domaine | Outil | Usage prévu |
| --- | --- | --- |
| Radiologie | OpenIQ-CT (projet personnel) | NPS, TTF, d' en scanner |
| Radiologie | pylinac (CatPhan, imagerie planaire) | Qualité image scanner et radiographie |
| Radiologie (IRM) | hazen | SNR, uniformité, ghosting, géométrie de coupe |
| Médecine nucléaire | pynemaiqpet, ChameleonIQ | NEMA IQ en TEP |
| Radiothérapie | pylinac | Winston-Lutz, picket fence, VMAT, starshot |
| Radiothérapie | PyMedPhys | Indice gamma, logs machine |

**Distribution**

- Un dossier `pymphub/` contenant Python embarqué, l'environnement figé, le code, `modules/` et un `lancer.bat`.
- Copiable sur clé USB ou partage réseau, sans installation ni droits admin.
- Les archives sont séparées du logiciel : on ouvre `ArchiveStage1` depuis n'importe quelle version compatible.

**Licence du projet** : MIT (section 15). Une licence permissive facilite l'adoption par les services.

## 13. Qualité logicielle

Un module n'est utilisé en clinique que s'il passe ses tests sur des données de référence et que sa méthode est documentée ; c'est ce qui rendra l'outil défendable en audit et partageable.

**Tests**

- Cœur : tests unitaires (pytest) sur la lecture/écriture de pages, la propagation d'incertitudes, les conversions d'unités, l'intégrité.
- Modules : chaque module livre des données de référence (fantômes numériques ou acquisitions anonymisées) et les résultats attendus avec tolérance numérique.
- Validation croisée : pour chaque module, comparaison documentée avec l'outil du service ou un outil publié (ex. pylinac, NMQC) ; écarts consignés dans le README.
- Rétrocompatibilité : une archive de test de chaque version du format doit toujours se relire.

**Documentation**

- README par module : objectif, méthode, références, hypothèses, limites, historique des versions.
- Guide « écrire un module en 30 minutes » avec un module exemple commenté ligne à ligne.
- Docstrings en français ; noms de variables explicites, en français pour le métier (`activite`, `diametre_roi_mm`).

**Conventions**

- Formatage et vérifications automatiques (ruff), annotations de type obligatoires dans les modules.
- Versions sémantiques pour le logiciel, le format d'archive, chaque module et le référentiel de normes.
- Dépôt Git du code séparé des dépôts d'archives et du référentiel.

**Données de santé** : les DICOM de patients ne doivent pas servir de données de test partagées ; prévoir un outil d'anonymisation avant tout partage.

## 14. Feuille de route et critères de réussite

Les phases 0 à 2 forment la première version utilisable en stage ; on ne passe à la phase suivante qu'une fois son critère atteint (losange).

Une version utile dès la phase 2, puis un module à chaque stage.

| Phase | Contenu | Critère de passage |
| --- | --- | --- |
| 0. Fondations (cœur sans interface) | format page.json + gabarits TOML ; GTC, pint, Git, index SQLite ; API Python Archive() | Une page se relit en une ligne de Python |
| 1. Saisie (application Qt) | espaces Archive et Saisie ; page libre à onglets extensibles, photos annotées ; visualiseur DICOM, calculatrice GUM, graphes 2D et 3D, ajustements lmfit et indicateurs ; export en gabarit | Un CQ réel saisi au lieu du tableur |
| 2. Hub (premiers modules) | contrat de module + magicgui ; assignation des entrées (gabarit ou manuelle) ; OpenIQ-CT + activimètre ; normes BibTeX + rapport LaTeX | Premier rapport PDF validé manuellement |
| 3. Extension (au fil des stages) | modules pylinac, hazen, NEMA ; export .eln, vérification d'intégrité ; guide « écrire un module » | Un module ajouté en moins d'une journée |
| 4. Partage (après le DQPRM) | documentation complète ; anonymisation des données de test ; référentiel des normes partagé | Utilisé par un autre physicien |

Le premier rapport PDF validé (phase 2) est le jalon clé : c'est le moment où l'outil fait gagner du temps sur un vrai CQ.

## 15. Décisions et points ouverts

| Point | Décision | Statut |
| --- | --- | --- |
| Nom du logiciel | pymphub | tranché |
| Licence du projet | MIT : permissive, facile à adopter par un service | tranché |
| Python embarqué sur les PC des services | à tester dès le premier stage (antivirus, exécution de .bat, chemins réseau) | action |
| Versionnage Git | dulwich, Git en Python pur, rien à faire autoriser | tranché |
| Distribution LaTeX | TeX Live portable | tranché |
| Règle de décision si U chevauche la limite | bande de garde (paramètre imprimé dans le rapport) | tranché |
| Granularité d'une page | une page par CQ, divisée en onglets extensibles | tranché |
| Premiers modules | médecine nucléaire et radiologie : activimètre, TEP, TEMP, scanner | tranché |
| Identifiants machines et sites | format `site.service.modalite.fabricant-modele.numero-serie`, défini dans `archive.toml` | tranché |
| Stockage des fichiers bruts | trois modes : copie dédupliquée, référence fichier, référence DICOM par UID ; copie par défaut à la validation | tranché |
| Historique des corrections | Git seul, à condition de retrouver facilement où chaque correction a été faite | tranché |
| Contenu du dépôt Git d'une archive | fichiers légers seulement ; tout changement d'un fichier lourd est tracé dans un commit | tranché |
| Nombres et identifiants | un nombre peut perdre ses zéros finaux ; un identifiant reste un texte exact | tranché |
| Écriture du TOML | tomli-w | tranché |
| Noms de colonnes | uniques dans leur tableau seulement ; désignés par `tableau.colonne` | tranché |
| Erreurs communes entre mesures | repère facultatif stocké dès maintenant ; exploitation dans les calculs plus tard | tranché |
| Unité et incertitude | obligatoires pour toute valeur numérique | tranché |
| Syntaxe des unités | syntaxe MetroloPy dès maintenant, calculs avec pint et GTC ; MetroloPy limité aux tests de compatibilité | tranché |
| Adoption de MetroloPy dans le logiciel | à décider plus tard : licence GPL-3.0, incompatible avec une distribution entièrement sous MIT | ouvert |
| Rôle des gabarits | facultatifs, créés depuis une page libre ; ils listent les variables utiles au post-traitement, jamais leurs données | tranché |
| Entrées d'une analyse | lues via le gabarit ou assignées à la main ; assignation enregistrée avec l'analyse | tranché |
| Motif de modification | brouillon : versionné sans motif ; page validée : motif obligatoire | tranché |
| Relecture d'une page précise | `Archive(chemin).page(id)` | tranché |
| Compatibilité Windows | développement sous Fedora ; tests Windows vers mi-décembre 2026, à l'arrivée du poste | tranché |

## 16. Anticipation à long terme

Sur une carrière, les risques principaux ne sont pas techniques mais de pérennité : relire une archive dans 20 ans, interpréter une dérive, savoir quelle référence et quel étalonnage s'appliquaient. Les points ci-dessous sont à prévoir dans la structure dès la v1, même si leur interface vient plus tard.

| Point | Risque si on l'ignore | Ce qu'on prévoit |
| --- | --- | --- |
| Évolution du format d'archive | les anciennes archives deviennent illisibles après une mise à jour | numéro de version du schéma dans chaque `page.json`, scripts de migration, lecture des anciennes versions testée |
| Lisibilité sans le logiciel | archive inexploitable si pymphub disparaît | fichier `FORMAT.md` décrivant le format, copié dans chaque archive ; rapports en PDF/A |
| Registre des équipements de mesure | incertitude type B ressaisie à la main, mesure faite avec un appareil hors étalonnage | registre (chambres, électromètres, activimètres de référence, sources étalons, thermomètre, baromètre) avec certificats, dates de validité et incertitudes ; alerte si étalonnage expiré à la date de mesure |
| Valeurs de référence par machine | impossible d'évaluer un critère relatif (« écart à la valeur de référence ») | état de référence daté par machine, pointant vers la page de recette ou de référence ; type de critère `relatif_reference` |
| Journal des événements machine | une dérive ou une rupture de tendance reste inexpliquée | événements datés (maintenance, changement de tube ou de détecteur, mise à jour logicielle) ; un événement peut ouvrir une nouvelle référence |
| Données nucléaires et constantes | périodes ou rapports d'embranchement codés en dur, non sourcés | fichier de données nucléaires versionné et sourcé en BibTeX, comme les normes (tables recommandées du LNHB) |
| Heure et fuseaux | erreur de décroissance au changement d'heure ou si l'horloge d'un appareil dérive | horodatage avec fuseau ; décalage d'horloge par équipement dans le registre |
| Sauvegarde | perte totale de l'archive (disque, vol, effacement) ; Git n'est pas une sauvegarde | commande de sauvegarde vers un second support + vérification des empreintes après copie ; règle 3-2-1 recommandée dans la documentation |
| Propriété et confidentialité des données | emporter des données qui appartiennent à l'hôpital, fuite de données personnelles dans des en-têtes DICOM | statut des données défini avec chaque service ; pseudonymisation des en-têtes DICOM à l'import ; export « sans données brutes » pour ce qu'on peut emporter |
| Conflits de dépendances entre modules | deux bibliothèques exigent des versions incompatibles | v1 : un environnement figé ; plus tard : modules lourds exécutés dans leur propre environnement, en sous-processus |
| Rejouabilité des analyses anciennes | impossible de refaire un calcul de 2027 en 2040 | versions des modules étiquetées dans Git, version enregistrée dans chaque analyse, ancienne version récupérable |
| Suivi temporel et planification | données riches mais pas de vue de tendance ni d'échéances | identifiants machines stables, périodicité dans les gabarits ; vue de tendance simple puis export vers un outil tiers |
| Gouvernance du partage | modules non revus utilisés en clinique par d'autres | statut « validé » d'un module seulement après revue par un second physicien et tests passés ; référentiel de normes en dépôt commun |
| Réglementaire | glissement vers un usage relevant du dispositif médical sans s'en rendre compte | périmètre écrit (CQ machine, pas de décision patient) ; matrice exigences ↔ tests tenue dès la v1, utile si l'article 5(5) du MDR devient applicable |
