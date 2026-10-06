# DESIGN-CONTRACT-01 — conception statique sous exigences

Cette livraison ajoute une API et une CLI de recherche géométrique bornée. Elle
traite un instrument **fixe**, une configuration CONFIG nominale, des grandeurs
scalaires géométriques et les maxima locaux de `|Zin|` du TMM natif. Aucun champ de
coulisse n'est requis. Elle ne livre pas encore les instruments coulissants ou
modulaires, le jeu, l'accessibilité des toots, les seuils/régimes, les paramètres
incertains ni une garantie sur un continuum. L'issue #79 garde ce périmètre plus
large ouvert.

Les demandes U01–U07 restent conservées : fixe/coulissant/modulaire, verrous et
obligations non compensables, toots choisis multiples, relations entre géométrie,
composants, matériau, joueur/source, environnement et fabrication, et distinction
entre incertitudes et variables choisies. Les corrections ci-dessous ne couvrent
aucune capacité physique supplémentaire ; passif et joué restent distincts.

## Exécution et API

Depuis la racine du dépôt, avec NumPy et PyYAML déjà disponibles :

```bash
python -m tools.constrained_design \
  --config project_specs/examples/constrained_design/config.yaml \
  --design project_specs/examples/constrained_design/design.json \
  --request project_specs/examples/constrained_design/request.yaml \
  --output-dir /tmp/dcalc-contract-plan --dry-run

python -m tools.constrained_design \
  --config project_specs/examples/constrained_design/config.yaml \
  --design project_specs/examples/constrained_design/design.json \
  --request project_specs/examples/constrained_design/request.yaml \
  --output-dir /tmp/dcalc-contract-result
```

Les destinations doivent être neuves, y compris en dry-run. Remplacer les chemins
ci-dessus si ces dossiers existent déjà. Les symlinks de sortie, y compris dans
les ancêtres, sont refusés. Une publication partielle n'est jamais écrasée.
CONFIG, DESIGN et REQUEST explicites sont résolus depuis le répertoire courant ;
la DB et ses variantes doivent correspondre aux chemins relatifs à CONFIG.
Le cas fourni autorise explicitement les marches et utilise le véritable id DB
`pvc_pressure` (PVC pression), sans coefficient ajouté/modifié.

```python
from didgeridoo_optimizer.pipeline.constrained_design import run, load_inputs, Evaluator
contract, plan = load_inputs(config, design, request)
trial = Evaluator(contract)(contract.initial)  # essai, pas conformité finale
final = Evaluator(contract).verify(contract.initial)  # vérification distincte
result = run(config, design, request, output_dir, dry_run=False)
```

`run` supervise un enfant POSIX, BLAS mono-thread, plafond 768 Mio, CPU 175 s,
mur 180 s, tué et récolté sur expiration/interruption. L'API d'évaluation directe
est synchrone : son appelant doit assurer les limites temporelles/processus ; les
budgets de tailles restent contrôlés par le code. Le dry-run est portable, lit et
valide les entrées, établit le plan/capacités et ne lance aucun TMM, solveur ou
écriture. Code de sortie 0 : commande terminée et publication réussie, **pas**
preuve de conformité ; lire `status`, `conforming` et `request_fully_covered`.
Code 1 : exécution incomplète ; code 2 : entrée invalide ou erreur avant livraison.

## Schéma strict `dcalc.design_request.v1`

Un document YAML/JSON contient les clés suivantes. Les clés non décrites sont
refusées ; `metadata` est réservé aux annotations JSON finies. Doublons à toute
profondeur, merges/aliases YAML cycliques, clés YAML non textuelles, NaN/Inf,
booléens dans les nombres et versions inconnues sont refusés. Les booléens dans
les annotations restent des annotations. Une entrée mal formée est
`invalid_request`. Une capacité connue et correctement décrite mais absente de
ce produit est `unsupported`, sans conversion vers une exigence passive.

| Clé | Contrat |
| --- | --- |
| `schema_version` | Obligatoire, exactement `dcalc.design_request.v1`. |
| `variables` | Obligatoire, liste éventuellement vide. Chaque entrée : `id`, `fields`, `unit`, `bounds`. |
| `derived` | Liste facultative. Chaque entrée : `id`, `field`, `expression`, `unit`, `bounds`. |
| `criteria` | Obligatoire, 1 à 64 critères décrits ci-dessous. |
| `modes` | Jusqu'à 16 modes : `id`, `order` entier 1..32, `window_hz: [min,max]`. |
| `spectrum` | Obligatoire : `min_hz`, `max_hz`, `step_hz`, `final_step_hz`, `refinement_hz`, `h_cm`, tous positifs. |
| `budgets` | Tous les plafonds publics du tableau suivant sont obligatoires. |
| `models` | Facultatif : `loss_model`, `air_reference`, `radiation_model`. |
| `preference_method` | `lexicographic` par défaut ; `weighted` reconnu mais unsupported avant calcul. |
| `scope` | Portée commune facultative, composée avec celle de chaque critère. |
| `metadata` | Annotations facultatives conservées. |

