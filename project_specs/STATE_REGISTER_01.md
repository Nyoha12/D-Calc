# STATE-REGISTER-01

REGISTER-TARGET accepte un modèle `PassiveState` déjà réalisé pour une configuration
sauvegardée fixe. Il ne simule pas un joueur, ne refait aucun fit et ne raccorde pas
un modèle figé à `played_search` : chaque géométrie proposée par ce moteur exige son
propre fit. Les chemins historiques `saved` modal et `synthetic` sont conservés.

## Plan et API

Le plan conserve le schéma `dcalc.register_target.plan.v1`, les commandes, fenêtres,
observations et critères natifs. Le nouveau cas est :

```json
{"kind":"saved_state","id":"instrument","config":"config.json",
 "design":"design.json","realization":"realization-B",
 "evidence":"evidence.json",
 "recipe":{"sample_rate_hz":12000,"h_cm":0.5,"loss_model":"zk",
           "radiation_model":"legacy","air_reference":"ck_dry20"}}
```

Les chemins sont résolus relativement au fichier du plan. `realization` désigne
le bundle complet R61, pas une simple matrice ou un modèle réétiqueté. Le domaine
`continuous` ou `discrete_prewarped`, la fréquence native et les unités restent
ceux de la source scalaire et du modèle. L'état complet est
`[déplacement_m, vitesse_m_s, x_resonateur...]`, de dimension `2+n` et d'unités
`m`, `m/s`, puis `m^3/s`. PHASE, lorsqu'il est demandé, exige toutes ces unités et
échelles. L'identité native du couplage et celle du contexte restent distinctes.

```python
from didgeridoo_optimizer.pipeline.register_target import preflight, run
p = preflight("plan.json", "nouvelle-sortie")  # lecture seulement
receipt = run("plan.json", "nouvelle-sortie", steps_this_run=128)
# Reprise avec le même plan et une NOUVELLE destination :
receipt = run("plan.json", "reprise", resume="nouvelle-sortie/checkpoint-000001.json")
```

L'API mémoire `nonlinear.register_target.Experiment` accepte aussi un vrai
`PassiveState`, avec source explicitement synthétique ou contexte vérifié par
l'adaptateur. Le constructeur vérifie réellement les preuves pour `saved_state` ; un SHA
`verified_state` inventé ne suffit pas. Il refuse un état plat sous `saved` modal.
Elle utilise exclusivement `SimultaneousCoupling.step`, sans
attributs modaux fictifs. Un appel direct à l'API mémoire ne constitue pas une
preuve de fidélité instrumentale. `run` est le point d'entrée supervisé recommandé ;
`calculate` et les lecteurs scientifiques exigent un appelant borné.

## Preuve acoustique finie

`reporting.state_register_context` expose `inspect_evidence(path)` et
`validate_evidence(manifest, model, files)`. Le manifeste JSON strict de schéma
`dcalc.state_register.evidence.v1` contient exactement :

- `inputs` : SHA256 des quatre fichiers réellement lus, `config`, `design`,
  `materials`, `variant_rules` ; les quatre fichiers sont requis.
- `recipe`, `effective`, `physical_radius_m` : recette, air effectif et air CONFIG,
  pertes, radiation et rayon physique de sortie. `h_cm` et `sample_rate_hz` sont
  explicites dans la recette.
- `source_sha256`, `model_sha256`, `domain_hz`, `coefficient_domain` : source
  scalaire originale (octets), paramètres réalisés (digest JSON canonique),
  domaine d'apprentissage et convention fréquentielle.
- `acquisition` : `nature` (`simulation_tmm`, `measurement` ou `synthetic`),
  `provenance`, date ISO `date`, révision/données `sha` (40 ou 64 hexadécimaux).
  Cette origine est déclarée, pas authentifiée par son auto-empreinte. Le schéma
  scalaire R59 connu impose `simulation_tmm` et refuse un réétiquetage en mesure.
- `gates` : valeurs natives immuables `complex_nrmse=.005`, `relative_max=.08`,
  `phase_rms_deg=.3`.
- `training` : `file`, `sha256`, `points` ; `audit` : `file`, `sha256`.
- `bands` : liste de `{id, role, domain_hz, points}`, avec rôle `audit` ou
  `extrapolation`. Les bandes audit partitionnent le domaine déclaré sans trou
  ni recouvrement intérieur ; leurs frontières ne sont pas les extrema des
  échantillons irréguliers.

Le schéma fixe les fréquences en Hz et les cibles d'impédance en Pa.s/m^3.
L'archive d'apprentissage contient `frequency_hz`, `real`, `imag` en float64.
L'archive audit contient, pour chaque bande, `<id>_f` float64 et `<id>_target`
complex128. Aucun objet NumPy ni pickle. Noms de fichiers simples relatifs au
manifeste ; fichiers <=4 Mio, apprentissage <=8192 points, <=16 bandes de <=4096
points et <=16384 points au total. Inventaire ZIP, en-têtes NPY <=256 octets,
formes, dtypes, ordre et tailles décompressées sont contrôlés avant `np.load`.

