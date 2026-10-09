# TOLERANCE-AUDIT-01 — audit dimensionnel fini

L’audit évalue un instrument fourni et fixé sous une liste explicite d’écarts.
Il ne recherche aucune géométrie, ne réaccorde pas les commandes, ne répare pas
une contradiction et ne lance ni fit ni simulation jouée. Il est indépendant
des opérations JOINT-DIRECTIONS et FIT-ADAPT.

## Utilisation

```bash
python -m tools.tolerance_audit --job project_specs/examples/tolerance_audit/fixed_job.json --output-dir /tmp/tolerance-fixed
python -m tools.tolerance_audit --job project_specs/examples/tolerance_audit/assembly_job.json --output-dir /tmp/tolerance-assembly
python -m tools.tolerance_audit --job project_specs/examples/tolerance_audit/assembly_job.json --output-dir /tmp/tolerance-unused --dry-run
```

La destination doit être nouvelle. Le dry-run réussit silencieusement : lectures,
validation des contrats et géométries, estimation des budgets ; aucune écriture,
aucun TMM, maillage acoustique, fit ou simulation. L’API retourne le plan :

Pour inclure les caches propres à l’interpréteur dans la garantie d’absence
d’écriture, utiliser `python -B -m ...` ou définir `PYTHONDONTWRITEBYTECODE=1`
avant le lancement. Python peut mettre le module CLI en cache avant même son
exécution ; le CLI désactive ensuite les écritures de cache de ses imports.

```python
from didgeridoo_optimizer.pipeline.tolerance_audit import run, load_inputs
response = run(job, output_dir, dry_run=True)
```

L’API publique `run` supervise exactement un enfant de calcul, avec BLAS1,
768 Mio d’espace d’adressage, 175 s CPU et 180 s mur. Le plan limite le calcul
à 165 s au maximum. Le parent dispose de 200 s, avec marge de clôture. La
supervision exige POSIX, le thread principal et l’absence d’alarme existante.
L’enfant refuse une invocation directe sans ces limites. Le corps synchrone
`execute` est réservé aux hôtes imposant déjà cette même supervision ; ce n’est
pas une API de reprise. Les imports natifs peuvent charger les modules du
runner historique pour ses lecteurs CONFIG/DB ; aucune phase d’optimisation
n’est appelée.

## JOB v1 strict

`schema_version` vaut `dcalc.tolerance_audit.v1`. Tous les champs sont obligatoires :

- `input` : `kind: fixed`, `config`, `design`, `request` ; ou `kind: assembly`,
  `config`, `assembly`, `assembly_request`. Les références sont relatives au JOB.
- `uncertainties` : objets `id`, `fields`, `delta: {value, unit}`, `origin`.
  L’amplitude est positive, l’unité explicite est m/cm/mm et l’origine est un
  texte. Une origine synthétique ne devient pas une tolérance industrielle.
- `scenarios` : objets `id`, `coefficients`. Chaque incertitude a exactement un
  coefficient explicite dans [-1,1]. Un seul vecteur nul est obligatoire.
  Identifiants, champs et vecteurs répétés sont refusés.
- `coverage: {kind: finite|continuous}` : explicite. Le second conserve une
  demande non couverte ; des coins favorables ne certifient pas l’intérieur.
- `budgets` : tous les compteurs du tableau suivant, entiers positifs.

| Budget | Plafond |
| --- | ---: |
| uncertainties | 8 |
| scenarios | 65 |
| fields | 64 |
| scenario_fields | 4160 |
| projections | 4096 |
| spectral_calls | 10000 |
| frequencies | 2000000 |
| frequency_segment_product | 200000000 |
| segments | 200000 |
| seconds | 165 |
| memory_mib | 700 |
| output_mib | 100 |

Les produits scénarios × champs/projections et une estimation conservatrice de
mémoire sont vérifiés avant les copies natives. Les coûts cumulés des trois
grilles de base par projection sont vérifiés avant calcul. Ce minimum n’est pas
une promesse de coût total : les raffinements dépendent des pics rencontrés.
Le `Budget` natif compte les segments cumulés, appels spectraux, fréquences et
produits fréquence-segment avant leurs allocations/TMM. Un budget épuisé laisse
un résultat incomplet et les observations déjà écrites. Le budget output est
cumulé, avec réserve proportionnelle au nombre maximal d’artefacts de clôture.

Les lecteurs `read_request`, `strict_input`, `quantity` et CONFIG/DB natifs sont
réutilisés. Pas de syntaxe YAML concurrente : doublons de clés, cycles,
valeurs non finies, booléens numériques et champs inconnus sont refusés.

## Géométrie et liaisons

Pour chaque champ exposé :

`dimension_effective = dimension_nominale + coefficient × delta`

Le même écart est appliqué à tous les champs d’un groupe. Un champ appartient à
une seule incertitude. Pour un cylindre, exposer ensemble ses deux diamètres
permet de conserver sa loi cylindrique. Le fixe utilise `field_info/set_field`
sur les longueurs et diamètres effectifs natifs ; les paramètres sans dimension
et `throat_diameter_cm`, non représenté acoustiquement par cette chaîne, sont
refusés dans cette v1.

