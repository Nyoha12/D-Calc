# IO-01 — réponse entrée-sortie sous excitation acoustique définie

Base: `ae94a15b7d5d2ca11043752bc448961a0ccb6622`, arbre
`764cec39db82d4127a0b7d9dc5ff42da4e93ba0d`. API additive et diagnostic séparé,
sans changement de défaut, matériau, géométrie, rayonnement, score ou modèle de
joueur. Les sept fichiers IO sont nouveaux ; `transfer_matrix.py` reste intact.
La PR donne le SHA propre testé/publié et les résultats exacts de la remise.

## Convention, ports et source

Convention exp(+j omega t), onde progressive exp(-j k x), U1 et U2 vers la sortie.
Amplitudes complexes **crête** ; p en Pa, U en m³/s, f en Hz, omega=2pi f en rad/s,
Z en Pa.s/m³. La matrice conceptuelle relie [p1,U1] à [p2,U2] ; p2=Zr U2.

```
Zin=(A Zr+B)/(C Zr+D)       Yin=(C Zr+D)/(A Zr+B)
Hu=U2/U1=1/(C Zr+D)         Yt=U2/p1=1/(A Zr+B)
Zt=p2/U1=Zr Hu              Hp=p2/p1=Zr Yt
```

Ces quatre transferts sont chargés, et non les paramètres intrinsèques ABCD.
Hu, Yt et Yin sont calculés directement : aucun quotient par un Zin projeté
éventuellement indisponible. Sous débit : p1=Zin U1, U2=Hu U1. Sous pression :
U1=Yin p1, U2=Yt p1. Dans les deux cas p2=Zr U2, avec exactement la même charge.

Pin=0.5 Re(p1 conj(U1)), Pload=0.5 Re(Zr)|U2|², Pdiss=Pin-Pload en W.
eta=Pload/Pin est défini seulement pour une entrée positive et numériquement
résolue dans le domaine logarithmique, même si sa projection en W déborde ou
sous-déborde. Il est indépendant de l'amplitude et du type de source à système fixé.
Il ne mesure aucun rendement du joueur. Les watts d'une grille ne sont pas
sommés : aucun spectre broadband ni poids de source n'est implicite.

## API et backend

Imports directs depuis `didgeridoo_optimizer.acoustics.forced_response` :

```python
mesh = prepare_mesh(physical_design, h_cm=0.5, count=len(frequency_hz))
transfer = loaded_transfer(
    frequency_hz, mesh, materials, air,
    exit_radius_m=physical_design.segments[-1].d_out_cm / 200,
    loss_model=model,
)
flow = apply_source(transfer, 'volume_flow', 1e-6)
pressure = apply_source(transfer, 'pressure', 1.0)
hu = transfer['transfers']['Hu'].payload()
```

Les deux applications de source réutilisent le transfert : aucun nouveau calcul
de k/Zc ou propagation. `ComplexCurve.values()` refuse une projection manquante ;
`payload()` expose value, log_abs, phase_rad, status et reason alignés. Un scalaire
de source est explicitement commun aux points ; un vecteur doit avoir exactement
la forme de la grille. Zéro source est admis à l'API : états et puissances nuls,
eta indisponible. La CLI demande une amplitude réelle strictement positive.

`loaded_transfer` attend un maillage uniforme, le rayon physique séparé, des
matériaux résolus et un AirProperties. Les helpers existants d'aire/impédance et
les LossModel existants sont appelés dans l'ordre sortie→entrée, sans deuxième
appel caché à input_impedance. La comparaison Zin avec ce dernier appartient
aux tests uniquement. Aucun tableau matrices/états de taille Nsegments×Nf n'est
retenu ; les tableaux de propagation sont O(Nf), plus le maillage O(Nsegments).

Le backend `transfer_from_slices(f, slices_from_outlet, load, zref=...)` accepte
un itérateur de (longueur_m,k,Zc), depuis la sortie, et une charge finie passive
scalaire ou alignée, y compris zéro. Pas de charge infinie ni terminaison fermée
ajoutée à la radiation publique. Il refuse Im(k)>0 et Re(Zc)<=0. Ce backend
n'est pas une certification universelle de passivité pour une paire arbitraire.

Pour theta=k l=u-jb, b>=0, on transporte [p/Zref,U], Zref constant positif :

```
cstar=(exp(j u)+exp(-2b-j u))/2
sstar=(exp(j u)-exp(-2b-j u))/2
p_new=cstar*p+(Zc/Zref)*sstar*U
U_new=(Zref/Zc)*sstar*p+cstar*U
m=max(abs(p_new),abs(U_new))
p,U=p_new/m,U_new/m ; Lscale+=b+ln(m)
```

