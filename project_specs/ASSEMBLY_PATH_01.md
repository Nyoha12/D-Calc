# ASSEMBLY-PATH-01 — assemblages physiques et exigences multipositions

Ce chemin produit assemble des pièces physiques partagées, génère leurs cavités
natives et cherche leurs dimensions et réglages sous un même ensemble
d'obligations. Il réutilise les critères, unités, modèles, extracteur de pics et
solveur de R41. Il n'ajoute ni modèle acoustique, ni coefficient matériau, ni
simulation de lèvres, ni score de facilité de jeu.

## Exécuter l'exemple fourni

Depuis la racine du dépôt, avec son environnement Python opérationnel :

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python -m tools.assembly_path \
  --config project_specs/examples/assembly_path/config.yaml \
  --assembly project_specs/examples/assembly_path/assembly.yaml \
  --request project_specs/examples/assembly_path/request.yaml \
  --output-dir ./assembly-example-dry --dry-run
```

La commande sèche lit, valide et planifie. Elle ne calcule aucun spectre et ne
crée aucun répertoire, Design temporaire ou sortie acoustique. Une capacité connue
non couverte reste visible dans le plan. Une structure inconnue est rejetée.
Retirer `--dry-run` et choisir une destination nouvelle pour calculer :

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 \
python -m tools.assembly_path \
  --config project_specs/examples/assembly_path/config.yaml \
  --assembly project_specs/examples/assembly_path/assembly.yaml \
  --request project_specs/examples/assembly_path/request.yaml \
  --output-dir ./assembly-example-run
```

Toute destination existante, même partielle, et tout lien symbolique dans le
chemin de sortie sont refusés. Un code de commande réussi indique la production
d'un résultat lisible ; il ne signifie pas que les objectifs sont conformes.
Lire les statuts métier dans la réponse et dans `result.json`.

L'exemple part volontairement de `Li = 0.56 m` et `q_long = 0.40 m`, avec un
stock extérieur de `0.5 m`. Deux variables cherchent réellement les cibles
passives 140 et 70 Hz avec une tolérance de 0.2 cent, sous ZK / CKdry20 /
radiation legacy. Le catalogue comprend une collision radiale à ID extérieur
31 mm, un stock extérieur de 0.6 m incompatible au réglage court, puis
l'alternative ID40 mm admissible. Les rejets restent dans l'historique.
Un rejeu de développement a obtenu environ 140.000579 et 70.001092 Hz,
avec des erreurs respectives de 0.0072 et 0.0271 cent. Ces résultats illustrent
la recherche numérique ; ils ne sont ni une mesure ni une promesse de notes
jouées. Le résultat propre à chaque exécution reste l'autorité pour son témoin.

## Schéma d'assemblage `dcalc.assembly.v1`

Le document porte `schema_version`, `id`, `pieces`, `blocks` et
`configurations`. `paths` et `annotations` sont optionnels. Les identifiants
sont uniques dans leur collection et commencent par une lettre ; ils peuvent
ensuite contenir lettres, chiffres, `_` et `-`. Les champs inconnus, booléens
numériques, nombres non finis, clés dupliquées et cycles YAML sont refusés.
Les alias YAML acycliques ne créent pas de liberté physique implicite.

`pieces` est un objet indexé par identité physique. Chaque pièce désigne un
`material_id` résolu par la vraie DB du CONFIG. Le CONFIG et la DB passent par
les helpers natifs ; aucun parseur parallèle ne redéfinit leur interprétation.

| Pièce | Champs physiques |
| --- | --- |
| `kind: tube` | `length`, `inner_diameter`, exactement l'un de `outer_diameter` ou `wall` |
| `kind: fixed` | `segments` natifs entiers ; `stock` optionnel avec `length` et `outer_diameter` |