L’assemblage utilise les identités stables `pieces.<id>...`, y compris les
identités des segments de pièces fixes. Les indices de tranches acoustiques,
les commandes `q`, orientation, joint, `min_overlap`, matières et lois de profil
ne sont jamais des incertitudes. Une seule géométrie physique perturbée sert à
toutes les configurations du scénario. Les épaisseurs ou diamètres extérieurs
renseignés participent aux contrôles mécaniques natifs, même s’ils ne sont pas
exposés. Les champs `stock` ne sont pas exposables par le setter natif v1 ; leurs
contraintes restent actives.

Les variables du REQUEST restent la description du problème de conception
antérieur. Le nominal fourni est fixé ; l’audit ne change aucune variable et
n’impose pas les anciennes bornes de recherche aux écarts de fabrication. Le
contrat nominal reste validé par les API natives. Les liaisons d’égalité doivent
partager une même incertitude ; les relations dérivées doivent rester vraies
dans chaque scénario prescrit. Une incompatibilité est refusée avant acoustique,
sans supprimer ni recalculer les relations pour la faire disparaître. Les
limitations du validateur natif restent visibles sous forme de refus explicite.

Le masque propre à l’audit contrôle tous les champs non exposés. Les positions
de segments et `metadata.total_length_cm` du Design sont recalculés seulement
par `DesignBuilder` natif. Les profils d’assemblage et leurs métadonnées de
pièces, spans et positions proviennent de `Assembly.generate`. Ces métadonnées
dérivées sont distinguées des annotations libres, qui restent identiques.
Le nominal d’entrée n’est jamais réécrit.

Une contradiction géométrique doit être établie par un diagnostic natif
spécifique : positivité dimensionnelle, contraintes de géométrie, diamètre
intérieur/extérieur, marges mécaniques exactes ou stock insuffisant. Une autre
exception reste `unresolved`. Les observations des autres configurations et
scénarios continuent d’être conservées.

Un écart rationnel exact d’assemblage non représentable en quantité numérique
m/cm/mm reste non résolu, avec descripteur `unavailable_<scenario>.json` ; aucun
arrondi correctif ni faux assemblage nominal n’est exporté à sa place. Les
autres scénarios continuent. Cette limite du setter natif est explicite en v1.

## Calcul et interprétation

Chaque projection conserve ses critères et son REQUEST natif. L’évaluation
utilise `ProjectionEvaluator.evaluate_design(..., verify=True)`, les maxima
locaux de |Zin|, modèles effectifs, rayon terminal physique et raffinements
natifs. Aucun remplacement par les zéros de Im(Z), aucune égalité artificielle
au centre d’une tolérance, aucune nouvelle règle de convergence.

- `execution_complete` : toutes les tentatives prévues ont été exécutées sans
  interruption/budget épuisé ; ce n’est pas la résolution de tous les critères.
- `sampled_hard_conforming` : toutes les obligations hard de toutes les
  projections/scénarios ont une observation calculée et satisfaite, avec unité,
  et aucune géométrie violée. Une observation facultative indisponible n’altère
  pas ce booléen si tous les hard ont été calculés.
- `request_fully_covered` : l’exécution et tous les critères demandés sont
  couverts ; scopes robustes/joués/continus conservés empêchent cette affirmation.
- `continuous_robustness_certified` : toujours `false`.
- `counterexample_found` : violation hard ou géométrique démontrée, conservée
  même si une autre observation manque ou reste non résolue.

Les minima de marge sont calculés critère par critère parmi les observations
ayant valeur et marge disponibles, avec scénario et unité. Les estimations
proches de la frontière restent étiquetées non résolues selon la politique
native. Aucun minimum échantillonné n’est une borne sur un domaine continu ;
aucune moyenne ne compense une obligation violée.

## Exports, interruption et relecture

Le dossier contient le plan, les géométries fixes/assemblages par scénario,
les profils par projection, les observations immuables, les traces des niveaux
spectraux terminés, `deltas.csv`, `criteria.csv`, `scenarios.csv`, `margins.csv`,
la synthèse française, le résultat JSON strict, le manifeste et les reçus.
Les traces partielles ne constituent pas des critères acoustiques vérifiés.
Les fichiers d’une destination existante ne sont jamais écrasés.

Les helpers publics natifs de chemins sûrs, écritures sans écrasement et clôture
sont réutilisés. La publication du reçu terminal intervient après récolte du
seul enfant possédé et restauration des signaux/alarmes. Une interruption ou
un code enfant non nul ne produit pas de reçu `ok=true`. Les observation(s)
commises restent lisibles même sans résultat final ; l’absence de reçu terminal
empêche d’affirmer un succès. Les fichiers `.pending-*` sont des inodes de
publication conservés par le helper natif, pas de nouvelles observations.

