# Format d'une archive MedPhysNotebook

Version du format : 1

Ce fichier permet de relire cette archive sans le logiciel. Tout y est en formats ouverts : JSON, TOML, CSV et Git.

## Arborescence

```
archive.toml       identité de l'archive et machines déclarées
FORMAT.md          ce fichier
normes/            référentiel de normes figé pour cette archive
gabarits/          gabarits utilisés : <nom>@<version>.toml
objets/            fichiers bruts copiés, nommés par leur empreinte SHA-256
pages/<id>/        une page par CQ :
  page.json          métadonnées, mesures, incertitudes, onglets
  brut/              tableaux en CSV
  pieces_jointes/    photos, PDF, notes scannées
  analyses/          une sous-page par exécution d'un module d'analyse
corbeille/<id>/    dossier complet des pages supprimées, hors de Git
index.sqlite       cache de recherche : peut être supprimé et reconstruit
.git/              historique des pages et des fichiers légers
```

## Gabarits

Un fichier `gabarits/<nom>@<version>.toml` liste les `variables` attendues : nom, nature, colonnes et unités, jamais leurs valeurs. À partir de son format 2, ses `onglets` préparent une nouvelle page :

- `type = "notes"` : `titre` et `texte`. Les photos ne sont pas reprises.
- `type = "graphes"` : `titre` et `reglages`, décrits plus bas.
- `type = "ajustements"` : `titre` et `prereglage`, décrit à la section sur les ajustements.

Un onglet préparé n'utilise que des colonnes numériques attendues par le gabarit. Une fois créée, la page garde sa propre copie de ces onglets.

## Identifiant d'une page

`AAAA-MM-JJ_sujet_NNNN` : date de la mesure, sujet en minuscules sans accent, puis numéro d'ordre dans l'archive. Exemple : `2026-11-14_activimetre-linearite_0007`.

Un numéro ne resert jamais, même après la suppression d'une page.

## Suppression d'une page

Un commit de nature `suppression` retire `page.json` et les tableaux du dépôt : leur contenu reste lisible dans l'historique. Le dossier de la page part dans `corbeille/<id>/`, avec ses pièces jointes. Le motif est obligatoire pour une page validée ou corrigée. `objets/` ne change pas.

## page.json

Fichier JSON en UTF-8. Les principaux champs sont :

| Champ | Contenu |
|---|---|
| `version_schema` | version du format de ce fichier, indépendante de celle de l'archive |
| `id`, `titre`, `description` | identification de la page |
| `date_mesure`, `date_saisie` | dates ISO 8601 avec fuseau horaire |
| `auteur`, `site`, `equipements`, `tags` | contexte de la mesure |
| `machine` | `site.service.modalite.fabricant-modele.numero-serie` |
| `gabarit` | `nom@version`, ou `null` pour une page libre |
| `statut` | `brouillon`, `validée` ou `corrigée` |
| `mesures` | grandeurs de la page, décrites ci-dessous |
| `onglets` | organisation de l'affichage ; référence les grandeurs par leur nom |
| `pieces_jointes` | chemin et empreinte SHA-256 de chaque pièce jointe |

## Grandeurs

Le champ `nature` donne le type de chaque élément de `mesures` :

- `scalaire` : `valeur` numérique, `unite` et `composantes` d'incertitude, toutes obligatoires. À partir du format 2 de la page, `repetitions` garde les valeurs brutes de mesures répétées, dont `valeur` est la moyenne.
- `identifiant` : `valeur` texte, à conserver à l'identique.
- `tableau` : `fichier` CSV dans `brut/` et description de ses `colonnes`. Une colonne se désigne par `tableau.colonne`. À partir du format 2 de la page, chaque colonne peut porter un `commentaire`. À partir du format 3, une colonne d'incertitude peut porter une `formule`, comme `sqrt(N)` : elle se recalcule à la lecture à partir des valeurs des colonnes numériques, et n'est pas dans le CSV. Son résultat est une incertitude-type exacte, dans l'`unite` de la colonne. À partir du format 4, une colonne de nature `date` donne une date et une heure : la cellule du CSV s'écrit en ISO 8601 avec son fuseau, comme `2026-11-04T14:30:05+01:00`, et reste du texte dans le DataFrame (`pd.to_datetime(colonne, utc=True)` la convertit). À partir du format 5, une cellule du CSV peut être vide, et les colonnes n'ont donc pas forcément la même longueur : elle se relit `nan` dans une colonne de nombres et `None` dans une colonne de textes ou de dates. Une incertitude à formule qui lit une cellule vide reste vide. Une cellule d'incertitude vide ne déclare rien.
- `derivee` et `colonne_calculee` : résultat d'un calcul, décrit plus bas (à partir du format 2 de la page).
- `ajustement` et `indicateur` : décrits plus bas (à partir du format 2 de la page).
- `fichier` : fichier brut, avec son mode de stockage :
  - `copie` : dans `objets/`, retrouvé par son empreinte ;
  - `reference_fichier` : chemin externe, empreinte, taille et date ;
  - `reference_dicom` : UID DICOM de l'étude, de la série et des instances, avec le nœud DICOM.

