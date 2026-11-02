# Phase 1 — Saisie : découpage en étapes

**État : étapes 0 à 8 codées, jalon « premier essai réel » à faire.** Premier CQ réel complet prévu vers début février 2027.

**Objectif :** une application Qt avec les espaces Archive et Saisie, pour tenir un cahier de labo libre à la place du tableur.

**Critère de passage :** un CQ réel saisi au lieu du tableur.

Référence : [cahier des charges](cahier-des-charges.md), sections 6, 11 et 14.

## Règles

- Chaque étape dépend de la précédente, sauf indication contraire.
- Une étape n'est terminée que si ses critères sont couverts par des tests et si `uv run pytest` et `uv run ruff check .` passent.
- La logique métier reste dans le cœur et se teste sans Qt. L'interface se teste sans écran, avec `pytest-qt`.
- L'interface est sobre, moderne et pratique. Chaque valeur affiche son unité et son incertitude.
- **Aucun DICOM de patient n'entre dans une archive avant l'étape 9.**

## Jalons

| Jalon | Après l'étape | Ce qui devient possible |
|---|---|---|
| Premier essai réel | 5 | Saisir un vrai CQ sans DICOM : scalaires, tableaux, photos et correction motivée |
| Poste Windows | Dès son arrivée, vers mi-décembre 2026 | Étape W, puis répétée à chaque jalon |
| Critère de passage | 12 | Un CQ réel complet, sous Windows, vers début février 2027 |

## Étapes

### 0. Socle de l'application

**Contenu :** ajout de PySide6 et `pytest-qt`, sous-paquet `pymphub.interface`, fenêtre principale à deux espaces, création et ouverture d'une archive, thème sobre, commande de lancement.

**Validée quand :**
- l'application démarre sans écran dans les tests, et crée puis rouvre une archive ;
- `import pymphub` ne charge toujours pas Qt, et seul `pymphub.interface` l'importe ;
- une archive invalide ou trop récente affiche un message clair, sans plantage.

### W. Compatibilité Windows et version portable

Cette étape se fait à l'arrivée du poste, quelle que soit l'avancée des autres.

**Contenu :** suite de tests du cœur et de l'interface sous Windows, dossier portable avec Python embarqué et `lancer.bat`.

**Validée quand :**
- tous les tests passent sous Windows, y compris les chemins de plus de 260 caractères, dulwich et SQLite ;
- le dossier portable démarre sans installation ni droits administrateur ;
- le même essai passe sur un PC d'hôpital dès que possible : antivirus, `.bat`, partage réseau.

### 1. Espace Archive

**Contenu :** filtres (tags, dates, gabarit, machine, statut, auteur, texte), liste de pages, fiche de page en lecture, chronologie des corrections, alertes.

**Validée quand :**
- les filtres de l'interface donnent les mêmes résultats que `Archive.chercher` ;
- une valeur corrigée s'affiche barrée, avec son motif en info-bulle ;
- les pages illisibles et les modifications hors du logiciel sont signalées ;
- l'interface reste réactive sur 5 000 pages.

### 2. Page libre et onglets

**Contenu :** création d'une page, ajout, renommage, réorganisation et retrait d'onglets, onglet de notes, enregistrement automatique du brouillon, validation, demande de motif pour une correction.

**Validée quand :**
- un nouveau type d'onglet s'ajoute par une déclaration à un seul endroit, démontré par un type de test ;
- un type d'onglet inconnu s'affiche en lecture seule, et ses données restent intactes ;
- réorganiser les onglets ne change pas la relecture d'une grandeur ;
- une page validée ne se modifie qu'avec un motif.

### 3. Scalaires

**Contenu :** saisie de la valeur, de l'unité et des composantes d'incertitude, identifiants, horodatage avec bouton « maintenant », affichage de U en absolu et en relatif, choix de k à l'affichage, raccourcis clavier, mesures répétées.

**Validée quand :**
- une unité fausse est signalée pendant la frappe, et sa suggestion s'accepte en un geste ;
- une valeur sans unité ou sans incertitude ne s'enregistre pas ;
- n valeurs répétées donnent la moyenne et le type A ;
- changer k ne crée aucun commit ;
- une saisie complète au clavier suit l'ordre de la page.

### 4. Tableaux

**Contenu :** grille type tableur, collage depuis Excel, import CSV et tableur, déclaration de l'unité et de l'incertitude par colonne ou par cellule, sélection de valeurs répétées pour le type A, fonctions d'import personnalisées dans un fichier dédié.

**Validée quand :**
- un collage depuis Excel avec virgule décimale donne les bonnes valeurs ;
- une colonne d'identifiants garde `007` ;
- une colonne numérique sans unité ni incertitude est refusée ;
- une erreur dans une fonction d'import personnalisée s'affiche sans faire planter l'application.

### 5. Pièces jointes et photos annotées

**Contenu :** glisser-déposer, vignettes, annotation des photos, liens vers des fichiers en copie ou en référence.

**Validée quand :**
- annoter une photo laisse le fichier d'origine identique, empreinte comprise ;
- les annotations se retrouvent après réouverture, quelle que soit la taille d'affichage ;
- un fichier altéré ou manquant est signalé à l'affichage.

