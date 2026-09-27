# Accorder et comparer vos propres profils

`tools.design_pitch_compare` charge une CONFIG et **1 à 4 fichiers physiques DESIGN JSON/YAML**. Il ajuste toutes les longueurs d'un même facteur, conserve diamètres/profils/matériaux/annotations, puis compare les réponses sous une source acoustique commune. Il ne cherche aucun optimum universel et n'exécute ni optimiseur, ni calibration, ni validation A–E.

Depuis la racine du dépôt, les trois exemples sont réellement rechargeables avec la base `pvc_pressure` existante :

```bash
python -m tools.design_pitch_compare \
  --config project_specs/examples/design_pitch/config.yaml \
  --design project_specs/examples/design_pitch/cylinder.json \
           project_specs/examples/design_pitch/cone.yaml \
           project_specs/examples/design_pitch/exponential.yaml \
  --target-hz 70 --scale-min 0.8 --scale-max 1.4 \
  --pressure-peak-pa 1 --output-dir results/mes-profils --dry-run
```

Retirez `--dry-run` pour calculer dans un **nouveau** répertoire. Le préflight est portable : il lit les fichiers et la base, valide les sources, modèles, géométries aux bornes, budgets et destinations, prépare les maillages bornés, sans propagation ni création de résultat. Le calcul borné nécessite POSIX ; sur les autres plateformes il refuse avant toute écriture. Les entrées restent inchangées.

Parcours court d'un profil, pertes et radiation legacy par défaut :

```bash
python -m tools.design_pitch_compare \
  --config project_specs/examples/design_pitch/config.yaml \
  --design project_specs/examples/design_pitch/cylinder.json \
  --target-hz 70 --pressure-peak-pa 1 --output-dir results/accord-pvc
```

Comparaison ZK/Silva explicite de deux profils, avec résistance de source **absolue identique**, et maillage d'accord de 1 cm / vérification de 0,5 cm :

```bash
python -m tools.design_pitch_compare \
  --config project_specs/examples/design_pitch/config.yaml \
  --design project_specs/examples/design_pitch/cylinder.json \
           project_specs/examples/design_pitch/cone.yaml \
  --target-hz 70 --scale-min 0.8 --scale-max 1.4 --h-cm 1 \
  --thevenin-pressure-peak-pa 1 --source-resistance-pa-s-m3 500000 \
  --loss-model zk --air-reference ck_dry20 --radiation-model silva_unflanged \
  --output-dir results/accord-zk-silva
```

Choisissez **exactement une source** : `--pressure-peak-pa P`, `--flow-peak-m3-s U`, ou `--thevenin-pressure-peak-pa Ps` avec `--source-resistance-pa-s-m3 Rs`. Les amplitudes crête sont finies et strictement positives ; Rs est fini, réel et ≥0. Rs=0 est autorisé en Thévenin et reste identifié comme tel. Une seule propagation de réponse forcée est effectuée par géométrie originale/accordée ; aucune source supplémentaire n'est simulée.

En legacy, l'air vient exactement de CONFIG et `--air-reference` est refusé. En ZK, `ck_dry20` ou `ck_dry25` est obligatoire ; l'air d'origine et la substitution explicite sont exportés. ZK modélise des parois rigides, lisses et étanches : les fiches et statuts matériaux restent conservés, sans promotion ni interprétation comme calibration. Radiation : `legacy` par défaut, `silva_unflanged` ou `silva_flanged` sur demande. Aucune option ne change les défauts de l'optimiseur.

## Contrat des entrées

Le chargement emploie `load_fixed_context` et la frontière stricte `design_input`. Les doublons de clés, annotations non JSON finies, matériaux inconnus et géométries invalides sont refusés. Les chemins DESIGN sont exacts ; les chemins matériaux sont interprétés relativement à CONFIG ou comme chemins absolus. Si la résolution historique trouve un autre fichier depuis le répertoire courant, le workflow refuse cette ambiguïté et demande un chemin absolu. Aucun matériau de test n'est injecté.

Un ID peut contenir slash, Unicode, espaces et caractères de chemin : il reste une **étiquette**, jamais un nom de fichier. Les mêmes chemins physiques, même via symlink/hardlink, et les IDs répétés sont refusés explicitement. Des fichiers de même basename dans des répertoires différents sont acceptés si leurs IDs sont distincts. Le champ dérivé `metadata.total_length_cm` est recalculé par le chargeur natif ; les annotations utilisateur et un éventuel `longitudinal_factor` restent conservés. Les positions sont recalculées à partir des longueurs.

## Accord, vérification et bornes