Les longueurs sont des quantités R41, par exemple `{value: 30, unit: mm}`.
Les unités de longueur acceptées sont `m`, `cm`, `mm`. Une pièce fixe comprend
une liste de segments portant `id`, `kind`, `length`, `diameter_in`,
`diameter_out` et éventuellement `profile_params`. `diameter_in/out` sont les
diamètres du conduit aux deux extrémités axiales, pas les diamètres intérieur et
extérieur du matériau. Les `profile_params` conservent leurs noms et unités
natifs, par exemple `power` ou `throat_diameter_cm`. Leurs valeurs sont
verrouillées dans cette version. Les profils natifs hors domaine, qui exigeraient
un clamp implicite, sont rejetés.

Les segments fixes gardent leur ordre et leurs profils. Ils ne sont pas
tronqués pour fabriquer une partie télescopique. Le stock fixe optionnel est une
enveloppe cylindrique déclarée : sa longueur doit couvrir le profil entier et
son diamètre extérieur dépasser le diamètre du conduit. Une surlongueur de
stock est conservée, jamais automatiquement coupée ; son volume de matière
reste indisponible si l'usinage correspondant n'est pas défini.

`blocks` est une liste ordonnée en série. Un bloc fixe est
`{id: mouth, kind: fixed, piece: prefix}`. Un bloc télescopique porte `inner`,
`outer`, `orientation`, `seal` et `min_overlap`, par exemple :

```yaml
- id: slide
  kind: telescope
  inner: inner
  outer: outer
  orientation: outer_first
  seal: inner_tip_isolated
  min_overlap: {value: 80, unit: mm}
```

`outer_first` expose `[outer(q), inner(Li)]` ; `inner_first` inverse ces deux
tronçons. Préfixes et suffixes fixes sont des blocs explicites. Plusieurs blocs
indépendants en série sont possibles. Chaque pièce déclarée est utilisée une
seule fois par l'architecture. Le double emploi, la double insertion dans une
même pièce, les branches, coudes et annulus communicants sont incompatibles
avec ce schéma ; ils ne sont jamais convertis silencieusement en série.

Une configuration associe une quantité `q` à chaque bloc télescopique :

```yaml
configurations:
  short: {q: {slide: {value: 10, unit: mm}}}
  long: {q: {slide: {value: 400, unit: mm}}}
paths:
  - {id: travel, kind: affine, from: short, to: long}
```

Un instrument fixe utilise une configuration `{nominal: {}}` : il n'a besoin
d'aucun champ de coulisse ni d'un chemin artificiel.

## Géométrie, stock et portée du certificat

Avec tube intérieur `Li, di, de`, tube extérieur `Lo, Do, w` et exposition `q` :

- `o = Lo - q`, `q >= 0`, `0 < di < de < Do`, `w > 0` ;
- `min_overlap <= o <= min(Li, Lo)` ;
- longueur déployée = somme des longueurs fixes + `Li + q` ;
- volume du conduit des tubes = `pi/4 * (di² Li + Do² q)` ;
- volume matière des tubes = `pi/4 * ((de²-di²) Li + ((Do+2w)²-Do²) Lo)`.

Ces inégalités utilisent les nombres décimaux d'entrée sous forme rationnelle
exacte. Aucune marge flottante cachée ne prolonge la course. `q = 0` supprime
exactement le tronçon extérieur exposé nul ; la pièce extérieure reste dans la
nomenclature. Les références physiques ne dépendent pas du numéro d'une tranche
TMM ou du retrait de ce tronçon.

Le joint `inner_tip_isolated` est une hypothèse explicite : le bord du tube
intérieur isole l'annulus du conduit. Un joint ailleurs correspond à une autre
topologie. L'admissibilité géométrique ne certifie ni guidage, ni étanchéité réelle,
ni frottement, ni usure. Aucune loi de fuite n'est ajoutée.

Le rayon terminal est celui du dernier segment physique exposé, même quand le
maillage le subdivise. Avec `q > 0`, inverser l'ordre change notamment le diamètre
de sortie et peut donc changer les résultats acoustiques ; à `q = 0`, la sortie
est celle du tube intérieur dans les deux sens, en l'absence de suffixe fixe.