Les bornes d'une variable/dérivée sont deux nombres strictement ordonnés dans
l'unité déclarée. Leurs extrémités sont admissibles ; aucune projection de la
valeur initiale ni renormalisation du DESIGN n'est effectuée. Les unités sont
`m`, `cm`, `mm` (longueur), `Hz`, `1` (sans dimension), `cent`, `Pa`. Les variables
physiques n'utilisent que longueur/sans dimension selon le champ natif.

Les chemins ont la forme `segments.1.d_in_cm` : indices à partir de zéro,
`length_cm`, `d_in_cm`, `d_out_cm`, ou
`profile_params.throat_diameter_cm`, `profile_params.flare_parameter`,
`profile_params.power`. Le champ doit déjà exister dans DESIGN. Plusieurs champs
de même dimension peuvent partager une variable, par exemple les deux diamètres
d'un cylindre ; leurs valeurs initiales doivent être identiques. Aucun champ ne
peut dépendre de deux variables/définitions. Une dérivée a une expression et des
bornes propres ; elle n'ajoute pas de liberté. Les définitions sont triées selon
leurs dépendances, cycles interdits ; le DESIGN initial doit les satisfaire.

Tous les autres champs sont verrouillés, y compris id, annotations, pièces,
matériaux, profils non exposés. Seuls les champs dérivés natifs recalculés par
`validate_design` (`position_start_cm`, `position_end_cm`,
`metadata.total_length_cm`) sont exclus de cette comparaison. Le masque est
contrôlé avant et après validation de chaque essai, à la vérification finale,
avant export et après relecture. `SearchSpace.repair_genome` n'est jamais appelé.

Le domaine des profils évite les clamps natifs : `power >= .05`,
`flare_parameter >= 1e-6` pour exponentiel et `>= .05` pour powerlaw.
`throat_diameter_cm` de bouche est une grandeur géométrique, mais n'est pas
représenté dans le profil acoustique natif ; cette omission est exposée au plan.
La présence de `power` rend le `flare_parameter` d'un powerlaw acoustiquement
inactif, conformément au moteur natif. Aucun nouveau modèle de bouche/profil
n'est introduit.

### Expressions et relations

Les expressions géométriques sont typées, sans `eval`, Python ou unité implicite :

```yaml
# Une référence de champ
expression: {field: segments.1.d_in_cm}
# Ou longueur totale physique
expression: {total_length: true}
# Ou constante dimensionnée
expression: {constant: {value: 3, unit: cm}}
# Ou combinaison affine ; coefficient explicitement sans dimension
expression:
  affine:
    - coefficient: 2
      expression: {field: segments.1.length_cm}
  offset: {value: 1, unit: cm}
# Ou rapport entre expressions de même dimension, dénominateur non nul
expression:
  ratio:
    - {field: segments.1.d_out_cm}
    - {field: segments.0.d_out_cm}
```

Une relation à satisfaire est un critère `geometry` avec cette expression et une
cible/bornes ; elle ne réécrit pas les champs. Une définition `derived` produit
explicitement un champ. Une relation non proposée par cette grammaire est
mal formée, et non « satisfaite ». Une portée ou capacité connue non calculée
reste `unsupported`. Les contradictions directes entre intervalles d'obligations
sur la même expression et entre une obligation et des champs tous verrouillés
sont détectées avant recherche. Cela n'est pas un solveur symbolique général de
contradictions ; un échec de recherche reste un échec de recherche.

### Critères, cibles, rôles et portées

Chaque critère conserve `id`, `observable`, `unit`, `tolerance`, `level`, `scope`,
`role`, et exactement l'un de `target`/`bounds`. Une quantité est
`{value: nombre, unit: unité}` ; `bounds` contient deux quantités. La tolérance
utilisateur est positive. Les bornes sont élargies de cette tolérance SI ; une
cible est entourée de la tolérance SI ou logarithmique en cents. Les bornes en
cents sont refusées : utiliser une cible en Hz/rapport et une tolérance en cents.