**Jalon : premier essai sur un vrai CQ sans DICOM.**

### 6. Graphes 2D et 3D

**Contenu :** une colonne contre une autre, barres d'incertitude, échelle log, graphes 3D, onglet de graphes dont les réglages sont stockés dans la page.

**Validée quand :**
- les réglages d'un graphe se retrouvent après réouverture ;
- les données affichées et leurs barres d'erreur correspondent au tableau et à ses incertitudes, à k choisi ;
- un graphe s'affiche sans OpenGL, pour les PC d'hôpital et le bureau à distance.

### 7. Erreurs communes, calculatrice GUM et grandeurs dérivées

**Contenu :** exploitation de `erreur_commune` dans les calculs d'une page, évaluateur d'expressions sûr, type de grandeur dérivée avec sa formule, passage éventuel au format 2 de la page.

**Validée quand :**
- le rapport de deux mesures à certificat commun donne le résultat attendu, calculé à la main ;
- `A0 * exp(-ln(2) * t / T12)` donne la valeur, U et le budget ;
- une formule ne peut pas exécuter de code arbitraire ;
- une grandeur dérivée garde sa formule et ne remplace jamais une valeur brute ;
- l'archive figée du format 1 se relit toujours.

### 8. Ajustements et indicateurs

**Contenu :** ajustements lmfit dans le cahier de labo (modèles usuels et personnalisés), fonction ajustée avec son incertitude, indicateurs usuels sur les points ou sur l'ajustement : minimum, maximum, moyenne, dérivée, intégrale. Enregistrement dans un tableau de la courbe suréchantillonnée, sans extrapolation, pour la retracer. Enregistrement dans `page.json` des coefficients, de leur matrice de corrélation et des résultats des indicateurs. Onglet dédié pour les afficher.

**Validée quand :**
- un ajustement exponentiel retrouve des paramètres connus, sur des données synthétiques ;
- l'incertitude de la fonction ajustée concorde avec un calcul de référence et tient compte des covariances ;
- les hypothèses sont documentées, notamment le traitement des incertitudes sur X ;
- coefficients, incertitudes et matrice de corrélation se retrouvent à l'identique après réouverture ;
- la fonction se réévalue depuis les coefficients sauvegardés avec la même incertitude ;
- la courbe enregistrée a plus de points que les données et reste dans leur intervalle ;
- un indicateur enregistré se relit avec sa méthode, ses données d'origine, son intervalle et son incertitude ;
- un ajustement ou un indicateur enregistré ne modifie aucune valeur brute ;
- chaque indicateur est vérifié sur une fonction dont le résultat est connu.

### 9. Import DICOM et pseudonymisation

**Contenu :** ajout de `pydicom`, pseudonymisation par défaut à l'import, option « fantôme » qui conserve le fichier tel quel, choix enregistré dans la page, noms de fichiers neutres, compression sans perte, empreinte des pixels, référence DICOM renseignée depuis les fichiers.

**Validée quand :**
- un import sans option particulière est pseudonymisé ;
- un fichier désigné comme fantôme reste identique, et le logiciel avertit si ses balises ressemblent à celles d'un patient ;
- sans l'option fantôme, aucune balise identifiante de la liste retenue ne reste dans la copie, sur des fichiers de test ;
- sans l'option fantôme, aucun nom présent dans le nom du fichier n'atteint `page.json`, Git ou l'index ;
- les pixels restent identiques, empreinte des pixels comprise ;
- les limites sont affichées : un texte incrusté dans l'image n'est pas détecté.

### 10. Visualiseur DICOM polyvalent

**Contenu :** défilement des coupes, fenêtrage, valeurs mises à l'échelle, balises principales, images multi-frames et formats compressés, onglet DICOM, cache des empreintes pour les grosses séries.

**Validée quand :**
- des fichiers de test CT, dose, TEP et TEMP s'affichent avec les bonnes valeurs physiques ;
- un fichier non pris en charge donne un message clair, sans plantage ;
- les balises identifiantes d'un fichier pseudonymisé ne s'affichent pas ;
- une grosse série s'ouvre sans bloquer l'interface.

### 11. Gabarits dans l'interface

**Contenu :** export d'une page en gabarit par choix des variables, nouvelle page depuis un gabarit, affichage des variables à remplir et des écarts.

**Validée quand :**
- le gabarit créé depuis une page lui est conforme ;
- une page créée depuis ce gabarit indique ce qu'il reste à remplir ;
- une unité incohérente est bloquée à la saisie.

### 12. Sortie de phase

**Contenu :** CQ réel complet, temps comparé au tableur, retours d'ergonomie corrigés, matrice exigences ↔ tests à jour.

**Validée quand :**
- un CQ réel est saisi dans l'application, sous Windows, au lieu du tableur ;
- la saisie prend moins de temps qu'avec le tableur, chronomètre à l'appui ;
- la page se relit en une ligne de Python.

## Hors phase 1

