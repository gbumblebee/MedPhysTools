# Calcul des incertitudes : méthode et hypothèses

Cette note décrit ce que fait réellement `src/pymphub/incertitudes.py`. Le calcul suit la loi de propagation du GUM au premier ordre, avec GTC.

**À retenir :** à l'intérieur d'un budget, les composantes sont supposées indépendantes, sans covariance. Entre les mesures d'une page, les composantes qui portent le même repère `erreur_commune` comptent comme une seule erreur. Sans repère, elles restent indépendantes, même si leur source a le même nom.

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
| Même repère `erreur_commune`, dans une même page | Une seule erreur partagée, voir ci-dessous |
| Deux budgets établis séparément, sans repère | Indépendants, même avec une composante identique |
| Même mesure relue et recalculée | Nouveau budget, donc indépendant du précédent |

## Erreurs communes

Chaque repère correspond à une seule variable e, de moyenne nulle et d'écart type 1. Une mesure x de la page reçoit u · e, où u est son incertitude-type pour cette composante, dans son unité :

```latex
x = x_0 + u_x \, e + \sum_i \delta_i
```

Cette écriture couvre les deux cas, sans distinction dans le code :

| Déclaration | Mesures | Effet |
|---|---|---|
| Relative : 1 % du même certificat | a = 100 + 1 · e, b = 50 + 0,5 · e | a / b ne dépend plus de e : le certificat se compense exactement |
| Absolue : ± 1 MBq du même zéro | a = 100 + e, b = 50 + e | a − b ne dépend plus de e, mais a / b en dépend encore : dR/de = 1/50 − 100/50² = −0,02 |

Sur une somme, l'erreur s'additionne au lieu de se combiner en quadrature : avec 1 % du même certificat sur 100 et 50 MBq, elle pèse 1,5 MBq et non √(1² + 0,5²) ≈ 1,12 MBq.

**Hypothèses :**
- La corrélation est complète et de même signe : c'est la même erreur, en valeur comme en signe.
- Un même repère doit avoir les mêmes degrés de liberté partout. Sinon, le calcul est refusé.
- Une composante de colonne sans repère est indépendante d'une cellule à l'autre. Pour un certificat partagé par toute une colonne, il faut donc déclarer un repère.
- Le partage se limite à une page. Deux pages ne se combinent pas dans un calcul.

## Calculs entre grandeurs

Une formule comme `A0 * exp(-ln(2) * t / T12)` se calcule sur les valeurs enregistrées de la page, avec les règles ci-dessus :

- **Unités.** Elles suivent le calcul et se simplifient entre unités de même dimension : `h/min` devient 60. `MBq*h` reste tel quel, alors que pint en ferait un nombre sans dimension.
- **Températures.** Une température en °C passe en kelvins à l'entrée : un rapport n'a de sens qu'ainsi, et un écart garde sa valeur. Le résultat reste en K, car le logiciel ne peut pas distinguer un écart d'une température.
- **Résultat figé.** Il ne change pas tout seul si une grandeur est corrigée : la fiche le signale, et l'onglet Calculs propose le nouveau résultat, à enregistrer. Sur une page validée, il demande donc son propre motif.
- **Budget.** Il donne la part de chaque source dans l'incertitude du résultat. Une erreur compensée y figure à 0.

## Conversions d'unités

La conversion est linéaire : y = f · x + d. Seul le facteur f s'applique à l'incertitude, jamais le décalage d : 20 ± 0,5 °C donne 293,15 ± 0,5 K. Le facteur de conversion est considéré comme exact.

## Limites connues

1. **Erreurs communes à déclarer.** Le logiciel ne devine rien : sans repère `erreur_commune`, deux mesures au même certificat restent indépendantes. Il surestime alors l'incertitude d'un rapport ou d'une différence, et sous-estime celle d'une somme ou d'un produit. Une corrélation partielle ne se déclare pas.
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
