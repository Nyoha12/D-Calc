# IO-N2 — transport de l'erreur d'arrondi à maillage fixé

Base : `bda62479ee9a4b767fb6e52685bbf41b89d094d5`. Correction numérique bornée,
sans modification des coefficients physiques, du maillage, de la charge, des
interfaces, budgets ni règles de puissance IO-N1. La stabilité numérique décrite
ici n'est pas une validation expérimentale ou A–E.

## Dérivation et objet de la borne

À chaque fréquence, l'état représenté est x=(p/Zref,U), divisé par les facteurs
de normalisation calculés, avec leur échelle accumulée séparément en logarithme.
Pour une tranche, theta=k*l=phi-i*b, b>=0 ; les coefficients mis à l'échelle sont
c=(exp(i*phi)+exp(-2*b-i*phi))/2 et s=(exp(i*phi)-exp(-2*b-i*phi))/2.
La matrice mise à l'échelle est T=[[c,r*s],[s/r,c]], r=Zc/Zref.

Choisir une racine complexe de r et poser D=diag(sqrt(r),1/sqrt(r)) donne
D^-1*T*D=B=[[c,s],[s,c]]. Avec H=2^-1/2*[[1,1],[1,-1]], H est unitaire et
H*B*H=diag(exp(i*phi),exp(-2*b-i*phi)). Donc ||B||2=1 pour b>=0.
Cette identité algébrique reste vraie pour r complexe non nul. Elle concerne
la propagation normalisée ; elle ne certifie pas la passivité énergétique de
n'importe quelle paire k,Zc simplement conforme aux gardes de signe de l'API.

La norme adaptée d'une erreur e est

```
w = sqrt(abs(r))
N_w(e) = hypot(abs(e_p)/w, abs(e_u)*w)
```

Les phases des racines sont unitaires : nul besoin de calculer ces racines
complexes ou de transformer numériquement l'état physique. Les états p,U,
coefficients c,s et facteurs m restent calculés dans le même ordre qu'avant.
Seule une boule d'erreur scalaire E est transportée par fréquence. Lors du
passage du repère précédent w0 au nouveau w, la norme de D^-1*D0 est
q=max(w0/w,w/w0). On applique E <- q*E, même si q est grand ; aucun plafonnement.
Pour une impédance constante q=1, quelle que soit la subdivision. Pour un module
monotone, le produit des q télescope en un rapport de modules aux extrémités.
Des alternances d'impédance peuvent au contraire amplifier réellement la borne.
L'ancienne propagation par |T| détruisait les compensations de phase et pouvait
croître exponentiellement même dans un cylindre homogène sans perte.

## Défauts locaux et arithmétique du majorant

EPS et les marges 8*EPS / 4*EPS ne sont pas réduits. Pour les termes calculés
p1=c*p, p2=r*s*U, u1=ri*s*p, u2=c*U, le défaut local estimé est

```
C = 8*EPS*((1+abs(phi))*(abs(eplus)+abs(eminus))/2
           + b*abs(eminus)) + F
Dp = 8*EPS*(abs(p1)+2*abs(p2)) + C*(abs(p)+abs(r)*abs(U)) + F
Du = 8*EPS*(2*abs(u1)+abs(u2)) + C*(abs(ri)*abs(p)+abs(U)) + F
F = 8 * smallest_binary64_subnormal
```

C conserve la marge d'argument trigonométrique et des sommes/différences de
coefficient, y compris près des zéros de cos/sin. Le terme b*|eminus| couvre la
sensibilité de l'exponentielle décroissante à l'argument d'atténuation (sans
former exp(+b)). Le terme off-diagonal supplémentaire compte les divisions
r=Zc/Zref et ri=Zref/Zc ; les produits et additions restent majorés par leurs
échelles absolues. La boule initiale contient les erreurs des divisions et de
la normalisation initiale : ep=8*EPS*|p|+F, eu=8*EPS*|U|+F.

En omettant seulement les facteurs de marge dans cette écriture :