L'état initial est [Zr/Zref,1], normalisé de la même façon. Aucune cosh géante
ni matrice globale brute. A l'entrée : ln|Hu|=-Lscale-ln|U|,
ln|Yt|=-Lscale-ln(Zref)-ln|p|, phases -arg(U) et -arg(p).
Zt/Hp et les états sous source sont formés dans le domaine logarithmique avant
projection : un transfert non représentable peut redevenir représentable après
application d'une source ou charge. Zref est une normalisation numérique,
pas une nouvelle impédance physique ; la CLI utilise rho*c/S de l'entrée physique.

## Disponibilité et limites numériques

Entrées : grille 1D non vide, strictement croissante, réelle finie, f>0 ; booléens,
complexes, NaN/Inf et broadcasting multidimensionnel refusés. Budgets par cas et
modèle : Nf<=20000, Nsegments<=10000, produit<=2000000. Le chemin CONFIG/DESIGN
contrôle le budget avant allocation de grille/propagation. Le backend itérateur
vérifie aussi sa limite au fur et à mesure de la consommation des tranches.

Une projection trop petite est `underflow`, complexe=null avec log/phase gardés ;
un dépassement de composante est `overflow`. Le module peut dépasser float.max
si ses deux composantes cartésiennes sont encore représentables. Les subnormaux
sont signalés. Une composante annulée ou sous sa borne d'arrondi est indisponible
avec raison ; elle n'est pas convertie en zéro physique. Une charge exactement
nulle impose au contraire p2=Pload=0 analytiques. Aucun nan_to_num, epsilon de
charge ou eta tronqué à [0,1]. Les données JSON ne contiennent aucun NaN/Inf.

Les bornes d'arrondi sont des estimations conservatrices d'arithmétique binary64,
pas des incertitudes expérimentales ni une preuve par arithmétique d'intervalles.
Le transport de l'erreur utilise désormais une boule dans la norme adaptée à
Zc/Zref, contractante pour chaque tranche mise à l'échelle ; les changements
d'impédance, défauts locaux de coefficients/produits et normalisations restent
comptabilisés. La dérivation, le domaine et les preuves IO-N2 sont détaillés dans
[IO_N2_REFERENCE.md](IO_N2_REFERENCE.md). Ce majorant à maillage fixé est distinct
de l'erreur de discrétisation spatiale mesurée contre les références ODE.
Pour les puissances, ep|U|+eu|p|+ep*eu borne le produit sur l'échelle |p||U|,
avec 64eps(Nsegments+1) et une marge de calcul logarithmique. Pin trop proche de
cette borne garde sa valeur signée avec statut `roundoff_limited` ; eta est null.
Pdiss garde aussi son résidu signé et sa tolérance en W. Une valeur négative
significative est signalée `passivity_violation`, jamais corrigée artificiellement.
`numerically_complete` et les comptes de statuts distinguent un export terminé
de la résolution numérique de toutes ses observables.

## CLI, contexte et exports

Depuis le checkout, avec EVIDENCE un nouveau dossier local choisi par le caller :

```text
python -B -m tools.forced_response_compare --config CONFIG --design DESIGN --pressure-peak-pa 1 --loss-model both --air-reference ck_dry25 --output-dir EVIDENCE/design
python -B -m tools.forced_response_compare --case cylinder expansion constriction body_bell --flow-peak-m3-s 1e-6 --loss-model both --air-reference ck_dry20 --f-min 40 --f-max 1000 --points 97 --h-cm .5 --output-dir EVIDENCE/synthetic
python -B -m tools.forced_response_compare --config CONFIG --design DESIGN --flow-peak-m3-s 1e-6 --output-dir EVIDENCE/dry --dry-run
```

`--flow-peak-m3-s` et `--pressure-peak-pa` sont exclusifs et exactement un est
requis. Legacy est le défaut ; zk/both exige ck_dry20 ou ck_dry25. Legacy seul
sans référence conserve l'air config ou DEFAULT_AIR du built-in. Une référence
substitue l'air seulement en mémoire pour les deux modèles : original_air,
original_context et effective_parameters restent distincts. Les six coefficients
CK sont exportés quand ce jeu est utilisé, avec T/RH, provenance et indicateur
d'usage thermovisqueux. Aucune simulation d'air humide n'est prétendue par CK sec.

CONFIG/DESIGN utilise directement load_fixed_context : MaterialDatabase réelle,
F1–F3, builder/validator et fingerprints restent ceux du contrat FIXED. Les
annotations valides restent présentes. DESIGN vient du cwd sans homonyme de
secours. `--h-cm/--f-min/--f-max/--points` remplacent seulement les paramètres
effectifs du diagnostic ; le contexte original n'est pas réattribué. Dry-run
lit/valide contexte, matériau, géométrie et budgets, sans acoustique ni sortie.
Les règles de variantes absentes conservent la politique FIXED et son warning.