Les volumes de conduits cylindriques ou linéaires utilisent l'intégrale
polynomiale. Pour les autres profils natifs fixes, le volume exporté est une
approximation Simpson-256 du profil natif, explicitement étiquetée. Le volume
matière et le volume du conduit sont distincts. La DB native actuelle ne fournit
pas de densité effective numérique documentée : `density_kg_m3` et `mass_kg`
restent `null`. Une catégorie qualitative de masse ou de densité n'est pas une
densité physique.

Pour un chemin affine entre deux configurations, les dimensions des pièces
restent constantes et les marges de recouvrement sont affines en `q`.
Leurs extrêmes aux extrémités donnent un certificat géométrique nominal sur
l'intervalle. Ce certificat ne porte jamais sur les fréquences, même si tous
les échantillons acoustiques calculés sont conformes.

## Demande commune `dcalc.assembly_request.v1`

Le conteneur porte `schema_version`, `variables`, `projections`, `budgets` et,
optionnellement, `derived`, `catalogue`, `coverage`, `metadata`. Le parent
complet est conservé et haché. Chaque projection identifiée associe une
configuration à un sous-contrat **exactement** `dcalc.design_request.v1` :

```yaml
projections:
  - id: short
    configuration: short
    request:
      schema_version: dcalc.design_request.v1
      variables: []
      # criteria, modes, spectrum, budgets, models : contrat R41 natif
```

Chaque configuration déclarée doit avoir sa projection. Les libertés se trouvent
exclusivement dans le conteneur physique ; les `variables` des sous-demandes
sont vides et elles ne déclarent pas de dérivées. Les plans effectifs restent
reliés au hash parent, à la configuration, aux pièces et à leur version de
projection. Le parent n'est pas réécrit pour masquer une portée non couverte.

Les noms acoustiques natifs sont `resonance_frequency` et `resonance_ratio`.
Une fréquence passive ne devient pas une `played_frequency` ou une
`toot_accessibility`. Les cibles musicales, conversions en cents, tolérances,
rôles `hard`, `preference`, `observe`, modèles et validations sémantiques viennent
de R41. Les modes utilisent un ordre et une fenêtre explicites ; aucun pic le
plus proche d'une cible n'est sélectionné. Le suivi au raffinement distingue
une modification du domaine bas demandé et un voisin de haute fréquence hors
de ce domaine. Une ambiguïté ou un mode absent reste non résolu.

Les scopes natifs ne sont pas élargis par une simple projection. Conditions
communes : statique, air CONFIG et options R41, legacy par défaut. Les demandes
jouées, incertaines, robustes ou portant une autre condition connue sont
conservées comme non couvertes ; un champ de scope inconnu est rejeté.
Des cibles différentes peuvent être déclarées à chaque position. Cette version
n'offre pas de critère acoustique reliant directement deux configurations.

Une variable lie un ou plusieurs champs physiques avec une valeur commune :

```yaml
variables:
  - id: length
    fields: [pieces.inner.length]
    unit: m
    bounds: [0.52, 0.59]
  - id: position
    fields: [configurations.long.q.slide]
    unit: m
    bounds: [0.35, 0.42]
```

Références stables supplémentaires : `pieces.outer.inner_diameter`,
`pieces.outer.wall`, `pieces.prefix.segments.bore.length`,
`pieces.prefix.segments.bore.diameter_in`, `diameter_out`. Seuls les champs
effectivement déclarés sont exposables. Un champ non exposé est verrouillé.
Les essais exposés sont sérialisés canoniquement en SI pour ne pas perdre une
liaison par conversion d'unité ; la notation utilisateur reste dans le parent.
Les vérifications des verrous et des bornes s'appliquent aux essais, aux
projections et à la relecture.

