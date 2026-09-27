# EQUAL-PITCH-01 — premier pic passif commun

Statut du protocole et de sa dérivation : **inferred**. Les modèles empiriques et
paramètres joueur restent **to_calibrate**. Aucun changement des moteurs, lois
physiques, matériaux, lèvres, scores ou politiques de validation.

Depuis la racine du checkout, avec l'environnement Python du projet :

```bash
python -B -m tools.equal_pitch_study --output-dir /chemin/absolu/nouveau-run --dry-run
python -B -m tools.equal_pitch_study --output-dir /chemin/absolu/nouveau-run --target-hz 70
```

`--output-dir` est obligatoire ; `--target-hz` vaut 70 par défaut et est borné à
[50, 90] Hz. Le dry-run annonce les destinataires sans calcul acoustique ni
création de fichiers. Un dossier déjà existant est refusé, même vide ou partiel.
Il n'y a ni reprise implicite, ni écrasement, ni téléchargement.

## Géométries et accord

Les quatre profils proviennent directement de `tools/forced_response_compare.py` :
`cylinder`, `expansion`, `constriction`, `body_bell`. Les trois premiers partagent
entrée/sortie de 3 cm ; `body_bell` est un groupe distinct (3,8 cm vers 12 cm et
longueur initiale différente). Chaque design conserve diamètres, types de segment,
paramètres relatifs et identifiants matériaux ; un seul facteur positif multiplie
**toutes** les longueurs. Les positions sont recalculées par le builder existant.
C'est une comparaison conditionnelle **géométrie + accord**, pas une expérience
isolant une seule bosse.

L'évaluateur appelle `input_impedance` sur la discrétisation existante en cylindres
aux milieux des tranches, sans fusion ni matrice conique. Le rayon de sortie
physique est explicite. Les pertes sont ZK, l'air `CK_DRY_20C` sec et le rayonnement
`silva_unflanged`. Les valeurs complètes d'air, les coefficients/hypothèses de
rayonnement et le matériau synthétique existant sont exportés. ZK omet les effets
de paroi matérielle ; cette omission ne mesure pas leur nullité. La transposition
de la terminaison cylindrique à un pavillon n'est pas une validation géométrique.

Le maximum de **|Zin|** est accordé, jamais le zéro de phase. Pour chaque facteur,
un balayage 10..700 Hz au pas de 1 Hz identifie le premier candidat, puis deux
grilles locales de 81 et 161 points couvrent ses deux intervalles voisins. Il doit
rester exactement un maximum intérieur. L'écart des deux estimations doit être
au plus 0,0005 Hz. Les trois modes complets ne sont pas recalculés à chaque essai.
Le rapport fréquence initiale/cible initialise un bracket ±12 % ; le solveur
sécante sécurisé par bissection utilise ensuite exclusivement des fréquences
mesurées. Refus si bracket sans changement de signe, mode absent/ambigu, valeur
non finie ou limite de 16 itérations atteinte. Chaque mesure conserve facteur,
fréquence, erreur, nombre de tranches et résolution locale.

L'accord est réalisé à h=0,5 cm, résidu requis ≤0,002 Hz. Sur le **même design
fixe**, `extract_modes` et `mode_metrics` existants extraient ensuite f1/f2/f3 à
h=0,5 et 0,25 cm : balayage 10..700 Hz, bassins délimités par leurs vallées,
raffinements de 1025 puis 2049 points. Les candidats doivent rester uniques et
ordonnés ; un contrôle local indépendant du découpage en bassins doit recouper
f1 à 0,002 Hz. Aucun réaccord à h=0,25 ne masque la convergence spatiale.

Le Q est celui de la largeur à -3 dB de |Zin| ; la règle native d'au moins
11 intervalles entre croisements est conservée. Q indisponible reste `null`
avec sa raison. Les pas réels, écarts de fréquence et de Q entre raffinements,
écarts entre maillages, résidus de cible et zéros de phase distincts sont exportés.
Une estimation interpolée sous le pas de grille n'est pas une précision physique
garantie. Les critères ci-dessus et le balayage fini sont des critères d'étude,
non une preuve de l'absence de tout pic à une résolution arbitraire.