| Observable | Références | Niveau et dimensions |
| --- | --- | --- |
| `geometry` | `expression` | `geometry`, dimension de l'expression |
| `resonance_frequency` | `mode` | `passive`, Hz |
| `resonance_ratio` | `numerator`, `denominator` (ids modes) | `passive`, unité `1` |
| `played_frequency` | aucune conversion en mode passif | `played`, Hz ; unsupported |
| `toot_accessibility`, `threshold`, `regime`, `material_property` | cible/bornes quantitatives conservées | unsupported |

Les niveaux connus sont `geometry`, `passive`, `played`. Un niveau incompatible
avec une observation calculable est explicitement non couvert. Les capacités
futures quantitatives peuvent porter leurs références métier dans `metadata` ;
les classes/régimes catégoriels n'ont pas encore de grammaire de cible dédiée.

Une cible fréquentielle peut aussi être une note à octave explicite :
`target: {note: A3, reference_hz: 440, cents: 0}` (référence A4, tempérament égal).
La notation originale est conservée. Plusieurs fréquences et des rapports
arbitraires positifs sont permis ; aucune préférence universelle pour 3.
Le même mode peut être référencé par une fréquence et plusieurs ratios. Les
ordres de modes sont uniques : un pic ne devient pas deux modes/cibles différents. Deux cibles fréquentielles distinctes sur le même id de mode sont refusées, même si leurs tolérances se recouvrent.

- `hard` : obligation ; une violation n'est jamais compensée par une préférence.
- `preference` : cible explicite ; en méthode lexicographique, `priority` entier
  0..1000, plus petit d'abord, priorités distinctes. Les erreurs normalisées
  absolues sont comparées par tuple, jamais moyennées. Le poll local et le tri
  des témoins vérifiés n'établissent pas un optimum global.
- `observe` : observation conservée, sans action sur la recherche ou les verrous.

Zéro préférence est valide : rechercher un témoin faisable, sans coût caché de
simplicité. `weighted` impose un `weight` positif à chaque préférence et interdit
`priority` ; cette méthode reste unsupported. Les obligations calculables peuvent
être recherchées, mais l'optimisation demandée n'est pas revendiquée. Une
préférence non calculable ne transforme pas une obligation violée en solution.

`scope` admet `configurations`, `registers`, `common_conditions` (listes de noms),
`q` et `uncertain_parameters`. La seule portée calculée est configurations
`[nominal]`, registres `[passive]`, conditions `[CONFIG]`, sans `q` ni incertitude.
Omettre ces trois listes sélectionne ces valeurs. Un `q` déclare `kind`
`fixed|discrete|continuous`, `unit`, et `values` (une pour fixed) ou `bounds`
(deux pour continuous). Chaque incertitude déclare `id`, `field` (référence
native), `piece_id` et `variation` dimensionnée positive. Une pièce commune
conserve le même identifiant d'erreur dans les configurations ; elle n'est pas
multipliée en erreurs indépendantes. Toute portée plus large reste conservée et
unsupported, même si ses samples nominaux paraissent conformes.

Deux contre-exemples contractuels sont testés sans les qualifier de validation
acoustique : les observations prescrites T03 `5.6545/8.0135/4.4727` cents sous
±0,1 mm de **rayon** (±0,2 mm diamètre) ne garantissent pas ±5 cents ;
`e(s)=6 sin²(12πs)` passe 13 repères mais dépasse 5 aux milieux. Aucune grille
finie arbitraire n'est présentée comme une couverture de continuum.

## Acoustique, identité des modes et vérification

Le défaut du contrat est `legacy`, air CONFIG exact. `zk` exige
`air_reference: ck_dry20|ck_dry25`. `radiation_model` vaut `legacy`,
`silva_unflanged` ou `silva_flanged`. Le choix passe par `design_pitch.models` et
les arguments effectifs de `input_impedance` ; les champs CONFIG ne peuvent
activer implicitement un modèle. CONFIG original et options effectives sont
exportés séparément. Avec ZK, les propriétés acoustiques matériau sont **omises**,
ni nulles mesurées ni source d'une discrimination artificielle. La base réelle
sert toujours à valider les identifiants.

La sortie physique est `exit_radius_m = d_out_cm / 200` du dernier segment
physique, indépendamment du maillage. Les cylindres homogènes restent entiers
pendant la recherche ; les profils non uniformes passent par
`GeometryDiscretizer`. Le contrôle final compare la grille `step_hz` à
`final_step_hz`, puis h à h/2, avec subdivision des cylindres pour une comparaison
indépendante. Chaque maillage est identifié par nombre de segments, métadonnées
et empreinte ; il est reconstructible depuis le DESIGN et h. Ce n'est pas un
nouveau DESIGN physique.