Les `derived` contiennent `id`, `field`, `unit`, `bounds`, `expression`.
L'expression physique peut être `{field: ...}`, `{constant: {value: ..., unit: ...}}`
ou `{affine: [{coefficient: ..., expression: ...}], offset: {value: ..., unit: ...}}`.
Les dépendances doivent être acycliques, compatibles dimensionnellement et
satisfaites dans l'entrée initiale. Elles ajoutent des relations, pas des libertés.
Quantités, coefficients affines, bornes et liaisons utilisent la même convention
décimale rationnelle exacte que la géométrie : `0.1 m + 0.2 m = 0.3 m`, également
en cm ou mm. `0.30000000000000004 m` reste une valeur différente, sans tolérance
physique de secours. Les chaînes sont évaluées dans l'ordre des dépendances.
Le parent, les annotations et les quantités nominales déjà exactes sont conservés.
Le solveur conserve son interface numérique SI ; chaque candidat est revérifié
exactement. La sérialisation préfère les mètres, puis utilise cm ou mm si cette
notation permet de conserver exactement la valeur. Une nouvelle valeur dérivée
impossible à sérialiser exactement dans ces quantités numériques est refusée
explicitement, jamais arrondie pour faire passer une relation. Cela ne crée
aucune liberté supplémentaire.
Les critères géométriques projetés peuvent utiliser `total_length` ; les
expressions visant `segments.N` sont refusées, car cet index peut changer à
`q = 0`. Les dimensions individuelles sont contrôlées par les champs physiques,
leurs bornes, liaisons et dérivées.

Le catalogue énumère des objets `{id, values, orientations?}`. `values` mappe
un champ physique à une quantité ; `orientations` peut changer le sens d'un
bloc télescopique identifié. Une alternative ne réécrit pas un champ déjà
variable ou dérivé. Les identités de pièces restent communes. L'architecture
initiale doit actuellement être géométriquement admissible pour construire le
plan ; les alternatives incompatibles sont, elles, conservées puis rejetées
pendant l'exploration. Cette limite ne constitue pas une preuve d'impossibilité
de ces autres familles d'assemblages.

Toutes les obligations de toutes les projections participent à l'admissibilité
avant préférence. Aucune position ne compense une autre. Zéro préférence est
valide ; le classement des préférences disponibles est lexicographique, avec
priorités globales distinctes et sans coût caché de masse ou de complexité.
Une préférence pondérée native conserve son statut non couvert.

`coverage: {kind: discrete}` porte sur les positions déclarées.
`coverage: {kind: continuous, path: travel}` conserve une exigence continue,
mais l'acoustique reste seulement échantillonnée. Un contre-exemple calculé
constitue une violation ; l'absence de contre-exemple ne certifie pas l'intervalle.
`sampled_hard_conforming`, `hard_conforming`, `request_fully_covered` et
`continuous_acoustics_certified` sont distincts. Une observation optionnelle
indisponible ne retire pas la conformité d'obligations indépendantes ; elle
reste visible dans la couverture globale (`hard_conforming_partial_coverage`).

Dans la réponse de commande, `ok` signifie que l'exécution a terminé ; il ne
certifie aucun objectif physique. `sampled_hard_conforming` porte sur les
obligations vérifiées aux positions échantillonnées ; `hard_conforming` exige
aussi la portée discrète déclarée. `request_fully_covered` inclut la disponibilité
des observations et préférences demandées. `conforming` correspond au statut
global `conforming`, qui exige cette couverture complète en plus des obligations.
Ainsi une observation `played_frequency` non prise en charge peut laisser
`hard_conforming=true`, avec `conforming=false`, `request_fully_covered=false`
et `status=hard_conforming_partial_coverage`. Elle ne change ni les lignes hard
ni la sélection du candidat. Ce sens global de `conforming` n'est pas le seul
booléen de conformité hard de R41 ; cette différence de vocabulaire n'établit
pas une contamination scientifique.

## Cas fixe complet, sans coulisse

Enregistrer les deux documents suivants comme `fixed-assembly.yaml` et
`fixed-request.yaml`, puis utiliser le CONFIG de l'exemple avec les mêmes
options CLI. Ce cas vérifie une obligation géométrique sans demander de calcul
acoustique ; il utilise le même API et le même lecteur de résultat.

