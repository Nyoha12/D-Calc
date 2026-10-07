# PLAYED-SEARCH-01 — recherche géométrique sous scénarios prescrits

Ce produit cherche un **témoin de faisabilité** pour un instrument fixe ou les
configurations statiques d'un assemblage natif. Les dimensions et pièces sont
communes à tous les scénarios. Les contrôles de jeu sont prescrits avant la
recherche. Une bonne résonance passive ne remplace jamais une fréquence observée.
Les paramètres labiaux restent à calibrer ; aucune validation physiologique,
matérielle A–E, de mouvement de coulisse ou de continuum n'est revendiquée.

```sh
python -m tools.played_search \
  --job project_specs/examples/played_search/request.yaml \
  --output-dir /tmp/played-plan --dry-run

python -m tools.played_search \
  --job project_specs/examples/played_search/request.yaml \
  --output-dir /tmp/played-result
```

Les destinations doivent être nouvelles. Le dry-run refuse aussi une destination
existante et n'en crée aucune. Aucun TMM, fit, eigensolve ou pas temporel n'est
exécuté par `preflight`. Le lecteur final effectue des vérifications natives qui
peuvent allouer des séries et reconstruire un modèle ; il n'est pas un dry-run.

```python
from didgeridoo_optimizer.pipeline.played_search import preflight, run
from didgeridoo_optimizer.reporting.played_search import read_result, read_checkpoint

plan = preflight(job_path, new_output_path)
response = run(job_path, new_output_path)
closed = read_result(new_output_path)  # appel synchrone à superviser
witness = read_checkpoint(new_output_path)
```

Code CLI 0 : exploration et publication terminées, pas nécessairement conformité.
Code 1 : calcul partiel/budget interrompu. Code 2 : refus ou erreur avant livraison
confirmée. Un témoin complet déjà acquis peut subsister dans une exploration
partielle. Lire `conforming`, `search.witness`, les erreurs et les clôtures ; ne
pas promouvoir le dernier candidat simplement parce qu'il existe.

## JOB strict versionné

`schema: dcalc.played_search.job.v1`. Les lecteurs YAML/JSON natifs contrôlent les
mêmes octets, doublons, cycles/alias dangereux, types, nombres finis et tailles.
Les booléens ne sont pas des nombres. Les clés inconnues sont refusées. Tous les
chemins du JOB sont **relatifs au répertoire du JOB** ; les chemins DB restent
relatifs au CONFIG selon sa convention native. JSON de sortie interdit NaN/Inf.

| Champ | Contrat |
| --- | --- |
| `config` | Chemin vers le véritable CONFIG. |
| `input` | `{kind: fixed, design: DESIGN, request: REQUEST}` R41, ou `{kind: assembly, assembly: ASSEMBLY, request: REQUEST}` R43. |
| `fit` | Recette et gates explicites ci-dessous, constantes pendant l'exécution. |
| `scenarios` | 1–8 objets `{id, configuration, template}`. Chaque configuration générée doit avoir au moins un scénario. |
| `search` | `normalized_steps`, `directions`, `stop_on_witness`, tous explicites. |
| `budgets` | Tous les plafonds du tableau de ressources, obligatoires. |
| `scope` | `coverage`, `player_controls`, `objective`, `physiological_guarantee`. |
| `metadata` | Annotations JSON finies facultatives, conservées. |

La portée v1 calculée est `{coverage: finite, player_controls: prescribed,
objective: feasibility, physiological_guarantee: false}`. `continuous`,
`optimized`, `preference` ou une garantie physiologique demandée sont conservés
comme **unsupported** et empêchent une annonce de conformité globale. Les
préférences R41 et la couverture continue R43 restent également unsupported.
Les obligations jouées R41 non calculables restent présentes ; il faut exprimer
les observations jouées dans leurs PLANs REGISTER-TARGET, sans réinterpréter
silencieusement une ancienne demande.

Les rôles `observe` n'éliminent pas un candidat. Une observation passive
indisponible ou défavorable n'empêche pas un hard joué indépendant. Aucun seuil
local n'est utilisé comme filtre. Les contraintes géométriques, mécaniques et
statiques sont évaluées par Contract/AssemblyContract et leurs évaluateurs
natifs avec raffinement final. Une violation statique hard prouvée peut éviter
les fits et trajectoires coûteux ; ces unités restent alors `not_evaluated`.

### Templates et déclarations communes

Chaque `template` est un PLAN REGISTER-TARGET **complet**, avec `case.kind: saved`
et trois emplacements explicites : `config: '@CONFIG'`, `design: '@DESIGN'`,
`model_in: '@MODEL'`. Ce ne sont pas des fichiers modèle factices. La validation
initiale est qualifiée « structure/protocole natif et recette ; association
CONFIG/DESIGN/modèle générés à acquérir ». Aucune fidélité de fit n'est revendiquée
à ce stade. La validation native du contexte réel est répétée après le nouveau fit.