## Pièces jointes et photos annotées

`pieces_jointes` donne le `chemin`, l'`empreinte` et le `commentaire` de chaque fichier de `pieces_jointes/`, qui reste hors de Git. Un fichier retiré d'une page reste sur le disque : les anciennes versions de la page y renvoient encore.

Un onglet de notes liste ses `photos` : `chemin` d'une pièce jointe, `legende` et `annotations`. L'image n'est jamais modifiée : tout est dans `page.json`. Une annotation a une `forme` (`point`, `fleche`, `rectangle`, `ellipse`, `trace`), des `points` et un `texte`. Les coordonnées vont de 0 à 1, à partir du coin haut gauche, sur l'image redressée selon son orientation EXIF. Une photo qui est la capture d'un graphe garde son origine dans `capture` : `tableau` et `empreinte_donnees`.

Une ligne d'une note qui commence par `# `, `## ` ou `### ` est un titre, à l'affichage seulement.

Une formule entre `$`, comme `$A_0 e^{-t}$`, est une équation, dessinée à l'affichage par mathtext, le moteur de matplotlib. Le fichier garde la formule. Le `$` ouvrant et le `$` fermant collent à la formule ; `\\$` écrit un dollar.

Le texte d'une note peut contenir des tags, comme `{{activite}}` ou `{{fit.T12}}`. Le fichier garde le tag, jamais la valeur : elle se calcule à l'affichage. `{{nom:valeur}}` donne la valeur sans incertitude, `{{nom:U}}` son incertitude élargie, `{{nom:unite}}` son unité. Entre les accolades, toute formule de calcul est permise.

## Onglet de graphes

Un onglet de type `graphes` nomme ses `indicateurs` et garde ses `reglages` : `tableau`, `mode` (`2d` ou `3d`), colonnes `x`, `y` (une ou plusieurs) et `z`, échelles `echelle_x`, `echelle_y`, `echelle_z` (`lineaire` ou `log`), `barres`, `relier`, `surface` et `courbes`, les ajustements à superposer. Le facteur k n'y figure pas : il se choisit à l'affichage. Sans réglages, les références de `grandeurs` désignent les colonnes à tracer.

## Résultat d'un calcul

La nature `derivee` garde le résultat d'une formule sur d'autres grandeurs de la page : `formule`, `valeur`, `unite`, `incertitude_type`, `ddl` (`null` signifie infinis) et `budget_calcul`, la part de chaque source dans l'incertitude. Le résultat est figé à l'enregistrement. Les valeurs brutes restent à part : une dérivée ne les remplace jamais.

La nature `colonne_calculee` garde une formule calculée ligne par ligne : `formule`, `unite`, `tableaux` où elle s'affiche, `valeurs` et `incertitudes_types`, avec `null` pour une ligne sans résultat. Ces valeurs sont dans `page.json` : les fichiers CSV restent les valeurs brutes, et ne contiennent jamais de colonne calculée.

Une `formule` peut appeler `decroissance(valeur, periode, date_cible)` : `valeur * exp(-ln(2) * (date_cible - date) / periode)`. L'unité du deuxième argument distingue une période (`h`, `min`…) d'une constante de décroissance (`1/h`…). La date cible est écrite entre guillemets, avec son fuseau, ou vient d'une colonne de dates : ainsi, l'heure actuelle choisie à l'écran est figée dans la formule, et le résultat se refait à l'identique. La date de départ est l'`horodatage` du scalaire, sinon `date_mesure`. Pour une colonne ou un résultat de calcul, elle est donnée en quatrième argument. Les dates n'ont pas d'incertitude.

Dans une formule, `tableau.colonne` désigne la valeur de la ligne en cours, et `tableau.colonne[3]` une cellule précise, en comptant les lignes à partir de 1. Les composantes de même `erreur_commune` y comptent comme une seule erreur.

## Ajustements et indicateurs

La nature `ajustement` garde un modèle ajusté sur deux colonnes d'un `tableau` : `modele`, `formule`, `x`, `y`, leurs unités, `x_min` et `x_max`, le nombre de `points`, les `parametres` (`nom`, `valeur`, `incertitude_type`, `unite`), la matrice `correlation`, `chi2_reduit`, la `methode` et `empreinte_donnees`. La covariance se déduit des incertitudes et de la corrélation. `courbe` nomme un tableau de colonnes `x`, `y` et `u_y`, plus fin que les données et sans extrapolation, qui sert seulement à retracer la fonction : tout calcul repart des coefficients.

La nature `indicateur` garde un résultat lu sur une courbe : `methode` (`minimum`, `maximum`, `moyenne`, `mediane`, `percentile`, `ecart_type`, `coefficient_variation`, `moyenne_quadratique`, `somme`, `etendue`, `nombre_points`, `integrale`, `derivee`, `largeur_mi_hauteur`, `abscisse_de_valeur`), `source` (`points` ou `ajustement`), ses données d'origine (`tableau`, `x`, `y`, `ajustement`), son intervalle (`de`, `a`), `parametre`, son abscisse (`position`, `incertitude_position`, `unite_position`), `valeur`, `incertitude_type`, `unite` et `afficher`, qui place un repère sur le graphe.