```yaml
# fixed-assembly.yaml
schema_version: dcalc.assembly.v1
id: fixed_cylinder
pieces:
  body:
    kind: fixed
    material_id: pvc_pressure
    segments:
      - id: bore
        kind: cylinder
        length: {value: 60, unit: cm}
        diameter_in: {value: 30, unit: mm}
        diameter_out: {value: 30, unit: mm}
    stock:
      length: {value: 60, unit: cm}
      outer_diameter: {value: 33, unit: mm}
blocks:
  - {id: body, kind: fixed, piece: body}
configurations:
  nominal: {}
```

```yaml
# fixed-request.yaml
schema_version: dcalc.assembly_request.v1
variables: []
coverage: {kind: discrete}
budgets:
  candidates: 1
  evaluations: 2
  projections: 2
  spectral_calls: 20
  segments: 1000
  frequencies: 4000
  frequency_segment_product: 1000000
  seconds: 30
  memory_mib: 650
  output_mib: 10
projections:
  - id: nominal
    configuration: nominal
    request:
      schema_version: dcalc.design_request.v1
      variables: []
      criteria:
        - id: length
          observable: geometry
          expression: {total_length: true}
          target: {value: 0.6, unit: m}
          unit: m
          tolerance: {value: 0.000001, unit: m}
          level: geometry
          scope: {}
          role: hard
      modes: []
      spectrum:
        min_hz: 10
        max_hz: 700
        step_hz: 2
        final_step_hz: 0.5
        refinement_hz: 0.00001
        h_cm: 2
      budgets:
        evaluations: 2
        iterations: 1
        variables: 1
        frequencies: 2000
        segments: 128
        history: 2
        seconds: 30
        frequency_segment_product: 1000000
```

## Budgets et API

Le budget global cumule `candidates`, `evaluations`, `projections`,
`spectral_calls`, `frequencies`, `segments`, `frequency_segment_product`, `seconds`,
`memory_mib` et `output_mib`. Tous sont explicitement positifs. Ils ne sont pas
remis à zéro pour une nouvelle note, alternative ou vérification finale.
Les bornes physiques anticipent le pire maillage avant allocation. Les budgets
natifs de chaque sous-contrat restent des plafonds locaux supplémentaires.
Le nombre de fréquences compte aussi les raffinements réellement évalués ;
le produit fréquence-segment compte le travail spectral cumulé.

Les plafonds du conteneur sont respectivement 32 candidats, 300 évaluations,
4096 projections, 10000 appels spectraux, 2000000 fréquences cumulées,
200000 segments maillés cumulés, 200000000 produits fréquence-segment, 165 s, 700 Mio et 100 Mio de sortie.
Une interruption budgétaire garde les critères disponibles, les compteurs et
le meilleur témoin déjà calculé. Elle n'invente pas la satisfaction d'une
projection non exécutée. L'exhaustion du catalogue discret est séparée de
l'exploration continue locale : ni optimum global ni impossibilité générale
ne sont annoncés.

API publique supervisée :

```python
from didgeridoo_optimizer.pipeline.assembly_path import run, load_inputs
from didgeridoo_optimizer.reporting.assembly_path import read_result, read_checkpoint

response = run(config_path, assembly_path, request_path, new_output_dir)
contract, plan = load_inputs(config_path, assembly_path, request_path)
closed = read_result(new_output_dir, contract)
witness = read_checkpoint(new_output_dir, contract)
```

`run` supervise un enfant POSIX, BLAS à un thread, mémoire virtuelle 768 Mio,
CPU 175 s et mur 180 s. La préparation, préflight et écritures candidates compris,
dispose d'une alarme POSIX de 200 s. Cette alarme est restaurée avant le lien
terminal ; borner l'appel entier, y compris cette dernière opération de système
de fichiers, exige un superviseur externe. Le client utilise le thread principal
sans alarme préexistante. Il récolte son propre enfant. Les signaux et expirations
ne sont pas des succès métier. L'API synchrone `execute(contract, plan, output)`
est destinée à un appelant qui a déjà préparé la sortie et imposé cette
supervision, ainsi que BLAS1 avant import NumPy. Elle ne produit pas à elle
seule un reçu de commande terminée.