Seuls ces trois emplacements sont instanciés. Identifiants, lèvres, rho, plateaux,
pressions, fréquences labiales, durées, budgets, fenêtres, bande/protocole
d'observation, cibles et critères du template sont conservés. Les déclarations
complètes de chaque scénario figurent dans le plan résolu, avec leur empreinte.
Plusieurs scénarios peuvent partager le même template ; des templates distincts
conservent leurs différences prescrites. Aucun nouvel utilisateur, nouveau
coefficient ou réglage joueur n'est généré pour une position.

Les notes avec octave et référence, cents, rapports quelconques, plusieurs
plateaux/fenêtres, et PHASE explicitement déclaré restent natifs. PHASE dépendant
du nombre d'états est revérifié après le fit : une incompatibilité est un échec
partiel, jamais une suppression de la demande. Les observations sont calculées
sans connaître les cibles. La cible passive « 70 Hz » et la cible jouée « 70 Hz »
sont deux critères différents ; aucune cible passive n'est ajoutée implicitement.

### Fit et identité

`fit` contient exactement les champs de la recette native REGISTER-TARGET :
`sample_rate_hz`, `h_cm`, `loss_model`, `radiation_model`, `air_reference`, `R0`,
`dc_origin`, `basis_completion`, `fit_min_hz`, `fit_max_hz`, `fit_points`,
`guard_max_hz`, `guard_points`, `audit_points`, ainsi que :

- `gates: {complex_nrmse, relative_max, phase_rms_deg}` ;
- `mesh_gate`, `seconds`, `fit_seconds`, `max_passes` ;
- `signal_samples`, `flow_peak_m3_s` pour les contrôles passifs natifs.

Les domaines et limites natives s'appliquent. Le sous-ensemble recette de chaque
PLAN doit être identique au JOB avant calcul. Aucun `model_in` externe n'est
accepté pour préparer le fit. Le moteur `time_domain_reference` fait le TMM,
fit passif, audit réservé, raffinement de maille, sauvegarde et relecture.
Les gates ne sont jamais ajustées après lecture d'un résultat.

Dans une exécution, le cache exige mêmes octets DESIGN, CONFIG/DB, recette
complète et sources productrices. Il évite uniquement un fit identique déjà
acquis. Les identités documentaires d'assemblages/configurations peuvent rendre
différents deux profils acoustiquement égaux : pas de réétiquette de modèle.
Les modèles rejetés restent rejetés et leurs fichiers/codes sont préservés.
Le contexte de chaque expérience est validé par l'adaptateur natif
`paired_onset._case_context` ; son lecteur contrôle le modèle sauvegardé et son
certificat historique. Le reçu courant du fit contrôle en plus les gates
courantes et l'intégralité de la recette (dont les grilles guard/audit).

## Recherche et verdicts

L'ordre est celui du catalogue natif, puis des variables déclarées, des pas
normalisés décroissants, puis des directions explicitement `[-1, 1]` ou `[1, -1]`.
Chaque déplacement vaut `direction × pas × (borne haute − borne basse)` en SI.
Un poll utilise une origine fixe ; son meilleur candidat devient le centre du
poll suivant. Les candidats identiques sont dédupliqués. Hors bornes : refus,
pas de clamp. Aucune graine aléatoire n'est utilisée.

L'exemple public commence à **120,1604159825398 cm**, diamètre 30 mm PVC, longueur
seule entre 110 et 130 cm. Le premier déplacement de 0,125 propose
**117,6604159825398 cm** par le seul domaine géométrique. Sa cible jouée est
70 Hz ±5 cents à 3500 Pa, sur [2,2,5] et [2,5,3] s, avec activité ≥100 Pa,
pression ≤4000 Pa et durée acquise ≥0,49 s par fenêtre. Cet exemple est numérique,
pas une calibration ou une recommandation physiologique. Aucune longueur
solution n'est injectée au solveur.

La conformité exige toutes les unités acquises et vérifiées, tous les modèles
acceptés et tous les hard satisfaits. Un seul scénario violé suffit à refuser
le candidat. Aucun score moyen n'est calculé. Une fréquence unresolved, une
unité absente ou une erreur conserve son absence de valeur. Sans variable,
l'évaluation multi-scénarios reste valide. Sans hard, seuls les points initiaux
du catalogue sont décrits : aucun optimum implicite n'est recherché.

Pour des observations complètes, le classement minimise la **violation maximale
normalisée** des hard. Pour une cible, le dénominateur est sa tolérance (cents
ou SI). Pour une borne jouée, c'est la valeur absolue de la borne, ou une unité SI
pour une borne nulle. Les limites statiques suivent la tolérance R41 native.
Une tolérance jouée nulle reste une condition exacte sans pénalité lissée inventée.
Les violations disponibles d'un candidat incomplet donnent seulement une borne
inférieure diagnostique ; leur comparaison exige le même inventaire d'obligations
acquises. Les inconnues ne deviennent pas des zéros. Un témoin complet prime
toujours ce classement. Les choix, doublons et refus de bornes sont exportés.