Dans une formule, `ajustement.coefficient` désigne un coefficient, corrélé aux autres, et `indicateur.x` l'abscisse d'un indicateur.

Un onglet de type `ajustements` issu d'un gabarit garde son `prereglage` : `nom`, `tableau`, `x`, `y`, `modele` et `formule`. Pour un modèle `Personnalisé`, `coefficients` donne le `nom`, l'`unite` et le `depart` de chacun. Tant que la page est un brouillon, l'ajustement se refait à chaque enregistrement de ses points. Une page validée ne se réajuste jamais seule.

## Modifications hors du logiciel

À chaque ouverture dans l'application, tout fichier suivi par Git qui diffère du dernier commit donne une nouvelle version : le fichier est enregistré tel quel, jamais réécrit. Le commit a la nature `modification externe` et l'auteur `hors logiciel`. Son motif, écrit par le logiciel, dit que le changement vient d'une modification hors du logiciel ou d'un enregistrement interrompu, sans pouvoir les distinguer, et donne le nom et la date de chaque fichier. La date du commit est celle de la détection. Le champ `statut` de la page reste celui du fichier.

Les fichiers hors de Git (`objets/`, `pieces_jointes/`) ne sont pas concernés : leur ancien contenu n'est pas conservé. Ils sont contrôlés par empreinte à l'affichage.

Relire une archive depuis un script n'écrit jamais rien.

## Tableaux CSV

UTF-8, virgule comme séparateur, point décimal, fins de ligne LF et une ligne d'en-tête. Le type de chaque colonne se lit dans `page.json`, jamais dans le CSV : une colonne d'identifiants reste du texte, même si elle contient `007`.

## Magasin objets/

Un fichier d'empreinte SHA-256 `3fa9c2…` se trouve dans `objets/3f/a9c2…`, sans extension. Son nom d'origine se lit dans le champ `nom_origine` de `page.json`. Ces fichiers ne sont pas dans Git : seule leur empreinte y est versionnée.

## Séries DICOM

Une série importée est une grandeur `fichier` de `type_fichier` `dicom`, stockée en `copie`. Son objet dans `objets/` est un zip compressé sans perte, dont les membres portent des noms neutres (`00001.dcm`, dans l'ordre des SOP Instance UID). Le champ facultatif `dicom` la décrit :

| Champ | Contenu |
|---|---|
| `mode` | `pseudonymise` ou `fantome` (fichiers conservés octet pour octet) |
| `modalite` | Modalité DICOM : `CT`, `RTDOSE`, `RTSTRUCT`… |
| `study_instance_uid`, `series_instance_uid` | UID de l'étude et de la série |
| `frame_of_reference_uid` | Repère commun, facultatif : il relie une dose et des structures à une image |
| `fichiers` | Nombre de fichiers de la série |
| `empreinte_pixels` | SHA-256 des pixels dans l'ordre des SOP Instance UID ; absente sans pixels, comme pour un RTSTRUCT |
| `avertissements` | Remarques faites à l'import |

`empreinte` du stockage porte sur le zip, `empreinte_pixels` sur les seuls pixels : celle-ci ne change pas si les balises sont réécrites.

`objets/.cache_empreintes.json` retient les empreintes déjà calculées. Il n'est pas versionné et peut être supprimé : il se reconstruit.

## Composante d'incertitude

| Champ | Contenu |
|---|---|
| `type` | `A` ou `B` |
| `valeur` | valeur déclarée ; en pourcentage de la mesure si `relative` est vrai |
| `unite` | unité de la valeur ; vide si `relative` est vrai |
| `loi` | `normale` (valeur / k), `rectangulaire` (demi-largeur / √3) ou `triangulaire` (demi-largeur / √6) |
| `k` | facteur de la valeur déclarée, pour une loi normale |
| `ddl` | degrés de liberté ; `null` signifie infinis |
| `source` | origine de la composante |
| `erreur_commune` | repère d'une erreur partagée entre plusieurs mesures d'une page, ou `null` ; elle compte alors comme une seule erreur |

Une mesure saisie sans incertitude déclarée reçoit une composante de type B, de valeur 0, et de source `incertitude nulle par défaut`.

Les composantes d'une mesure se combinent en somme quadratique. L'incertitude élargie vaut k · u_c, le facteur k étant choisi à l'affichage et jamais stocké.

## Unités

Les unités s'écrivent dans la syntaxe de MetroloPy : `MBq`, `kBq/mL`, `kg/m**3`, `mA*s`, `um`. L'unité `1` désigne une grandeur sans dimension. Les unités `Ci`, `HU`, `cps` et `lp` sont propres à MedPhysNotebook ; le curie vaut 3,7 × 10¹⁰ Bq.
