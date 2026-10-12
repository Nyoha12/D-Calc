# ADJUSTMENT-POLICY-01 — commandes sur fabrication fixée

Ce raccord applique une politique explicite à un instrument fourni par un JOB
`dcalc.tolerance_audit.v1`. Il appelle son `load_inputs`, ses scénarios natifs et
`perturb`. CONFIG, base de matériaux, REQUEST complet, rôles, tolérances, scopes,
modèles, recettes, pièces et origine des incertitudes sont conservés dans le plan.
Les variables historiques ne deviennent pas de nouvelles libertés de conception.
Aucune réparation de pièce, physique, calibration ou validation jouée n'est ajoutée.

## API et CLI

```python
from didgeridoo_optimizer.pipeline.adjustment_policy import load_inputs, run
prepared, plan = load_inputs("job.json")  # préflight sans acoustique
response = run("job.json", "nouvelle_sortie", dry_run=False)

from didgeridoo_optimizer.reporting.adjustment_policy import read_result
bundle = read_result("nouvelle_sortie")
```

```bash
python -B -m tools.adjustment_policy --job JOB --output-dir NOUVEAU --dry-run
python -B -m tools.adjustment_policy --job JOB --output-dir NOUVEAU
```

Le dry-run réussi est silencieux, sans recherche, TMM, maillage de calcul ou
écriture. `-B` empêche aussi les écritures de bytecode avant l'entrée de la CLI.
Le code de retour 0 indique une exécution complète, pas la conformité physique.
Le code 1 indique une exécution incomplète et le code 2 un refus/échec de lancement.
Une sortie existante ne peut pas être écrasée. `execute` est une primitive interne
synchrone : son appelant doit imposer les mêmes limites que le superviseur public.

## JOB v1 strict

Tous les champs suivants sont obligatoires. Les champs inconnus, booléens
numériques, doublons, cycles, nombres non finis et références conflictuelles sont
refusés par les lecteurs/contrats natifs et le contrat de politique. `audit_job`
est relatif au JOB, désigne un JOB natif, et peut référencer un exemple existant.

```json
{
  "schema_version": "dcalc.adjustment_policy.v1",
  "audit_job": "audit.json",
  "policy": {
    "kind": "shared_offset",
    "block": "slide",
    "q": {"unit": "m", "bounds": [0.05, 0.35]},
    "delta": {"unit": "m", "bounds": [-0.02, 0.02]}
  },
  "information": "scenario_known",
  "budgets": {
    "candidates": 65, "evaluations": 90, "iterations": 12,
    "projections": 360, "spectral_calls": 10000,
    "frequencies": 2000000, "frequency_segment_product": 200000000,
    "segments": 200000, "seconds": 60, "memory_mib": 700, "output_mib": 20
  }
}
```

Les plafonds v1 sont : 65 sous-problèmes (`candidates`), 300 évaluations,
30 itérations par sous-problème, 2048 projections cumulées, 10000 appels spectraux,
2 millions de fréquences cumulées, 200 millions de produits fréquence-segment,
200000 segments cumulés, 165 secondes scientifiques, 700 Mio de RSS et 60 Mio
d'exports. Les budgets du JOB audit et des demandes natives restent aussi validés.
Les produits, grilles minimales et représentations sont contrôlés avant allocation;
les maillages, appels et raffinements utilisent ensuite le `Budget` natif partagé.
Les compteurs d'essais comprennent les refus mécaniques et les sondes. Le contrôle
et la régénération des exports ne sont pas de nouveaux essais acoustiques.

## Politiques et quantificateurs

| Politique | Commandes autorisées | Quantification |
| --- | --- | --- |
| `fixed` | Aucune variable; `policy` contient seulement `kind` | Toutes les fabrications aux commandes d'entrée, DESIGN ou ASSEMBLY |
| `per_configuration` | Un bloc nommé, un q SI borné par configuration | Une commande commune à toutes les projections de cette configuration dans chaque fabrication connue |
| `shared_offset` | Un bloc nommé, un delta SI borné et des bornes absolues q | Le même delta pour toutes les configurations/projections d'une fabrication; carte entière évaluée ensemble |

`per_configuration` omet `delta`. La graine est le q nominal de chaque
configuration. `shared_offset` commence à delta=0. Les graines hors bornes sont
refusées, sans clamp. Toutes les configurations déclarées doivent avoir leurs
projections, conformément au contrat d'assemblage natif. Les autres commandes,
annotations, matériaux et topologie restent gelés.

Les deux modes adaptatifs exigent `information="scenario_known"`. Il s'agit
d'existence conditionnelle lorsque la fabrication est connue du calcul, sans
identification de fabrication ni contrôleur disponible pour le joueur. Une table
par scénario n'est pas une règle utilisable sans cette information. `fixed`
accepte `none` ou `scenario_known`. Aucune autre portée n'est convertie implicitement.

Les catalogues non triviaux sont refusés. Une liaison historique de plusieurs
champs touchée par une commande, ou une relation dérivée dépendant d'une commande
sélectionnée, est explicitement refusée comme capacité non implémentée. Les autres
liaisons et relations restent validées par les contrats/scénarios natifs. Les
bornes de conception passées ne sont pas imposées aux écarts de fabrication.
Les paths d'assemblage restent documentaires : aucun continuum n'est certifié.