## Sources sur les designs fixes

Au maillage h=0,25 cm, **un transfert** par design est calculé sur l'union triée
des fréquences. `apply_source` et `apply_thevenin_source` sont ensuite réutilisés
sans repropagation : pression 1 Pa crête, débit 10⁻⁶ m³/s crête, Thévenin Ps=1 Pa
crête et Rs=0, Zref_common, 10×Zref_common. Pour **tous** les profils,
`Zref_common = rho*c/(pi*0.015²)` en Pa·s/m³ ; aucune normalisation de Rs au
diamètre propre du profil. Le recoupement Rs=0/pression est enregistré pour
Pin/Pload/Pdiss/eta (écart relatif requis ≤10⁻⁸).

Convention exp(+jωt), débit positif vers la sortie ; puissances moyennes en W
issues d'amplitudes crête. Les points communs sont 1..7 fois la cible, soit
70/140/210/280/350/420/490 Hz par défaut, avec cible±1 et ±5 Hz pour la sensibilité.
Ce sont des harmoniques d'une excitation hypothétique définie, pas une FFT jouée.
Le pic propre h=0,25 est exporté dans un groupe séparé, y compris s'il coïncide
exactement avec un point commun. Aucun rapport entre fréquences propres différentes.

JSON et CSV conservent Zin, Hu, Yt, autres transferts/ports disponibles,
Pin/Pload/Pdiss/eta, et Psupply/Pinternal/eta_source pour Thévenin, avec unités,
logs disponibles, `null`, statuts et raisons natifs. Les rapports au cylindre
utilisent même source et même fréquence commune : rapport de modules pour les
observables complexes, rapport signé pour les scalaires. Un dénominateur nul ou
une valeur non résolue produit `null` et une raison. La distinction des groupes
géométriques reste explicite. Pas de somme de watts, classement, score, coefficient
ajusté, causalité ou rendement physiologique. Le spectre joué reste inconnu.

## Exécution et preuves

L'outil Linux lance des enfants **séquentiels**, BLAS=1, limite d'espace d'adressage
768 Mio, CPU 175 s, wall ≤180 s. Le budget total acoustique hors tests est 420 s.
Les réservations de lancement sont 20 s/accord, 35 s/validation, 3 s/sources ;
plafonds respectifs 60/90/20 s, toujours réduits au budget restant. Ces estimations
servent à préserver le budget ; elles ne sont pas des mesures scientifiques.
Tous les accords précèdent les validations, puis les sources. Chaque résultat
intermédiaire est écrit immédiatement. Une étape impossible ou hors budget reste
un échec explicite, sans relance ; les artefacts acquis sont conservés.

Les destinataires sont `design_*.json`, `tune_*.json`, `validation_*.json`,
`sources_*.json`, `run.json`, `run.csv`, `ratios.csv`, `run.md`. Les fichiers design
sont des profils physiques réutilisables. `run.json` contient provenance du vrai
checkout, HEAD/origin-main, état local, empreintes SHA-256 des sources Python,
versions Python/NumPy, méthodes, durées et codes de sortie des enfants. JSON strict
sans NaN/Inf. Le SHA du commit final et les commandes/exits des tests sont à
réconcilier dans le handoff externe, sans prétendre qu'un calcul préalable au
commit utilisait déjà son SHA.

Tests dédiés : solveur non inverse-longueur, paramètres/limites/échecs, préservation
géométrique, exclusivité des sorties, dry-run, sources absolues communes,
séparation fréquence commune/pic propre et petit smoke moteur à 12 fréquences.
Complément : tests natifs ciblés d'extraction modale, sans suite globale ni A–E.
Le livrable n'autorise aucune promotion de matériau.
