# Calcul des incertitudes : méthode et hypothèses

Cette note décrit ce que fait réellement `src/pymphub/incertitudes.py`. Le calcul suit la loi de propagation du GUM au premier ordre, avec GTC.

**À retenir :** à l'intérieur d'un budget, les composantes sont supposées indépendantes, sans covariance. Entre deux mesures, GTC ne suit une corrélation que si elles proviennent du même objet Python. Un même certificat déclaré dans deux budgets est donc traité comme deux erreurs indépendantes (voir « Limites connues »).

## Calcul pour une mesure

**1. Incertitude-type de chaque composante, dans l'unité de la mesure**

| Déclaration | Sens de `valeur` | Incertitude-type |
|---|---|---|
| Type A | écart type de la moyenne | u = valeur |
| Type B, loi normale | incertitude avec son facteur k (1 par défaut) | u = valeur / k |
| Type B, loi rectangulaire | demi-largeur a | u = a / √3 |
| Type B, loi triangulaire | demi-largeur a | u = a / √6 |
| Répétitions (n ≥ 2) | calculée à partir des valeurs | u = s / √n, avec n − 1 degrés de liberté |
| Composante relative | pourcentage de la valeur mesurée | valeur absolue calculée, puis divisée selon la loi |
| Valeur exacte | déclarée explicitement | u = 0 |

**2. Incertitude-type combinée**

Le modèle est y = x + Σ δᵢ, où chaque δᵢ est une erreur de moyenne nulle et d'écart type uᵢ. Sans covariance, on obtient :

```latex
u_c^2 = \sum_i u_i^2
```

La valeur mesurée n'est pas corrigée : chaque δᵢ est centré sur zéro.

**3. Degrés de liberté effectifs (Welch-Satterthwaite)**

```latex
\nu_{\mathrm{eff}} = \frac{u_c^4}{\sum_i u_i^4 / \nu_i}
```

Une composante de type B a des degrés de liberté infinis, sauf si elle en déclare. Une composante de type A doit les déclarer.

**4. Incertitude élargie**

U = k · u_c, avec k = 2 par défaut. L'incertitude relative vaut 100 · U / |valeur|. Le facteur k n'est jamais stocké : le changer ne modifie que l'affichage.

## Calculs entre mesures

GTC propage au premier ordre :

```latex
u^2(y) = \sum_i \sum_j \frac{\partial f}{\partial x_i} \frac{\partial f}{\partial x_j} u(x_i, x_j)
```

| Situation | Traitement |
|---|---|
| Même objet `Budget` utilisé deux fois | Corrélation complète : `x − x` donne u = 0 |
| Après conversion d'unité avec `.en()` | Corrélation conservée |
| Deux budgets établis séparément | Indépendants, même avec une composante identique |
| Même mesure relue et recalculée | Nouveau budget, donc indépendant du précédent |

## Conversions d'unités

La conversion est linéaire : y = f · x + d. Seul le facteur f s'applique à l'incertitude, jamais le décalage d : 20 ± 0,5 °C donne 293,15 ± 0,5 K. Le facteur de conversion est considéré comme exact.

## Limites connues

1. **Erreurs communes non prises en compte.** Deux activités mesurées avec le même étalon ont une erreur d'étalonnage commune, que le logiciel traite comme indépendante.
   - Sur un **rapport** ou une **différence**, cette erreur devrait se compenser en partie : le logiciel surestime alors l'incertitude.
   - Sur une **somme** ou un **produit**, il la sous-estime.
   - Exemple vérifié : le rapport de deux mesures de 100 MBq, chacune à 1 % avec le même certificat, donne 1,41 %. Si le certificat est la seule source d'incertitude, le résultat exact est 0 %.
   - Le champ facultatif `erreur_commune` d'une composante permet déjà de repérer cette erreur, par exemple avec le numéro du certificat. Il est conservé dans les pages, mais **les calculs ne l'utilisent pas encore**.
2. **k = 2 ne garantit pas 95 %.** Le taux de couverture dépend de ν_eff, selon une loi de Student :

   | ν_eff | 2 | 3 | 5 | 9 | 19 | 50 |
   |---|---|---|---|---|---|---|
   | Couverture avec k = 2 | 81,6 % | 86,1 % | 89,8 % | 92,3 % | 94,0 % | 94,9 % |
   | k nécessaire pour 95 % | 4,30 | 3,18 | 2,57 | 2,26 | 2,09 | 2,01 |

   Le logiciel calcule ν_eff, mais n'ajuste pas k automatiquement.
3. **Premier ordre seulement.** Le résultat devient approximatif pour un modèle très non linéaire ou une grande incertitude relative. Il n'y a pas de calcul Monte-Carlo.
4. **Répétitions.** La formule s / √n suppose des mesures indépendantes, dans les mêmes conditions et sans dérive. Elle ne convient donc pas à des valeurs qui évoluent, comme une décroissance, et elle donne 0 si toutes les valeurs sont identiques. La résolution doit alors être déclarée en type B.
5. **Lois symétriques seulement.** Une erreur systématique connue doit être corrigée avant la saisie, et non déclarée comme une incertitude.
6. **Composante relative.** Elle est calculée à partir de la valeur mesurée, puis traitée comme une valeur fixe. Elle est refusée sur une valeur nulle.

Les tests de `tests/test_incertitudes.py` couvrent ces comportements, y compris l'indépendance de deux budgets.