```python
from didgeridoo_optimizer.reporting.tolerance_audit import read_result
result = read_result(output_dir)
```

Cette relecture peut être exécutée dans un processus neuf. Elle vérifie les
octets, le manifeste, les sources productrices par lecture directe, les versions,
les relations entre plan/résultat/observations et le reçu terminal. Elle ne
recalcule ni acoustique ni fit ni simulation. `ok` désigne la clôture de commande,
pas la conformité physique. Les empreintes assurent l’intégrité relative du
bundle ; elles ne sont pas une signature externe contre sa réécriture intégrale.

La provenance distingue les modules chargés des fichiers de modules réellement
exécutés, avec SHA256, et le SHA Git lorsque les sources chargées sont suivies.
Les sources sont vérifiées avant et après calcul ; une CLI copiée hors du dépôt
est refusée. Les entrées JOB/CONFIG/DB/REQUEST/géométrie sont tracées par leurs
empreintes et les demandes/nominal/configuration sont conservés dans le plan.
Une archive ancienne n’est ni réétiquetée ni assimilée à un nouveau calcul.

## Exemples et tests

Les deux JOB publics sont de petits exemples synthétiques documentés par leurs
`origin`. Le fixe fournit un cylindre de 120 cm, diamètre 3 cm, et une demande
passive native de 70 Hz ; sa géométrie n'a pas été recherchée ni accordée.
L'assemblage référence les entrées publiques `assembly_path`, sans adaptation
acoustique. Chacun comprend le nominal et deux combinaisons explicites.
L’assemblage comporte `short` et `long` avec
commandes inchangées. Une réduction de 25 mm du tube externe laisse 75 mm de
recouvrement en configuration longue pour 80 mm requis : contre-exemple
mécanique indépendant du TMM. Une nominale acoustiquement non conforme est un
résultat admissible. Ces exemples ne sont pas des instruments validés A–E.
L’archive privée R43 n’est pas utilisée par ces JOB.

Les trois nouveaux modules de tests couvrent les unités et identités, les écarts
± et groupes, l’absence de modification cachée, les commandes constantes, les
contradictions mécaniques, les liens natifs, les capacités absentes, les budgets,
les résultats partiels, la vraie CLI, les sources copiées, le dry-run et la
relecture fraîche avec détection d’altération. Les tests historiques ciblés
restent inchangés : design_contract, constrained_design, assemblies,
assembly_contract et assembly_path.

### Cohérence de relecture (correction C1)

Le lecteur reconstruit la grille depuis les scénarios du JOB original et les
projections du REQUEST conservé. Il refuse les identités dupliquées/inconnues,
les critères manquants, les rôles modifiés, les configurations, coefficients,
demandes effectives ou unités contradictoires. Les conversions, marges et statuts
sont confrontés aux cibles/tolérances originales et aux valeurs conservées, sans
élargir de tolérance. Les minima et les booléens de résultat sont recalculés à
partir des lignes présentes ; une déclaration complète exige toute la grille.
Une sortie partielle conserve ses contre-exemples et ne couvre pas la demande.
Les hard tous acquis restent conformes si seul un budget facultatif manque.

Le préflight conserve des empreintes de la géométrie, du profil et des métadonnées
attendus par projection. Le lecteur compare aussi les écarts physiques au nominal
et aux coefficients prescrits ; les profils sont associés au scénario et à la
configuration prévus. L’ordre natif des pièces et des `component_ids` est conservé.
`semantic_verified` distingue ce contrôle d’une simple lecture de pièces partielles
sans résultat. Aucun import préalable du producteur n’est requis. Les fréquences
et estimations acoustiques conservées ne sont **pas** revérifiées acoustiquement ;
aucun TMM, fit ou simulation n’est relancé. Ces contrôles empêchent un verdict
contraire aux propres pièces du bundle, sans authentifier un bundle entièrement
réécrit, plan compris.

### Collecte bornée (correction C2)

Le plafond de calcul reste de 180 s et le plafond public de 200 s inclut le
préflight et la clôture. La collecte et la fermeture partagent un délai cumulé
de 10 s, limité aussi par l’échéance publique avec une réserve de clôture de 2 s.
Chaque `communicate` et `wait` reçoit le temps restant ; la communication de
collecte réserve la moitié de ce temps pour récolter l’enfant si ses canaux ne
se ferment pas. Seul l’objet enfant créé par cet appel peut être arrêté.

La récolte du processus, la collecte des canaux et la fermeture des trois
descripteurs sont distinguées. Un second timeout, une fermeture défaillante ou
un retour tardif interdit une clôture réussie ; les observations déjà commises
restent lisibles. Les signaux INT/TERM pendant cette collecte sont enregistrés,
rendent la commande interrompue et laissent terminer la tentative bornée de
récolte. Les gestionnaires et l’alarme sont restaurés avant le reçu terminal.
Les tests de ces défauts utilisent des doubles contrôlés, sans processus
réellement suspendu ni augmentation des quotas de calcul.