Les pics sont les maxima locaux de `|Zin|` selon `acoustics.peaks.find_peaks`,
ordonnés dans le domaine spectral déclaré. `order: 1` signifie premier maximum
**de ce domaine**, sans prétention sur les fréquences extérieures. Fenêtres,
bassins initiaux, ordre, prominence, courbure et intervalles de raffinement sont
exportés. Des grilles locales successives contrôlent l'unicité du maximum du
bassin ; pic absent, perdu, multiple, trop plat, près du bord ou hors fenêtre
produit `unresolved`. Un changement du nombre de pics entre essais/raffinements
empêche une substitution silencieuse de branche. Aucun choix du pic le plus
proche d'une cible, aucun zéro de ImZ ni harmonie FFT ne définit un toot.

L'indisponibilité d'un mode au raffinement affecte seulement les critères qui
le référencent, y compris les ratios. Un mode facultatif absent ou un mode
déclaré sans critère ne retire pas les valeurs, marges et estimations des autres
modes vérifiables. Une obligation dépendante reste non conforme ; une observation
ou préférence non résolue rend la couverture partielle explicite. Les erreurs
communes de propagation et changements du nombre de pics continuent à empêcher
la conformité acoustique lorsque l'identité des branches n'est plus établie.

La tolérance utilisateur, l'estimation numérique de convergence et une borne
certifiée sont distinctes. `certified_bound`/`certified_uniform_bound` restent
null : h/h2 et différences de grilles ne sont pas des preuves mathématiques.
Une marge trop proche de l'estimation devient `unresolved`. Silva hors |ka|≤2
sur le domaine déclaré devient `out_of_domain`. Le TMM reçoit uniquement des
fréquences réelles. Aucune trajectoire temporelle, FIR, fit, seuil ou classification
n'est appelée.

## Recherche et plafonds

Recherche déterministe NumPy : différences finies mises à l'échelle, moindres
carrés amortis, déplacement borné, backtracking, redémarrages éventuels avec
`seed=0`. L'acceptation utilise la violation maximale, sans dilution par moyenne.
Les itérations et sondes restent des essais, même si géométriquement valides. Le
rang du Jacobien est seulement un diagnostic local. Budget épuisé, Jacobien
incomplet, modes perdus et absence de témoin ne prouvent jamais l'impossibilité.

| Budget obligatoire | Plafond absolu v1 |
| --- | ---: |
| `evaluations` | 300 évaluations de recherche |
| `iterations` | 40 |
| `variables` | 16 libertés choisies |
| `frequencies` | 10000 par appel TMM |
| `segments` | 2048 mailles |
| `history` | 300, au moins evaluations |
| `seconds` | 170 secondes de recherche (garde entre appels) |
| `frequency_segment_product` | 2000000 |

Le fichier d'entrée est limité à 2 Mio, DESIGN à 128 segments physiques,
expressions à profondeur 16 et 64 termes, dérivées à 64. Le plan estime les
maillages extrêmes à partir des bornes avant allocation. Les tailles réelles
sont aussi contrôlées avant génération de maillage et propagation. La recherche
est suivie d'au plus quatre vérifications finales distinctes ; ce plafond fixe
est public, distinct d'`evaluations`. Tous ces calculs partagent le plafond mur
de 180 secondes de l'enfant, jamais une succession de nouveaux budgets enfants.
Les témoins de sondes conformes sont conservés même s'ils ne deviennent pas un
pas de l'optimiseur. Sans préférence, aucun classement de complexité n'intervient.
`search.best` considère chaque évaluation, y compris les sondes avant épuisement
du budget ou Jacobien incomplet et les redémarrages. Un témoin admissible prime
toujours sur un essai non admissible ; seuls les critères de préférence explicites
et évaluables classent les témoins. Une préférence non résolue ne devient pas
zéro. Historique et témoins conservés restent distincts de la vérification finale.

## Exports, provenance et reprise

Sorties : `plan.json`, `checkpoint_*.json`, `result.json`, `criteria.csv`,
`trials.csv`, `report.txt`, `manifest.json`, `execution.json`, et seulement pour
les témoins finaux conformes `design_001.json`, etc. Sans témoin conforme,
`partial_design.json` est clairement un résultat partiel. Les JSON sont stricts,
CSV scalaires en SI/cents, sans valeurs non finies déguisées en zéro. États par
critère : `satisfied`, `violated`, `unresolved`, `unsupported`, `out_of_domain`,
`invalid_request` (refus de la demande avant essais). Valeur absente = null/CSV
vide, avec raison. Marges positives satisfont le critère sous la résolution
déclarée, et non une validation physique.