Le résultat est un témoin trouvé, une violation prouvée pour un essai, un résultat
unresolved/unsupported, ou une absence de témoin dans le budget. Ce n'est ni un
optimum global ni une preuve générale d'impossibilité.

### Assemblage

Les variables, liens `derived`, alternatives du catalogue, verrous, pièces,
profils, stocks, chevauchements et configurations sont ceux du contrat R43.
Chaque candidat est généré et contrôlé une fois comme assemblage partagé, puis
projeté dans toutes ses configurations. La nomenclature n'est pas le maillage
acoustique. Un assemblage fixe à une configuration vide, sans `q`, est valide.

Pour essayer le cas fixe assemblé fourni, copier le JOB dans une nouvelle entrée
et remplacer son `input` par :

```yaml
input:
  kind: assembly
  assembly: assembly.yaml
  request: assembly_request.yaml
```

Les autres chemins restent relatifs à ce nouveau JOB. Plusieurs configurations
exigent leurs projections R43 et leurs scénarios explicitement déclarés ; les
mêmes pièces restent communes. Les certificats géométriques de chemins natifs
n'établissent aucune couverture acoustique continue.

## Supervision, sorties et reprise

| Plafond JOB v1 | Maximum |
| --- | ---: |
| `candidates` | 16 |
| `scenarios` | 8 |
| `fits` | 32 |
| `trajectories` | 64 |
| `steps` | 2 000 000 |
| `seconds` total orchestration | 3600 |
| `child_seconds` | 180 |
| `memory_mib` enfant | 768 |
| `output_mib` nouveaux artefacts | 800 |

Un seul enfant scientifique est actif, BLAS1, limites natives CPU/mémoire et
fichier. Chaque lancement réserve toute sa durée et sa capacité de stockage
possible, ses fits/trajectoires et ses pas. Pas de remise à zéro entre candidats.
Les pas facturés sont le quota entier des trajectoires lancées, y compris essais
échoués et morceaux non engagés ; les pas effectivement acquis sont séparés.
Un crash ne permet pas de connaître exactement les tentatives non checkpointées.
Cette facturation conservative interdit de les dépenser une seconde fois.
Les compteurs `reserved` distinguent réservations et processus effectivement lancés.
Une réserve de 15 s précède la finalisation, également soumise au délai total.
Les lecteurs scientifiques sont eux aussi des enfants bornés ; ils ne refont
aucun fit et ne prolongent aucune trajectoire.

Les limites natives locales peuvent être plus restrictives (par exemple
REGISTER-TARGET ≤72 000 pas/6 s). La v1 refuse plus de 512 lignes scalaires par
candidat et vérifie le pire maillage de fit avant allocation scientifique.
Les réservations de stockage incluent 64 Mio par fit, le quota natif complet par
trajectoire et une réserve d'export. Un budget trop petit arrête avant l'enfant.
Un signal récolte uniquement l'enfant créé par ce workflow. Les fichiers natifs
partiels et codes ne sont ni nettoyés ni réécrits pour leur donner l'air complets.

Sorties principales : `plan.json`, `result.json`, `candidates.csv`,
`variables.csv`, `criteria.csv`, `summary.txt`, checkpoints et clôture. Chaque
répertoire candidat conserve designs physiques, assemblage/nomenclature le cas
échéant, statique raffinée, fit/recette et expériences natives liées. Les CSV
contiennent les résidus signés SI, cents, marges et unités par configuration,
scénario et fenêtre ; les valeurs absentes restent des cellules vides.

Les lecteurs natifs REGISTER-TARGET font autorité pour leurs bundles, y compris
leur clôture et chaîne. TD-PASS ne fournit pas de lecteur de bundle terminal :
ses artefacts et son reçu enfant sont contrôlés par l'orchestrateur, avec
association native du vrai modèle. Les sources historiques du fit et celles du
calcul actuel sont distinctes. Une CLI copiée ou une source non suivie par le
dépôt ne peut revendiquer sa provenance. Les entrées/sources sont contrôlées avant
et après les unités. Les checksums établissent l'intégrité, pas une preuve d'auteur.

Le manifeste seul ne clôt pas la commande. Le marqueur `execution.completed.json`
est un lien publié après les écritures, fermetures, récoltes d'enfants et
restaurations de signaux. Toute erreur avant ce marqueur laisse la publication
non confirmée ; aucune garantie de transaction disque universelle n'est promise.
Aucun overwrite, clean ou changement d'une publication existante n'est permis.

**Checkpoint = témoin. Reprise globale unsupported.** Le checkpoint conserve
unités terminées, candidat en cours, erreurs, localisateurs et compteurs cumulés.
Il ne contient pas un état complet du solveur. Relire ses données ne reprend pas
le poll. Une nouvelle recherche est une nouvelle exécution dans une destination
neuve. Les checkpoints R46 demeurent utilisables avec leur protocole natif,
indépendamment de cette restriction d'orchestration.