- Cible 50..90 Hz ; premier maximum de `|Zin|` identifié dans 10..700 Hz, jamais le plus grand pic. Les fonctions existantes `solve_factor`, `first_peak`, `scale_design` et `extract_modes` sont réutilisées sans modification.
- Bornes par défaut 0,8..1,4, modifiables dans `0.25 <= scale_min < scale_max <= 4`. Les deux extrémités et la géométrie originale doivent satisfaire CONFIG. Aucune contrainte n'est assouplie et chaque proposition est revalidée.
- Le facteur est trouvé par mesures acoustiques, pas par une hypothèse de proportionnalité exacte 1/L. Un intervalle ne contenant pas la cible échoue avec son historique.
- Maillage d'accord : `--h-cm`, sinon CONFIG ; h positif ≤2 cm. La même géométrie physique est vérifiée à h et h/2, **sans réaccord**. Les deux extractions doivent vérifier la cible à **0,002 Hz**, avec cohérence du premier pic et des raffinements fréquentiels.
- Trois premiers modes, Q à demi-puissance et raisons d'indisponibilité sont conservés. Une absence de mode ou une ambiguïté empêche le succès. Un Q absent et motivé peut accompagner un mode résolu.
- Maximum 128 segments physiques, 20 000 fréquences, 10 000 tranches, 2 000 000 cellules par évaluation. `prepare_mesh`/`check_budget` précèdent les allocations de maillage/propagation. Raffinements fixes 1025/2049 points ; itérations 16 par défaut, 1..64 autorisées.
- Enfants séquentiels, BLAS à un thread, espace d'adressage 768 Mio, CPU 175 s, temps mural ≤180 s par profil et ≤420 s pour le run numérique. Un timeout tue et attend l'enfant ; une interruption SIGINT/SIGTERM du parent conserve un bilan partiel et ne lance plus de profil.

## Lire les sorties

`stdout` contient une réponse JSON finale unique ; les progrès vont sur `stderr`. Code 0 signifie que tous les profils et comparaisons ont été vérifiés. Un simple code enfant 0 ne suffit pas. Erreur de préflight : objet `error` avec `type` et `message`, aucune sortie. Échec numérique : code non nul, statut `partial`, traces et sorties déjà acquises conservées. Un répertoire existant, même partiel, n'est jamais écrasé.

| Sortie | Contenu |
|---|---|
| `plan.json` | Table index/ID/chemin/hash, bornes, paramètres, budgets, destinations prévues |
| `original_design_001.json`, `tuned_design_001.json` | Géométries physiques rechargeables avec la même CONFIG et la même base |
| `trace_001.jsonl`, `tuning_001.json` | Propositions, mesures, erreurs, historique d'accord ; la trace survit au timeout |
| `original_001.json`, `validation_001.json` | Modes et Q, résidus, empreintes des géométries à h/h2, deltas de maillage |
| `response_original_001/`, `response_tuned_001/` | Bundles natifs forced_response JSON/CSV/TXT : v1 pression/débit, v2 Thévenin |
| `comparison_before_after_001/`, `comparison_001_002/` | Comparateur existant : avant/après et profils accordés contre le premier profil fourni |
| `job_001.json`, `profile_001.json` | Codes enfants, durée, sorties, statut et raisons d'échec |
| `summary.json`, `summary.csv`, `report.md` | Original/accordé côte à côte : longueur, f1, f2/f1, Q1, Pload cible, résidu et statut |

Les bundles utilisent tous **la même grille** : cible ×1..7 et cible ±1/±5 Hz, au maillage fin. Les pics propres ne sont pas ajoutés à cette grille de comparaison. Les rapports proviennent du comparateur natif, sans ratio ajouté ni interpolation. Ils décrivent des écarts conditionnels, sans classement instrumental, causalité isolée ni rendement physiologique. Les hashes des entrées, des sources chargées, l'air et les modèles rendent le calcul traçable.

Recharger un export physique sans acoustique :

```bash
python -m didgeridoo_optimizer.pipeline.run_optimizer \
  --config project_specs/examples/design_pitch/config.yaml \
  --design results/accord-pvc/tuned_design_001.json --dry-run
```

Voir aussi [le contrat IO courant](USER_IO_CONTRACT_CURRENT.md). Les CLI historiques et `equal_pitch_study` restent inchangés.

### Revue de livraison

Les tests résident dans `didgeridoo_optimizer/tests`. La provenance vérifie les sources réellement chargées et leur appartenance au même worktree ; une racine mélangée laisse le SHA non établi. Les comparaisons identifient le vrai CLI producteur. Les annotations utilisateur sont conservées, tandis que `metadata.total_length_cm`, champ dérivé par le builder historique, est recalculé et vérifié.