`conforming` concerne les obligations hard du modèle/scénario ;
`request_fully_covered` indique séparément si toutes les observations/préférences
sont calculées. Une demande sans hard ne crée pas d'obligation implicite, et
une préférence unsupported ne permet aucune revendication d'optimisation. Une demande dont toutes les observations sont unsupported ne produit aucune solution conforme ; une couverture partielle avec obligations satisfaites porte le statut `hard_feasible_partial_coverage`.
`optimization_status` et `global_optimum_proven: false` restent explicites.
Les suggestions de relaxation sont un champ séparé, vide en v1 ; REQUEST
n'est jamais réécrit.

Le chargeur natif vérifie les empreintes CONFIG/DESIGN/DB/variantes avant/après
lecture. REQUEST est parsé et fingerprinté depuis les mêmes octets. Les sources
Python effectivement chargées sont lues, hashées et contrôlées avant/après
calcul ; l'identité Git est déclarée inconnue si les sources ne sont pas suivies.
Le parent compare le plan aux entrées/sources relues par l'enfant. Un hôte API peut avoir chargé des modules supplémentaires : leurs octets sont contrôlés séparément, sans prétendre que l'enfant les a exécutés.
La CLI vérifie avant lecture des entrées/calcul/écriture qu'elle est exécutée
depuis son fichier canonique et que ses octets n'ont pas changé depuis son
chargement. Une copie extérieure, même identique, est refusée avec une erreur
de provenance ; son hash n'est jamais remplacé par celui du fichier canonique.
`python -m tools.constrained_design` et les scripts clients de l'API restent
pris en charge. Les versions Python/NumPy/PyYAML effectives sont enregistrées.

Les primitives natives `safe_path`, `atomic_bytes`, `write_json` publient sans
écrasement, avec fsync et plafond total 250 Mio, JSON individuel 4 Mio. Les
`.pending-*` sont des traces non validées et peuvent rester après interruption.
Le checkpoint conserve le dernier témoin/pas accepté par la recherche, son
checksum, REQUEST et l'identité CONFIG/DESIGN/DB/modèles/sources. Il est étiqueté
**non vérifié final**. `reporting.constrained_design.read_checkpoint` relit et
contrôle ce contexte et les verrous. Le format
`dcalc.constrained_design.checkpoint.v2` fige séparément le contexte du calcul
(dont les octets REQUEST, modèles effectifs, versions et masque de verrous) et
le manifeste des sources du producteur. Un checksum lie contexte, manifeste et
candidat. Le lecteur rehash les fichiers de ce manifeste sans les importer ;
ses propres imports supplémentaires ne deviennent pas des sources du producteur.
CLI vers API et API vers processus neuf sont ainsi relisibles sans import CLI
artificiel. Les anciens checkpoints sans ce manifeste explicite sont refusés
comme incompatibles ; produire un nouveau bundle avec la CLI corrigée.
Il n'y a aucune reprise automatique ni remise à zéro du budget dans un dossier
existant. Une nouvelle exécution peut utiliser un DESIGN exporté, une nouvelle
sortie et un REQUEST explicitement choisi. Un reçu d'exécution absent ou en erreur
n'est pas un succès de publication.

## Acceptation et limites de cette livraison

L'exemple dissipatif démarre des six longueurs et diamètres prescrits, avec
uniquement les diamètres 1..4 liés entrée/sortie et bornés à ±25 %. Les segments
0/5 et toutes les longueurs sont verrouillés. Cibles : 70,
163.8704944609, 297.7709899052 Hz, tolérance 0,1 cent chacune ; domaine 10..700 Hz,
grilles 1 puis 0,5 Hz et raffinement local. Le témoin final fourni au brief n'est
pas un seed du solveur. Cette tolérance numérique n'est pas une précision
physiologique.

Les tests incluent aussi un cylindre à longueur libre ciblant 140 Hz, comparaison
indépendante à l'équation homogène avec mêmes pertes/charge natives, un oracle
synthétique sans pertes, des cas entièrement verrouillés, les ratios non égaux à
3, refus stricts, verrous, provenance, reprise, ambiguïté des pics et vraie CLI.
Les tests historiques ciblés sont rejoués sans modification de leurs assertions,
fixtures ou tolérances. Aucun matériau n'est promu et aucune politique A–E n'est
modifiée. Fixe nominal ne signifie ni instrument joué, ni conception modulaire,
ni robustesse géométrique, ni couverture de configurations continues.