```
E_initial = N_w((ep,eu))
E_new = (q*E_old + N_w((Dp,Du))) / m
        + N_w((4*EPS*abs(p_new)+F, 4*EPS*abs(U_new)+F))
ep_new = w*E_new ; eu_new = E_new/w
```

`_roundoff_norm` utilise hypot pour éviter le carré d'une très grande/petite
quantité. Chaque calcul de norme, transport q, addition/division de rayon et
reconversion ep/eu reçoit une marge relative 1+8*EPS ; les opérations de
normalisation reçoivent 4*EPS et F. Ces marges couvrent également le calcul du
poids (division r, module, racine), le changement de repère et l'arithmétique du
majorant. Il n'y a pas de défaut additif de transformation des états, car aucun
état n'est effectivement transformé : seuls des rayons et poids le sont.

Ces expressions sont des **estimations conservatrices binary64**, avec les
hypothèses usuelles d'erreur des opérations et fonctions élémentaires NumPy.
Ce ne sont pas des intervalles à arrondi dirigé, ni une certification libm.
Les produits de défauts d'ordre supérieur sont couverts par les marges usuelles
dans le régime d'erreur petite ; une erreur devenue comparable à l'état retire
la disponibilité. Les erreurs de calcul des modèles physiques k/Zc en amont,
leurs incertitudes matérielles et celles de mesure n'appartiennent pas à E.

Le majorant porte sur les composantes normalisées au maillage **fixé**, avec
l'échelle commune séparée. L'arrondi du logarithme d'échelle reste distinct,
traité par les marges logarithmiques IO-N1 existantes pour les puissances ; E
n'est pas une borne absolue de l'exponentielle d'un logarithme astronomique.
Ainsi b=9e307 conserve un log Hu fini et des composantes résolues, mais le
logarithme de Pload non représentable reste indisponible, conformément à IO-N1.

## Domaine et disponibilité

Stockage O(Nf), propagation en un passage, mêmes budgets. Ni récurrence tan de
secours, ni minimum avec l'ancien majorant, ni retrait de fréquence. La
récurrence tan indépendante n'est utilisée que par les tests.

r et ri doivent avoir un module au moins normal binary64 et des composantes
finies. Une sortie non finie de la propagation, du transport de repère ou du
majorant conserve l'indisponibilité motivée. Les ratios représentables 1e±300
sont testés ; des ratios 1e±600 sont refusés numériquement sans fausse valeur
résolue. Ces gardes peuvent rendre indisponibles certains cas extrêmes auparavant
acceptés sans garantie relative valable. Aucune promesse sur tous les Zref
finis : les matrices et états intermédiaires doivent rester représentables.

Le critère |p|<=ep ou |U|<=eu reste actif. Quart d'onde et pression nulle sont
indisponibles si non résolus ; une petite composante résolue reste disponible.
Source nulle, charge nulle et résistance nulle gardent leurs zéros analytiques.
Les projections subnormales, les logs et la récupération après source/charge,
les puissances signées, eta indisponible près d'une annulation et les gardes
IO-N1 ne changent pas. `loaded_transfer`, `ComplexCurve`, `_curve`, `apply_source`
et les helpers de puissance sont inchangés (comparaison AST avec la baseline).

## Vérifications effectivement exécutées

Environnement de cette remise : Python 3.12.3, NumPy 2.5.3, interpréteur
`/home/dcalc/.venv/bin/python`, calculs séquentiels, BLAS un thread, limite mémoire
2 Gio et timeout 120 s par commande. Aucune installation. pytest, SciPy et
mpmath sont absents. Preuves nouvelles séparées :
`/home/dcalc/rdc-runs/IO-N2-20260925T192506Z/` (commandes, codes, versions,
rapport, JSON, SHA256 des fichiers gelés avant/après).

Le module autonome `test_forced_response_roundoff` couvre :

- corps+pavillon, CK20/ZK, charge legacy, 1000/1500 Hz et h=1/.5/.25 cm,
  parité Zin, deux sources et puissances ;
- oracle stdlib Decimal à 70 chiffres : exponentielles complexes par Taylor,
  sections homogènes analytiques et produit ABCD indépendant à impédances
  complexes variables, dont forts changements de phase ; longue subdivision
  jusqu'à 4096 tranches et Zref=1e-5/1e5/1e15 ;