- **Hub et rapports :** modules d'analyse, magicgui, assignation des entrées, normes et rapports LaTeX (phase 2).
- **Intégrité et exports :** vérification globale et export `.eln` (phase 3).
- **Section 16 du cahier des charges :** registre des équipements, journal machine et sauvegarde, sauf décision contraire.
- **Accès au PACS et outils externes** (3D Slicer, ImageJ).

## Décisions

| Sujet | Décision |
| --- | --- |
| Emplacement de l'interface | Sous-paquet `pymphub.interface`, seul autorisé à importer Qt |
| Pseudonymisation | Par défaut à chaque import, UID conservés ; un fichier désigné comme fantôme est conservé tel quel |
| Ajustements | Dans le cahier de labo ; courbe suréchantillonnée sans extrapolation en tableau, pour le tracé seulement ; coefficients et corrélations dans `page.json` et dans un onglet dédié |
| Indicateurs | Minimum, maximum, intégrale, dérivée, etc. enregistrés dans `page.json` |
| Premier CQ réel | Vers début février 2027 |
| Pièces jointes | Copiées dans le dossier de la page dès qu'on les joint, hors de Git. Deux fichiers de même nom sont gardés (`image_2.png`). Retirer une pièce laisse le fichier sur le disque, car l'historique y renvoie |
| Erreurs communes | Un repère correspond à une seule erreur d'écart type 1, multipliée par l'incertitude de chaque mesure. Valable dans une page, formule comme API de lecture |
| Formules | Jamais exécutées : seuls nombres, noms, cellules `tableau.colonne[n]`, + - * / **, `exp ln log10 sqrt sin cos tan` et `pi` sont interprétés |
| Ajustements | Un onglet par ajustement. Fait à la demande, puis figé : si les points changent, il est signalé à refaire. Modèles usuels ou formule personnalisée, dont les coefficients demandent alors leur unité |
| Pondération | Par l'incertitude propre des points, sans remise à l'échelle du χ² ; variance effective pour x ; erreurs communes propagées à part, voir `docs/incertitudes.md` |
| Indicateurs | Sur les points ou sur l'ajustement, depuis l'onglet de graphes ou d'ajustement. Enregistrés avec méthode, données d'origine, intervalle et abscisse. Utilisables dans un calcul, comme les coefficients (`fit.T12`, `pic.x`). Repère facultatif sur le graphe |
| Capture d'un graphe | Image PNG en pièce jointe, insérée dans le premier onglet de notes. Elle reste figée : sa légende donne la date et le k, et un message signale que le tableau a changé depuis |
| Tags dans les notes | `{{nom}}` donne la valeur, U et k. La note garde le tag, jamais la valeur : le texte affiché d'une page validée suit donc les corrections, qui restent tracées avec leur motif. Un tag cassé s'affiche en rouge |
| Colonne calculée | Une formule avec `tableau.colonne` se calcule ligne par ligne. Elle s'affiche dans tous les tableaux concernés, en lecture seule et marquée « calculée », mais reste dans `page.json` : les CSV bruts ne changent jamais. Mêmes nombres de lignes exigés |
| Saisie d'une composante | Une nouvelle composante est préremplie avec k = 2 et la source « None », modifiables. k s'efface pour le type A et les lois non normales. Une composante déjà enregistrée ne change pas |
| Résultat d'un calcul | Figé à l'enregistrement. Un brouillon le met à jour au tour d'enregistrement suivant ; une page validée le propose, à enregistrer avec son propre motif. La fiche signale un résultat périmé |
| Format de la page | La grandeur dérivée entre dans le format 2, déjà courant : pas de format 3 |
| Graphes | matplotlib en 2D et en 3D : même code, aucun appel à OpenGL, et les mêmes figures pour les rapports. Un seul axe y par graphe, des unités incompatibles sont refusées. Un graphe par onglet, 8 séries au plus. Le graphe montre la dernière version enregistrée du tableau |
| Import personnalisé | Peut renvoyer un tableau, des scalaires et des informations. Une valeur sans incertitude reste à compléter ; un nom déjà pris devient `nom_2`, rien n'est écrasé |
| Commentaire d'une pièce jointe | Modifiable dans l'onglet Fichiers ; la légende reste propre à la photo affichée dans une note |
| Fichier importé dans un tableau | Copié automatiquement en pièce jointe ; le commentaire du tableau dit où |
| DICOM avant l'étape 9 | Refusés par l'interface, en pièce jointe comme en lien, d'après l'extension ou la marque `DICM` |
| Vérification à l'affichage | Empreinte calculée jusqu'à 100 Mo ; au-delà, l'état affiché est « non vérifié » |
| Suppression d'une page | Brouillon : confirmation. Page validée ou corrigée : motif obligatoire et identifiant à retaper. Commit de nature `suppression`, dossier de la page dans `corbeille/<id>/`, `objets/` non touché, numéro jamais réutilisé |
| Facteur de Student à l'affichage | Refusé : U = k·u, k = 2 par défaut |
| Liste déroulante des pages dans Saisie | Refusée : l'espace Archive suffit |
| Incertitude « identique / propre à chaque ligne » | Refusée : la colonne d'incertitude actuelle convient |