L'API géométrique `Assembly(raw, material_db, config)` conserve une copie de
l'entrée. `generate(configuration_id)` retourne Design natif, nomenclature,
positions, spans physiques, volume du conduit et certificat nominal.
`certify_paths()` retourne les certificats affines. Les pièces physiques ne
sont jamais les tranches du maillage d'analyse.

## Sorties, provenance et relecture

Les sorties comprennent le plan, le résultat JSON strict, une synthèse française,
les CSV de pièces physiques uniques, positions et critères SI/cents,
`assembly.json`, `design_<projection>.json`, `request_<projection>.json`,
les checkpoints, le manifeste et les reçus d'exécution. Chaque Design exporté
est rechargeable par le validateur natif et porte un hash de profil généré
relié à l'assemblage et à sa configuration, distinct du hash du fichier source.

Les données de calcul et le manifeste des sources productrices sont séparés.
Le contexte inclut demande, options, pièces, positions, CONFIG, DB et versions.
Le lecteur vérifie le manifeste producteur sans exiger que son processus ait
préalablement importé les mêmes modules. Une CLI copiée ne peut revendiquer le
SHA du dépôt canonique. Les modifications de source ou de contexte rendent la
relecture/reprise incompatible.

Les écritures sont atomiques et sans écrasement. `manifest.json` seul ne vaut
pas reçu complet. Les nouveaux reçus `execution.json` portent
`completion_protocol: dcalc.assembly_completion.v1`. `execution.closed.json`
est une clôture candidate ; `.pending-execution-completion.json` contient les
hashes du reçu et de cette clôture. Les écritures, synchronisations, fermetures,
récolte de l'enfant et restaurations des handlers/alarme se terminent avant
la publication terminale. Celle-ci est un unique lien atomique sans écrasement
vers `execution.completed.json`, suivi du retour sans autre opération de clôture.
Le lecteur exige ce marqueur cohérent, un enfant récolté et un code zéro pour
annoncer le succès. `execution.cancelled.json` conserve sa priorité.

Une erreur ou interruption avant ce commit reste non réussie, même si la
notification d'annulation échoue elle aussi : l'absence d'autorité terminale
suffit. Une interruption injectée après l'ancien helper de clôture se situe
désormais avant le commit. À l'inverse, un événement réellement postérieur au
lien terminal ne rend pas rétroactivement l'exécution non effectuée ; le lecteur
peut alors confirmer le commit même si l'appelant observe cet événement.
Il n'y a aucune garantie universelle contre une panne matérielle ou une coupure
après commit : l'entrée finale du répertoire n'est pas resynchronisée après le lien.

Compatibilité explicite : les reçus historiques sans `completion_protocol`
restent interprétés selon l'ancienne clôture, avec
`completion_assurance=legacy_closure`. Ils ne sont ni déclarés faux à cause du
nouveau marqueur absent, ni réputés bénéficier rétroactivement de sa garantie.
Les nouveaux reçus valides portent `completion_assurance=terminal_commit` à la
lecture ; un protocole inconnu ou un marqueur manquant/incohérent reste non
confirmé. La vérification des sources productrices de `read_result` reste
indépendante : relire des résultats historiques complets exige toujours les
sources et versions compatibles, sans réécrire leurs preuves.

Un checkpoint est un témoin vérifiable accompagné de compteurs cumulés.
`solver_state_complete` est faux : la lecture ne reprend pas automatiquement
l'algorithme. La relance est une nouvelle exécution dans une nouvelle destination.
Aucune promotion de matériau ni validation physique A–E n'est effectuée ici.
