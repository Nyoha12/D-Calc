# Préparation et exécution bornée

Depuis la racine du checkout, avec l'environnement Python existant :

```bash
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
export PYTHONPATH=.
python project_specs/examples/state_register/prepare.py \
  --config /mes-entrees/config.json --design /mes-entrees/design.json \
  --template /mes-entrees/experiment.json \
  --acquisition /mes-acquisitions/case-B \
  --realization /mes-acquisitions/realization-B \
  --date 2026-10-17 --output /mes-sorties/contexte-B
python -m tools.register_target --plan /mes-sorties/contexte-B/plan-r49.json \
  --output-dir /mes-sorties/controle --dry-run
```

Aucun chemin privé n'est codé dans le préparateur. Les fichiers CONFIG et DESIGN
fournis doivent correspondre aux empreintes d'acquisition R59 ; une relocation
conserve leurs octets, ainsi que l'accès aux chemins matériaux qu'ils désignent.
Sur un autre poste, ajouter `--materials /mes-db/materials.yaml` et
`--variant-rules /mes-db/variants.yaml` : le préparateur conserve le CONFIG original
pour vérifier son empreinte puis crée `config-relocated.json`. Seuls ces deux
chemins changent ; les empreintes des DB doivent rester celles de R59.
Modifier une géométrie, une condition physique ou une DB exige une acquisition
correspondante, pas le remplacement d'une étiquette. Pour une acquisition hors
R59, utiliser directement le contrat public décrit dans STATE_REGISTER_01.md.

Pour A, fournir `case-A` et `realization-A` aux mêmes options. Les supports
sauvegardés `upper=[3500,5500]` et `edge=[5500,5990]` restent identiques pour
A et B ; seuls leurs rôles audit/extrapolation suivent l'acquisition. Le
préparateur conserve le domaine admis et ne produit aucune nouvelle trajectoire.

Le préparateur ne joue pas le plan. Pour une acquisition temporelle partielle,
`--steps-this-run 128` conserve le plan complet et ses fenêtres futures non
acquises. La sortie sera partielle (code 1), jamais une conformité à 70 Hz.
La reprise utilise le checkpoint annoncé dans `result.json` et une nouvelle
sortie. La trajectoire entière de 3 s relève d'un budget distinct du pilote.
Le code 0 signifie trajectoire complète ; consulter séparément `hard_conforming`
pour les critères. `null`/`unresolved` n'est ni zéro Hz ni un critère réussi.