Le précontrôle vérifie les formes JSON/listes, unités, chemins, empreintes,
recettes, supports annoncés, taille et structure des preuves. Il ne construit
pas de résonateur, ne charge pas les tableaux NPZ, ne fait pas d'eigensolve,
conversion, fit, TMM, vérification scientifique de certificat ou pas temporel.
Les objets de conditions physiques natifs servent seulement à comparer la
recette aux entrées ; aucune impédance n'y est calculée.

Dans le worker, avant le premier pas, le lecteur R61 valide réellement le
certificat acquis, la représentation, le conditionnement et la clôture. Le
`PassiveState` exporté est chargé, sans reconversion. Les fréquences et cibles
sauvegardées sont relues, la disjonction apprentissage/audit et entre bandes est
recalculée, puis les métriques natives sont recalculées sur chaque support.
La réponse auditée est toujours la réponse **discrète réellement exécutée** à
la fréquence native, même pour des coefficients de provenance `continuous`.
`coefficient_domain` conserve cette provenance ; le reçu de fidélité porte
`audited_response_domain="discrete"`. Des cibles continues ne sont admises que
si la réponse discrète réelle passe les gates, sans réétiquetage des coefficients.
Toute bande audit et l'apprentissage doivent passer les trois gates natives.
Les métriques extrapolées sont enregistrées même si elles échouent ; elles ne
prolongent jamais le domaine admis. La bande d'observation doit appartenir à la
couverture déclarée. Cela ne borne pas tout le spectre d'un régime non linéaire.
Fidélité finie, passivité scalaire/structurelle, conditionnement numérique et
validité instrumentale restent des conclusions différentes.

## Reprise et relecture

Les checkpoints lient plan, sources du logiciel, quatre entrées, bundle et
preuves. Les nouveaux pas restent soumis aux quotas cumulatifs du plan ;
`new_steps_this_run` et, pour `saved_state`, `new_duration_s` concernent la seule
invocation ; `accepted_steps` est cumulatif, `historical_source_steps` reste zéro.
La reprise identique refuse une modification de ces entrées avant de prendre
un nouveau pas. Les répertoires de sortie sont toujours neufs.

```bash
python -m tools.register_target --plan plan.json --output-dir preflight --dry-run
python -m tools.register_target --plan plan.json --output-dir run --steps-this-run 128
python -m tools.register_target --plan plan.json --output-dir resume --resume run/checkpoint-000001.json
python -m tools.register_target --read-result resume
```

Le CLI de relecture impose 175 s CPU/mur, 768 Mio et 8 Mio par fichier dans
un processus neuf qu'il possède. Il établit BLAS1 avant tout import de NumPy.
Un appel importé de `tools.register_target.main --read-result` est refusé sans
modifier limites, handlers ou timers ; un timer ou handler SIGALRM déjà actif
est également refusé. L’API `reporting.register_target.read_result` reste
disponible sous un budget externe.

Le worker état vérifie BLAS1 capturé avant NumPy et encore présent, puis les
plafonds réels soft/hard AS <=768 Mio, CPU <=175 s et FSIZE <=8 Mio, avant
lecture de la tâche fichier et avant calcul. Cette garde ne pose aucune limite
sur l'appelant mémoire ; `calculate`/`experiment_for` restent des API à budget
externe. Le worker utilise une tâche fichier et une collecte native bornée,
sans envoi bloquant sur stdin. Les modes `--read-result`, `--worker-plan` et
`--worker` sont exclusifs entre eux et incompatibles avec les options du mode
plan (`--plan`, `--output-dir`, `--dry-run`, `--resume`, `--steps-this-run`).

Le lecteur exige toujours le plan original, les quatre entrées, les preuves et
le bundle R61 aux chemins enregistrés, ainsi que les sources compatibles du
logiciel. Il n'est donc **pas un lecteur hors ligne autonome**. Il ne prend aucun
pas et ne fait aucun fit/TMM. Il vérifie les checkpoints, états, continuité,
ledgers et résidus sur les endpoints acquis, puis recalcule observations,
PHASE et critères natifs. Il compare aussi les tables CSV aux données vérifiées.
Recomposer les SHA après avoir falsifié observations ou critères ne suffit pas.
Un résultat partiel, annulé, tardif ou sans clôture ne devient pas un succès.

## Préparer une acquisition existante

Le préparateur exécutable est `project_specs/examples/state_register/prepare.py`.
Il prend toutes les localisations en arguments, lit les artefacts R49/R59/R61,
vérifie leurs empreintes et normalise uniquement l'archive des cibles audit. Il
ne lance aucun calcul acoustique ou temporel. Voir son exemple adjacent.
Les supports R59 sauvegardés restent `upper=[3500,5500]` et
`edge=[5500,5990]` pour A comme B. Le rôle audit/extrapolation vient de
l'acquisition ; le domaine admis reste celui de sa source (3500 Hz pour A,
5500 Hz pour B).
La provenance garde les empreintes des acquisitions originales ; une simulation
TMM n'est jamais rebaptisée mesure. Des acquisitions d'autre origine peuvent
fournir le même contrat public, sous responsabilité documentaire explicite.