- comparaison des erreurs absolues des deux états normalisés à ep/eu, ainsi
  que des transferts, et cylindres/ABCD avec pertes nulles, legacy et ZK,
  charges réelle, complexe et nulle ;
- vraies annulations, k=2-1000j, logs 800/9e307, sous-débordement et récupération,
  grands composants cartésiens, ratios extrêmes, zéros, puissances signées,
  rendement lorsque la puissance projetée déborde/sous-déborde ;
- convergence contre la fixture ODE main inchangée.

Le rouge initial comprend 10 méthodes unittest : 2 échouent dans 9 sous-cas,
8 passent. Les mêmes 10 passent après correction ; un onzième test renforce
ensuite les projections extrêmes. Les passages répétés ne sont pas additionnés
au nombre de tests uniques. Le rapport de remise donne les passages finaux.

Sur 24 scénarios (4 profils × 2 pertes × 3 maillages), p,U,log_scale et charge
sont bit à bit identiques à la baseline. Les transferts et puissances disponibles
dans les deux versions ont les mêmes valeurs. Les changements de statuts sont
énumérés dans `regression.json`, y compris les puissances devenues résolues.
À 1500 Hz, corps+pavillon CK20/ZK, |p|=1 :

| h cm | ep avant | ep après |
|---|---:|---:|
| 1 | 0.152440788 | 1.370429108e-12 |
| .5 | 16.61601141 | 2.431099564e-12 |
| .25 | 308.3346697 | 4.543075170e-12 |

La croissance résiduelle avec le nombre d'opérations est attendue. Sur les
six oracles cylindriques Decimal à 4096 tranches, le plus grand rapport
(erreur absolue observée)/(borne) est 0.104 (arrondi vers le haut). Les oracles
emploient les valeurs binary64 d'entrée converties exactement en Decimal et
leur longueur totale exacte ; ils ne régénèrent aucune fixture historique.

## Discrétisation spatiale et couverture #56

L'erreur spatiale est mesurée **séparément** par raffinement contre les ODE
indépendantes préexistantes. E ne la majore pas. Les normes L2 finales sont :

| Référence, h=.25 cm | Zin | Hu | Yt |
|---|---:|---:|---:|
| main, 7 fréquences jusqu'à 1000 Hz | 1.02288602e-4 | 1.03394556e-4 | 4.13774088e-4 |
| #56, 70/400/1000/1500 Hz | 1.17647611e-4 | 1.27163843e-4 | 4.31378428e-4 |

Les trois seuils 2e-4/2e-4/6e-4 et les réductions >3 à chaque h/2 passent.
Les erreurs par fréquence et par maillage restent dans le JSON. Les coefficients
Silva #56 (.167,1.393,.457), le code de charge et sa fixture sont lus depuis les
fichiers téléchargés du head `6e42c4ff92c5e0c026e8dbc13d4413ebd7042394`, hors
dépôt, sans copie de production, modification ou régénération de référence.

Le script direct vérifie aussi 36 scénarios de parité sous les trois charges
legacy/unflanged/flanged, deux pertes, matériau synthétique ou PVC de la DB,
trois maillages et six fréquences dont 1500 Hz ; puis 18 scénarios cylindre/deux
sections, trois pertes et trois charges avec ABCD, sources et puissances.
Ce sont des scénarios numériques directs, **pas les fonctions pytest #56**.
La sélection de modèle dans son API haut niveau, les spies, l'interleaving des
défauts, la CLI, le dry-run et les exports de la PR ne sont pas exécutés ici.

Suites unittest historiques pertinentes exécutées séparément : terminaison
physique, invariants de pertes et ordres de grandeur linéaires. Les suites
pytest historiques IO/CLI/THERMO et celles de #56 ne sont pas exécutées. Aucun
affaiblissement de test/fixture, rejeu global/A–E, nonlinéaire, optimisation,
calibration ou promotion. Le pilote reste chargé de la revue complémentaire.