Exports propres : `forced_response.json`, `.csv`, `.txt`, schéma
`dcalc.forced_response.v1`, distinct de FIXED/optimizer. Chaque point comprend
charge, ka_out, source, transferts, ports, puissances, logs/statuts/raisons et
unités. CSV et JSON ont les mêmes lignes. Le résumé français explicite la portée.
Sérialisation et alignement précèdent toute création de sortie ; écrasement refusé,
erreur I/O/calcul/syntaxe donne un code non nul et stdout JSON contrôlé. Une erreur
d'écriture tardive peut laisser un bundle partiel neuf, sans annonce de réussite.

SHA/dirty/origine reprennent la garde des sources réellement chargées ; le script
tools a sa propre vérification de suivi/racine et son SHA256. Origine inconnue
reste null avec raison ; un HEAD dirty n'identifie pas exactement le code modifié.
Les chemins/profils de l'utilisateur restent locaux. Aucun score, optimisation,
Pareto, robustesse, non-linéaire, pics, champ lointain ou son joué n'est exécuté.

## Références et vérifications numériques

Relations de ports et efficacité : R10, Kausel, Mayer & Beauchamp (2021),
JASA149,2698–2710, [DOI10.1121/10.0004303](https://doi.org/10.1121/10.0004303),
équation1 et §II.B équation15, expressions fournies intégralement dans le brief.
La forme publiée |Hp|²Re(1/Zr)/Re(1/Zin) est équivalente lorsque les quotients sont
définis ; le bilan de ports couvre ici aussi Zr=0. Le notebook annexe est annoncé,
non reçu. Certains profils de l'article ont été ajustés sur mesure : aucun statut
de validation indépendante n'en découle. Aucun code tiers ou jeu réel n'est copié.

ZK, air CK et provenance suivent [THERMO](THERMO_01_ZK_REFERENCE.md), sans copie
de noyau. La charge suit [PHYS](PHYS_REF_01_BOUNDARY_REFERENCE.md), sans rallonge.
Le fichier `tests/fixtures/forced_response_reference.json` contient uniquement
les sept points fournis par le pilote : ODE DOP853 à aire variable, jve constitutif,
rtol1e-12/atol1e-14 et corps analytique. Stabilité répétée1.6309492e-11. Référence
numérique synthétique, pas mesure3D, pas fixture régénérée par le backend testé.

Les cylindres chargés/ABCD modérés sont contrôlés à rtol1e-10 (plus de petits
atol propres aux unités), incluant pertes nulles, legacy/ZK, charges passives
réelles/complexes/nulles et subdivision. Parité Zin avec la récurrence tan, y
compris près des résonances et avec MaterialDatabase/dict : erreur relative L2
observée au plus4.581e-13 sur les tests dédiés, sans changement de tolérance.
Pour k=2-1000j, Zc=Zr=3e5,l=1 : Zin=Zc, ln|Hu|=-1000, phase=-2, complexe
indisponible ; l'oracle ABCD brut déborde comme attendu. Variations Zref1e5/1e7,
récupération après source en logs, séparation Yin/Zin, zéros et annulations sont
testés. Les bilans intègrent indépendamment la densité dissipée
0.5[Re(jkZc)|U|²+Re(jk/Zc)|p|²] par quadrature NumPy sur deux tranches, sans SciPy.
Rotation de phase, facteur4 en puissance et invariance eta sont aussi contrôlés.

Convergence du corps+pavillon, mêmes air/charge et fréquences de fixture :

| h cm | L2 relative Zin | Hu | Yt |
| --- | ---: | ---: | ---: |
|1|0.00163347585|0.00165463562|0.00660817601|
|0.5|0.000408995605|0.000413591642|0.00165449437|
|0.25|0.000102288602|0.000103394556|0.000413774088|

Réduction >3 à chaque division h/2, cohérente avec l'ordre2 des tranches milieu.
Bornes finales2e-4 pour Zin/Hu,6e-4 pour Yt : critères numériques de cette fixture,
pas des tolérances empiriques/universelles. Aucun raffinement modal n'est lancé.

CK20/ZK,h=.5cm,70Hz, mêmes longueur1.21m et entrée/sortie30mm pour les trois :

| Profil | abs(Hu) | Pload W, débit1e-6m³/s | Pload W, pression1Pa |
| --- | ---: | ---: | ---: |
|cylinder|43.6044978183|5.13443200685e-8|7.81067037099e-11|
|expansion|7.43654614878|1.49338705827e-9|1.46736881644e-10|
|constriction|12.2389835877|4.04501869789e-9|5.07985570878e-11|

La charge et son proxy sont strictement identiques ; les transferts diffèrent.
L'ordre de puissance cylindre/expansion s'inverse suivant la source. L'exemple
ne prétend aucune monotonie générale, facilité de jeu ou mesure physiologique.
Ces nombres sont des calculs locaux, sans ajustement vers les repères du pilote.

Les suites IO couvrent aussi les vraies CLI YAML/JSON/built-ins, exports alignés,
provenance outil, entrée intacte, dry-run, refus d'ambiguïtés, budgets avant calcul,
sentinelles contre scoring et seconde propagation. Un premier rouge (1 échec,
45 succès) a montré que la borne des sommes seules manquait l'annulation interne
de cos/sin : corrigé dans IO, trace et source exactes conservées. La revue a
ensuite propagé ces bornes aux puissances et explicité les zéros analytiques.
Les preuves locales conservent tous les runs, commandes, versions, SHA/diff,
sources avant/après, codes, timeouts et paramètres TEMP/TMP/caches de processus.

La remise PR indique le passage final groupé et le petit comparatif sur SHA propre,
ses temps séparés legacy/ZK et ses limites. Les résultats historiques PHYS/FIXED/
THERMO ne sont pas rebaptisés comme nouveaux tests. Pas de rejeu A–E, campagne
nonlinéaire, acquisition Zenodo, optimisation ou calibration. Python3.13.1,
NumPy2.2.2, PyYAML6.0.2, pytest9.0.3, Matplotlib3.10.0 existants ; aucune dépendance
installée. Aucun résultat ne constitue une validation expérimentale ou promotion
matériau, une FFT jouée, un SPL distant ou une pression respiratoire statique.

## Retouche IO-N1 / revue R18 : limite du logarithme de puissance

Point de départ relu : `986fd7e2d45e9e009cb852a2f5835879e9901f36`.
La revue R18 de la PR #55 a identifié un défaut de disponibilité, sans remettre
en cause les relations de ports ni le backend physique. Pour la tranche
astronomique de test k=2-9e307j, l=1, Zc=Zr=Zref=3e5 et une source non nulle,
ln|Hu|=-9e307 reste fini mais le doublement du logarithme dans Pload dépasse
binary64. Le précédent -Inf était étiqueté à tort comme zéro analytique.

Le marqueur de zéro de puissance est désormais réservé aux conditions
explicitement établies : source nulle, état analytique nul, résistance de charge
nulle. Un logarithme de puissance non fini après calcul donne null,
`unavailable` et une raison explicite. Cette indisponibilité est transmise à
eta et à Pdiss ; Pin et les logs/phases de Hu encore calculables sont conservés.
Les opérations susceptibles de déborder sont contrôlées après calcul ; leur
signal flottant ne constitue ni une valeur physique ni un zéro. Le quotient
logarithmique d'efficacité est lui aussi contrôlé avant projection.

Les logarithmes finis avec projection trop petite restent `underflow`, avec
log conservé. Un produit peut redevenir représentable après application de la
source en domaine logarithmique. Aucun clamp du rendement, modification de
tolérance acoustique ou assimilation d'une puissance inconnue à zéro. Les
valeurs et résidus signés restent exposés avec leur qualité numérique.

Les mêmes régressions ont d'abord produit **5 échecs, 91 succès**, avec les deux
sources et le vrai export JSON/CSV : faux zéro Pload, contrôle direct du helper,
et statuts d'export. Sources exactes et logs rouges conservés avant correction.
Les positifs couvrent aussi l'underflow à log fini, la récupération après source,
les zéros réels, et les cas antérieurs d'atténuation 1000/800 et d'eta indépendant
de la projection absolue. Commande de remise bornée :

```text
python -B -m pytest -q didgeridoo_optimizer/tests/test_forced_response.py didgeridoo_optimizer/tests/test_forced_response_cli.py
```

Le compte rendu de PR attribue le résultat vert observé à son SHA effectivement
testé, incluant cette note, et donne la comparaison stricte des quatre profils
usuels avec le point de départ. Les anciennes assertions sont conservées ; seules
les fonctions de disponibilité des puissances changent en production. Backend,
CLI, reporting, fixtures indépendantes et tous les modèles existants sont gelés.
Les 446 tests et 120 sous-tests historiques restent attribués à `986fd7e`, sans
cumul avec ce passage ciblé ni prétention de nouveau rejeu global/A–E. Aucun
nouveau benchmark de performance, téléchargement, résultat expérimental ou
calibration ; environnement Python/bibliothèques inchangé.

## Extension SOURCE-02 optionnelle

La source Thévenin explicite (`--thevenin-pressure-peak-pa` avec
`--source-resistance-pa-s-m3`) utilise `dcalc.forced_response.v2` et un groupe
`source_powers` distinct. Les deux sources idéales et leurs exports v1 gardent
leur contrat. Voir [SOURCE_02_REFERENCE](SOURCE_02_REFERENCE.md) pour l'API,
les bornes numériques, les unités et les limites physiques.