Les fixtures compactes B gardent les vrais coefficients, certificat,
réalisation et cibles acquis. Leur CONFIG ne change que les deux chemins des
fichiers matériaux pour rendre les tests portables ; cette relocation et le SHA
CONFIG d'origine sont déclarés. Ce ne sont pas des mesures d'instrument.

Le plan R49 préparé garde 36000 pas à 12000 Hz (3 s), 3500 Pa, les deux fenêtres
[2,2.5]/[2.5,3] s, cible 70 Hz ±5 cents, activité >=100 Pa et durée >=.49 s,
ainsi que tous les paramètres R49. Il est remis au pilote sans exécution de sa
trajectoire complète. Une démonstration courte ne résout pas cette cible.

## Lecteur C3 : coût borné et producteur historique explicite

Après admission scientifique complète, le lecteur construit une seule copie
native indépendante par `PassiveState.from_parameters` et vérifie l'égalité
exacte des paramètres. Les coefficients natifs sont immuables ; cette copie et
les copies locales des matrices (`L` comprise) ne survivent pas à l'appel.
Les solveurs de relecture gardent les méthodes natives de port et d'énergie.
Aucun `step`, `advance`, fit, TMM, conversion ou nouveau point de trajectoire
n'est exécuté. Formules, tolérances, sommes, ordre des lignes et observations
restent ceux du lecteur natif.

Le modèle original chargé depuis son fichier reste épinglé. Les inputs, le
plan, le bundle, les preuves et les sources sont contrôlés aux frontières,
au début de chaque chunk du plan (32 à 1200 lignes), et obligatoirement après
la dernière ligne avant confirmation. Les checkpoints et artefacts admis sont
également revérifiés avant retour. Une altération persistante pendant la
lecture refuse la confirmation, y compris dans le dernier chunk. Les contrôles
natifs par opération restent actifs sur la copie en mémoire ; ils n'ont plus
à re-hacher le gros fichier modèle pour chaque ligne.

La lecture stricte des empreintes courantes reste le défaut. Pour les données
acquises par le producteur exact `9caefe691f285d71f64ea3ccad1383d915f62969`,
l'exception suivante est explicite et exclusivement réservée à `saved_state` :

```bash
python -m tools.register_target --read-result chemin/run-10 \
  --reader-producer-sha 9caefe691f285d71f64ea3ccad1383d915f62969
```

```python
from didgeridoo_optimizer.reporting.register_target import read_result
reading = read_result("chemin/run-10",
    producer_sha="9caefe691f285d71f64ea3ccad1383d915f62969")
```

Le producteur enregistré doit annoncer exactement ce SHA, l'origine native
`git HEAD of verified worktree with tracked loaded D-Calc sources` et
`working_tree_dirty=false`. Cette déclaration reste documentaire, non une
signature d'acquisition. Seuls les quatre fichiers modifiés de plomberie et
lecture ci-dessous peuvent porter leurs empreintes historiques exactes :

| Fichier | SHA256 historique accepté |
| --- | --- |
| `didgeridoo_optimizer/reporting/register_target.py` | `71a720dbd7a8d9608064ab12c8fc20b9996219f8f4ae6f2fba192a10cb0c103b` |
| `didgeridoo_optimizer/reporting/state_register_context.py` | `f5dc669645946dcc34c1639b7ee37498a0633d01eee3e11e4f2e1f07a0702cd5` |
| `didgeridoo_optimizer/pipeline/register_target.py` | `3c3dd51edef50f6911ef58c2242423dcff029a883888b21921e47ed20943f6a3` |
| `tools/register_target.py` | `673f1b784b04625c3e048fe4d44753767a914b35456bfe81c70795a8e5b07aa4` |

Tout autre ancien hash, SHA, origine, source requise absente, version logicielle
ou différence des autres sources (physique, core, observateurs, critères inclus)
est refusé. Les instantanés des modules réellement chargés et les empreintes
actuelles du lecteur restent vérifiés séparément. Aucun ancien code n'est
importé ni exécuté depuis les données ou un checkout fourni par celles-ci.

Le reçu ajoute `reading` à côté de `result` : il distingue le producteur
historique du lecteur présent (SHA Git disponible, état dirty et empreintes
courantes), et énumère les seuls chemins compatibles. `result` et les fichiers
originaux ne sont ni réécrits ni re-hachés sur disque. L'exception est locale
à l'appel ; worker, préflight, reprise et évolution restent stricts. Un objet
expérience interne créé pour la lecture refuse `advance`. Les mêmes limites
CLI 175 s / 768 Mio / 8 Mio s'appliquent ; l'API ne modifie pas les limites,
handlers ou timers de son appelant.
