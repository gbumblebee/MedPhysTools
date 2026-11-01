# Format d'une archive pymphub

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
index.sqlite       cache de recherche : peut être supprimé et reconstruit
.git/              historique des pages et des fichiers légers
```

## Identifiant d'une page

`AAAA-MM-JJ_sujet_NNNN` : date de la mesure, sujet en minuscules sans accent, puis numéro d'ordre dans l'archive. Exemple : `2026-11-14_activimetre-linearite_0007`.

## page.json

Fichier JSON en UTF-8. Les principaux champs sont :

| Champ | Contenu |
|---|---|
| `version_schema` | version du format de ce fichier |
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

- `scalaire` : `valeur` numérique, `unite` et `composantes` d'incertitude, toutes obligatoires.
- `identifiant` : `valeur` texte, à conserver à l'identique.
- `tableau` : `fichier` CSV dans `brut/` et description de ses `colonnes`. Une colonne se désigne par `tableau.colonne`.
- `fichier` : fichier brut, avec son mode de stockage :
  - `copie` : dans `objets/`, retrouvé par son empreinte ;
  - `reference_fichier` : chemin externe, empreinte, taille et date ;
  - `reference_dicom` : UID DICOM de l'étude, de la série et des instances, avec le nœud DICOM.

## Tableaux CSV

UTF-8, virgule comme séparateur, point décimal, fins de ligne LF et une ligne d'en-tête. Le type de chaque colonne se lit dans `page.json`, jamais dans le CSV : une colonne d'identifiants reste du texte, même si elle contient `007`.

## Magasin objets/

Un fichier d'empreinte SHA-256 `3fa9c2…` se trouve dans `objets/3f/a9c2…`, sans extension. Son nom d'origine se lit dans le champ `nom_origine` de `page.json`. Ces fichiers ne sont pas dans Git : seule leur empreinte y est versionnée.

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
| `erreur_commune` | repère d'une erreur partagée entre plusieurs mesures, ou `null` |

Les composantes d'une mesure se combinent en somme quadratique. L'incertitude élargie vaut k · u_c, le facteur k étant choisi à l'affichage et jamais stocké.

## Unités

Les unités s'écrivent dans la syntaxe de MetroloPy : `MBq`, `kBq/mL`, `kg/m**3`, `mA*s`, `um`. L'unité `1` désigne une grandeur sans dimension. Les unités `Ci`, `HU`, `cps` et `lp` sont propres à pymphub ; le curie vaut 3,7 × 10¹⁰ Bq.
