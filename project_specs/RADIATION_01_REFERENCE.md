# RAD-01 — charges de radiation cylindrique de référence

Base : `bda62479ee9a4b767fb6e52685bbf41b89d094d5`, arbre
`00638025d1062ec014bb4cd14bafe81b63d2fbf0`. Le choix `legacy` reste le défaut.
Les deux variantes Silva sont des frontières opt-in, pas une adoption matériau,
une nouvelle propagation, ni un modèle validé de pavillon.

## Loi et références

RAD-S01 : Silva et al., JSV322,255–263(2009),
[DOI10.1016/j.jsv.2008.11.008](https://doi.org/10.1016/j.jsv.2008.11.008),
[prépublication v1](https://arxiv.org/pdf/0811.3625), Eq16 et Table1.
RAD-S02 : corrigendum JSV336,296(2015),
[DOI10.1016/j.jsv.2014.10.001](https://doi.org/10.1016/j.jsv.2014.10.001).
Les équations, coefficients et références numériques ont été fournis dans le
brief. Le pilote déclare avoir inspecté le PDF2009 ; le texte des auteurs
reproduit dans l'abstract du corrigendum signale le signe erroné d'Eq20 et
confirme la table. Le PDF original2015 n'est pas reçu. Aucune nouvelle
acquisition ni copie de code tiers dans ce lot.

Convention D-Calc exp(+jωt), onde progressive exp(-jkx), U vers la sortie.
Avec a physique, x=ωa/c réel, s=jx et Z0=ρc/(πa²) :

```
R=-(1+n1*s)/(1+d1*s+d2*s*s)
z=((d1-n1)*s+d2*s*s)/(2+(d1+n1)*s+d2*s*s)
Zr=Z0*z
```

La forme de z est redérivée de (1+R)/(1-R), avec conversion de convention et
signe corrigé. Elle ne recopie pas l'ancienne Eq20. Tableau arrondi inchangé :

| Choix | n1 | d1 | d2 | limite Re(z)/x² | limite Im(z)/x |
|---|---:|---:|---:|---:|---:|
|silva_unflanged|.167|1.393|.457|.24964|.613|
|silva_flanged|.182|1.825|.649|.49987525|.8215|

K=d1²−n1²−2d2>0 ; Re(z)=(Kx²+d2²x⁴)/D≥0 avec
D=(2−d2x²)²+(d1+n1)²x². Les deux polynômes dénominateurs ont leurs pôles dans
Re(s)<0 et |R|≤1. z→1 et R→0 à haute fréquence sont des limites mathématiques,
pas une validation physique. Le beta de réflexion du papier n'est pas beta
matériau. Ni k/Zc complexes de propagation ni rallonge de longueur n'entrent
dans Z0 ou x.

La résistance est calculée directement, sans l'annulation 1+R à basse fréquence.
Pour |x|>1, les polynômes sont divisés par x⁴ (impédance) ou x² (réflexion),
en utilisant 1/x. x=0 donne z=0,R=−1 exactement. Entrées réelles finies
scalaire/1D seulement, booléens/complexes refusés ; a,ρ,c positifs finis.
Les fréquences signées sont admises par le helper de référence pour la symétrie
hermitienne ; cela n'ouvre pas f=0 dans IO ou ZK. Un dépassement de normalisation
ou un coefficient non nul perdu hors plage binaire est refusé explicitement,
sans fallback legacy, epsilon de charge ni nan_to_num.

## Montage, API et exports

Sans collerette : tube cylindrique rigide à paroi mince et bord vif. Flanged :
collerette plane infinie. Air au repos, sans débit moyen ni vortex. Une paroi
épaisse, une collerette finie ou une variation de section proche ne sont pas
rendues exactes par ces lois. Une charge cylindrique transposée au pavillon est
une hypothèse documentaire, pas une meilleure loi ni une borne garantie.

`radiation_models.get_radiation_model(name)` retourne un objet avec `describe`
(sans acoustique) et `evaluate(omega, radius_m, air)` donnant l'impédance et les
métadonnées. `input_impedance` et `loaded_transfer` acceptent le keyword-only
`radiation_model=None`. None conserve l'appel historique direct. Le modèle
explicite est évalué une fois avant propagation, avec le rayon terminal physique.
L'adaptateur legacy appelle le helper inchangé. Aucun défaut global n'est muté.
La charge dans les transferts est aussi celle des puissances.

```text
python -B -m tools.forced_response_compare --config CONFIG --design DESIGN --pressure-peak-pa 1 --loss-model zk --air-reference ck_dry20 --radiation-model silva_unflanged --output-dir EVIDENCE/unflanged
python -B -m tools.forced_response_compare --case body_bell --flow-peak-m3-s 1e-6 --radiation-model silva_flanged --output-dir EVIDENCE/dry --dry-run
```

Une charge par invocation, sorties distinctes sans écrasement. Le contexte
initial reste intact ; l'air effectif et la sélection diagnostique restent
séparés. Le dernier segment est lu dans le profil physique, jamais dans la
dernière tranche milieu. L'épaisseur et l'environnement restent inconnus.

Schéma `dcalc.forced_response.v1` conservé, nouveaux champs additifs `radiation`
et CSV `radiation_*`. `model` continue à nommer les pertes. Nom, variante,
version, coefficients, source, catégorie de fit numérique publié, rayon, ka,
bande et hypothèses accompagnent les valeurs. |ka|>2 porte `extrapolation` avec
raison, sans cutoff ni substitution. Dans la bande, aucune validation empirique
ou du montage n'est sous-entendue. Legacy reste une asymptote basse fréquence
sans seuil de précision inventé. `numerically_complete` ne mesure que la
disponibilité numérique ; les statuts du modèle restent séparés. Les fixtures
bas niveau à charge explicite n'inventent pas de provenance de radiation.

## Vérifications et limite bloquante observée

La fixture contient les16points80décimales du fit arrondi fournis par le pilote,
comparés composante par composante à rtol2e-12,atol0, avec zéro analytique exact.
Ce n'est pas une comparaison à la solution exacte de diffraction. Les quatre
points ODE indépendants corps+pavillon CK20/ZK/Silva sans collerette sont copiés,
pas régénérés avec le backend testé. SciPy/mpmath ne sont pas nécessaires.

Les nouveaux contrôles couvrent passivité/symétrie/pôles, limites arrondies,
gardes, appels de charge uniques et tranches inchangées, dictionnaire et vraie
MaterialDatabase, legacy/ZK, analytique du cylindre et ABCD modéré, parité Zin,
puissances et sources. Les vraies CLI DESIGN YAML/JSON vérifient charge,
extrapolation, contexte, annotations, CSV/JSON/TXT et refus d'écrasement.
Dry-run interdit charge, pertes et propagation. Les tests antérieurs sont gelés.

Premier passage : 9 échecs,51 succès, sources/logs conservés. Huit échecs viennent
de deux erreurs de nouveaux tests : nom `alpha` au lieu de `alpha_total` dans
l'espion, et oubli de l'annotation `total_length_cm` ajoutée par le builder
existant. Ces fixtures sont corrigées, sans modifier le builder ni les assertions
historiques. Le neuvième révèle une limite distincte du backend IO gelé.

À1500Hz sur corps+pavillon, h=.5cm, CK20/ZK, les bornes d'arrondi cumulées ep/eu
sous Silva sans collerette sont environ18.397/18.383 pour |p|=1 et |U|=.49252.
Àh=.25cm, elles atteignent342.133/341.868. Le backend déclare donc Zin/Hu/Yt
indisponibles. Avec la charge legacy, ep vaut aussi16.616 puis308.335 : la
nouvelle frontière n'est pas la cause de cette limite. Àh=1cm, les composantes
sont encore disponibles. La récurrence tan donne un Zin fini, ce qui ne rend
pas pour autant les observables IO indisponibles valides.

Le contrôle de convergence demandé sur les quatre fréquences ne peut être
annoncé réussi via l'API IO. Les bornes, statuts et backend sont réservés hors
scope et restent inchangés ; aucune exclusion silencieuse de1500Hz, projection
forcée, tolérance relâchée ou calcul alternatif présenté comme résultat IO.
Le rapport de remise attribue les résultats exécutés et les éventuels échecs
restants au SHA exact. Une publication validée requiert une décision du pilote
sur ce blocage avant de dépasser le scope.

Suite bornée : deux nouveaux modules, deux IO, PHYS, invariants pertes, deux
THERMO et benchmarks canoniques linéaires, limite300s. Comparatif séparé :
quatre built-ins, f=[40,70,300,500,1000,1500]Hz, h=.5cm, CK20sec, deux pertes
et trois charges ;24propagations réutilisées pour48applications de source.
Les nulls et leurs raisons y restent visibles et ne constituent pas un succès.
Pas de cumul avec pytest, A–E, acquisition externe ou mesure ; aucun matériau
calibré/promu. Les preuves brutes restent locales et expurgées pour la remise.