### Loi numérique du décalage commun

Pour chaque configuration, en unités SI :

```python
q_float = float(Fraction(str(q_nominal)) + Fraction(str(delta)))
```

Le setter natif reçoit `q_float`. Les exports conservent la somme rationnelle et
`Fraction(str(q_float)) - somme_rationnelle`. Les bornes absolues sont contrôlées
sur la quantité décimale sérialisée selon cette loi. Aucune tolérance mécanique
ou musicale n'est élargie. Un écart exact de pièce non représentable reste
`ScenarioUnavailable`; seules les commandes suivent cette convention annoncée.

## Recherche et résultats

Chaque sous-problème utilise `constrained_search.search`, avec zéro variable pour
`fixed`, une pour les deux autres politiques. Toutes les évaluations demandent
`verify=True`; les sondes et leurs raffinements sont comptés. Les hard restent
hard. Une obligation hard unsupported limite la recherche à l'évaluation initiale;
sa portée demeure explicitement indisponible. Une observation optionnelle absente
n'annule pas un témoin dont les obligations hard indépendantes sont vérifiées.

Chaque essai est sauvegardé avant le retour au solveur. Un témoin déjà vérifié,
y compris une sonde, survit à l'épuisement ultérieur du budget. Un groupe non
résolu n'efface pas les témoins des autres groupes. Les q retenus sont réunis en
un seul instrument physique par scénario; les profils sont régénérés et leurs
segments comparés à ceux de l'essai retenu, sans nouvelle acoustique.

- `candidate_violated` décrit un essai; en adaptatif il ne démontre aucune impossibilité.
- `witness_found` et `verified` désignent un témoin conservé satisfaisant tous les hard du groupe.
- `search_not_resolved`, `unsupported` et `out_of_domain` restent distincts.
- `execution_complete` décrit le calcul et sa clôture; il ne garantit aucune conformité.
- `sampled_policy_conforming` exige tous les groupes témoins et la grille de scénarios/projections complète.
- `request_fully_covered` exige aussi les observations/scopes déclarés, même optionnels.
- `counterexample_found` concerne uniquement les commandes imposées en `fixed`.

Une géométrie invalide à la graine peut arrêter la recherche locale : sa cause est
conservée, sans réparation ni verdict d'impossibilité. Une contradiction native
physique est séparée d'une indisponibilité technique. Aucun résultat ne porte
`infeasibility_proven=true`, une garantie d'optimum ou de robustesse continue.

## Exports, supervision et relecture

Les JSON sont stricts et immuables. Le bundle contient le plan, les essais et
leurs traces attribuées, les observations finales, commandes, profils natifs,
assemblages physiques, demandes rechargeables, cinq CSV (commandes, critères,
scénarios, écarts, marges), une synthèse française et le reçu terminal natif.
Les assemblages préservent l'ordre natif des pièces. Les exports propres à ce run
ne réattribuent jamais l'identité d'une ancienne archive.

Le parent lance un seul enfant POSIX, BLAS1, AS 768 Mio, CPU 175 s, mur 180 s.
Le délai public de 200 s réserve 10 s cumulées pour récolter cet enfant et 2 s de
clôture. Les logs sont des fichiers bornés par RLIMIT_FSIZE (4 Mio par fichier,
également plafond natif JSON); leur lecture diagnostic est limitée à 64 Kio.
Tous les `communicate`/`wait` sont bornés. Signaux, timeout, retour tardif, défaut
de fermeture ou code enfant non nul interdisent un faux succès. Le protocole
terminal natif est publié après la restauration des signaux; les observations
partielles restent lisibles sans être promues en résultat complet.

Le lecteur frais n'importe pas `pipeline.adjustment_policy`, ne relance ni
recherche ni TMM, et utilise la génération géométrique native. Il vérifie les
hashes relatifs, le manifeste exhaustif, les recettes/scénarios du plan original,
les pièces partagées, les groupes et commandes, bornes et arrondis, la grille
complète, demandes/rôles, profils régénérés, marges, agrégats, CSV et synthèse.
Les versions et empreintes des sources chargées/exécutées sont contrôlées.
Une CLI copiée ne peut pas revendiquer la provenance de celle du dépôt.
Les scalaires acoustiques sont des observations conservées, pas une nouvelle
certification acoustique. Un bundle entièrement réécrit n'est pas authentifié
par une autorité extérieure.

## Exemples et vérifications bornées

`examples/adjustment_policy/fixed.json` référence directement l'exemple R52 pour
la parité native. `per_configuration.json` et `shared_offset.json` utilisent le
même assemblage synthétique de trois configurations et les mêmes trois
fabrications prescrites. Leurs demandes (longueur, f1, f2, f3, ratio f2/f1) sont
déclarées avant calcul dans `request.json`. Leurs larges fenêtres acoustiques sont
un test de raccord; elles ne reprennent ni ne reclassent une carte R53.

Les tests dédiés incluent les oracles indépendants de longueur, les deltas
incompatibles entre configurations, plusieurs projections pour un q, les
obligations jouées indisponibles, le budget après témoin, les archives falsifiées
avec hashes cohérents, la vraie CLI, la relecture fraîche et la supervision par
doubles sans processus réellement bloqué. Les suites historiques demandées sont
exécutées séparément de toute suite globale, calibration ou validation A–E.
